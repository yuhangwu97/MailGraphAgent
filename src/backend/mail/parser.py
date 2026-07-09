"""
邮件解析器
从 email.message.EmailMessage 中提取正文、附件、元数据
"""
import re
import hashlib
import logging
from pathlib import Path
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Any

from config.settings import get_settings

logger = logging.getLogger(__name__)


class ParsedEmail:
    """解析后的邮件结构"""

    def __init__(self):
        self.message_id: str = ""
        self.subject: str = ""
        self.from_addr: str = ""
        self.from_name: str = ""
        self.to_addrs: list[str] = []
        self.cc_addrs: list[str] = []
        self.date: str = ""  # ISO 格式
        self.timestamp: float = 0.0
        self.body_text: str = ""
        self.body_html: str = ""
        self.attachments: list[dict[str, Any]] = []  # [{filename, path, size, mime_type}]
        self.hash_id: str = ""

    @property
    def body(self) -> str:
        """主体正文（优先返回纯文本）"""
        return self.body_text or self.body_html

    @body.setter
    def body(self, value: str):
        """设置主体正文"""
        self.body_text = value

    @classmethod
    def from_raw(cls, raw_email: Any) -> "ParsedEmail":
        """从原始邮件创建 ParsedEmail 实例"""
        from email import policy, message_from_bytes, message_from_string

        # 如果是 bytes，转换为 message
        if isinstance(raw_email, bytes):
            try:
                msg = message_from_bytes(raw_email, policy=policy.default)
            except Exception:
                msg = message_from_bytes(raw_email)
        elif isinstance(raw_email, str):
            try:
                msg = message_from_string(raw_email, policy=policy.default)
            except Exception:
                msg = message_from_string(raw_email)
        else:
            msg = raw_email

        # 使用 parse_email 函数解析
        parsed = parse_email(msg)
        
        # 生成哈希ID
        import hashlib
        content_for_hash = f"{parsed.message_id}{parsed.subject}{parsed.from_addr}"
        parsed.hash_id = hashlib.md5(content_for_hash.encode()).hexdigest()[:12]
        
        return parsed

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "subject": self.subject,
            "from_addr": self.from_addr,
            "from_name": self.from_name,
            "to_addrs": self.to_addrs,
            "cc_addrs": self.cc_addrs,
            "date": self.date,
            "timestamp": self.timestamp,
            "body_text": self.body_text,
            "body_html": self.body_html,
            "attachments": [a["filename"] for a in self.attachments],
        }


def parse_email(msg: EmailMessage, download_dir: Path | None = None) -> ParsedEmail:
    """
    解析 email.message.EmailMessage，提取所有字段。
    附件可选保存到 download_dir。
    """
    cfg = get_settings()
    parsed = ParsedEmail()

    # ── 头部元信息 ──
    parsed.message_id = str(msg.get("Message-ID", "")).strip()
    parsed.subject = _decode_header(msg.get("Subject", ""))
    parsed.from_addr = _extract_email(msg.get("From", ""))
    parsed.from_name = _extract_name(msg.get("From", ""))
    parsed.to_addrs = _extract_email_list(msg.get("To", ""))
    parsed.cc_addrs = _extract_email_list(msg.get("Cc", ""))

    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
        if dt:
            parsed.date = dt.isoformat()
            parsed.timestamp = dt.timestamp()
    except Exception:
        parsed.date = str(msg.get("Date", ""))
        parsed.timestamp = 0.0

    # ── 正文提取 ──
    parsed.body_text, parsed.body_html = _extract_body(msg)

    # ── 附件提取 ──
    if download_dir:
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        parsed.attachments = _extract_attachments(msg, download_dir, cfg.max_attachment_size_mb)

    return parsed


def _decode_header(value: str) -> str:
    """解码邮件头部（处理 =?UTF-8?B?...?= 等编码）"""
    if not value:
        return ""
    from email.header import decode_header

    parts = decode_header(value)
    result = ""
    for part, charset in parts:
        if isinstance(part, bytes):
            result += part.decode(charset or "utf-8", errors="ignore")
        else:
            result += str(part)
    return result


