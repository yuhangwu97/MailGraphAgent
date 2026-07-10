"""
.pst / .ost 文件源（Outlook 邮件库，pypff/libpff）
==================================================
两步：
- scan_headers：递归遍历文件夹树，逐封只读 subject + transport_headers（不读正文/附件）。
- read_message：按 locator（folder_path + index）定位单封，重建成 EmailMessage
  （transport headers + plain/html 正文 + 附件），供上层统一走 parser。

批量读用 PstReader 只 open 一次文件；零散读用 read_message 便捷函数。
"""
from __future__ import annotations

import logging
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from config.settings import get_settings
from .base import (
    HeaderRecord,
    build_email_message,
    format_date_header,
    normalize_message_id,
)

logger = logging.getLogger(__name__)

# MAPI 属性标签（取附件文件名 / MIME 类型）
_PR_ATTACH_LONG_FILENAME = 0x3707
_PR_ATTACH_FILENAME = 0x3704
_PR_DISPLAY_NAME = 0x3001
_PR_ATTACH_MIME_TAG = 0x370E


def _require_pypff():
    try:
        import pypff  # noqa
        return pypff
    except ImportError as e:  # pragma: no cover - 环境相关
        raise RuntimeError(
            "解析 .pst/.ost 需要 libpff-python（pypff）。请 `pip install libpff-python`；"
            "若当前 Python 版本无预编译 wheel，可改用 readpst(libpst) 先导出为 .eml。"
        ) from e


def _entry_str(record_sets, tag: int) -> str | None:
    """从 message/attachment 的 record_sets 中按 MAPI tag 取字符串值。"""
    try:
        for i in range(record_sets.get_number_of_record_sets()
                       if hasattr(record_sets, "get_number_of_record_sets")
                       else record_sets.number_of_record_sets):
            rs = record_sets.get_record_set(i)
            for j in range(rs.number_of_entries):
                entry = rs.get_entry(j)
                if entry.entry_type == tag:
                    try:
                        return entry.get_data_as_string()
                    except Exception:
                        return None
    except Exception:
        return None
    return None


def _attachment_filename(att, index: int) -> str:
    for tag in (_PR_ATTACH_LONG_FILENAME, _PR_ATTACH_FILENAME, _PR_DISPLAY_NAME):
        name = _entry_str(att, tag)
        if name:
            return name.strip("\x00").strip()
    return f"attachment_{index}.bin"


def _walk_folders(folder, path_indices: list[int]):
    """深度优先遍历：yield (folder, path_indices)。path_indices 是从 root 到该 folder 的下标链。"""
    yield folder, path_indices
    try:
        n = folder.number_of_sub_folders
    except Exception:
        n = 0
    for i in range(n):
        try:
            sub = folder.get_sub_folder(i)
        except Exception:
            continue
        yield from _walk_folders(sub, path_indices + [i])


def _navigate(root, path_indices: list[int]):
    """按下标链从 root 定位到目标 folder。"""
    folder = root
    for idx in path_indices:
        folder = folder.get_sub_folder(idx)
    return folder


def _msg_message_id(headers_msg) -> str:
    if headers_msg is None:
        return ""
    return str(headers_msg.get("Message-ID", "") or "")


def _parse_transport_headers(raw: str):
    """把 transport_headers 文本解析成 email.message（可能为 None）。"""
    if not raw:
        return None
    try:
        import email
        import email.policy
        return email.message_from_string(raw, policy=email.policy.default)
    except Exception:
        return None


