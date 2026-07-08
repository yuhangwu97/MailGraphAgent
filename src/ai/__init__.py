"""
AI 实体提取模块
使用 OpenAI API 从邮件中提取结构化实体
"""
import logging
import json
import re
from typing import Dict, Any, Optional
from openai import OpenAI

from config.settings import get_settings

logger = logging.getLogger(__name__)

# 初始化 OpenAI 客户端
settings = get_settings()
client = OpenAI(
    api_key=settings.openai_api_key,
    base_url=f"{settings.openai_base_url}/v1",
)

# 提取模板
EXTRACTION_SYSTEM_PROMPT = """
你是一个专业的邮件内容分析专家。

你的任务是从邮件内容中提取以下结构化信息：
1. 公司名称 (company)
2. 公司类型 (company_type): 如"铁路局"、"工程公司"、"设备供应商"等
3. 联系人 (contact): 数组，包含 name、role、department、email
4. 项目信息 (project): 数组，包含 name、code、category
5. 项目类型 (project_category): 如"基础设施建设"、"信息系统"等
6. 内部负责人 (internal_owner): 数组，包含 name、role、department
7. 合同金额 (contract_amount): 单位万元
8. 关键日期 (key_dates): 数组，包含 date、event
9. 项目状态详情 (project_status_detail): stage、progress_percentage、health

返回格式必须是有效的 JSON，并且只返回 JSON，不要有其他文本。

如果某个字段信息不存在，使用 null 或空数组。
"""

EXTRACTION_USER_PROMPT_TEMPLATE = """
请从以下邮件内容中提取实体信息。

邮件主题: {subject}

邮件正文:
{body}

请返回 JSON 格式的提取结果。
"""


def extract_entities_from_email(
    subject: str, body: str, max_retries: int = 3
) -> Dict[str, Any]:
    """
    使用 AI 从邮件中提取实体
    
    Args:
        subject: 邮件主题
        body: 邮件正文
        max_retries: 最大重试次数
    
    Returns:
        提取结果字典
    """
    for attempt in range(max_retries):
        try:
            # 截断长邮件
            body_truncated = body[:3000] if len(body) > 3000 else body
            subject_truncated = subject[:200] if len(subject) > 200 else subject

            user_prompt = EXTRACTION_USER_PROMPT_TEMPLATE.format(
                subject=subject_truncated, body=body_truncated
            )

            # 调用 OpenAI API
            response = client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=settings.ai_temperature,
                max_tokens=settings.ai_max_tokens,
            )

            # 解析响应
            response_text = response.choices[0].message.content.strip()

            # 提取 JSON
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                result = json.loads(response_text)

            logger.info(f"成功提取邮件: {subject[:50]}")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败（第 {attempt + 1} 次尝试）: {e}")
            if attempt == max_retries - 1:
                logger.error(f"邮件提取失败（已超过重试次数）: {subject}")
                return {"error": "JSON parse error"}

        except Exception as e:
            logger.error(f"邮件提取异常（第 {attempt + 1} 次尝试）: {e}")
            if attempt == max_retries - 1:
                logger.error(f"邮件提取失败（已超过重试次数）: {subject}")
                return {"error": str(e)}

    return {"error": "Max retries exceeded"}


def extract_batch(emails: list) -> list:
    """
    批量提取邮件实体
    
    Args:
        emails: 邮件列表，每个邮件包含 subject 和 body
    
    Returns:
        提取结果列表
    """
    results = []
    for i, email in enumerate(emails):
        try:
            result = extract_entities_from_email(
                subject=email.get("subject", ""),
                body=email.get("body", ""),
            )
            results.append(
                {
                    "email_id": email.get("id"),
                    "extraction": result,
                    "status": "success" if "error" not in result else "failed",
                }
            )
        except Exception as e:
            logger.error(f"批量提取失败，第 {i + 1} 封邮件: {e}")
            results.append(
                {
                    "email_id": email.get("id"),
                    "extraction": {"error": str(e)},
                    "status": "failed",
                }
            )

    return results


__all__ = ["extract_entities_from_email", "extract_batch"]
from src.ai.openai_client import OpenAIExtractor, TokenCounter
from src.ai.prompts import PromptGenerator, AdvancedPromptGenerator

__all__ = [
    "OpenAIExtractor",
    "TokenCounter",
    "PromptGenerator",
    "AdvancedPromptGenerator",
]
