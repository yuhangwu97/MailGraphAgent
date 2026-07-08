"""
Redis 进度缓存
轻量级邮件处理进度追踪，替代原来的 MySQL 缓存。
"""
import time
import logging
import redis
from config.settings import get_settings

logger = logging.getLogger(__name__)


class MailCache:
    """邮件处理进度缓存 (Redis 后端)"""

    def __init__(self):
        cfg = get_settings()
        self._r = redis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            db=cfg.redis_db,
            password=cfg.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        self._prefix = "mailgraph:"

    @property
    def r(self) -> redis.Redis:
        return self._r

    def _k(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    # ── 邮件处理状态 ──

    def is_processed(self, message_id: str) -> bool:
        return self.r.exists(self._k("mail", message_id, "done"))

    def mark_processing(self, message_id: str, uid: str, folder: str,
                        subject: str, from_addr: str, date: str):
        key = self._k("mail", message_id)
        self.r.hset(key, mapping={
            "uid": uid, "folder": folder,
            "subject": subject, "from_addr": from_addr, "date": date,
            "status": "processing", "updated_at": str(time.time()),
        })

    def mark_done(self, message_id: str):
        key = self._k("mail", message_id)
        self.r.hset(key, "status", "done")
        self.r.hset(key, "updated_at", str(time.time()))
        self.r.setex(self._k("mail", message_id, "done"), 30 * 86400, "1")

    def mark_failed(self, message_id: str, error: str):
        key = self._k("mail", message_id)
        self.r.hset(key, mapping={
            "status": "failed", "error_msg": error[:500],
            "updated_at": str(time.time()),
        })

    def mark_skipped(self, message_id: str, reason: str = ""):
        key = self._k("mail", message_id)
        self.r.hset(key, mapping={
            "status": "skipped", "error_msg": reason[:500],
            "updated_at": str(time.time()),
        })

    def get_processed_uids(self, folder: str) -> set[str]:
        # scan all mail keys for done status
        uids = set()
        for key in self.r.scan_iter(match=self._k("mail", "*")):
            status = self.r.hget(key, "status")
            f = self.r.hget(key, "folder")
            if status == "done" and f == folder:
                uid = self.r.hget(key, "uid")
                if uid:
                    uids.add(uid)
        return uids

    def get_unprocessed_count(self) -> int:
        count = 0
        for key in self.r.scan_iter(match=self._k("mail", "*")):
            status = self.r.hget(key, "status")
            if status in ("failed", "pending"):
                count += 1
        return count

    def get_stats(self) -> dict:
        stats = {"done": 0, "failed": 0, "skipped": 0, "processing": 0, "pending": 0}
        for key in self.r.scan_iter(match=self._k("mail", "*")):
            status = self.r.hget(key, "status") or "pending"
            if status in stats:
                stats[status] += 1
            else:
                stats["pending"] += 1
        return stats

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
        self.r.hset(key, mapping={
            "status": "pending", "error_msg": "",
            "updated_at": str(time.time()),
        })
        self.r.delete(self._k("mail", message_id, "done"))

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
