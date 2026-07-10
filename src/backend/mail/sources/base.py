"""
文件邮件源 — 公共数据结构与工具
================================
HeaderRecord：Step 1 扫描产出的轻量表头记录。
另含 message_id 归一/合成、把零散字段组装成 EmailMessage 的工具。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any

# 支持的文件后缀（.ost 与 .pst 同格式）
EXTENSIONS = {".eml", ".msg", ".pst", ".ost"}


@dataclass
class HeaderRecord:
    """一封邮件的轻量表头（不含正文）。"""

    message_id: str
    subject: str = ""
    from_addr: str = ""
    from_name: str = ""
    date: str = ""              # ISO 或原始 Date 头，MailCache 两者都能解析
    folder: str = ""            # PST 内文件夹名 / 文件所在目录名
    has_attachment: bool = False
    # 回读定位符：必须含 source_type + path，PST 另含 folder_path/index
    locator: dict[str, Any] = field(default_factory=dict)


def expand_paths(paths: list[str]) -> list[Path]:
    """把文件/目录路径列表展开成受支持的邮件文件列表（去重、排序）。

    目录会递归收集其下所有受支持后缀的文件。
    """
    out: list[Path] = []
    seen: set[str] = set()
    for raw in paths or []:
        p = Path(raw).expanduser()
        if not p.exists():
            continue
        candidates: list[Path]
        if p.is_dir():
            candidates = [c for c in sorted(p.rglob("*"))
                          if c.is_file() and c.suffix.lower() in EXTENSIONS]
        elif p.suffix.lower() in EXTENSIONS:
            candidates = [p]
        else:
            candidates = []
        for c in candidates:
            key = str(c.resolve())
            if key not in seen:
                seen.add(key)
                out.append(c)
    return out


def synth_message_id(path: str, folder: str, index: int | str) -> str:
    """为缺失 Message-ID 的邮件合成一个稳定、看起来像真 id 的标识。

    以 文件路径 + 文件夹 + 序号 作 hash，保证同一封邮件在 scan/read 两段一致。
    """
    h = hashlib.sha1(f"{path}|{folder}|{index}".encode("utf-8", "ignore")).hexdigest()[:16]
    return f"<file-{h}@mailgraph.local>"


def normalize_message_id(raw: str, *, path: str, folder: str, index: int | str) -> str:
    """清洗邮件头里的 Message-ID；为空则合成。"""
    mid = (raw or "").strip()
    if mid:
        return mid
    return synth_message_id(path, folder, index)


def to_iso(dt: datetime | None) -> str:
    """datetime → ISO；None → 空串。"""
    if not dt:
        return ""
    try:
        return dt.isoformat()
    except Exception:
        return ""


def build_email_message(
    *,
    message_id: str = "",
    subject: str = "",
    from_header: str = "",
    to_header: str = "",
    cc_header: str = "",
    date_header: str = "",
    plain_body: str = "",
    html_body: str = "",
    attachments: list[dict] | None = None,
) -> EmailMessage:
    """把零散字段组装成标准 EmailMessage。

    - 正文：有纯文本用 set_content(text)，再有 HTML 用 add_alternative(html)。
    - 附件：add_attachment(data, maintype, subtype, filename)（自动带
      Content-Disposition: attachment，供 parser._extract_attachments 识别）。

    attachments: [{"filename": str, "data": bytes, "mime_type": "a/b"}]
    """
    msg = EmailMessage()
    if message_id:
        msg["Message-ID"] = message_id
    if subject:
        msg["Subject"] = subject
    if from_header:
        msg["From"] = from_header
    if to_header:
        msg["To"] = to_header
    if cc_header:
        msg["Cc"] = cc_header
    if date_header:
        msg["Date"] = date_header

    plain = plain_body or ""
    html = html_body or ""
    if plain or not html:
        msg.set_content(plain)
        if html:
            msg.add_alternative(html, subtype="html")
    else:
        # 只有 HTML：直接作为正文（保留标记，parser 会做剥离/清洗）
        msg.set_content(html, subtype="html")

    for att in attachments or []:
        data = att.get("data")
        if not data:
            continue
        filename = att.get("filename") or "attachment.bin"
        mime = att.get("mime_type") or "application/octet-stream"
        maintype, _, subtype = mime.partition("/")
        try:
            msg.add_attachment(
                data,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=filename,
            )
        except Exception:
            # 附件损坏不应阻断正文入库
            continue

    return msg


def format_date_header(dt: datetime | None) -> str:
    """datetime → RFC2822 Date 头文本。"""
    if not dt:
        return ""
    try:
        return format_datetime(dt)
    except Exception:
        return ""
