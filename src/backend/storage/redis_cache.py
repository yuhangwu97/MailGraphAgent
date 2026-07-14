"""
Redis 进度缓存
轻量级邮件处理进度追踪，替代原来的 MySQL 缓存。

同时作为 fetch → ingest 之间的正文暂存（单一事实源，替代 JSON 文件中转）：
正文带 TTL 存于 Redis，待入库的 message_id 放入 ingest 队列（set）。
"""
import json
import time
import logging
import redis
from config.settings import get_settings

logger = logging.getLogger(__name__)


def _sender_matches(data: dict, sender: str) -> bool:
    s_lower = (sender or "").lower().strip()
    if not s_lower:
        return True
    return (
        s_lower in (data.get("from_addr", "") or "").lower()
        or s_lower in (data.get("from_name", "") or "").lower()
    )


class MailCache:
    """邮件处理进度缓存 (Redis 后端)"""

    def __init__(self, account_id: str | None = None):
        cfg = get_settings()
        self._cfg = cfg
        self._r = redis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            db=cfg.redis_db,
            password=cfg.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        # 数据全局共享：mail/body/ingest_queue/idx/usage/indexed 不分账号，与知识图谱一致
        # （项目看板、工作台列表都看统一池）。仅 IMAP 抓取簿记（done_uids/progress，基于
        # 每邮箱唯一的 UID）按账号隔离，避免不同邮箱同名 folder 的 UID 相互误去重（见 _mbx_k）。
        self._account_id = account_id
        self._prefix = "mailgraph:"
        self._mbx_prefix = f"mailgraph:mbx:{account_id}:" if account_id else "mailgraph:mbx:_:"
        self._body_ttl = int(cfg.fetched_body_ttl_days) * 86400

    @property
    def r(self) -> redis.Redis:
        return self._r

    def _k(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    def _mbx_k(self, *parts: str) -> str:
        """按账号隔离的 IMAP 抓取簿记键（done_uids / progress）。UID 只在单个邮箱内唯一，
        全局合并会导致不同邮箱同名 folder 的 UID 相互误去重、漏抓邮件。"""
        return self._mbx_prefix + ":".join(parts)

    def _status_key(self, status: str) -> str:
        return self._k("idx", "status", status or "pending")

    def _sender_key(self, sender: str) -> str:
        return self._k("idx", "sender", sender or "未知")

    def _attachment_key(self, has_attachment: bool) -> str:
        return self._k("idx", "attachment", "yes" if has_attachment else "no")

    def _date_key(self) -> str:
        return self._k("idx", "date")

    def _doc_key(self, doc_id: str) -> str:
        return self._k("idx", "doc", doc_id)

    def _parse_mail_timestamp(self, date_str: str) -> float:
        from datetime import datetime
        from email.utils import parsedate_to_datetime

        if not date_str:
            return 0.0
        try:
            dt = parsedate_to_datetime(date_str)
        except (ValueError, TypeError):
            try:
                dt = datetime.fromisoformat(date_str)
            except (ValueError, TypeError):
                return 0.0
        return dt.timestamp()

    def _doc_ids_from_state(self, data: dict) -> list[str]:
        """从状态哈希取出所有知识库文档 id（正文 + 附件）。

        兼容旧字段名 ragflow_doc_id / ragflow_att_doc_ids。
        """
        primary = data.get("knowledge_doc_id") or data.get("ragflow_doc_id", "") or ""
        atts = (data.get("knowledge_att_doc_ids")
                or data.get("ragflow_att_doc_ids", "") or "")
        ids = [primary] + atts.split(",")
        return [d for d in ids if d]

    def _remove_indexes_for_state(self, pipe, message_id: str, data: dict):
        status = data.get("status") or "pending"
        pipe.srem(self._status_key(status), message_id)
        pipe.zrem(self._date_key(), message_id)
        from_addr = data.get("from_addr")
        if from_addr:
            pipe.srem(self._sender_key(from_addr), message_id)
        from_name = data.get("from_name")
        if from_name:
            pipe.srem(self._sender_key(from_name), message_id)
        try:
            att_count = int(data.get("attachment_count") or 0)
        except (TypeError, ValueError):
            att_count = 0
        pipe.srem(self._attachment_key(att_count > 0), message_id)
        for doc_id in self._doc_ids_from_state(data):
            pipe.delete(self._doc_key(doc_id))

    def _index_state(self, pipe, message_id: str, data: dict):
        status = data.get("status") or "pending"
        pipe.sadd(self._status_key(status), message_id)
        score = self._parse_mail_timestamp(data.get("date", ""))
        pipe.zadd(self._date_key(), {message_id: score})
        from_addr = data.get("from_addr")
        if from_addr:
            pipe.sadd(self._sender_key(from_addr), message_id)
        from_name = data.get("from_name")
        if from_name:
            pipe.sadd(self._sender_key(from_name), message_id)
        try:
            att_count = int(data.get("attachment_count") or 0)
        except (TypeError, ValueError):
            att_count = 0
        pipe.sadd(self._attachment_key(att_count > 0), message_id)
        for doc_id in self._doc_ids_from_state(data):
            pipe.set(self._doc_key(doc_id), message_id)

    def _replace_indexes(self, message_id: str, data: dict):
        old = self.get_mail_state(message_id)
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        self._index_state(pipe, message_id, data)
        pipe.execute()

    def message_ids_for_docs(self, doc_ids: list[str]) -> set[str]:
        """Resolve knowledge-base document ids back to message_ids via ingest indexes."""
        mids = set()
        for doc_id in doc_ids:
            if not doc_id:
                continue
            mid = self.r.get(self._doc_key(doc_id))
            if mid:
                mids.add(mid)
        return mids

    # ── 邮件处理状态 ──

    def is_processed(self, message_id: str) -> bool:
        return self.r.exists(self._k("mail", message_id, "done"))

    def mark_processing(self, message_id: str, uid: str, folder: str,
                        subject: str, from_addr: str, date: str,
                        from_name: str = "", attachment_count: int = 0):
        key = self._k("mail", message_id)
        old = self.get_mail_state(message_id)
        data = {
            "message_id": message_id,
            "uid": uid, "folder": folder,
            "subject": subject, "from_addr": from_addr, "date": date,
            "from_name": from_name or "",
            "attachment_count": str(max(0, int(attachment_count or 0))),
            "status": "processing", "updated_at": str(time.time()),
        }
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        pipe.hset(key, mapping=data)
        self._index_state(pipe, message_id, data)
        pipe.execute()

    def mark_done(self, message_id: str):
        key = self._k("mail", message_id)
        old = self.get_mail_state(message_id)
        data = {**old, "message_id": message_id, "status": "done", "updated_at": str(time.time())}
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        pipe.hset(key, mapping={"status": "done", "updated_at": data["updated_at"]})
        pipe.setex(self._k("mail", message_id, "done"), 30 * 86400, "1")
        self._index_state(pipe, message_id, data)
        # 将 done_uids 写入移入 pipeline，与 done 键原子执行，避免两层去重不一致
        uid = (old or {}).get("uid", "")
        folder = (old or {}).get("folder", "")
        if uid and folder:
            pipe.sadd(self._mbx_k("done_uids", folder), uid)
        pipe.execute()

    def mark_failed(self, message_id: str, error: str):
        key = self._k("mail", message_id)
        old = self.get_mail_state(message_id)
        data = {**old, "message_id": message_id, "status": "failed",
                "error_msg": error[:500], "updated_at": str(time.time())}
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        pipe.hset(key, mapping={
            "status": "failed", "error_msg": error[:500],
            "updated_at": data["updated_at"],
        })
        self._index_state(pipe, message_id, data)
        pipe.execute()

    def mark_ingest_failed(self, message_id: str, error: str, drop_body: bool = False):
        """标记入库失败并移出 ingest 队列，避免下次 ingest 重复卡住。

        正文默认保留到 TTL 过期，便于排查；需要重新入库时可走 reprocess/reset。
        """
        key = self._k("mail", message_id)
        old = self.get_mail_state(message_id)
        data = {**old, "message_id": message_id, "status": "failed",
                "error_msg": error[:500], "updated_at": str(time.time())}
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        pipe.srem(self._k("ingest_queue"), message_id)
        pipe.hset(key, mapping={
            "status": "failed",
            "error_msg": error[:500],
            "updated_at": data["updated_at"],
        })
        self._index_state(pipe, message_id, data)
        if drop_body:
            pipe.delete(self._k("body", message_id))
        pipe.execute()

    def mark_skipped(self, message_id: str, reason: str = ""):
        key = self._k("mail", message_id)
        old = self.get_mail_state(message_id)
        data = {**old, "message_id": message_id, "status": "skipped",
                "error_msg": reason[:500], "updated_at": str(time.time())}
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        pipe.hset(key, mapping={
            "status": "skipped", "error_msg": reason[:500],
            "updated_at": data["updated_at"],
        })
        self._index_state(pipe, message_id, data)
        pipe.execute()

    def get_processed_uids(self, folder: str) -> set[str]:
        """返回该账号该 folder 下已入库的 UID 集合（抓取去重用，O(1) SMEMBERS）。

        UID 只在单个邮箱内唯一，故按账号隔离（_mbx_k）。集合不存在时返回空集——不做
        跨账号全量扫描回填（否则会把别的邮箱的 UID 混进来导致误跳过、漏抓）；真正的重复
        由 pipeline 侧 message_id 级兜底去重（is_processed）兜住，最多重下一次不会重复入库。
        """
        return set(self.r.smembers(self._mbx_k("done_uids", folder)))

    def get_unprocessed_count(self) -> int:
        # 优先用 status 索引集 (SCARD O(1))：failed + pending
        failed_key = self._status_key("failed")
        pending_key = self._status_key("pending")
        if self.r.exists(failed_key) or self.r.exists(pending_key):
            return int(self.r.scard(failed_key)) + int(self.r.scard(pending_key))
        # 回退：无索引集时全量扫描一次
        count = 0
        for key in self.r.scan_iter(match=self._k("mail", "*")):
            if self.r.type(key) != "hash":
                continue
            status = self.r.hget(key, "status")
            if status in ("failed", "pending"):
                count += 1
        return count

    def get_stats(self) -> dict:
        statuses = ["done", "failed", "skipped", "processing", "pending", "indexed"]
        stats = {s: 0 for s in statuses}
        # 优先用 status 索引集 (SCARD O(1))；任一索引集存在即认为索引可用
        if any(self.r.exists(self._status_key(s)) for s in statuses):
            for s in statuses:
                stats[s] = int(self.r.scard(self._status_key(s)))
            return stats
        # 回退：无索引集时全量扫描一次
        for key in self.r.scan_iter(match=self._k("mail", "*")):
            if self.r.type(key) != "hash":
                continue
            status = self.r.hget(key, "status") or "pending"
            if status in stats:
                stats[status] += 1
            else:
                stats["pending"] += 1
        return stats

    def query_stats(self, start_time=None, end_time=None,
                    status: str | None = None,
                    statuses: list[str] | tuple[str, ...] | set[str] | None = None,
                    group_by: str | None = None,
                    has_attachment: bool | None = None,
                    limit: int = 0,
                    message_ids: list[str] | tuple[str, ...] | set[str] | None = None,
                    sender: str | None = None) -> dict:
        """按多维度统计邮件。

        Args:
            start_time: 起始时间（datetime 对象）
            end_time: 结束时间（datetime 对象）
            status: 可选单状态过滤（兼容旧接口）
            statuses: 可选多状态过滤（done/failed/skipped/processing/pending）
            group_by: 分组维度 — "sender" 按发件人分组计数
            has_attachment: True=仅统计有附件的，False=仅无附件的，None=不限
            limit: group_by 时只返回 top N（0=全部）
            message_ids: 可选候选 message_id 集合，用于 hybrid 主题命中后求交
            sender: 可选发件人姓名/地址过滤

        Returns:
            {"total": N, "by_status": {...}, "by_sender": [{addr, name, count}],
             "items": [{message_id, subject, from_addr, date, status, has_attachment}],
             "matched_ids": [...]}
        """
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        from collections import Counter

        by_status = {"done": 0, "failed": 0, "skipped": 0, "processing": 0, "pending": 0}
        sender_counter = Counter()
        sender_names = {}
        matched_ids = []
        items = []
        status_set = set(statuses or [])
        if status:
            status_set.add(status)
        candidate_ids = self._candidate_ids_from_indexes(
            start_time=start_time,
            end_time=end_time,
            statuses=status_set,
            has_attachment=has_attachment,
            message_ids=message_ids,
            sender=sender,
        )
        if candidate_ids is None:
            key_iter = self.r.scan_iter(match=self._k("mail", "*"))
        else:
            key_iter = [self._k("mail", mid) for mid in candidate_ids]

        for key in key_iter:
            if self.r.type(key) != "hash":
                continue
            data = self.r.hgetall(key)
            st = data.get("status") or "pending"

            if status_set and st not in status_set:
                continue
            if sender and not _sender_matches(data, sender):
                continue

            # 时间过滤
            date_str = data.get("date", "")
            if start_time or end_time:
                mail_dt = None
                try:
                    mail_dt = parsedate_to_datetime(date_str)
                except (ValueError, TypeError):
                    try:
                        mail_dt = datetime.fromisoformat(date_str)
                    except (ValueError, TypeError):
                        pass
                if mail_dt is not None:
                    if mail_dt.tzinfo is not None:
                        mail_dt = mail_dt.replace(tzinfo=None)
                    if start_time and mail_dt < start_time:
                        continue
                    if end_time and mail_dt > end_time:
                        continue

            # 附件过滤：看 body 暂存或标记
            att_count = 0
            try:
                att_count = int(data.get("attachment_count") or 0)
            except (TypeError, ValueError):
                att_count = 0
            if has_attachment is not None:
                mid_for_body = data.get("message_id") or key.rsplit(":", 1)[-1]
                body = self.get_mail(mid_for_body)
                if body and body.get("attachments") is not None:
                    att_count = len(body.get("attachments", []) or [])
                if has_attachment and att_count == 0:
                    continue
                if not has_attachment and att_count > 0:
                    continue

            by_status[st] = by_status.get(st, 0) + 1
            mid = data.get("message_id") or key.rsplit(":", 1)[-1]
            matched_ids.append(mid)

            # group_by: sender
            if group_by == "sender":
                addr = data.get("from_addr", "未知")
                sender_counter[addr] += 1
                if data.get("from_name"):
                    sender_names[addr] = data["from_name"]
                # 尝试从 body 取发件人姓名（已入库邮件 body 可能已释放）
                if not sender_names.get(addr):
                    body = self.get_mail(mid)
                    if body and body.get("from_name"):
                        sender_names[addr] = body["from_name"]

            # list 模式：收集邮件摘要
            if group_by is None:
                body = self.get_mail(mid)
                if body:
                    att_count = len(body.get("attachments", []) or [])
                items.append({
                    "message_id": mid,
                    "subject": data.get("subject", "(无主题)"),
                    "from_addr": data.get("from_addr", ""),
                    "from_name": data.get("from_name", ""),
                    "date": date_str,
                    "status": st,
                    "has_attachment": att_count > 0,
                    "attachment_count": att_count,
                })

        # 排序：items 按日期倒序
        items.sort(key=lambda x: x.get("date", ""), reverse=True)

        # sender top N
        by_sender = []
        if group_by == "sender":
            for addr, count in sender_counter.most_common(limit if limit > 0 else None):
                by_sender.append({
                    "addr": addr,
                    "name": sender_names.get(addr, ""),
                    "count": count,
                })

        total = sum(by_status.values())
        logger.info("MailCache.query_stats: total=%d", total)
        return {
            "total": total,
            "by_status": by_status,
            "by_sender": by_sender,
            "items": items[:limit] if limit > 0 and group_by is None else items,
            "matched_ids": matched_ids,
        }

    def _candidate_ids_from_indexes(self, start_time=None, end_time=None,
                                    statuses: set[str] | None = None,
                                    has_attachment: bool | None = None,
                                    message_ids=None,
                                    sender: str | None = None) -> set[str] | None:
        indexed_sets: list[set[str]] = []

        if message_ids is not None:
            indexed_sets.append(set(message_ids))

        if statuses:
            status_ids: set[str] = set()
            missing_status_index = False
            for st in statuses:
                key = self._status_key(st)
                if self.r.exists(key):
                    status_ids.update(self.r.smembers(key))
                else:
                    missing_status_index = True
            if not missing_status_index:
                indexed_sets.append(status_ids)

        if has_attachment is not None:
            key = self._attachment_key(has_attachment)
            if self.r.exists(key):
                indexed_sets.append(set(self.r.smembers(key)))

        if sender:
            key = self._sender_key(sender)
            if self.r.exists(key):
                indexed_sets.append(set(self.r.smembers(key)))

        if start_time or end_time:
            date_key = self._date_key()
            if self.r.exists(date_key):
                start_score = start_time.timestamp() if start_time else "-inf"
                end_score = end_time.timestamp() if end_time else "+inf"
                indexed_sets.append(set(self.r.zrangebyscore(date_key, start_score, end_score)))
            elif indexed_sets:
                # Other indexed filters can still reduce the scan; date will be checked per row.
                pass
            else:
                return None

        if not indexed_sets:
            return None
        candidate_ids = indexed_sets[0]
        for ids in indexed_sets[1:]:
            candidate_ids = candidate_ids & ids
        return candidate_ids

    # ── 正文暂存 + ingest 队列 (fetch → ingest 单一事实源) ──

    def store_mail(self, mail: dict):
        """暂存一封已清洗邮件的完整正文/元数据（带 TTL），并加入 ingest 队列。

        mail 需含 message_id；其余字段（subject/from_addr/to_addrs/cc_addrs/
        date/cleaned_body/attachments/folder/uid）原样存储。
        """
        mid = mail.get("message_id", "")
        if not mid:
            return
        pipe = self.r.pipeline()
        pipe.setex(self._k("body", mid), self._body_ttl,
                   json.dumps(mail, ensure_ascii=False))
        pipe.sadd(self._k("ingest_queue"), mid)
        # 会话线程分组索引（供前端按线程折叠展示；全局，不分账号）
        thread_id = mail.get("thread_id")
        if thread_id:
            pipe.sadd(self._k("idx", "thread", thread_id), mid)
        pipe.execute()

    def get_mail(self, message_id: str) -> dict | None:
        """取出暂存的邮件正文，过期或不存在返回 None"""
        raw = self.r.get(self._k("body", message_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def list_pending_ingest(self) -> list[str]:
        """列出待入库的 message_id（正文仍在 TTL 内的才有效）"""
        return list(self.r.smembers(self._k("ingest_queue")))

    def iter_pending_mails(self):
        """迭代待入库邮件（正文已过期的自动从队列剔除）"""
        for mid in self.list_pending_ingest():
            mail = self.get_mail(mid)
            if mail is None:
                # 正文已过期，清理队列残留
                self.r.srem(self._k("ingest_queue"), mid)
                continue
            yield mail

    def claim_pending_mail(self) -> dict | None:
        """原子领取一封待入库邮件：SPOP 出队后返回其内容。

        多 worker 并发时，SPOP 保证一封邮件只会被一个 worker 领取到，不会重复处理。
        正文已过期的（get_mail 为 None）跳过并继续领取下一封；队列取空时返回 None。
        """
        while True:
            mid = self.r.spop(self._k("ingest_queue"))
            if not mid:
                return None
            mail = self.get_mail(mid)
            if mail is None:
                continue  # 正文已过期，已被 SPOP 移除，继续取下一封
            return mail

    def claim_pending_mail_tracked(self) -> dict | None:
        """SPOP a pending mail AND record it in the in-flight zset (score=claimed_at).

        Recoverable variant of claim_pending_mail: if the worker crashes before
        release_inflight, the reaper can find and requeue it. Expired bodies are
        skipped (already SPOP'd out) without touching in-flight.
        """
        import time as _t
        while True:
            mid = self.r.spop(self._k("ingest_queue"))
            if not mid:
                return None
            mail = self.get_mail(mid)
            if mail is None:
                continue
            self.r.zadd(self._k("inflight"), {mid: _t.time()})
            return mail

    def release_inflight(self, message_id: str) -> None:
        """Remove a mail from the in-flight zset once graph-build finished."""
        if message_id:
            self.r.zrem(self._k("inflight"), message_id)

    def requeue_pending(self, message_id: str) -> None:
        """把已领取但处理中断的邮件放回队列，供后续重试。"""
        if message_id:
            self.r.sadd(self._k("ingest_queue"), message_id)

    def mark_ingested(self, message_id: str, doc_id: str = "", drop_body: bool = True,
                      att_doc_ids: list[str] | None = None):
        """标记已入库：出队、记录正文/附件 doc_id、可选删除正文（即用即删）"""
        key = self._k("mail", message_id)
        old = self.get_mail_state(message_id)
        data = {**old, "message_id": message_id, "status": "done",
                "knowledge_doc_id": doc_id,
                "knowledge_att_doc_ids": ",".join(att_doc_ids or []),
                "updated_at": str(time.time())}
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        pipe.srem(self._k("ingest_queue"), message_id)
        pipe.hset(key, mapping={
            "message_id": message_id,
            "status": "done", "knowledge_doc_id": doc_id,
            "knowledge_att_doc_ids": ",".join(att_doc_ids or []),
            "updated_at": data["updated_at"],
        })
        pipe.setex(self._k("mail", message_id, "done"), 30 * 86400, "1")
        self._index_state(pipe, message_id, data)
        if drop_body:
            pipe.delete(self._k("body", message_id))
        # 将 done_uids 写入移入 pipeline，与 done 键原子执行，避免两层去重不一致
        uid = (old or {}).get("uid", "")
        folder = (old or {}).get("folder", "")
        if uid and folder:
            pipe.sadd(self._mbx_k("done_uids", folder), uid)
        pipe.execute()

    def get_mail_state(self, message_id: str) -> dict:
        """取一封邮件的处理状态哈希（uid/folder/status/knowledge_doc_id 等）"""
        return self.r.hgetall(self._k("mail", message_id))

    def count_done_mails(self) -> int:
        """返回已入库(done)邮件总数。优先使用 status 索引集 (SCARD O(1))。"""
        done_key = self._status_key("done")
        if self.r.exists(done_key):
            return self.r.scard(done_key)
        # Fallback: scan all mail hashes
        count = 0
        for key in self.r.scan_iter(match=self._k("mail", "*")):
            if self.r.type(key) != "hash":
                continue
            if self.r.hget(key, "status") == "done":
                count += 1
        return count

    def count_recent_mails(self) -> int:
        """返回暂存邮件正文（body TTL 内）总数。"""
        count = 0
        for _ in self.r.scan_iter(match=self._k("body", "*")):
            count += 1
        return count

    def list_done_mails(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """列出已入库(done)邮件的元数据（正文已释放，用于强制重新处理）。

        返回含 message_id / subject / from_addr / date / uid / folder /
        knowledge_doc_id。按 updated_at 倒序。
        """
        prefix_len = len(self._prefix) + len("mail:")
        mails = []
        for key in self.r.scan_iter(match=self._k("mail", "*")):
            # 跳过 mail:<id>:done 这类字符串标记键，只处理哈希
            if self.r.type(key) != "hash":
                continue
            data = self.r.hgetall(key)
            if data.get("status") != "done":
                continue
            data["message_id"] = key[prefix_len:]
            mails.append(data)
            if len(mails) >= offset + limit:
                break
        mails.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        return mails[offset:offset + limit]

    # ── 文件邮件源：indexed（表头已知、正文未解析）──

    def store_indexed(self, rec) -> bool:
        """暂存一条文件邮件表头（status=indexed），记录回读定位符，不写正文/不入队。

        rec 为 HeaderRecord。返回是否新增（已存在任何状态则跳过，保证去重幂等）。
        """
        import json as _json

        mid = getattr(rec, "message_id", "") or ""
        if not mid:
            return False
        # 去重：已存在（indexed/pending/processing/done/failed/skipped）则跳过
        if self.get_mail_state(mid):
            return False

        locator = getattr(rec, "locator", {}) or {}
        key = self._k("mail", mid)
        data = {
            "message_id": mid,
            "uid": "",
            "folder": getattr(rec, "folder", "") or "",
            "subject": getattr(rec, "subject", "") or "",
            "from_addr": getattr(rec, "from_addr", "") or "",
            "from_name": getattr(rec, "from_name", "") or "",
            "date": getattr(rec, "date", "") or "",
            "attachment_count": "1" if getattr(rec, "has_attachment", False) else "0",
            "status": "indexed",
            "source_type": locator.get("source_type", ""),
            "source_path": locator.get("path", ""),
            "source_locator": _json.dumps(locator, ensure_ascii=False),
            "updated_at": str(time.time()),
        }
        pipe = self.r.pipeline()
        pipe.hset(key, mapping=data)
        self._index_state(pipe, mid, data)
        pipe.execute()
        return True

    # status filter 参与的目标 status 值；all 走全量
    _INDEXED_STATUSES = {
        "pending": ("indexed",),       # 已扫描待解析
        "done": ("done", "skipped"),   # 已处理
    }

    def count_indexed(self, status: str = "pending") -> int:
        """已扫描(indexed)/已处理(done)邮件总数。优先 status 索引集 (SCARD O(1))。"""
        if status in self._INDEXED_STATUSES:
            total = 0
            for s in self._INDEXED_STATUSES[status]:
                sk = self._status_key(s)
                if self.r.exists(sk):
                    total += self.r.scard(sk)
            return total
        # all：取所有非 transient 邮件
        count = 0
        for s in ("indexed", "pending", "processing", "done", "failed", "skipped"):
            sk = self._status_key(s)
            if self.r.exists(sk):
                count += self.r.scard(sk)
        return count

    def list_indexed(self, limit: int = 100, offset: int = 0, status: str = "pending") -> list[dict]:
        """列出 indexed（pending=未处理/done=已处理/all=全部）邮件，按 date 倒序分页。"""
        date_key = self._date_key()
        if status in self._INDEXED_STATUSES:
            # 收集目标状态集的所有 mid，用 ZSET date 排序分页
            idset: set[str] = set()
            for s in self._INDEXED_STATUSES[status]:
                sk = self._status_key(s)
                if self.r.exists(sk):
                    idset |= set(self.r.smembers(sk))
            scored = [(mid, self.r.zscore(date_key, mid) or 0.0) for mid in idset]
            scored.sort(key=lambda x: x[1], reverse=True)
            total = len(scored)
            page_ids = [mid for mid, _ in scored[offset:offset + limit]]
        else:
            # all：直接从 date ZSET 分页
            page_ids = self.r.zrevrange(date_key, offset, offset + limit)

        mails: list[dict] = []
        for mid in page_ids:
            data = self.r.hgetall(self._k("mail", mid))
            if not data:
                continue
            data["message_id"] = mid
            mails.append(data)
        return mails

    def list_recent_mails(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """列出最近暂存的邮件正文（供前端工作台展示）"""
        mails = []
        for key in self.r.scan_iter(match=self._k("body", "*")):
            raw = self.r.get(key)
            if not raw:
                continue
            try:
                mails.append(json.loads(raw))
            except Exception:
                pass
            if len(mails) >= offset + limit:
                break
        mails.sort(key=lambda m: m.get("date", ""), reverse=True)
        return mails[offset:offset + limit]

    # filter → 参与的原始 status 值；"all" 走 idx:date 全量分页
    _MAIL_FILTERS = {
        "todo": ("pending", "processing", "indexed", "failed"),  # 未完成
        "done": ("done", "skipped"),                             # 已完成（含已跳过）
    }

    def list_mails(self, filter: str = "all", limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        """统一邮件列表：所有状态合并、按日期倒序、分页。返回 (items, total)。

        filter: all=全部；todo=未完成(未处理/待入库/处理中/失败)；done=已完成(已入库/已跳过)。
        items 为 mail:{id} 哈希（含 status/subject/from/date/source_type/attachment_count…）。
        """
        date_key = self._date_key()
        if filter in self._MAIL_FILTERS:
            idset: set[str] = set()
            for s in self._MAIL_FILTERS[filter]:
                idset |= set(self.r.smembers(self._status_key(s)))
            scored = [(mid, self.r.zscore(date_key, mid) or 0.0) for mid in idset]
            scored.sort(key=lambda x: x[1], reverse=True)  # 按日期倒序
            total = len(scored)
            page_ids = [mid for mid, _ in scored[offset:offset + limit]]
        else:
            total = self.r.zcard(date_key)
            page_ids = self.r.zrevrange(date_key, offset, offset + limit - 1)

        items: list[dict] = []
        for mid in page_ids:
            h = self.r.hgetall(self._k("mail", mid))
            if not h:
                continue
            h["message_id"] = mid
            items.append(h)
        return items, total

    # ── 抓取进度 ──

    def get_fetch_progress(self, folder: str, year: int, month: int) -> dict | None:
        key = self._mbx_k("progress", folder, str(year), str(month))
        data = self.r.hgetall(key)
        if not data:
            return None
        data["folder"] = folder
        data["year"] = year
        data["month"] = month
        return data

    def update_fetch_progress(self, folder: str, year: int, month: int,
                               last_uid: str, fetched: int, processed: int,
                               completed: bool = False):
        key = self._mbx_k("progress", folder, str(year), str(month))
        self.r.hset(key, mapping={
            "last_uid": last_uid,
            "total_fetched": str(fetched),
            "total_processed": str(processed),
            "completed": "1" if completed else "0",
            "updated_at": str(time.time()),
        })

    # ── API 用量 ──

    def log_api_usage(self, model: str, prompt_tokens: int, completion_tokens: int):
        from datetime import datetime
        date_str = datetime.now().isoformat()[:10]
        key = self._k("usage", date_str)
        self.r.hincrby(key, "prompt_tokens", prompt_tokens)
        self.r.hincrby(key, "completion_tokens", completion_tokens)
        self.r.hset(key, "model", model)
        self.r.expire(key, 365 * 86400)

    def get_total_tokens(self) -> dict:
        prompt_total = 0
        completion_total = 0
        for key in self.r.scan_iter(match=self._k("usage", "*")):
            data = self.r.hgetall(key)
            prompt_total += int(data.get("prompt_tokens", 0))
            completion_total += int(data.get("completion_tokens", 0))
        return {"prompt_tokens": prompt_total, "completion_tokens": completion_total}

    # ── 邮件重置 ──

    def reset_email(self, message_id: str):
        key = self._k("mail", message_id)
        # 从 done_uids 集合移除，重置后才能被重新拉取
        old = self.get_mail_state(message_id)
        uid = self.r.hget(key, "uid")
        folder = self.r.hget(key, "folder")
        data = {**old, "message_id": message_id, "status": "pending",
                "error_msg": "", "updated_at": str(time.time())}
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        if uid and folder:
            pipe.srem(self._mbx_k("done_uids", folder), uid)
        pipe.hset(key, mapping={
            "status": "pending", "error_msg": "",
            "updated_at": data["updated_at"],
        })
        pipe.delete(self._k("mail", message_id, "done"))
        self._index_state(pipe, message_id, data)
        pipe.execute()

    def close(self):
        self._r.close()

    # ── 兼容旧接口 ──

    def get_statistics(self) -> dict:
        """兼容旧 get_statistics 接口"""
        stats = self.get_stats()
        total = sum(stats.values())
        return {
            "total_emails": total,
            "extracted_emails": stats.get("done", 0),
            "done": stats.get("done", 0),
            "failed": stats.get("failed", 0),
            "skipped": stats.get("skipped", 0),
            "processing": stats.get("processing", 0),
            "pending": stats.get("pending", 0),
        }
