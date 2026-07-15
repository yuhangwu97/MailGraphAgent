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
from src.backend.jobqueue import publish_event

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

            # 文件邮件兜底：无 Message-ID 时用扫描阶段合成的 id。
            # 同时归一化尖括号：parse_email 可能产出 "<id>"，scan 阶段是无尖括号
            # 的原始 id，若实质相同则统一用 forced_message_id 保持记录一致。
            if not parsed.message_id and forced_message_id:
                parsed.message_id = forced_message_id
            else:
                _norm = (parsed.message_id or "").strip().strip("<>")
                _forced = (forced_message_id or "").strip().strip("<>")
                if _norm and _norm == _forced:
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
                try:
                    publish_event("mail_processed", {
                        "message_id": parsed.message_id,
                        "subject": parsed.subject,
                        "status": "skipped",
                        "detail": "噪音邮件",
                    })
                except Exception:
                    pass
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
            try:
                publish_event("mail_processed", {
                    "message_id": parsed.message_id,
                    "subject": parsed.subject,
                    "status": "indexed",
                    "detail": "已入队，等待建图",
                })
            except Exception:
                pass
            return parsed
        except Exception as e:
            logger.error(f"  UID {uid} 处理失败: {e}")
            log(f"  [错误] UID {uid} 处理失败: {e}")
            # 已解析出 message_id 的落 failed 状态，避免永远卡在 processing
            if parsed is not None:
                cache.mark_failed(parsed.message_id, str(e))
                try:
                    publish_event("mail_processed", {
                        "message_id": parsed.message_id,
                        "subject": parsed.subject if parsed.subject else "(无主题)",
                        "status": "failed",
                        "detail": str(e)[:200],
                    })
                except Exception:
                    pass
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
                mail = cache.claim_pending_mail_tracked()   # 原子领取+inflight 追踪
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
                        att_fn = att.get("filename", "")
                        if apath and Path(apath).exists():
                            cache.set_attachment_status(mid, att_fn, "parsing")
                            att_text, degrade = _parse_attachment(apath, att_fn)
                            if att_text:
                                if degrade:
                                    cache.set_attachment_status(
                                        mid, att_fn, "degraded", degrade)
                                    logger.warning(
                                        "附件解析降级 %s/%s: %s", mid, att_fn, degrade)
                                else:
                                    cache.set_attachment_status(mid, att_fn, "parsed")
                                att_doc_id = f"{mid}:{att.get('filename', '')}"
                                try:
                                    insert_mail(att_text, att_doc_id)
                                    att_doc_ids.append(att_doc_id)
                                    stats["attachments"] += 1
                                    # 仅成功入库的附件才允许清理源文件
                                    uploaded_paths.append(apath)
                                except Exception as e:
                                    mail_att_failed += 1
                                    stats["att_failed"] += 1
                                    cache.set_attachment_status(
                                        mid, att_fn, "failed", str(e)[:100])
                                    logger.error(
                                        "LightRAG attachment insert failed (%s): %s", att_doc_id, e)
                                    log(f"  [{i}] ⚠ 附件入库失败：{att_fn}")
                            else:
                                mail_att_failed += 1
                                stats["att_failed"] += 1
                                cache.set_attachment_status(
                                    mid, att_fn, "failed", degrade or "no_text")

                    # 正文（及成功的附件）已入库，记录 doc_id → message_id 供证据归属
                    cache.mark_ingested(mid, doc_id=body_doc_id,
                                        att_doc_ids=att_doc_ids, drop_body=True)
                    _cleanup_attachments(mail, only_paths=uploaded_paths)
                    stats["uploaded"] += 1
                    try:
                        publish_event("mail_processed", {
                            "message_id": mid,
                            "subject": mail.get("subject", ""),
                            "status": "done",
                            "detail": f"正文 + {stats['attachments']} 附件已入库",
                        })
                    except Exception:
                        pass
                    if mail_att_failed:
                        log(f"  [{i}] ⚠ {subj}（正文已入库，{mail_att_failed} 个附件失败，已保留待重试）")
                    else:
                        log(f"  [{i}] ✓ {subj}")
                except Exception as e:
                    stats["failed"] += 1
                    if mid:
                        cache.mark_ingest_failed(mid, str(e), drop_body=False)
                    try:
                        publish_event("mail_processed", {
                            "message_id": mid,
                            "subject": mail.get("subject", "(无主题)")[:50],
                            "status": "failed",
                            "detail": str(e)[:200],
                        })
                    except Exception:
                        pass
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

    def run_ingest_one(self, message_id: str, on_log=None) -> dict:
        """处理单封邮件入 LightRAG（一邮件一 job，worker 崩了只丢一封）。"""
        from src.backend.knowledge.lightrag_wrapper import insert_mail
        from src.backend.storage.redis_cache import MailCache

        log = on_log or (lambda m: logger.info(m))
        cache = MailCache(self.account_id)

        try:
            # 从队列里原子取出（SREM 防并发）
            removed = cache.r.srem(cache._k("ingest_queue"), message_id)
            if not removed:
                log(f"邮件 {message_id} 不在 ingest 队列中")
                return {"message_id": message_id, "status": "not_in_queue"}

            mail = cache.get_mail(message_id)
            if not mail:
                cache.mark_ingest_failed(message_id, "正文已过期", drop_body=False)
                log(f"邮件 {message_id[:16]}… 正文已过期，标记 failed")
                return {"message_id": message_id, "status": "body_expired"}

            subj = (mail.get("subject") or "(无主题)")[:50]
            stats = {"message_id": message_id, "attachments": 0, "att_failed": 0, "status": "ok"}

            # 1) 正文 → LightRAG
            body = mail.get("cleaned_body") or ""
            body = self._with_thread_context(cache, mail, body)
            if body:
                try:
                    doc_id = insert_mail(body, message_id)
                except Exception as e:
                    cache.mark_ingest_failed(message_id, f"LightRAG body insert failed: {e}", drop_body=False)
                    log(f"✗ {subj}: 正文入库失败")
                    return {"message_id": message_id, "status": "failed", "error": str(e)[:200]}

            # 2) 附件
            uploaded_paths = []
            att_doc_ids = []
            for att in mail.get("attachments", []):
                apath = att.get("path", "")
                att_fn = att.get("filename", "")
                if apath and Path(apath).exists():
                    cache.set_attachment_status(message_id, att_fn, "parsing")
                    att_text, degrade = _parse_attachment(apath, att_fn)
                    if att_text:
                        if degrade:
                            cache.set_attachment_status(
                                message_id, att_fn, "degraded", degrade)
                        else:
                            cache.set_attachment_status(message_id, att_fn, "parsed")
                        att_doc_id = f"{message_id}:{att_fn}"
                        try:
                            insert_mail(att_text, att_doc_id)
                            att_doc_ids.append(att_doc_id)
                            stats["attachments"] += 1
                            uploaded_paths.append(apath)
                        except Exception as e:
                            stats["att_failed"] += 1
                            cache.set_attachment_status(
                                message_id, att_fn, "failed", str(e)[:100])
                            logger.error("LightRAG attachment insert failed (%s): %s", att_doc_id, e)
                    else:
                        stats["att_failed"] += 1
                        cache.set_attachment_status(
                            message_id, att_fn, "failed", degrade or "no_text")

            # 3) 标记完成
            cache.mark_ingested(message_id, doc_id=doc_id if body else "",
                                att_doc_ids=att_doc_ids, drop_body=True)
            _cleanup_attachments(mail, only_paths=uploaded_paths)
            log(f"✓ {subj}" + (f"（{stats['att_failed']} 附件失败）" if stats["att_failed"] else ""))
            try:
                publish_event("mail_processed", {
                    "message_id": message_id,
                    "subject": mail.get("subject", ""),
                    "status": "done",
                    "detail": "正文已入库",
                })
            except Exception:
                pass
            return stats

        except Exception as e:
            if message_id:
                try:
                    cache.mark_ingest_failed(message_id, str(e), drop_body=False)
                except Exception:
                    pass
                try:
                    publish_event("mail_processed", {
                        "message_id": message_id,
                        "subject": "(未知)",
                        "status": "failed",
                        "detail": str(e)[:200],
                    })
                except Exception:
                    pass
            logger.error("run_ingest_one failed for %s: %s", message_id, e)
            return {"message_id": message_id, "status": "failed", "error": str(e)[:200]}
        finally:
            cache.close()

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

    def run_scan(self, source: str, params: dict | None = None, on_log=None) -> dict:
        """统一扫描入口：IMAP 拉表头 / 文件扫表头 → indexed 清单。

        source="imap": 用 fetch_headers 只拉 ENVELOPE 级元数据，不下载正文。
        source="file":  复用 run_index_files 扫 .eml/.msg/.pst/.ost。

        产出 indexed 邮件后，用户从清单勾选 → parse_selected → worker 建图。
        """
        log = on_log or (lambda m: logger.info(m))
        params = params or {}

        if source == "imap":
            return self._run_scan_imap(params, log)
        elif source == "file":
            paths = params.get("paths", [])
            return self.run_index_files(paths, on_log=on_log)
        else:
            log(f"未知来源类型: {source}")
            return {"source": source, "scanned": 0, "indexed": 0}

    def _run_scan_imap(self, params: dict, log) -> dict:
        """IMAP header-only scan: 拉 ENVELOPE → store_indexed。"""
        from src.backend.mail.imap_client import IMAPClient
        from src.backend.storage.redis_cache import MailCache

        folder = params.get("folder", "INBOX")
        limit = params.get("limit")
        since = params.get("since")
        before = params.get("before")

        if not self.account:
            log("未配置邮箱账号")
            return {"source": "imap", "scanned": 0, "indexed": 0}

        client = IMAPClient(self.account)
        cache = MailCache(self.account_id)
        stats = {"source": "imap", "folder": folder, "scanned": 0, "indexed": 0}

        try:
            # 获取文件夹总量
            client.select_folder(folder)
            # 搜索 UID 范围
            uids = client.search_uids(folder, since=since, before=before)
            if limit:
                uids = uids[-limit:]  # 取最新的 N 封

            if not uids:
                log(f"IMAP {folder}: 没有匹配的邮件")
                return stats

            log(f"IMAP {folder}: 扫描 {len(uids)} 封邮件的表头...")
            stats["total"] = len(uids)

            # 批量拉表头（每批 100）
            batch_size = 100
            indexed_uids = []
            for i in range(0, len(uids), batch_size):
                chunk = uids[i:i + batch_size]
                headers = client.fetch_headers(chunk, folder)
                for uid in chunk:
                    hdr = headers.get(str(uid))
                    if not hdr:
                        continue
                    stats["scanned"] += 1
                    from src.backend.mail.sources.base import HeaderRecord
                    rec = HeaderRecord(
                        subject=hdr.get("subject", ""),
                        from_addr=hdr.get("from_addr", ""),
                        from_name="",
                        date=hdr.get("date", ""),
                        message_id=hdr.get("message_id", f"imap:{uid}"),
                        folder=folder,
                        has_attachment=False,
                        locator={
                            "source_type": "imap",
                            "folder": folder,
                            "uid": uid,
                            "account_id": self.account_id,
                        },
                    )
                    if cache.store_indexed(rec):
                        stats["indexed"] += 1
                        indexed_uids.append(uid)
                if stats["scanned"] % 200 == 0 or (i + batch_size >= len(uids)):
                    log(f"  已扫描 {stats['scanned']}/{stats['total']}，入库 {stats['indexed']}")

        finally:
            client.disconnect()
            cache.close()

        log(f"IMAP 扫描完成：{stats['indexed']}/{stats['total']} 封新邮件入库")
        return stats

    def parse_selected(self, message_ids: list[str], on_log=None) -> dict:
        """Step 2：对选中的 indexed 邮件拉取正文（含附件），清洗入队后 ingest 向量化。

        文件源（eml/msg/pst）按源文件归组，PST/OST 每个文件只 open 一次。
        IMAP 源通过 IMAPClient.fetch_batch 按 folder 归组拉取。
        用户显式勾选的邮件不做噪音过滤。
        """
        import json as _json

        from src.backend.mail.sources import open_reader
        from src.backend.mail.imap_client import IMAPClient
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
        # 按源分组：文件源按 (source_type, path)，IMAP 源按 folder
        file_groups: dict[tuple[str, str], list[tuple[str, dict, str]]] = {}
        imap_by_folder: dict[str, list[tuple[str, str, str]]] = {}  # folder → [(mid, uid, status)]

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
                stype = locator.get("source_type", "")
                if stype == "imap":
                    folder = locator.get("folder", "INBOX")
                    uid = str(locator.get("uid", ""))
                    if uid:
                        imap_by_folder.setdefault(folder, []).append(
                            (mid, uid, state.get("status", "indexed")))
                else:
                    key = (stype, locator.get("path", ""))
                    file_groups.setdefault(key, []).append((mid, locator, state.get("folder", "")))

            # ── 文件源：从本地文件读取正文 ──
            for (stype, spath), items in file_groups.items():
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

            # ── IMAP 源：按状态区分处理 ──
            #   indexed（待入库）：新邮件，直接 IMAP 拉正文。
            #   failed（失败·可重试）：先查 Redis 缓存，有就直接入队（省一次 IMAP），没有再拉。
            if imap_by_folder:
                retry_from_cache = 0
                uncached: dict[str, list[tuple[str, str]]] = {}  # folder → [(mid, uid)]

                for folder, items in imap_by_folder.items():
                    for mid, uid, status in items:
                        if status == "failed":
                            # 检查上次是否已拉过正文（缓存还在）
                            cached_body = cache.get_mail(mid)
                            last_error = (cache.get_mail_state(mid) or {}).get("error", "")
                            if cached_body is not None:
                                cache.r.sadd(cache._k("ingest_queue"), mid)
                                cache.mark_processing(
                                    mid, uid, folder,
                                    cached_body.get("subject", ""),
                                    cached_body.get("from_addr", ""),
                                    cached_body.get("date", ""),
                                )
                                queued += 1
                                retry_from_cache += 1
                                subj = (cached_body.get("subject") or "(无主题)")[:50]
                                reason = f"，上次失败原因: {last_error}" if last_error else ""
                                log(f"  [{queued}] [缓存] {subj}{reason}")
                            else:
                                uncached.setdefault(folder, []).append((mid, uid))
                        else:
                            # indexed 等：直接拉 IMAP
                            uncached.setdefault(folder, []).append((mid, uid))

                if retry_from_cache:
                    log(f"  ↳ {retry_from_cache} 封从缓存恢复，无需重复拉 IMAP")

                if uncached:
                    with IMAPClient(self.account) as client:
                        for folder, items in uncached.items():
                            uid_map = {uid: mid for mid, uid in items}
                            uids = list(uid_map.keys())
                            log(f"IMAP 拉取 {folder} 的 {len(uids)} 封...")
                            try:
                                for uid, msg in client.fetch_batch(uids, folder=folder):
                                    mid = uid_map.get(str(uid), "")
                                    parsed = self._store_fetched_mail(
                                        uid, msg, folder, cache, cleaner, attach_root,
                                        forced_message_id=mid, apply_noise_filter=False,
                                        on_log=log,
                                    )
                                    if parsed is not None:
                                        queued += 1
                                        log(f"  [{queued}] {(parsed.subject or '(无主题)')[:50]}")
                            except Exception as e:
                                logger.error("IMAP 拉取失败 folder=%s: %s", folder, e)
                                for mid, _uid in items:
                                    cache.mark_failed(mid, str(e))
        finally:
            cache.close()

        log(f"已解析入队 {queued} 封，待建图...")
        # 只做「准备+入队」：建图由 worker 的 ingest 任务统一处理（见路由 prep_then_ingest_stream）
        return {"queued": queued}

    # ══════════════════════════════════════════════
    # 强制重新处理（绕过幂等）
    # ══════════════════════════════════════════════

    def reprocess(self, message_ids: list[str], on_log=None) -> dict:
        """强制重新处理指定邮件：重置状态 → 重新拉取正文 → LightRAG 重建。

        按当前状态分流：
        - failed（失败·可重试）：先查 Redis 缓存，有就直接入队（省一次 IMAP）；没有再拉。
        - 其他（pending/skipped）：重置后走 IMAP 拉取全文。
        """
        import json as _json

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
        from_cache = 0
        try:
            # 1) 分流：failed 先查缓存，其余走重置+重拉
            by_folder: dict[str, list[str]] = {}
            for mid in message_ids:
                meta = cache.get_mail_state(mid)
                if not meta:
                    continue
                status = meta.get("status", "")

                # 失败邮件：优先用缓存正文（上次可能已拉取成功只是后续步骤失败）
                if status == "failed":
                    cached_body = cache.get_mail(mid)
                    last_error = meta.get("error_msg", "")
                    if cached_body is not None:
                        # 正文还在，直接重新入队
                        cache.r.sadd(cache._k("ingest_queue"), mid)
                        cache.mark_processing(
                            mid,
                            meta.get("uid", ""),
                            meta.get("folder", "INBOX"),
                            cached_body.get("subject", ""),
                            cached_body.get("from_addr", ""),
                            cached_body.get("date", ""),
                        )
                        requeued += 1
                        from_cache += 1
                        subj = (cached_body.get("subject") or "(无主题)")[:50]
                        reason = f"，上次失败: {last_error}" if last_error else ""
                        log(f"  [{requeued}] [缓存恢复] {subj}{reason}")
                        continue

                # 其他状态 / 缓存未命中 → 重置后走 IMAP 拉取
                cache.reset_email(mid)
                uid = meta.get("uid", "")
                folder = meta.get("folder", "INBOX")
                # uid 可能存于 source_locator 中（store_indexed 的顶层 uid 为空串）
                if not uid:
                    try:
                        locator = _json.loads(meta.get("source_locator", "{}") or "{}")
                        uid = str(locator.get("uid", ""))
                    except Exception:
                        pass
                if uid:
                    by_folder.setdefault(folder, []).append(uid)
                reset_n += 1

            if from_cache:
                log(f"  ↳ {from_cache} 封从缓存恢复，无需重复拉 IMAP")

            # 2) 重新从 IMAP 拉这些 uid → 重新入队
            if by_folder:
                log(f"已重置 {reset_n} 封，重新拉取正文...")
                with IMAPClient(self.account) as client:
                    for folder, uids in by_folder.items():
                        log(f"IMAP 拉取 {folder} 的 {len(uids)} 封...")
                        for uid, msg in client.fetch_batch(uids, folder=folder):
                            parsed = self._store_fetched_mail(uid, msg, folder,
                                                              cache, cleaner, attach_root,
                                                              skip_processed=True,
                                                              apply_noise_filter=False,
                                                              on_log=log)
                            if parsed is not None:
                                requeued += 1
                                log(f"  [{requeued}] {(parsed.subject or '(无主题)')[:50]}")
            log(f"重新入队 {requeued} 封，待建图...")
        finally:
            cache.close()

        # 只做「准备+入队」：建图由 worker 的 ingest 任务统一处理（见路由 prep_then_ingest_stream）
        return {"reset": reset_n, "requeued": requeued}