def _extract_email(header_value: str) -> str:
    """从 'Name <email>' 格式中提取纯邮箱地址"""
    match = re.search(r"<([^>]+)>", header_value)
    if match:
        return match.group(1).strip().lower()
    # 可能直接就是邮箱
    if "@" in header_value:
        return header_value.strip().lower()
    return header_value.strip()


def _extract_name(header_value: str) -> str:
    """从 'Name <email>' 格式中提取显示名"""
    if not header_value:
        return ""
    match = re.match(r"^([^<]+)", header_value)
    if match:
        name = match.group(1).strip().strip('"').strip("'")
        return _decode_header(name)
    return _decode_header(header_value.split("@")[0])


def _extract_email_list(header_value: str) -> list[str]:
    """从逗号分隔的地址列表中提取所有邮箱"""
    if not header_value:
        return []
    emails = []
    for part in header_value.split(","):
        addr = _extract_email(part.strip())
        if addr:
            emails.append(addr)
    return emails


def _extract_body(msg: EmailMessage) -> tuple[str, str]:
    """提取邮件正文 (纯文本, HTML)"""
    text_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disp = str(part.get("Content-Disposition", ""))

            # 跳过附件
            if "attachment" in content_disp:
                continue

            try:
                payload = part.get_content()
            except Exception:
                continue

            if isinstance(payload, str):
                if content_type == "text/plain":
                    text_parts.append(payload)
                elif content_type == "text/html":
                    html_parts.append(payload)
            elif isinstance(payload, list):
                # multipart 嵌套
                for sub in payload:
                    if hasattr(sub, "get_content"):
                        try:
                            sub_payload = sub.get_content()
                            if isinstance(sub_payload, str):
                                if sub.get_content_type() == "text/plain":
                                    text_parts.append(sub_payload)
                                elif sub.get_content_type() == "text/html":
                                    html_parts.append(sub_payload)
                        except Exception:
                            pass
    else:
        try:
            payload = msg.get_content()
            if isinstance(payload, str):
                if msg.get_content_type() == "text/html":
                    html_parts.append(payload)
                else:
                    text_parts.append(payload)
        except Exception:
            pass

    return "\n".join(text_parts), "\n".join(html_parts)


def _extract_attachments(
    msg: EmailMessage,
    download_dir: Path,
    max_size_mb: int,
) -> list[dict[str, Any]]:
    """提取附件并保存到本地"""
    cfg = get_settings()
    allowed = set(cfg.allowed_attachment_extensions)
    attachments = []
    max_bytes = max_size_mb * 1024 * 1024

    for part in msg.walk():
        content_disp = str(part.get("Content-Disposition", ""))
        if "attachment" not in content_disp:
            continue

        filename = part.get_filename()
        if not filename:
            continue

        filename = _decode_header(filename)
        ext = Path(filename).suffix.lower()

        if ext not in allowed:
            logger.debug(f"跳过附件: {filename} (类型 {ext} 不在白名单)")
            continue

        try:
            payload = part.get_payload(decode=True)
            if not payload:
                continue

            if len(payload) > max_bytes:
                logger.warning(f"附件过大，跳过: {filename} ({len(payload)/1024/1024:.1f}MB)")
                continue

            # 用 hash 去重
            file_hash = hashlib.md5(payload).hexdigest()[:12]
            safe_name = f"{file_hash}_{filename}"
            file_path = download_dir / safe_name

            with open(file_path, "wb") as f:
                f.write(payload)

            attachments.append({
                "filename": filename,
                "path": str(file_path),
                "size": len(payload),
                "mime_type": part.get_content_type(),
                "hash": file_hash,
            })
            logger.debug(f"附件已保存: {filename} ({len(payload)/1024:.0f}KB)")

        except Exception as e:
            logger.warning(f"附件提取失败: {filename} - {e}")

    return attachments
