"""项目全局日志工具。"""
import logging


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(name)


class LogManager:
    """日志管理器 — 兼容旧接口，实例化后可直接当 logger 使用。"""

    def __init__(self) -> None:
        self._logger = logging.getLogger("mailgraph")

    def debug(self, msg, *args, **kwargs):
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._logger.error(msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self._logger.exception(msg, *args, **kwargs)
