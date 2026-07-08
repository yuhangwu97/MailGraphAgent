"""
增强的实体提取器

基于 OpenAIExtractor 和 EXTRACTION_SCHEMA，
支持邮件正文和附件文本的结构化提取。
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.ai.openai_client import OpenAIExtractor
from src.ai.prompts import PromptGenerator
from src.ai.extraction_schema import EXTRACTION_SCHEMA, EXTRACTION_SCHEMA_SIMPLE, schema_to_json_str
from config.settings import get_settings

logger = logging.getLogger(__name__)

# 默认空结果模板
EMPTY_RESULT: Dict[str, Any] = {
    "company": {"name": None, "aliases": []},
    "contacts": [],
    "internal_owners": [],
    "projects": [],
    "summary": "",
    "financial_info": {"amount": None, "currency": None, "description": ""},
}

EMPTY_RESULT_SIMPLE: Dict[str, Any] = {
    "company": {"name": None, "aliases": []},
    "contacts": [],
    "projects": [],
    "summary": "",
    "financial_info": {"amount": None, "currency": None, "description": ""},
}


class Extractor:
    """增强的实体提取器，封装 OpenAIExtractor + PromptGenerator + Schema"""

    def __init__(self):
        self.openai_extractor = OpenAIExtractor()
        self.settings = get_settings()

    # ── 核心提取入口 ──

    def extract_from_email(
        self,
        subject: str,
        body: str,
        from_addr: Optional[str] = None,
        to_addrs: Optional[List[str]] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        从邮件正文中提取结构化实体信息。

        Args:
            subject: 邮件主题
            body: 邮件正文
            from_addr: 发件人地址
            to_addrs: 收件人地址列表
            date: 邮件日期字符串

        Returns:
            符合 EXTRACTION_SCHEMA 的字典，提取失败时返回空结构
        """
        # 截断过长的输入
        body_truncated = body[:self.settings.max_text_length] if len(body) > self.settings.max_text_length else body
        subject_truncated = subject[:200] if len(subject) > 200 else subject

        # 构建增强的上下文信息
        context_parts = []
        if from_addr:
            context_parts.append(f"发件人: {from_addr}")
        if to_addrs:
            context_parts.append(f"收件人: {', '.join(to_addrs)}")
        if date:
            context_parts.append(f"日期: {date}")
        context_str = "\n".join(context_parts)

        messages = self._build_email_messages(
            subject=subject_truncated,
            body=body_truncated,
            context=context_str,
        )

        return self._extract_with_retry(messages, default=EMPTY_RESULT)

    def extract_from_attachment_text(self, text: str, filename: str = "") -> Dict[str, Any]:
        """
        从附件文本中提取结构化实体信息（简化版提取）。

        Args:
            text: 附件文本内容
            filename: 附件文件名（可选）

        Returns:
            符合 EXTRACTION_SCHEMA_SIMPLE 的字典
        """
        text_truncated = text[:self.settings.max_text_length] if len(text) > self.settings.max_text_length else text
        messages = self._build_attachment_messages(text=text_truncated, filename=filename)
        return self._extract_with_retry(messages, default=EMPTY_RESULT_SIMPLE)

    # ── Prompt 构建 ──

    def _build_email_messages(
        self, subject: str, body: str, context: str = ""
    ) -> List[Dict[str, str]]:
        """构建邮件提取的 messages"""
        schema_str = schema_to_json_str(EXTRACTION_SCHEMA)
        system_prompt = (
            "你是一个企业邮件分析专家。\n"
            "请从邮件中提取结构化商业信息。\n"
            "严格按照以下 JSON Schema 格式输出，字段缺失时使用 null 或空数组。\n"
            "只输出 JSON，不要其他文字。\n\n"
            f"目标格式:\n{schema_str}"
        )
        user_content = f"邮件主题: {subject}\n\n邮件正文:\n{body}"
        if context:
            user_content = f"{context}\n\n{user_content}"

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    def _build_attachment_messages(self, text: str, filename: str = "") -> List[Dict[str, str]]:
        """构建附件文本提取的 messages"""
        schema_str = schema_to_json_str(EXTRACTION_SCHEMA_SIMPLE)
        system_prompt = (
            "你是一个文档分析专家。\n"
            "请从以下文档文本中提取结构化商业信息。\n"
            "严格按照以下 JSON Schema 格式输出，字段缺失时使用 null 或空数组。\n"
            "只输出 JSON，不要其他文字。\n\n"
            f"目标格式:\n{schema_str}"
        )
        file_info = f"文件名: {filename}\n\n" if filename else ""
        user_content = f"{file_info}文档内容:\n{text}"

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    # ── 调用与解析 ──

    def _extract_with_retry(
        self, messages: List[Dict[str, str]], default: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        调用 OpenAI API 并解析返回的 JSON。
        处理 API 503 / 超时 / JSON 解析错误等异常。
        返回符合 schema 的字典，极端失败时返回 default。
        """
        max_retries = self.settings.ai_max_retries

        for attempt in range(max_retries):
            try:
                response_text = self.openai_extractor.extract(messages)
                parsed = self._parse_json_response(response_text)
                if parsed is not None:
                    # 合并到默认结构，确保所有顶层 key 都存在
                    return {**default, **parsed}
                else:
                    logger.warning(
                        "JSON 解析失败（第 %d 次尝试），原始响应: %s",
                        attempt + 1,
                        response_text[:200],
                    )
            except Exception as e:
                status_code = self._extract_status_code(e)
                if status_code == 503:
                    logger.warning(
                        "API 503 服务不可用（第 %d 次尝试）: %s",
                        attempt + 1,
                        e,
                    )
                else:
                    logger.error(
                        "API 调用异常（第 %d 次尝试）: %s",
                        attempt + 1,
                        e,
                    )

            if attempt < max_retries - 1:
                import time
                wait = min(2 ** attempt * 2, 30)  # 指数退避: 2, 4, 8, ...
                logger.info("等待 %ds 后重试...", wait)
                time.sleep(wait)

        logger.error("所有重试均失败，返回空结构")
        return default

    @staticmethod
    def _parse_json_response(text: str) -> Optional[Dict[str, Any]]:
        """
        健壮的 JSON 解析：
        - 处理 ```json ... ``` 代码块
        - 处理 ``` ... ``` 代码块
        - 去除前后空白
        - 处理 trailing commas
        """
        if not text or not text.strip():
            return None

        text = text.strip()

        # 移除 markdown 代码块标记
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        # 找到第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            # 尝试找第一个 [ 和最后一个 ]
            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end == -1 or end <= start:
                return None
        text = text[start : end + 1]

        # 尝试标准解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 处理 trailing commas（在 } 和 ] 前移除多余的逗号）
        text = re.sub(r",\s*}", "}", text)
        text = re.sub(r",\s*]", "]", text)

        # 修复单引号为双引号（仅对值边界）
        text = re.sub(r"(?<!\\)'(.*?)(?<!\\)'", r'"\1"', text)

        # 处理布尔值和 null 的大小写变体
        text = re.sub(r':\s*True\b', ': true', text)
        text = re.sub(r':\s*False\b', ': false', text)
        text = re.sub(r':\s*None\b', ': null', text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_status_code(exception: Exception) -> Optional[int]:
        """尝试从异常中提取 HTTP 状态码"""
        msg = str(exception)
        match = re.search(r"\b(50[0-9])\b", msg)
        if match:
            return int(match.group(1))
        return None


# 便捷函数，兼容旧式调用
def extract_from_email(
    subject: str,
    body: str,
    from_addr: Optional[str] = None,
    to_addrs: Optional[List[str]] = None,
    date: Optional[str] = None,
) -> Dict[str, Any]:
    """便捷函数：从邮件提取实体（单例 Extractor）"""
    return Extractor().extract_from_email(subject, body, from_addr, to_addrs, date)


def extract_from_attachment_text(text: str, filename: str = "") -> Dict[str, Any]:
    """便捷函数：从附件文本提取实体（单例 Extractor）"""
    return Extractor().extract_from_attachment_text(text, filename)


__all__ = [
    "Extractor",
    "extract_from_email",
    "extract_from_attachment_text",
    "EMPTY_RESULT",
    "EMPTY_RESULT_SIMPLE",
]
