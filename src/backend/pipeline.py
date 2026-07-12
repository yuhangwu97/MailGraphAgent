"""
Pipeline 编排器
==============
两段一条流，单一事实源在 Redis，图谱在 LightRAG + Neo4j：

    run_fetch:  IMAP → 解析(含附件) → 清洗 → 噪音过滤
                → Redis 暂存正文 + 入 ingest 队列 + 附件落盘
    run_ingest: 遍历 Redis 队列 → LightRAG 增量建图 + 附件本地解析
                → GraphRAG 单遍跨文档建图 → 标记已入库 + 删本地附件(即用即删)

不再有 fetched_mails.json / extracted_mails.json 中转，也不再做 OpenAI 结构化提取。
"""
import logging
from datetime import datetime
from pathlib import Path

from config.settings import get_settings

logger = logging.getLogger("pipeline")


class Pipeline:
    """邮件处理流水线 — LightRAG + Neo4j + Milvus"""

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

                # 去重：先剔除该 folder 下已入库的 UID
                processed_uids = cache.get_processed_uids(folder)
                uids = [u for u in uids if u not in processed_uids]
                if not uids:
                    log("没有新邮件（均已入库）")
                    return 0

                # 按真实收信时间(INTERNALDATE)取最新 limit 封。不能用 uids[-limit:]：
                # UID 未必与日期同序（如 QQ 老邮件也有很大的 UID，取 UID 尾部会捞到老邮件）。
                # 每次取最新的未入库 N 封，同样能推进 backlog（最新的入库后下次自然轮到次新的）。
                dates = client.fetch_internaldates(uids, folder=folder)
                uids.sort(key=lambda u: dates.get(u, 0.0), reverse=True)
                uids = uids[:limit]
                log(f"拉取 {len(uids)} 封邮件（按收信时间取最新）...")

                for uid, msg in client.fetch_batch(uids, folder=folder):
                    parsed = self._store_fetched_mail(uid, msg, folder, cache, cleaner,
                                                      attach_root, on_log=log)
                    if parsed is not None:
                        queued += 1
                        log(f"  [{queued}] {parsed.subject[:50]}")
        finally:
            cache.close()

        log(f"已入队 {queued} 封邮件，待 ingest")
        return queued

    def _store_fetched_mail(self, uid, msg, folder, cache, cleaner, attach_root,
                            forced_message_id: str = "", apply_noise_filter: bool = True,
                            skip_processed: bool = False, on_log=None):
        """解析 + 清洗 + 噪音过滤 + 暂存一封邮件。

        入队成功返回 parsed 对象；被去重/噪音跳过或失败返回 None。
        run_fetch / reprocess / parse_selected（文件邮件）共用此逻辑。

        - forced_message_id: 文件邮件解析出的 msg 可能无 Message-ID，沿用扫描阶段
          合成的 id，保证「先扫表头、再解析」两段的 message_id 一致。
        - apply_noise_filter: 用户显式勾选解析的邮件不应被噪音过滤误跳过，可关掉。
        - skip_processed: 跳过 is_processed 检查（reprocess 已 reset_email 清掉 done 键，
          但以防万一，允许调用方显式强制重新入队）。
        """
        from src.backend.mail.parser import parse_email

        log = on_log or (lambda m: None)

        parsed = None
        try:
            # 关键：传 download_dir，附件才会被提取（旧流程漏了这一步）
            dl_dir = Path(attach_root) / _safe(uid or forced_message_id or "file")
            parsed = parse_email(msg, download_dir=dl_dir)

            # 文件邮件兜底：无 Message-ID 时用扫描阶段合成的 id
            if not parsed.message_id and forced_message_id:
                parsed.message_id = forced_message_id

            # message_id 级兜底去重（UID 复用 / 跨 folder 同信时 UID 过滤会漏）
            if not skip_processed and cache.is_processed(parsed.message_id):
                log(f"  [跳过] {parsed.subject[:50]}（已处理）")
                return None

            cleaned = cleaner.clean(parsed.body_text, parsed.body_html)

            cache.mark_processing(
                parsed.message_id, uid, folder,
                parsed.subject, parsed.from_addr, parsed.date,
                from_name=parsed.from_name,
                attachment_count=len(parsed.attachments or []),
            )

            if apply_noise_filter and self.cfg.enable_noise_filter and \
                    cleaner.is_noise_email(parsed.subject, parsed.from_addr, cleaned):
                cache.mark_skipped(parsed.message_id, "噪音邮件")
                log(f"  [跳过] {parsed.subject[:50]}（噪音邮件）")
                return None

            # 会话线程 id：取 References 链的根（最早的祖先），回退到直接父邮件，
            # 再回退到自身 message_id（单封=独立线程）。供图谱线程上下文注入 + 前端分组。
            thread_id = (parsed.references[0] if parsed.references
                         else parsed.in_reply_to) or parsed.message_id

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
                "in_reply_to": parsed.in_reply_to,
                "references": parsed.references,
                "thread_id": thread_id,
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
            log(f"  [错误] UID {uid} 处理失败: {e}")
            # 已解析出 message_id 的落 failed 状态，避免永远卡在 processing
            if parsed is not None:
                cache.mark_failed(parsed.message_id, str(e))
            return None

    # ══════════════════════════════════════════════
    # 阶段二：Redis → LightRAG + Neo4j + Milvus
    # ══════════════════════════════════════════════

    @staticmethod
    def _with_thread_context(cache, mail: dict, body: str) -> str:
        """回信邮件：在正文前注入会话线程上下文（父邮件主题/发件人/日期）。

        目的：LightRAG 以实体为节点建图，本身不感知邮件线程。把"这封是对某封的回复"
        写进喂给它的文本，抽取时就会把同一会话里反复出现的人/项目/主题在实体图上连起来。
        非回信（无 In-Reply-To / References）原样返回。
        """
        if not body:
            return body
        in_reply_to = mail.get("in_reply_to") or ""
        refs = mail.get("references") or []
        if not in_reply_to and not refs:
            return body
        parent = cache.get_mail_state(in_reply_to) if in_reply_to else {}
        lines = ["【邮件会话线程】本邮件是一封回信，与以下邮件同属一个会话："]
        if parent:
            who = parent.get("from_name") or parent.get("from_addr") or "未知发件人"
            lines.append(
                f"- 回复自 {who} 于 {parent.get('date', '')} 的邮件"
                f"「{parent.get('subject', '')}」")
        else:
            lines.append(f"- 所属会话线程 ID：{mail.get('thread_id', '')}")
        return "\n".join(lines) + "\n\n" + body

    def run_ingest(self, limit: int | None = None, on_log=None) -> dict:
        """邮件入 LightRAG 知识图谱（增量图+向量，Neo4j + Milvus）。附件 DeepDoc 解析。"""
        from src.backend.storage.redis_cache import MailCache

        log = on_log or (lambda m: logger.info(m))
        cache = MailCache(self.account_id)
        stats = {"total": 0, "uploaded": 0, "failed": 0, "attachments": 0, "att_failed": 0}

        try:
            # 原子领取消费：多个 worker 可同时跑 run_ingest，靠 SPOP 保证一封邮件
            # 只被一个 worker 处理（并行分担解析），不会重复入库。
            initial = len(cache.list_pending_ingest())
            if initial == 0:
                log("ingest 队列为空")
                return stats
            stats["total"] = min(initial, limit) if limit else initial
            log(f"ingest {initial} 封邮件到 LightRAG（多 worker 并行分担）...")

            i = 0
            while True:
                if limit and i >= limit:
                    break
                mail = cache.claim_pending_mail()   # 原子领取：多 worker 不会拿到同一封
                if mail is None:
                    break                            # 队列已被各 worker 取空
                i += 1
                mid = mail.get("message_id", "")
                subj = (mail.get("subject") or "(无主题)")[:50]
                try:
                    from src.backend.knowledge.lightrag_wrapper import insert_mail

                    # 1) 正文 → LightRAG（增量图+向量）
                    body = mail.get("cleaned_body") or ""
                    # 回信邮件：正文前注入会话线程上下文，让同线程邮件的实体在图上连起来
                    body = self._with_thread_context(cache, mail, body)
                    body_doc_id = ""
                    if body:
                        try:
                            body_doc_id = insert_mail(body, mid)
                        except Exception as e:
                            # 正文入库失败绝不能假成功：标记 failed、出队、保留正文供重试，
                            # 且不执行 mark_ingested（否则会 drop_body 丢正文并误报成功）。
                            stats["failed"] += 1
                            cache.mark_ingest_failed(
                                mid, f"LightRAG body insert failed: {e}", drop_body=False)
                            logger.error("LightRAG body insert failed for %s: %s", mid, e)
                            log(f"  [{i}] ✗ {subj}: 正文入库失败，已标记 failed")
                            continue

                    # 2) 附件 → DeepDoc 解析后也入 LightRAG
                    uploaded_paths = []
                    att_doc_ids = []
                    mail_att_failed = 0
                    for att in mail.get("attachments", []):
                        apath = att.get("path", "")
                        if apath and Path(apath).exists():
                            att_text = _parse_attachment(apath, att.get("filename", ""))
                            if att_text:
                                att_doc_id = f"{mid}:{att.get('filename', '')}"
                                try:
                                    insert_mail(att_text, att_doc_id)
                                    att_doc_ids.append(att_doc_id)
                                    stats["attachments"] += 1
                                    # 仅成功入库的附件才允许清理源文件
                                    uploaded_paths.append(apath)
                                except Exception as e:
                                    # 附件失败不阻断正文入库，但必须计入失败、保留源文件供重试、显性上报，
                                    # 绝不能像旧逻辑那样静默 warning 后仍按成功计数。
                                    mail_att_failed += 1
                                    stats["att_failed"] += 1
                                    logger.error(
                                        "LightRAG attachment insert failed (%s): %s", att_doc_id, e)
                                    log(f"  [{i}] ⚠ 附件入库失败：{att.get('filename', '')}")

                    # 正文（及成功的附件）已入库，记录 doc_id → message_id 供证据归属
                    cache.mark_ingested(mid, doc_id=body_doc_id,
                                        att_doc_ids=att_doc_ids, drop_body=True)
                    _cleanup_attachments(mail, only_paths=uploaded_paths)
                    stats["uploaded"] += 1
                    if mail_att_failed:
                        log(f"  [{i}] ⚠ {subj}（正文已入库，{mail_att_failed} 个附件失败，已保留待重试）")
                    else:
                        log(f"  [{i}] ✓ {subj}")
                except Exception as e:
                    stats["failed"] += 1
                    if mid:
                        cache.mark_ingest_failed(mid, str(e), drop_body=False)
                    logger.error(f"  [{i}] ✗ {subj}: {e}")
        finally:
            cache.close()

        done_msg = (f"完成：{stats['uploaded']}/{stats['total']} 封 "
                    f"(附件 {stats['attachments']}，失败 {stats['failed']}")
        if stats["att_failed"]:
            done_msg += f"，附件失败 {stats['att_failed']}"
        done_msg += ")"
        log(done_msg)
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
    # 文件邮件源：Step1 扫表头 / Step2 按需解析+向量化
    # ══════════════════════════════════════════════

    def run_index_files(self, paths: list[str], on_log=None) -> dict:
        """Step 1：扫描本地邮件文件（.eml/.msg/.pst/.ost）的表头，存入 indexed。

        只读 Subject/Message-ID/发件人/日期/文件夹，不读正文。返回统计。
        """
        from src.backend.mail.sources import expand_paths, scan_file
        from src.backend.storage.redis_cache import MailCache

        log = on_log or (lambda m: logger.info(m))
        if not self.account:
            log("未配置邮箱账号，请先在工作台添加")
            return {"files": 0, "scanned": 0, "indexed": 0}

        files = expand_paths(paths)
        if not files:
            log("未找到受支持的邮件文件（.eml/.msg/.pst/.ost）")
            return {"files": 0, "scanned": 0, "indexed": 0}

        cache = MailCache(self.account_id)
        stats = {"files": len(files), "scanned": 0, "indexed": 0}
        try:
            for fp in files:
                log(f"扫描 {fp.name} ...")
                try:
                    for rec in scan_file(str(fp)):
                        stats["scanned"] += 1
                        if cache.store_indexed(rec):
                            stats["indexed"] += 1
                        if stats["scanned"] % 200 == 0:
                            log(f"  已扫描 {stats['scanned']} 封，新增 {stats['indexed']}")
                except Exception as e:
                    logger.error("扫描文件失败 %s: %s", fp, e)
                    log(f"  ✗ {fp.name} 扫描失败: {e}")
        finally:
            cache.close()
        log(f"扫描完成：{stats['files']} 个文件，共 {stats['scanned']} 封，"
            f"新增待解析 {stats['indexed']}")
        return stats

    def parse_selected(self, message_ids: list[str], on_log=None) -> dict:
        """Step 2：对选中的 indexed 邮件回原文件读正文（含附件），清洗入队后 ingest 向量化。

        按源文件归组，PST/OST 每个文件只 open 一次。用户显式勾选的邮件不做噪音过滤。
        """
        import json as _json

        from src.backend.mail.sources import open_reader
        from src.backend.mail.cleaner import MailCleaner
        from src.backend.storage.redis_cache import MailCache

        log = on_log or (lambda m: logger.info(m))
        if not self.account:
            log("未配置邮箱账号")
            return {"total": 0, "uploaded": 0, "failed": 0, "attachments": 0, "queued": 0}
        cache = MailCache(self.account_id)
        cleaner = MailCleaner()
        attach_root = self.cfg.resolve_data_path("attachments")

        queued = 0
        # 按 (源类型, 源文件) 归组
        groups: dict[tuple[str, str], list[tuple[str, dict, str]]] = {}
        try:
            for mid in message_ids or []:
                state = cache.get_mail_state(mid)
                if not state or state.get("status") == "done":
                    continue
                try:
                    locator = _json.loads(state.get("source_locator") or "{}")
                except Exception:
                    locator = {}
                if not locator:
                    continue
                key = (locator.get("source_type", ""), locator.get("path", ""))
                groups.setdefault(key, []).append((mid, locator, state.get("folder", "")))

            for (stype, spath), items in groups.items():
                log(f"解析 {Path(spath).name} 的 {len(items)} 封...")
                reader = open_reader(stype, spath)
                try:
                    for mid, locator, folder in items:
                        try:
                            msg = reader.read(locator)
                            parsed = self._store_fetched_mail(
                                "", msg, folder, cache, cleaner, attach_root,
                                forced_message_id=mid, apply_noise_filter=False,
                                on_log=log,
                            )
                            if parsed is not None:
                                queued += 1
                                log(f"  [{queued}] {(parsed.subject or '(无主题)')[:50]}")
                        except Exception as e:
                            logger.error("解析失败 %s: %s", mid, e)
                            cache.mark_failed(mid, str(e))
                finally:
                    try:
                        reader.close()
                    except Exception:
                        pass
        finally:
            cache.close()

        log(f"已解析入队 {queued} 封，待建图...")
        # 只做「准备+入队」：建图由 worker 的 ingest 任务统一处理（见路由 prep_then_ingest_stream）
        return {"queued": queued}

    # ══════════════════════════════════════════════
    # 强制重新处理（绕过幂等）
    # ══════════════════════════════════════════════

    def reprocess(self, message_ids: list[str], on_log=None) -> dict:
        """强制重新处理指定邮件：重置状态 → 重新拉取正文 → LightRAG 重建。"""
        from src.backend.storage.redis_cache import MailCache
        from src.backend.mail.imap_client import IMAPClient
        from src.backend.mail.cleaner import MailCleaner

        log = on_log or (lambda m: logger.info(m))
        if not self.account:
            log("未配置邮箱账号")
            return {"total": 0, "uploaded": 0, "failed": 0, "attachments": 0, "att_failed": 0, "reset": 0, "requeued": 0}
        cache = MailCache(self.account_id)
        cleaner = MailCleaner()
        attach_root = self.cfg.resolve_data_path("attachments")

        reset_n = 0
        requeued = 0
        try:
            # 1) 重置状态，按 folder 归组待重拉的 uid
            by_folder: dict[str, list[str]] = {}
            for mid in message_ids:
                meta = cache.get_mail_state(mid)
                if not meta:
                    continue
                cache.reset_email(mid)
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
                                                        cache, cleaner, attach_root,
                                                        skip_processed=True, on_log=log) is not None:
                                requeued += 1
            log(f"重新入队 {requeued} 封，待建图...")
        finally:
            cache.close()

        # 只做「准备+入队」：建图由 worker 的 ingest 任务统一处理（见路由 prep_then_ingest_stream）
        return {"reset": reset_n, "requeued": requeued}


