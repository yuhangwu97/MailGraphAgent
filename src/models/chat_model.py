"""聊天模型桥接 —— DeepDoc 插件期望 src.models.chat_model，统一指向项目配置。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class OpenAIBase:
    """OpenAI 兼容接口桥接。"""

    def __init__(self, model_name: str, api_key: str, base_url: str):
        self.model = model_name
        self.api_key = api_key
        self.base_url = base_url

    def predict(self, messages: list[dict], **kwargs) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        resp = client.chat.completions.create(
            model=self.model, messages=messages, **kwargs,
        )
        return resp.choices[0].message.content or ""


class Bailian:
    """百炼 DashScope 接口桥接。"""

    def __init__(self, model_name: str):
        self.model = model_name

    def predict(self, messages: list[dict], **kwargs) -> str:
        from openai import OpenAI
        from config.settings import get_settings
        cfg = get_settings()
        client = OpenAI(api_key=cfg.openai_api_key, base_url=cfg.openai_base_url)
        resp = client.chat.completions.create(
            model=self.model, messages=messages, **kwargs,
        )
        return resp.choices[0].message.content or ""
