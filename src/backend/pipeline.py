"""
Pipeline 编排器
==============
两段一条流，单一事实源在 Redis，图谱在 RAGFlow GraphRAG：

    run_fetch:  IMAP → 解析(含附件) → 清洗 → 噪音过滤
                → Redis 暂存正文 + 入 ingest 队列 + 附件落盘
    run_ingest: 遍历 Redis 队列 → 邮件原文 + 附件(DeepDoc) 上传 RAGFlow
                → GraphRAG 单遍跨文档建图 → 标记已入库 + 删本地附件(即用即删)

不再有 fetched_mails.json / extracted_mails.json 中转，也不再做 OpenAI 结构化提取。
"""
import logging
from datetime import datetime
from pathlib import Path

from config.settings import get_settings

logger = logging.getLogger("pipeline")


class Pipeline:
    """邮件处理流水线 — RAGFlow GraphRAG 为核心"""

    def __init__(self, account_id: str | None = None):
        self.cfg = get_settings()
        # 解析当前账号：未指定则迁移 env 邮箱并取默认账号
        from src.backend.storage.account_store import AccountStore
        store = AccountStore()
        try:
            if account_id:
                self.account = store.get(account_id)
            else:
                store.ensure_default_from_env()
                did = store.default_id()
                self.account = store.get(did) if did else None
        finally:
            store.close()
        self.account_id = self.account["id"] if self.account else None
        # 每账号独立 dataset
        self.dataset_name = (
            f"{self.cfg.ragflow_dataset_name}-{self.account_id}"
            if self.account_id else self.cfg.ragflow_dataset_name
        )

    # ══════════════════════════════════════════════
    # 阶段一：拉取 → 清洗 → Redis 暂存
    # ══════════════════════════════════════════════

    def run_fetch(self, folder: str = "INBOX", limit: int = 20,
                  since: str | None = None, on_log=None) -> int:
        """拉取邮件，清洗后暂存到 Redis（并入 ingest 队列）。返回入队邮件数。"""
        from src.backend.mail.imap_client import IMAPClient
        from src.backend.mail.cleaner import MailCleaner
        from src.backend.storage.redis_cache import MailCache

        log = on_log or (lambda m: logger.info(m))
        if not self.account:
            log("未配置邮箱账号，请先在工作台添加")
            return 0
        cache = MailCache(self.account_id)
        cleaner = MailCleaner()
        since_dt = datetime.fromisoformat(since) if since else None

        # 附件下载目录（按批次落盘，ingest 后即删）
        attach_root = self.cfg.resolve_data_path("attachments")

        queued = 0
        try:
            with IMAPClient(self.account) as client:
                uids = client.search_uids(folder=folder, since=since_dt)
                if not uids:
                    log("未找到邮件")
                    return 0

                # 去重：先剔除该 folder 下已入库的 UID，再取最新 limit 封，
                # 保证每次都推进 backlog（否则最新 N 封若都已入库会永远捞不到老邮件）
                processed_uids = cache.get_processed_uids(folder)
                uids = [u for u in uids if u not in processed_uids]
                if not uids:
                    log("没有新邮件（均已入库）")
                    return 0
                uids = uids[-limit:]
                log(f"拉取 {len(uids)} 封邮件...")

                for uid, msg in client.fetch_batch(uids, folder=folder):
                    parsed = self._store_fetched_mail(uid, msg, folder, cache, cleaner, attach_root)
                    if parsed is not None:
                        queued += 1
                        log(f"  [{queued}] {parsed.subject[:50]}")
        finally:
            cache.close()

        log(f"已入队 {queued} 封邮件，待 ingest")
        return queued

    def _store_fetched_mail(self, uid, msg, folder, cache, cleaner, attach_root):
        """解析 + 清洗 + 噪音过滤 + 暂存一封邮件。

        入队成功返回 parsed 对象；被去重/噪音跳过或失败返回 None。
        run_fetch 与 reprocess 共用此逻辑。
        """
        from src.backend.mail.parser import parse_email

        parsed = None
        try:
            # 关键：传 download_dir，附件才会被提取（旧流程漏了这一步）
            dl_dir = Path(attach_root) / _safe(uid)
            parsed = parse_email(msg, download_dir=dl_dir)

            # message_id 级兜底去重（UID 复用 / 跨 folder 同信时 UID 过滤会漏）
            if cache.is_processed(parsed.message_id):
                return None

            cleaned = cleaner.clean(parsed.body_text, parsed.body_html)

            cache.mark_processing(
                parsed.message_id, uid, folder,
                parsed.subject, parsed.from_addr, parsed.date,
                from_name=parsed.from_name,
                attachment_count=len(parsed.attachments or []),
            )

            if self.cfg.enable_noise_filter and \
                    cleaner.is_noise_email(parsed.subject, parsed.from_addr, cleaned):
                cache.mark_skipped(parsed.message_id, "噪音邮件")
                return None

            cache.store_mail({
                "message_id": parsed.message_id,
                "uid": uid,
                "folder": folder,
                "subject": parsed.subject,
                "from_addr": parsed.from_addr,
                "from_name": parsed.from_name,
                "to_addrs": parsed.to_addrs,
                "cc_addrs": parsed.cc_addrs,
                "date": parsed.date,
                "timestamp": parsed.timestamp,
                "cleaned_body": cleaned,
                "attachments": [
                    {"filename": a["filename"], "path": a["path"],
                     "mime_type": a.get("mime_type", ""), "size": a.get("size", 0)}
                    for a in parsed.attachments
                ],
            })
            return parsed
        except Exception as e:
            logger.error(f"  UID {uid} 处理失败: {e}")
            # 已解析出 message_id 的落 failed 状态，避免永远卡在 processing
            if parsed is not None:
                cache.mark_failed(parsed.message_id, str(e))
            return None

    # ══════════════════════════════════════════════
    # 阶段二：Redis → RAGFlow GraphRAG
    # ══════════════════════════════════════════════

    def run_ingest(self, limit: int | None = None, on_log=None) -> dict:
        """把 Redis 队列里的邮件（原文 + 附件）上传到 RAGFlow，GraphRAG 建图。"""
        from src.backend.attachment.ragflow_client import get_ragflow_client
        from src.backend.storage.redis_cache import MailCache

        log = on_log or (lambda m: logger.info(m))
        rf = get_ragflow_client(self.account_id)
        rf.get_or_create_dataset(self.dataset_name)

        cache = MailCache(self.account_id)
        stats = {"total": 0, "uploaded": 0, "failed": 0, "attachments": 0}
        doc_ids: list[str] = []

        try:
            pending = list(cache.iter_pending_mails())
            if limit:
                pending = pending[:limit]
            stats["total"] = len(pending)
            if not pending:
                log("ingest 队列为空")
                return stats

            log(f"ingest {len(pending)} 封邮件到 GraphRAG...")

            for i, mail in enumerate(pending):
                mid = mail.get("message_id", "")
                subj = (mail.get("subject") or "(无主题)")[:50]
                try:
                    # 1) 邮件原文 → GraphRAG 建图
                    doc_id = rf.upload_email(mail)
                    if not doc_id:
                        stats["failed"] += 1
                        if mid:
                            cache.mark_ingest_failed(mid, "RAGFlow 邮件正文上传失败")
                        log(f"  [{i+1}] ✗ {subj}")
                        continue
                    doc_ids.append(doc_id)

                    # 2) 附件 → DeepDoc 解析后并入同一 dataset
                    uploaded_paths: list[str] = []
                    att_doc_ids: list[str] = []
                    for att in mail.get("attachments", []):
                        apath = att.get("path", "")
                        if apath and Path(apath).exists():
                            adoc = rf.upload_file(apath, att.get("filename"))
                            if adoc:
                                doc_ids.append(adoc)
                                att_doc_ids.append(adoc)
                                stats["attachments"] += 1
                                uploaded_paths.append(apath)
                            else:
                                logger.warning("  附件上传失败，保留本地文件待排查: %s", apath)

                    # 记录正文 + 附件全部 doc_id，供强制重处理时一并删除，避免孤儿/重复
                    cache.mark_ingested(mid, doc_id, drop_body=True, att_doc_ids=att_doc_ids)
                    # 只删上传成功的附件；失败的留在本地（RAGFlow 未收到，删了就永久丢）
                    _cleanup_attachments(mail, only_paths=uploaded_paths)
                    stats["uploaded"] += 1
                    log(f"  [{i+1}] ✓ {subj} → {doc_id}")
                except Exception as e:
                    stats["failed"] += 1
                    if mid:
                        cache.mark_ingest_failed(mid, str(e))
                    logger.error(f"  [{i+1}] ✗ {subj}: {e}")

            # 3) 触发解析（向量化 + GraphRAG 实体提取同步完成）
            if doc_ids:
                log(f"触发 RAGFlow 解析 {len(doc_ids)} 个文档（含 GraphRAG 建图）...")
                rf.start_parsing(doc_ids)
                if rf.wait_for_parsing(doc_ids):
                    log("解析完成，GraphRAG 图谱已同步构建")
                else:
                    log("⚠ 文档解析超时，可稍后重试")
        finally:
            cache.close()

        log(f"完成：上传 {stats['uploaded']}/{stats['total']} 封 "
            f"(附件 {stats['attachments']}，失败 {stats['failed']})")
        return stats

    # ══════════════════════════════════════════════
    # 完整流程
    # ══════════════════════════════════════════════

    def run_full(self, folder: str = "INBOX", limit: int = 100,
                 since: str | None = None, on_log=None) -> dict:
        """fetch + ingest 一条龙"""
        self.run_fetch(folder=folder, limit=limit, since=since, on_log=on_log)
        return self.run_ingest(on_log=on_log)

    # ══════════════════════════════════════════════
    # 强制重新处理（绕过幂等）
    # ══════════════════════════════════════════════

    def reprocess(self, message_ids: list[str], on_log=None) -> dict:
        """强制重新处理指定邮件（单封或批量）。

        步骤：删旧 RAGFlow 文档 → 重置状态（移出 done_uids）→ 重新从 IMAP
        拉正文入队 → run_ingest 重新建图。先删后传，避免图谱里重复实体。
        """
        from src.backend.attachment.ragflow_client import get_ragflow_client
        from src.backend.storage.redis_cache import MailCache
        from src.backend.mail.imap_client import IMAPClient
        from src.backend.mail.cleaner import MailCleaner

        log = on_log or (lambda m: logger.info(m))
        if not self.account:
            log("未配置邮箱账号")
            return {"total": 0, "uploaded": 0, "failed": 0, "attachments": 0, "reset": 0, "requeued": 0}
        cache = MailCache(self.account_id)
        cleaner = MailCleaner()
        attach_root = self.cfg.resolve_data_path("attachments")

        rf = get_ragflow_client(self.account_id)
        rf.get_or_create_dataset(self.dataset_name)

        reset_n = 0
        requeued = 0
        try:
            # 1) 删旧文档 + 重置状态，按 folder 归组待重拉的 uid
            by_folder: dict[str, list[str]] = {}
            for mid in message_ids:
                meta = cache.get_mail_state(mid)
                if not meta:
                    continue
                # 删旧的正文 doc + 附件 doc，避免图谱里残留重复实体
                old_docs = [d for d in (
                    [meta.get("ragflow_doc_id", "")]
                    + (meta.get("ragflow_att_doc_ids", "") or "").split(",")
                ) if d]
                if old_docs:
                    rf.delete_document(old_docs)
                cache.reset_email(mid)  # 清 done 标记 + 移出 done_uids
                uid = meta.get("uid", "")
                folder = meta.get("folder", "INBOX")
                if uid:
                    by_folder.setdefault(folder, []).append(uid)
                reset_n += 1

            log(f"已重置 {reset_n} 封，重新拉取正文...")

            # 2) 重新从 IMAP 拉这些 uid → 重新入队
            if by_folder:
                with IMAPClient(self.account) as client:
                    for folder, uids in by_folder.items():
                        for uid, msg in client.fetch_batch(uids, folder=folder):
                            if self._store_fetched_mail(uid, msg, folder,
                                                        cache, cleaner, attach_root) is not None:
                                requeued += 1
            log(f"重新入队 {requeued} 封，开始重新建图...")
        finally:
            cache.close()

        # 3) 重新 ingest（走既有队列逻辑）
        stats = self.run_ingest(on_log=on_log)
        stats["reset"] = reset_n
        stats["requeued"] = requeued
        return stats


def _safe(s: str) -> str:
    import re
    return re.sub(r"[^0-9A-Za-z]+", "_", str(s))[:32]


def _cleanup_attachments(mail: dict, only_paths: list[str] | None = None):
    """即用即删：删除本封邮件的临时附件。

    only_paths 为 None → 删全部（保持旧行为）；否则仅删名单内（上传成功）的文件，
    上传失败的保留在本地。目录仅在清空后才删，避免误删残留的失败附件。
    """
    allow = set(only_paths) if only_paths is not None else None
    dirs = set()
    for att in mail.get("attachments", []):
        p = att.get("path", "")
        if not p:
            continue
        if allow is not None and p not in allow:
            continue
        try:
            Path(p).unlink(missing_ok=True)
            dirs.add(str(Path(p).parent))
        except Exception:
            pass
    for d in dirs:
        try:
            dp = Path(d)
            if dp.is_dir() and not any(dp.iterdir()):
                dp.rmdir()
        except Exception:
            pass
