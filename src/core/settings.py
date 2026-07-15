"""设置桥接 —— DeepDoc 插件期望 src.core.settings.settings，统一指向项目配置。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from config.settings import get_settings

_cfg = get_settings()


class _Paths:
    @property
    def model_ocr_path(self) -> Path:
        return Path(_cfg.data_dir) / "models" / "ocr"

    @property
    def data_parser_data(self) -> Path:
        return Path(_cfg.data_dir) / "parsed"


class _Llm:
    @property
    def model_name(self) -> str:
        return _cfg.openai_model

    @property
    def api_key(self) -> str:
        return _cfg.openai_api_key

    @property
    def api_base(self) -> str:
        return _cfg.openai_base_url


class _Settings:
    def __init__(self):
        self.paths = _Paths()
        self.llm = _Llm()

    def get_api_key(self, provider: str = "") -> str:
        return _cfg.openai_api_key


settings = _Settings()
