"""
.eml 文件源
===========
1 文件 = 1 封邮件。scan 只读头，read 读整封（标准库解析）。
"""
from __future__ import annotations

import email
import email.policy
import logging
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from .base import HeaderRecord, normalize_message_id

logger = logging.getLogger(__name__)


def _load(path: str) -> EmailMessage:
    with open(path, "rb") as f:
        return email.message_from_binary_file(f, policy=email.policy.default)


def _has_attachment(msg: EmailMessage) -> bool:
    for part in msg.walk():
        if "attachment" in str(part.get("Content-Disposition", "")):
            return True
    return False


def scan_headers(path: str) -> Iterator[HeaderRecord]:
    """扫描单个 .eml 文件（就一封）。"""
    from src.backend.mail.parser import _decode_header, _extract_email, _extract_name

    p = Path(path)
    try:
        msg = _load(path)
    except Exception as e:
        logger.warning("跳过无法解析的 .eml: %s (%s)", path, e)
        return

    from_raw = str(msg.get("From", ""))
    date_raw = str(msg.get("Date", ""))
    date_iso = date_raw
    try:
        dt = parsedate_to_datetime(date_raw)
        if dt:
            date_iso = dt.isoformat()
    except Exception:
        pass

    mid = normalize_message_id(str(msg.get("Message-ID", "")),
                               path=str(p), folder=p.parent.name, index=0)
    yield HeaderRecord(
        message_id=mid,
        subject=_decode_header(str(msg.get("Subject", ""))),
        from_addr=_extract_email(from_raw),
        from_name=_extract_name(from_raw),
        date=date_iso,
        folder=p.parent.name,
        has_attachment=_has_attachment(msg),
        locator={"source_type": "eml", "path": str(p)},
    )


def read_message(locator: dict) -> EmailMessage:
    """按 locator 读整封 .eml。"""
    return _load(locator["path"])