def _safe(s: str) -> str:
    import re
    return re.sub(r"[^0-9A-Za-z]+", "_", str(s))[:32]


def _parse_attachment(filepath: str, filename: str) -> str:
    """解析附件文本：优先 DeepDoc，回落 pypdf/docx。"""
    ext = Path(filepath).suffix.lower()

    # 尝试 DeepDoc（PDF: 布局+OCR+表格）
    if ext == ".pdf":
        try:
            from src.backend.knowledge.plugins.parser.pdf_parser import RAGFlowPdfParser
            parser = RAGFlowPdfParser()
            text_blocks, _ = parser(filepath, need_image=False, zoomin=3, return_html=False)
            text = "\n".join(parser.remove_tag(t) for t in (text_blocks or []))
            if text.strip():
                return text[:20000]
        except Exception as e:
            logger.debug("DeepDoc PDF failed, fallback: %s", e)

    if ext in (".docx", ".doc"):
        try:
            from src.backend.knowledge.plugins.parser.docx_parser import DocxParser
            parser = DocxParser()
            text_blocks, _ = parser(filepath)
            text = "\n".join(b if isinstance(b, str) else str(b) for b in (text_blocks or []))
            if text.strip():
                return text[:20000]
        except Exception as e:
            logger.debug("DeepDoc Docx failed, fallback: %s", e)

    # 回落简单解析
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(filepath)
            return "\n".join(p.extract_text() or "" for p in reader.pages)[:10000]
        elif ext in (".docx", ".doc"):
            from docx import Document
            doc = Document(filepath)
            return "\n".join(p.text for p in doc.paragraphs)[:10000]
        elif ext in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, data_only=True)
            texts = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    texts.append("\t".join(str(c or "") for c in row))
            return "\n".join(texts)[:10000]
        elif ext in (".txt", ".md", ".csv", ".log"):
            return Path(filepath).read_text(encoding="utf-8", errors="ignore")[:10000]
    except Exception as e:
        logger.warning("附件解析失败 %s: %s", filename, e)
    return ""


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
