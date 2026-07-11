"""MySQL 持久化邮件元数据 — 与 Redis 双写，保证数据不丢。

Redis 作为操作缓存（队列、状态、TTL 正文），MySQL 存最终状态用于持久化查询。
"""
import logging
import time
from contextlib import contextmanager
from typing import Optional

import pymysql
from pymysql.cursors import DictCursor

from config.settings import get_settings

logger = logging.getLogger(__name__)

# ── DDL ──

CREATE_TABLE_MAILS = """
CREATE TABLE IF NOT EXISTS mails (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    message_id VARCHAR(512) NOT NULL,
    account_id VARCHAR(64) NOT NULL DEFAULT '',
    uid VARCHAR(255) DEFAULT '',
    folder VARCHAR(255) DEFAULT '',
    subject TEXT,
    from_addr VARCHAR(512) DEFAULT '',
    from_name VARCHAR(255) DEFAULT '',
    `date` VARCHAR(64) DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    source_type VARCHAR(32) DEFAULT '',
    source_path TEXT,
    attachment_count INT DEFAULT 0,
    ragflow_doc_id VARCHAR(128) DEFAULT '',
    ragflow_att_doc_ids TEXT,
    error_msg TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_msg_id (message_id),
    INDEX idx_account_status (account_id, status),
    INDEX idx_date (`date`),
    INDEX idx_source_type (source_type),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

CREATE_TABLE_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS mail_accounts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    account_id VARCHAR(64) NOT NULL UNIQUE,
    email VARCHAR(255) DEFAULT '',
    provider VARCHAR(32) DEFAULT 'imap',
    imap_server VARCHAR(255) DEFAULT '',
    imap_port INT DEFAULT 993,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

# ── Connection pool ──

_pool: Optional[pymysql.connections.Connection] = None


def _get_conn() -> pymysql.connections.Connection:
    """获取或创建 MySQL 连接（简单单连接，邮件量不大时够用）。"""
    global _pool
    if _pool is not None:
        try:
            _pool.ping(reconnect=True)
            return _pool
        except Exception:
            _pool = None

    cfg = get_settings()
    _pool = pymysql.connect(
        host=cfg.mysql_host,
        port=cfg.mysql_port,
        user=cfg.mysql_user,
        password=cfg.mysql_password,
        database=cfg.mysql_database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=5,
    )
    logger.info("MySQL connected: %s:%s/%s", cfg.mysql_host, cfg.mysql_port, cfg.mysql_database)
    return _pool


@contextmanager
def get_cursor():
    conn = _get_conn()
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()


def init_db():
    """初始化表结构（幂等）。"""
    try:
        with get_cursor() as cur:
            cur.execute(CREATE_TABLE_MAILS)
            cur.execute(CREATE_TABLE_ACCOUNTS)
        logger.info("MySQL tables initialized")
    except Exception as e:
        logger.warning("MySQL init skipped (DB may not be ready): %s", e)


# ── Mail CRUD ──

UPSERT_MAIL = """
INSERT INTO mails
    (message_id, account_id, uid, folder, subject, from_addr, from_name,
     `date`, status, source_type, source_path, attachment_count,
     ragflow_doc_id, ragflow_att_doc_ids, error_msg)
VALUES
    (%(message_id)s, %(account_id)s, %(uid)s, %(folder)s, %(subject)s,
     %(from_addr)s, %(from_name)s, %(date)s, %(status)s, %(source_type)s,
     %(source_path)s, %(attachment_count)s, %(ragflow_doc_id)s,
     %(ragflow_att_doc_ids)s, %(error_msg)s)
ON DUPLICATE KEY UPDATE
    account_id = VALUES(account_id),
    uid = VALUES(uid),
    folder = VALUES(folder),
    subject = VALUES(subject),
    from_addr = VALUES(from_addr),
    from_name = VALUES(from_name),
    `date` = VALUES(`date`),
    status = VALUES(status),
    source_type = VALUES(source_type),
    source_path = VALUES(source_path),
    attachment_count = VALUES(attachment_count),
    ragflow_doc_id = VALUES(ragflow_doc_id),
    ragflow_att_doc_ids = VALUES(ragflow_att_doc_ids),
    error_msg = VALUES(error_msg);
