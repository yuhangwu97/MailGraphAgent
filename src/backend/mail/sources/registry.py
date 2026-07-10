"""
文件邮件源分发
==============
按扩展名分发 scan_file，按 locator.source_type 分发 read_message，
并提供 open_reader —— 批量读时对同一文件只 open 一次（PST 尤其重要）。
"""
from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Iterator

from . import eml_source, msg_source, pst_source
from .base import HeaderRecord

_EXT_TO_MODULE = {
    ".eml": eml_source,
    ".msg": msg_source,
    ".pst": pst_source,
    ".ost": pst_source,
}

_SOURCE_TYPE_TO_MODULE = {
    "eml": eml_source,
    "msg": msg_source,
    "pst": pst_source,
}


def scan_file(path: str) -> Iterator[HeaderRecord]:
    """按扩展名扫描单个文件，产出 HeaderRecord。"""
    ext = Path(path).suffix.lower()
    mod = _EXT_TO_MODULE.get(ext)
    if mod is None:
        return iter(())
    return mod.scan_headers(path)


def read_message(locator: dict) -> EmailMessage:
    """按 locator.source_type 读整封邮件，归一到 EmailMessage。"""
    st = locator.get("source_type", "")
    mod = _SOURCE_TYPE_TO_MODULE.get(st)
    if mod is None:
        raise ValueError(f"未知邮件源类型: {st!r}")
    return mod.read_message(locator)


class _SingleFileReader:
    """eml/msg：每封独立文件，read 直接分发即可（无需保持句柄）。"""

    def read(self, locator: dict) -> EmailMessage:
        return read_message(locator)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def open_reader(source_type: str, path: str):
    """为「按需批量解析」返回一个可复用的 reader（上下文管理器）。

    - pst/ost：返回 PstReader，对该文件只 open 一次。
    - eml/msg：返回轻量 reader（每封文件独立）。
    """
    if source_type in ("pst",):
        return pst_source.PstReader(path)
    return _SingleFileReader()
