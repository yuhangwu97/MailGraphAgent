"""
OpenAI API 客户端封装
支持重试、token 计数
"""
import logging
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings

logger = logging.getLogger(__name__)


class TokenCounter:
    """Token 计数工具"""

    @staticmethod
    def estimate(text: str) -> int:
        """粗略估算 token 数"""
        import re
        chinese = len(re.findall(r"[一-鿿]", text))
        other = len(text) - chinese
        return int(chinese / 1.5 + other / 4)


class OpenAIExtractor:
    """OpenAI 实体提取器，带重试机制"""

    def __init__(self):
        cfg = get_settings()
        self.client = OpenAI(
            api_key=cfg.openai_api_key,
            base_url=f"{cfg.openai_base_url}/v1",
            timeout=120.0,
        )
        self.model = cfg.openai_model
        self.max_tokens = cfg.ai_max_tokens
        self.temperature = cfg.ai_temperature

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def extract(self, messages: list[dict]) -> str:
        """调用 OpenAI 提取，自动重试"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return response.choices[0].message.content
