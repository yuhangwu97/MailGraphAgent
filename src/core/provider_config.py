"""API provider 配置桥接。"""
from __future__ import annotations

from config.settings import get_settings


def get_provider_api_base(provider: str = "") -> str:
    cfg = get_settings()
    return cfg.openai_base_url


def get_provider_api_key(provider: str = "") -> str:
    cfg = get_settings()
    return cfg.openai_api_key