def _safe(s: str) -> str:
    import re
    return re.sub(r"[^0-9A-Za-z]+", "_", str(s))[:32]


def _parse_attachment(filepath: str, filename: str,
                     timeout: float | None = None,
                     on_status=None) -> tuple[str, str]:
    """解析附件文本：优先 DeepDoc，回落 pypdf/docx。

    Returns (text, degrade_reason):
      - degrade_reason="" → 满血 DeepDoc 解析
      - degrade_reason="deepdoc_model_missing" → DeepDoc 模型缺失，已回落
      - degrade_reason="deepdoc_error" → DeepDoc 异常，已回落
      - degrade_reason="fallback" → 无 DeepDoc 支持此格式，直接回落
    """
    import signal
    ext = Path(filepath).suffix.lower()
    degrade = ""

    def _deepdoc_pdf():
        from src.backend.knowledge.plugins.parser.pdf_parser import RAGFlowPdfParser
        parser = RAGFlowPdfParser()
        text_blocks, _ = parser(filepath, need_image=False, zoomin=3, return_html=False)
        text = "\n".join(parser.remove_tag(t) for t in (text_blocks or []))
        return text.strip()

    def _deepdoc_docx():
        from src.backend.knowledge.plugins.parser.docx_parser import DocxParser
        parser = DocxParser()
        text_blocks, _ = parser(filepath)
        text = "\n".join(b if isinstance(b, str) else str(b) for b in (text_blocks or []))
        return text.strip()

    # 尝试 DeepDoc（PDF: 布局+OCR+表格）
    if ext == ".pdf":
        try:
            text = _deepdoc_pdf()
            if text:
                return text[:20000], ""
        except FileNotFoundError as e:
            # onnx 模型缺失（LFS 132 字节指针占位符）→ 显式标 degraded
            degrade = "deepdoc_model_missing"
            logger.warning("DeepDoc PDF model missing, degraded fallback for %s: %s", filename, e)
        except Exception as e:
            degrade = "deepdoc_error"
            logger.warning("DeepDoc PDF failed, degraded fallback for %s: %s", filename, e)

    if ext in (".docx", ".doc"):
        try:
            text = _deepdoc_docx()
            if text:
                return text[:20000], ""
        except FileNotFoundError as e:
            degrade = "deepdoc_model_missing"
            logger.warning("DeepDoc Docx model missing, degraded fallback for %s: %s", filename, e)
        except Exception as e:
            degrade = "deepdoc_error"
            logger.warning("DeepDoc Docx failed, degraded fallback for %s: %s", filename, e)

    if not degrade and ext in (".pdf", ".docx", ".doc"):
        degrade = "fallback"

    if on_status:
        on_status("parsing")

    # 回落简单解析
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(filepath)
            text = "\n".join(p.extract_text() or "" for p in reader.pages)[:10000]
            return text, degrade
        elif ext in (".docx", ".doc"):
            from docx import Document
            doc = Document(filepath)
            text = "\n".join(p.text for p in doc.paragraphs)[:10000]
            return text, degrade
        elif ext in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, data_only=True)
            texts = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    texts.append("\t".join(str(c or "") for c in row))
            return "\n".join(texts)[:10000], ""
        elif ext in (".txt", ".md", ".csv", ".log"):
            text = Path(filepath).read_text(encoding="utf-8", errors="ignore")[:10000]
            return text, ""
    except Exception as e:
        logger.warning("附件解析失败 %s: %s", filename, e)
    return "", "parse_error"


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
