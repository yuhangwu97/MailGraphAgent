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
        # 按账号隔离命名空间：mail/body/ingest_queue/done_uids/progress/usage 全部自动分账号
        self._account_id = account_id
        self._prefix = f"mailgraph:{account_id}:" if account_id else "mailgraph:"
        self._body_ttl = int(cfg.fetched_body_ttl_days) * 86400

    @property
    def r(self) -> redis.Redis:
        return self._r

    def _k(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

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
        for doc_id in [data.get("ragflow_doc_id", "")] + (data.get("ragflow_att_doc_ids", "") or "").split(","):
            if doc_id:
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
        for doc_id in [data.get("ragflow_doc_id", "")] + (data.get("ragflow_att_doc_ids", "") or "").split(","):
            if doc_id:
                pipe.set(self._doc_key(doc_id), message_id)

    def _replace_indexes(self, message_id: str, data: dict):
        old = self.get_mail_state(message_id)
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        self._index_state(pipe, message_id, data)
        pipe.execute()

    def message_ids_for_docs(self, doc_ids: list[str]) -> set[str]:
        """Resolve RAGFlow document ids back to message_ids via ingest indexes."""
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
        pipe.execute()
        self._index_done_uid(key)

    def _index_done_uid(self, mail_key: str):
        """把已完成邮件的 uid 加入 per-folder done_uids 集合（供快速去重）"""
        uid = self.r.hget(mail_key, "uid")
        folder = self.r.hget(mail_key, "folder")
        if uid and folder:
            self.r.sadd(self._k("done_uids", folder), uid)

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
        """返回该 folder 下已入库的 UID 集合（去重用）。

        优先读 per-folder done_uids 集合（O(1) 一次 SMEMBERS）；
        集合不存在（老数据）时回退全量扫描一次并回填，之后即走快路径。
        """
        set_key = self._k("done_uids", folder)
        if self.r.exists(set_key):
            return set(self.r.smembers(set_key))

        # 回退 + 回填
        uids = set()
        for key in self.r.scan_iter(match=self._k("mail", "*")):
            if self.r.type(key) != "hash":
                continue
            status = self.r.hget(key, "status")
            f = self.r.hget(key, "folder")
            if status == "done" and f == folder:
                uid = self.r.hget(key, "uid")
                if uid:
                    uids.add(uid)
        if uids:
            self.r.sadd(set_key, *uids)
        return uids

    def get_unprocessed_count(self) -> int:
        count = 0
        for key in self.r.scan_iter(match=self._k("mail", "*")):
            if self.r.type(key) != "hash":
                continue
            status = self.r.hget(key, "status")
            if status in ("failed", "pending"):
                count += 1
        return count

    def get_stats(self) -> dict:
        stats = {"done": 0, "failed": 0, "skipped": 0, "processing": 0, "pending": 0}
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
            saw_status_index = False
            for st in statuses:
                key = self._status_key(st)
                if self.r.exists(key):
                    saw_status_index = True
                    status_ids.update(self.r.smembers(key))
            if saw_status_index:
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

    def mark_ingested(self, message_id: str, doc_id: str = "", drop_body: bool = True,
                      att_doc_ids: list[str] | None = None):
        """标记已入库：出队、记录正文/附件 doc_id、可选删除正文（即用即删）"""
        key = self._k("mail", message_id)
        old = self.get_mail_state(message_id)
        data = {**old, "message_id": message_id, "status": "done",
                "ragflow_doc_id": doc_id,
                "ragflow_att_doc_ids": ",".join(att_doc_ids or []),
                "updated_at": str(time.time())}
        pipe = self.r.pipeline()
        if old:
            self._remove_indexes_for_state(pipe, message_id, old)
        pipe.srem(self._k("ingest_queue"), message_id)
        pipe.hset(key, mapping={
            "message_id": message_id,
            "status": "done", "ragflow_doc_id": doc_id,
            "ragflow_att_doc_ids": ",".join(att_doc_ids or []),
            "updated_at": data["updated_at"],
        })
        pipe.setex(self._k("mail", message_id, "done"), 30 * 86400, "1")
        self._index_state(pipe, message_id, data)
        if drop_body:
            pipe.delete(self._k("body", message_id))
        pipe.execute()
        self._index_done_uid(key)

    def get_mail_state(self, message_id: str) -> dict:
        """取一封邮件的处理状态哈希（uid/folder/status/ragflow_doc_id 等）"""
        return self.r.hgetall(self._k("mail", message_id))

    def list_done_mails(self, limit: int = 100) -> list[dict]:
        """列出已入库(done)邮件的元数据（正文已释放，用于强制重新处理）。

        返回含 message_id / subject / from_addr / date / uid / folder /
        ragflow_doc_id。按 updated_at 倒序。
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
            if len(mails) >= limit:
                break
        mails.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        return mails

    def list_recent_mails(self, limit: int = 50) -> list[dict]:
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
            if len(mails) >= limit:
                break
        mails.sort(key=lambda m: m.get("date", ""), reverse=True)
        return mails

    # ── 抓取进度 ──

    def get_fetch_progress(self, folder: str, year: int, month: int) -> dict | None:
        key = self._k("progress", folder, str(year), str(month))
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
        key = self._k("progress", folder, str(year), str(month))
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
            pipe.srem(self._k("done_uids", folder), uid)
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
