"""
附件处理模块
支持 PDF、Word、Excel、纯文本等多种文件格式的解析
"""

from .extractor import AttachmentExtractor, FileType
from .parsers import (
    parse_pdf,
    parse_docx,
    parse_excel,
    parse_text,
    extract_and_parse_zip,
)

__all__ = [
    "AttachmentExtractor",
    "FileType",
    "parse_pdf",
    "parse_docx",
    "parse_excel",
    "parse_text",
    "extract_and_parse_zip",
]
