"""
文件邮件源（file-based mail sources）
====================================
把本地邮件文件（.eml / .msg / .pst / .ost）统一抽象成两步能力：

  scan_file(path)      -> Iterator[HeaderRecord]     # Step 1 轻量扫表头，不读正文
  read_message(locator)-> email.message.EmailMessage # Step 2 按需读整封（含附件）

关键：read 一律归一到标准 email.message.EmailMessage，好让上层原样复用
parser.parse_email() / Pipeline._store_fetched_mail() / run_ingest()。

对外只暴露：
  EXTENSIONS, expand_paths, scan_file, read_message, open_reader
"""
from __future__ import annotations

from .base import HeaderRecord, EXTENSIONS, expand_paths
from .registry import scan_file, read_message, open_reader

__all__ = [
    "HeaderRecord",
    "EXTENSIONS",
    "expand_paths",
    "scan_file",
    "read_message",
    "open_reader",
]
