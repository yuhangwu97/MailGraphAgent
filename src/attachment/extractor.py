"""
附件提取器
"""
import logging
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class FileType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    TXT = "txt"
    CSV = "csv"
    ZIP = "zip"
    RAR = "rar"
    UNKNOWN = "unknown"

    @classmethod
    def from_extension(cls, ext: str) -> "FileType":
        ext = ext.lower().lstrip(".")
        mapping = {
            "pdf": cls.PDF, "docx": cls.DOCX, "doc": cls.DOCX,
            "xlsx": cls.XLSX, "xls": cls.XLSX,
            "txt": cls.TXT, "csv": cls.CSV,
            "zip": cls.ZIP, "rar": cls.RAR,
        }
        return mapping.get(ext, cls.UNKNOWN)


class AttachmentExtractor:
    """附件提取器"""

    def __init__(self, download_dir: Path):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, file_path: str | Path) -> str:
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"附件不存在: {path}")
            return ""
        ext = path.suffix.lower()
        file_type = FileType.from_extension(ext)
        try:
            if file_type == FileType.PDF:
                from .parsers import parse_pdf; return parse_pdf(str(path))
            elif file_type == FileType.DOCX:
                from .parsers import parse_docx; return parse_docx(str(path))
            elif file_type == FileType.XLSX:
                from .parsers import parse_excel; return parse_excel(str(path))
            elif file_type in (FileType.TXT, FileType.CSV):
                from .parsers import parse_text; return parse_text(str(path))
            elif file_type in (FileType.ZIP, FileType.RAR):
                from .parsers import extract_and_parse_zip; return extract_and_parse_zip(str(path), str(self.download_dir))
            else:
                return ""
        except Exception as e:
            logger.error(f"附件解析失败: {path} - {e}")
            return ""
