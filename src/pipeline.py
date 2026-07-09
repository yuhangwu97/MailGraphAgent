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

    def __init__(self):
        self.cfg = get_settings()

    # ══════════════════════════════════════════════
    # 阶段一：拉取 → 清洗 → Redis 暂存
    # ══════════════════════════════════════════════

    def run_fetch(self, folder: str = "INBOX", limit: int = 20,
                  since: str | None = None, on_log=None) -> int:
        """拉取邮件，清洗后暂存到 Redis（并入 ingest 队列）。返回入队邮件数。"""
        from src.mail.imap_client import IMAPClient
        from src.mail.parser import parse_email
        from src.mail.cleaner import MailCleaner
        from src.storage.redis_cache import MailCache

        log = on_log or (lambda m: logger.info(m))
        cache = MailCache()
        cleaner = MailCleaner()
        since_dt = datetime.fromisoformat(since) if since else None

        # 附件下载目录（按批次落盘，ingest 后即删）
        attach_root = self.cfg.resolve_data_path("attachments")

        queued = 0
        try:
            with IMAPClient() as client:
                uids = client.search_uids(folder=folder, since=since_dt)
                if not uids:
                    log("未找到邮件")
                    return 0

                uids = uids[-limit:]
                log(f"拉取 {len(uids)} 封邮件...")

                for uid, msg in client.fetch_batch(uids, folder=folder):
                    try:
                        # 关键：传 download_dir，附件才会被提取（旧流程漏了这一步）
                        dl_dir = Path(attach_root) / _safe(uid)
                        parsed = parse_email(msg, download_dir=dl_dir)
                        cleaned = cleaner.clean(parsed.body_text, parsed.body_html)

                        cache.mark_processing(parsed.message_id, uid, folder,
                                              parsed.subject, parsed.from_addr, parsed.date)

                        if self.cfg.enable_noise_filter and \
                                cleaner.is_noise_email(parsed.subject, parsed.from_addr, cleaned):
                            cache.mark_skipped(parsed.message_id, "噪音邮件")
                            continue

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
                                {"filename": a["filename"], "path": a["path"]}
                                for a in parsed.attachments
                            ],
                        })
                        queued += 1
                        log(f"  [{queued}] {parsed.subject[:50]}")
                    except Exception as e:
                        logger.error(f"  UID {uid} 处理失败: {e}")
        finally:
            cache.close()

        log(f"已入队 {queued} 封邮件，待 ingest")
        return queued

    # ══════════════════════════════════════════════
    # 阶段二：Redis → RAGFlow GraphRAG
    # ══════════════════════════════════════════════

    def run_ingest(self, limit: int | None = None, on_log=None) -> dict:
        """把 Redis 队列里的邮件（原文 + 附件）上传到 RAGFlow，GraphRAG 建图。"""
        from src.attachment.ragflow_client import get_ragflow_client
        from src.storage.redis_cache import MailCache

        log = on_log or (lambda m: logger.info(m))
        rf = get_ragflow_client()
        rf.get_or_create_dataset(self.cfg.ragflow_dataset_name)
        rf.enable_graphrag()

        cache = MailCache()
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
                        log(f"  [{i+1}] ✗ {subj}")
                        continue
                    doc_ids.append(doc_id)

                    # 2) 附件 → DeepDoc 解析后并入同一 dataset
                    for att in mail.get("attachments", []):
                        apath = att.get("path", "")
                        if apath and Path(apath).exists():
                            adoc = rf.upload_file(apath, att.get("filename"))
                            if adoc:
                                doc_ids.append(adoc)
                                stats["attachments"] += 1

                    cache.mark_ingested(mid, doc_id, drop_body=True)
                    _cleanup_attachments(mail)
                    stats["uploaded"] += 1
                    log(f"  [{i+1}] ✓ {subj} → {doc_id}")
                except Exception as e:
                    stats["failed"] += 1
                    logger.error(f"  [{i+1}] ✗ {subj}: {e}")

            # 3) 触发解析 + GraphRAG 建图
            if doc_ids:
                log(f"触发 RAGFlow 解析 {len(doc_ids)} 个文档（含 GraphRAG 建图）...")
                rf.start_parsing(doc_ids)
                rf.wait_for_parsing(doc_ids)
                log("GraphRAG 图谱已更新")
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


def _safe(s: str) -> str:
    import re
    return re.sub(r"[^0-9A-Za-z]+", "_", str(s))[:32]


def _cleanup_attachments(mail: dict):
    """即用即删：删除本封邮件的临时附件及其目录"""
    import shutil
    dirs = set()
    for att in mail.get("attachments", []):
        p = att.get("path", "")
        if p:
            try:
                Path(p).unlink(missing_ok=True)
                dirs.add(str(Path(p).parent))
            except Exception:
                pass
    for d in dirs:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
