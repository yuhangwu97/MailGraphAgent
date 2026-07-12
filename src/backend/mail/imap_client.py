"""
IMAP 邮件客户端
支持 Gmail / QQ / 阿里企业邮箱，按月分片拉取，断点续传
"""
import imaplib
import email
import email.policy  # Python 3.14 需显式导入
import re
import time
import random
import logging
from datetime import datetime, timedelta
from typing import Iterator

from config.settings import get_settings

logger = logging.getLogger(__name__)

# ── 邮箱服务器配置 ──
PROVIDER_CONFIG = {
    "imap.gmail.com": {"sent_folder": '"[Gmail]/Sent Mail"', "all_mail": '"[Gmail]/All Mail"'},
    "imap.qq.com": {"sent_folder": '"Sent Messages"', "all_mail": None},
    "imap.mxhichina.com": {"sent_folder": '"已发送"', "all_mail": None},
}


from dataclasses import dataclass, field


@dataclass
class IMAPConfig:
    """IMAP 连接配置"""
    server: str = "imap.gmail.com"
    port: int = 993
    email: str = ""
    password: str = ""
    provider: str = "gmail"


def create_imap_client_from_settings() -> "IMAPClient":
    """从全局配置创建 IMAPClient 实例"""
    return IMAPClient()


class IMAPClient:
    """IMAP 邮件客户端，封装连接、搜索、获取操作"""

    def __init__(self, account: dict | None = None):
        cfg = get_settings()
        # account 传入时用账号配置；否则回退环境变量（向后兼容单邮箱）
        if account:
            self.server = account.get("imap_server") or cfg.imap_server
            self.port = int(account.get("imap_port") or cfg.imap_port)
            self.username = account.get("email_user") or cfg.email_user
            self.password = account.get("email_pass") or cfg.email_pass
        else:
            self.server = cfg.imap_server
            self.port = cfg.imap_port
            self.username = cfg.email_user
            self.password = cfg.email_pass
        self.batch_size = cfg.imap_batch_size
        self.delay_min = cfg.imap_request_delay_min
        self.delay_max = cfg.imap_request_delay_max
        self._conn: imaplib.IMAP4_SSL | None = None
        self._provider = PROVIDER_CONFIG.get(self.server, {})

    # ── 连接管理 ──

    def connect(self) -> imaplib.IMAP4_SSL:
        """建立 IMAP SSL 连接并登录"""
        if self._conn is not None:
            try:
                self._conn.noop()
                return self._conn
            except Exception:
                self._conn = None

        logger.info(f"连接 {self.server}:{self.port} ...")
        self._conn = imaplib.IMAP4_SSL(self.server, self.port)
        self._conn.login(self.username, self.password)
        logger.info("IMAP 登录成功")
        return self._conn

    def disconnect(self):
        """安全断开连接"""
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    # ── 文件夹操作 ──

    def select_folder(self, folder: str = "INBOX") -> tuple[str, int]:
        """选择文件夹，返回 (状态, 邮件总数)"""
        conn = self.connect()
        status, data = conn.select(folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"无法选择文件夹: {folder}")
        count = int(data[0].decode() if data else "0")
        logger.info(f"文件夹 [{folder}]: {count} 封邮件")
        return status, count

    def list_folders(self) -> list[str]:
        """列出所有文件夹"""
        conn = self.connect()
        status, data = conn.list()
        if status != "OK":
            return []
        folders = []
        for item in data:
            if isinstance(item, bytes):
                # 格式: b'(\\HasNoChildren) "/" "FolderName"'
                parts = item.decode().split('"')
                if len(parts) >= 4:
                    folders.append(parts[-2])
        return folders

    def get_sent_folder(self) -> str | None:
        """尝试获取发件箱文件夹名"""
        conn = self.connect()
        for candidate in [
            '"[Gmail]/Sent Mail"',
            '"[Gmail]/&' + self._b64("Sent Mail") + '"',
            '"Sent Messages"',
            '"Sent"',
            '"已发送"',
            '"已发送邮件"',
        ]:
            try:
                status, _ = conn.select(candidate, readonly=True)
                if status == "OK":
                    return candidate
            except Exception:
                continue
        return None

    @staticmethod
    def _b64(s: str) -> str:
        import base64
        return base64.b64encode(s.encode()).decode()

    # ── 搜索邮件 ──

    def search_uids(
        self,
        folder: str = "INBOX",
        since: datetime | None = None,
        before: datetime | None = None,
        search_all: bool = False,
    ) -> list[str]:
        """
        搜索邮件 UID 列表。
        - since/before: 时间范围
        - search_all: 忽略时间，搜全部
        """
        conn = self.connect()
        conn.select(folder, readonly=True)

        if search_all:
            criteria = "ALL"
            logger.info(f"搜索全部邮件 (ALL)...")
        elif since and before:
            date_since = since.strftime("%d-%b-%Y")
            date_before = before.strftime("%d-%b-%Y")
            criteria = f'(SINCE "{date_since}" BEFORE "{date_before}")'
            logger.info(f"搜索时间范围: {date_since} ~ {date_before}")
        elif since:
            date_since = since.strftime("%d-%b-%Y")
            criteria = f'(SINCE "{date_since}")'
            logger.info(f"搜索时间范围: {date_since} 至今")
        elif before:
            date_before = before.strftime("%d-%b-%Y")
            criteria = f'(BEFORE "{date_before}")'
            logger.info(f"搜索时间范围: 至 {date_before}")
        else:
            criteria = "ALL"
            logger.info("搜索全部邮件 (ALL)...")

        status, data = conn.uid("SEARCH", None, criteria)
        if status != "OK" or not data or not data[0]:
            return []

        uids = data[0].decode().strip().split()
        logger.info(f"找到 {len(uids)} 封邮件")
        return uids

    # ── 获取邮件 ──

    def fetch_by_uid(self, uid: str, folder: str = "INBOX") -> email.message.EmailMessage | None:
        """通过 UID 获取单封邮件"""
        conn = self.connect()
        conn.select(folder, readonly=True)

        status, data = conn.uid("FETCH", uid, "(RFC822)")
        if status != "OK" or not data or not data[0]:
            logger.warning(f"UID {uid}: 获取失败")
            return None

        raw = data[0]
        if isinstance(raw, tuple):
            raw_email = raw[1]
        else:
            # 可能是 bytes
            raw_email = raw

        try:
            msg = email.message_from_bytes(raw_email, policy=email.policy.default)
            return msg
        except Exception as e:
            logger.error(f"UID {uid}: 解析失败 - {e}")
            return None

    def fetch_by_uids(
        self, uids: list[str], folder: str = "INBOX",
    ) -> dict[str, email.message.EmailMessage]:
        """一条 UID FETCH 命令批量取多封邮件，返回 {uid: message}。

        比逐封 FETCH 少一个数量级的往返。按响应里的 `UID N` 回填映射，
        不依赖服务器返回顺序。解析失败的单封跳过，不影响其余。
        """
        if not uids:
            return {}
        conn = self.connect()
        conn.select(folder, readonly=True)

        uid_arg = ",".join(str(u) for u in uids)
        status, data = conn.uid("FETCH", uid_arg, "(RFC822)")
        result: dict[str, email.message.EmailMessage] = {}
        if status != "OK" or not data:
            logger.warning(f"批量 FETCH 失败 (status={status})")
            return result

        for item in data:
            # 有效负载是 (envelope_bytes, raw_email) 元组；b')' 等分隔项跳过
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            envelope, raw_email = item[0], item[1]
            m = re.search(rb"UID (\d+)", envelope or b"")
            if not m:
                continue
            uid = m.group(1).decode()
            try:
                msg = email.message_from_bytes(raw_email, policy=email.policy.default)
                result[uid] = msg
            except Exception as e:
                logger.error(f"UID {uid}: 解析失败 - {e}")
        return result

    def fetch_internaldates(
        self, uids: list[str], folder: str = "INBOX",
    ) -> dict[str, float]:
        """一条 UID FETCH 批量取 INTERNALDATE，返回 {uid: epoch 秒}。只取元数据、不下正文。

        用于按真实收信时间排序：UID 未必与日期同序（QQ 老邮件也会有很大的 UID），
        不能用 UID 位置当新旧。uids 为服务器升序，用 min:max 一条命令取回即可。
        用 imaplib.Internaldate2tuple 解析，避免 strptime %b 的 locale 依赖。
        """
        if not uids:
            return {}
        conn = self.connect()
        conn.select(folder, readonly=True)

        uid_arg = f"{uids[0]}:{uids[-1]}"
        status, data = conn.uid("FETCH", uid_arg, "(INTERNALDATE)")
        result: dict[str, float] = {}
        if status != "OK" or not data:
            logger.warning(f"批量取 INTERNALDATE 失败 (status={status})")
            return result

        for item in data:
            raw = item if isinstance(item, (bytes, bytearray)) else (
                item[0] if isinstance(item, tuple) and item else b"")
            m = re.search(rb"UID (\d+)", raw or b"")
            if not m:
                continue
            t = imaplib.Internaldate2tuple(raw)
            if not t:
                continue
            result[m.group(1).decode()] = time.mktime(t)
        return result

    def fetch_batch(
        self,
        uids: list[str],
        folder: str = "INBOX",
    ) -> Iterator[tuple[str, email.message.EmailMessage]]:
        """批量获取邮件，返回 (uid, message) 迭代器。

        按 batch_size 分块，每块一条 UID FETCH，块间自动延迟。
        yield 顺序与传入 uids 一致。
        """
        for i in range(0, len(uids), self.batch_size):
            if i > 0:
                delay = random.uniform(self.delay_min, self.delay_max)
                logger.debug(f"已处理 {i}/{len(uids)}, 休眠 {delay:.1f}s...")
                time.sleep(delay)

            chunk = uids[i:i + self.batch_size]
            fetched = self.fetch_by_uids(chunk, folder)
            for uid in chunk:
                msg = fetched.get(str(uid))
                if msg is not None:
                    yield uid, msg

    # ── 时间分片生成器 ──

    def iter_monthly_ranges(
        self,
        start_date: datetime,
        end_date: datetime | None = None,
    ) -> Iterator[tuple[datetime, datetime]]:
        """
        按月生成时间片段，用于分片拉取历史邮件。
        从 start_date 开始，每月一段，直到 end_date (默认今天)。
        """
        if end_date is None:
            end_date = datetime.now()

        current = start_date
        while current < end_date:
            # 计算本月结束
            year = current.year + (current.month // 12)
            month = (current.month % 12) + 1
            if month == 1:
                year += 1
            month_end = datetime(year, month, 1) - timedelta(days=1)
            # 简化：用下月1号减1天
            if current.month == 12:
                next_month = datetime(current.year + 1, 1, 1)
            else:
                next_month = datetime(current.year, current.month + 1, 1)

            chunk_end = min(next_month, end_date)
            yield current, chunk_end
            current = chunk_end

    # ── 便捷方法（用于主程序） ──

    def search_by_date(
        self,
        folder: str = "INBOX",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = None,
    ) -> list[str]:
        """搜索邮件 UID 列表（便捷方法）"""
        uids = self.search_uids(folder, start_date, end_date)
        if limit:
            uids = uids[-limit:]  # 取最后 N 个
        return uids

    def fetch_email(self, folder: str, email_id: str) -> email.message.EmailMessage | None:
        """获取单封邮件（便捷方法）"""
        return self.fetch_by_uid(email_id, folder)

    def close(self):
        """关闭连接"""
        self.disconnect()