"""

UPDATE_STATUS = """
UPDATE mails SET status = %(status)s, error_msg = %(error_msg)s
WHERE message_id = %(message_id)s
"""


def save_mail(account_id: str, data: dict):
    """保存或更新一封邮件的元数据到 MySQL。

    data 可包含: message_id, uid, folder, subject, from_addr, from_name,
                date, status, source_type, source_path, attachment_count,
                ragflow_doc_id, ragflow_att_doc_ids, error_msg
    """
    try:
        params = {
            "message_id": data.get("message_id", ""),
            "account_id": account_id or "",
            "uid": data.get("uid", "") or "",
            "folder": data.get("folder", "") or "",
            "subject": (data.get("subject") or "")[:1000],
            "from_addr": data.get("from_addr", "") or "",
            "from_name": data.get("from_name", "") or "",
            "date": data.get("date", "") or "",
            "status": data.get("status", "pending"),
            "source_type": data.get("source_type", "") or "",
            "source_path": data.get("source_path", "") or data.get("source_locator", "") or "",
            "attachment_count": int(data.get("attachment_count") or 0),
            "ragflow_doc_id": data.get("ragflow_doc_id", "") or "",
            "ragflow_att_doc_ids": data.get("ragflow_att_doc_ids", "") or "",
            "error_msg": (data.get("error_msg") or "")[:500],
        }
        # Timestamp for date sorting
        if not params["date"]:
            params["date"] = data.get("timestamp", "") or ""

        with get_cursor() as cur:
            cur.execute(UPSERT_MAIL, params)
    except Exception as e:
        logger.error("MySQL save_mail(%s) failed: %s", data.get("message_id", "?"), e)


def update_mail_status(message_id: str, status: str, error_msg: str = ""):
    """仅更新状态字段（轻量操作）。"""
    try:
        with get_cursor() as cur:
            cur.execute(UPDATE_STATUS, {
                "message_id": message_id,
                "status": status,
                "error_msg": (error_msg or "")[:500],
            })
    except Exception as e:
        logger.error("MySQL update_status(%s) failed: %s", message_id, e)


def save_account(account_id: str, email: str = "", provider: str = "imap",
                 imap_server: str = "", imap_port: int = 993):
    """保存邮箱账号信息。"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                INSERT INTO mail_accounts (account_id, email, provider, imap_server, imap_port)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    email = VALUES(email),
                    provider = VALUES(provider),
                    imap_server = VALUES(imap_server),
                    imap_port = VALUES(imap_port)
            """, (account_id, email, provider, imap_server, imap_port))
    except Exception as e:
        logger.error("MySQL save_account(%s) failed: %s", account_id, e)


def get_mails(account_id: str, status: Optional[str] = None,
              search: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """分页查询邮件列表（替代 Redis scan）。"""
    try:
        where = ["account_id = %s"]
        params = [account_id]

        if status:
            where.append("status = %s")
            params.append(status)

        if search:
            where.append("(subject LIKE %s OR from_addr LIKE %s OR from_name LIKE %s)")
            like = f"%{search}%"
            params.extend([like, like, like])

        where_clause = " AND ".join(where)

        with get_cursor() as cur:
            cur.execute(f"SELECT COUNT(*) as cnt FROM mails WHERE {where_clause}", params)
            total = cur.fetchone()["cnt"]

            cur.execute(
                f"SELECT * FROM mails WHERE {where_clause} ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                params + [limit, offset]
            )
            rows = cur.fetchall()

        # Map columns to frontend MailItem field names
        items = []
        for r in rows:
            items.append({
                "message_id": r.get("message_id", ""),
                "subject": r.get("subject", ""),
                "from_addr": r.get("from_addr", ""),
                "from_name": r.get("from_name", ""),
                "date": r.get("date", ""),
                "status": r.get("status", "pending"),
                "source_type": r.get("source_type", ""),
                "folder": r.get("folder", ""),
                "attachment_count": r.get("attachment_count", 0),
                "ragflow_doc_id": r.get("ragflow_doc_id", ""),
            })
        return items, total
    except Exception as e:
        logger.error("MySQL get_mails failed: %s", e)
        return [], 0


def get_mail_stats(account_id: str) -> dict:
    """获取邮件统计（替代 Redis get_stats）。"""
    try:
        with get_cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) as cnt FROM mails WHERE account_id = %s GROUP BY status",
                (account_id,)
            )
            rows = cur.fetchall()
        stats = {"total": 0, "done": 0, "pending": 0, "indexed": 0,
                 "failed": 0, "skipped": 0, "processing": 0, "ingested": 0}
        for r in rows:
            s = r.get("status", "")
            stats[s] = r.get("cnt", 0)
            stats["total"] += r.get("cnt", 0)
        return stats
    except Exception as e:
        logger.error("MySQL get_mail_stats failed: %s", e)
        return {"total": 0, "done": 0, "pending": 0, "indexed": 0,
                "failed": 0, "skipped": 0, "processing": 0, "ingested": 0}


def close():
    """关闭 MySQL 连接。"""
    global _pool
    if _pool:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None