class PstReader:
    """打开一次 PST/OST，批量按 locator 读整封。可作上下文管理器。"""

    def __init__(self, path: str):
        self.path = path
        self._pypff = _require_pypff()
        self._file = None

    def __enter__(self) -> "PstReader":
        self.open()
        return self

    def __exit__(self, *a):
        self.close()

    def open(self):
        if self._file is None:
            self._file = self._pypff.file()
            self._file.open(self.path)
        return self._file

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None

    # ── Step 1：扫表头 ──

    def scan_headers(self) -> Iterator[HeaderRecord]:
        from src.backend.mail.parser import _decode_header, _extract_email, _extract_name

        f = self.open()
        root = f.get_root_folder()
        for folder, path_indices in _walk_folders(root, []):
            try:
                n_msgs = folder.number_of_sub_messages
            except Exception:
                n_msgs = 0
            folder_name = ""
            try:
                folder_name = folder.name or ""
            except Exception:
                pass
            for j in range(n_msgs):
                try:
                    msg = folder.get_sub_message(j)
                    rec = self._header_record(
                        msg, path_indices, j, folder_name,
                        _decode_header, _extract_email, _extract_name,
                    )
                    if rec is not None:
                        yield rec
                except Exception as e:
                    logger.warning("PST 表头跳过 %s[%s/%d]: %s",
                                   self.path, path_indices, j, e)

    def _header_record(self, msg, path_indices, index, folder_name,
                       _decode_header, _extract_email, _extract_name) -> HeaderRecord | None:
        # subject 优先用 pypff 已解码值
        try:
            subject = msg.subject or ""
        except Exception:
            subject = ""

        headers_msg = None
        try:
            headers_msg = _parse_transport_headers(msg.transport_headers)
        except Exception:
            headers_msg = None

        from_addr = from_name = ""
        date_iso = ""
        if headers_msg is not None:
            from_raw = str(headers_msg.get("From", ""))
            from_addr = _extract_email(from_raw)
            from_name = _extract_name(from_raw)
            if not subject and headers_msg.get("Subject"):
                subject = _decode_header(str(headers_msg.get("Subject")))
            date_raw = str(headers_msg.get("Date", ""))
            try:
                dt = parsedate_to_datetime(date_raw)
                date_iso = dt.isoformat() if dt else date_raw
            except Exception:
                date_iso = date_raw

        # 头缺失时回退 pypff 字段
        if not from_name:
            try:
                from_name = msg.sender_name or ""
            except Exception:
                pass
        if not date_iso:
            try:
                dt = msg.delivery_time or msg.client_submit_time
                date_iso = dt.isoformat() if dt else ""
            except Exception:
                date_iso = ""

        try:
            has_att = (msg.number_of_attachments or 0) > 0
        except Exception:
            has_att = False

        mid = normalize_message_id(_msg_message_id(headers_msg),
                                   path=self.path, folder=folder_name, index=f"{path_indices}:{index}")
        return HeaderRecord(
            message_id=mid,
            subject=subject,
            from_addr=from_addr,
            from_name=from_name,
            date=date_iso,
            folder=folder_name,
            has_attachment=has_att,
            locator={
                "source_type": "pst",
                "path": self.path,
                "folder_path": path_indices,
                "index": index,
            },
        )

    # ── Step 2：读整封 ──

    def read(self, locator: dict) -> EmailMessage:
        f = self.open()
        root = f.get_root_folder()
        folder = _navigate(root, locator.get("folder_path", []))
        msg = folder.get_sub_message(locator["index"])
        return self._to_email_message(msg)

    def _to_email_message(self, msg) -> EmailMessage:
        cfg = get_settings()
        headers_msg = None
        try:
            headers_msg = _parse_transport_headers(msg.transport_headers)
        except Exception:
            headers_msg = None

        def h(name, default=""):
            return str(headers_msg.get(name, default)) if headers_msg is not None else default

        subject = ""
        try:
            subject = msg.subject or ""
        except Exception:
            pass
        if not subject:
            subject = h("Subject")

        from_header = h("From")
        if not from_header:
            try:
                from_header = msg.sender_name or ""
            except Exception:
                from_header = ""

        date_header = h("Date")
        if not date_header:
            try:
                date_header = format_date_header(msg.delivery_time or msg.client_submit_time)
            except Exception:
                date_header = ""

        plain = html = ""
        try:
            b = msg.plain_text_body
            plain = b.decode("utf-8", "ignore") if isinstance(b, bytes) else (b or "")
        except Exception:
            plain = ""
        try:
            b = msg.html_body
            html = b.decode("utf-8", "ignore") if isinstance(b, bytes) else (b or "")
        except Exception:
            html = ""

        attachments = self._read_attachments(msg, cfg.max_attachment_size_mb)

        return build_email_message(
            message_id=_msg_message_id(headers_msg),
            subject=subject,
            from_header=from_header,
            to_header=h("To"),
            cc_header=h("Cc"),
            date_header=date_header,
            plain_body=plain,
            html_body=html,
            attachments=attachments,
        )

    def _read_attachments(self, msg, max_mb: int) -> list[dict]:
        out: list[dict] = []
        try:
            n = msg.number_of_attachments or 0
        except Exception:
            n = 0
        max_bytes = max_mb * 1024 * 1024
        for k in range(n):
            try:
                att = msg.get_attachment(k)
                size = att.size or 0
                if size <= 0 or size > max_bytes:
                    continue
                data = att.read_buffer(size)
                if not data:
                    continue
                filename = _attachment_filename(att, k)
                mime = _entry_str(att, _PR_ATTACH_MIME_TAG) or "application/octet-stream"
                out.append({"filename": filename, "data": data, "mime_type": mime})
            except Exception as e:
                logger.warning("PST 附件读取跳过 [%d]: %s", k, e)
        return out


# ── 便捷函数（零散调用；批量请直接用 PstReader 只 open 一次）──

def scan_headers(path: str) -> Iterator[HeaderRecord]:
    with PstReader(path) as reader:
        yield from reader.scan_headers()


def read_message(locator: dict) -> EmailMessage:
    with PstReader(locator["path"]) as reader:
        return reader.read(locator)
