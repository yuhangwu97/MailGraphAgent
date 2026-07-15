"""
多邮箱账号注册表
================
全局存 Redis 哈希 `mailgraph:accounts`（field=account_id, value=JSON）。
按用户要求密码明文存储（个人内网工具，未加密）。

每个账号的数据隔离由 MailCache(account_id) 的键前缀负责，本模块只管账号本身。
"""
import json
import logging
import re

from config.settings import get_settings
from src.backend.redis_pool import make_client

logger = logging.getLogger(__name__)

ACCOUNTS_KEY = "mailgraph:accounts"
DEFAULT_KEY = "mailgraph:default_account"  # string: 用户指定的默认账号 id


def account_id_from_email(email: str) -> str:
    """把邮箱地址规整成稳定、安全的账号 id。"""
    return re.sub(r"[^0-9A-Za-z]+", "_", (email or "").strip()).strip("_")[:40] or "account"


class AccountStore:
    """邮箱账号 CRUD（全局注册表，不带账号前缀）。"""

    def __init__(self):
        cfg = get_settings()
        self._cfg = cfg
        self._r = make_client(
            decode_responses=True,
            socket_connect_timeout=5,
        )

    def list(self) -> list[dict]:
        """返回所有账号，按 label 排序。"""
        accounts = []
        for raw in self._r.hgetall(ACCOUNTS_KEY).values():
            try:
                accounts.append(json.loads(raw))
            except Exception:
                pass
        accounts.sort(key=lambda a: a.get("label") or a.get("email_user") or "")
        return accounts

    def get(self, account_id: str) -> dict | None:
        raw = self._r.hget(ACCOUNTS_KEY, account_id)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def add(self, label: str, imap_server: str, imap_port: int,
            email_user: str, email_pass: str, provider: str = "") -> dict:
        """新增/覆盖账号（同邮箱 id 覆盖）。返回账号 dict。"""
        aid = account_id_from_email(email_user)
        acct = {
            "id": aid,
            "label": label or email_user,
            "imap_server": imap_server,
            "imap_port": int(imap_port or 993),
            "email_user": email_user,
            "email_pass": email_pass,
            "provider": provider or "",
        }
        self._r.hset(ACCOUNTS_KEY, aid, json.dumps(acct, ensure_ascii=False))
        logger.info("已保存邮箱账号: %s (%s)", acct["label"], email_user)
        return acct

    def delete(self, account_id: str):
        """删除账号条目（该账号已入库的 Redis 数据/图谱不在此清理）。"""
        self._r.hdel(ACCOUNTS_KEY, account_id)
        logger.info("已删除邮箱账号: %s", account_id)

    def default_id(self) -> str | None:
        """默认账号 id：优先用户显式设置的（若仍存在），否则回退到列表第一个。"""
        stored = self._r.get(DEFAULT_KEY)
        if stored and self._r.hexists(ACCOUNTS_KEY, stored):
            return stored
        accounts = self.list()
        return accounts[0]["id"] if accounts else None

    def set_default(self, account_id: str) -> None:
        """指定默认账号（无 X-Account-Id 时 / CLI 使用）。账号需已存在。"""
        if not self._r.hexists(ACCOUNTS_KEY, account_id):
            raise ValueError(f"account not found: {account_id}")
        self._r.set(DEFAULT_KEY, account_id)
        logger.info("默认邮箱已设为: %s", account_id)

    def ensure_default_from_env(self):
        """存量迁移：无任何账号且环境变量配了邮箱时，导入为默认账号（一次性）。"""
        if self._r.hlen(ACCOUNTS_KEY) > 0:
            return
        cfg = self._cfg
        if cfg.email_user:
            self.add(
                label="默认",
                imap_server=cfg.imap_server,
                imap_port=cfg.imap_port,
                email_user=cfg.email_user,
                email_pass=cfg.email_pass,
                provider=getattr(cfg, "email_provider", ""),
            )
            logger.info("已从环境变量迁移默认邮箱账号: %s", cfg.email_user)

    def close(self):
        """释放客户端引用（共享连接池由进程级池管理）。"""
        self._r = None
