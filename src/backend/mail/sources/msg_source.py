"""
.msg 文件源（Outlook 专有格式）
===============================
用 extract-msg（纯 Python）解析。read 直接借其 asEmailMessage() 归一到
标准 EmailMessage（含附件），scan 只取头字段。
"""
from __future__ import annotations

import logging
from datetime import datetime
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from .base import HeaderRecord, normalize_message_id

logger = logging.getLogger(__name__)


def _date_to_iso(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        try:
            return value.isoformat()
        except Exception:
            return ""
    # 字符串：先按 RFC2822 再原样
    try:
        dt = parsedate_to_datetime(str(value))
        if dt:
            return dt.isoformat()
    except Exception:
        pass
    return str(value)


def _split_addr(raw: str) -> tuple[str, str]:
    """'Name <addr>' → (addr, name)。"""
    from src.backend.mail.parser import _extract_email, _extract_name
    raw = raw or ""
    return _extract_email(raw), _extract_name(raw)


def scan_headers(path: str) -> Iterator[HeaderRecord]:
    """扫描单个 .msg 文件（就一封）。"""
    import extract_msg

    p = Path(path)
    m = None
    try:
        m = extract_msg.Message(path)
        addr, name = _split_addr(m.sender or "")
        mid = normalize_message_id(m.messageId or "",
                                   path=str(p), folder=p.parent.name, index=0)
        has_att = bool(getattr(m, "attachments", None))
        yield HeaderRecord(
            message_id=mid,
            subject=m.subject or "",
            from_addr=addr,
            from_name=name,
            date=_date_to_iso(getattr(m, "date", None)),
            folder=p.parent.name,
            has_attachment=has_att,
            locator={"source_type": "msg", "path": str(p)},
        )
    except Exception as e:
        logger.warning("跳过无法解析的 .msg: %s (%s)", path, e)
    finally:
        if m is not None:
            try:
                m.close()
            except Exception:
                pass


def read_message(locator: dict) -> EmailMessage:
    """按 locator 读整封 .msg，归一到 EmailMessage。"""
    import extract_msg

    m = extract_msg.Message(locator["path"])
    try:
        eml = m.asEmailMessage()
        # asEmailMessage 若无 Message-ID，补一个（与 scan 合成规则一致由上层兜底）
        return eml
    finally:
        try:
            m.close()
        except Exception:
            pass
