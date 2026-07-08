"""
Prompt 模板管理
"""
import json


class PromptGenerator:
    """基础 Prompt 生成器"""

    SYSTEM_PROMPT = """你是一个企业邮件分析专家。
请从邮件中提取结构化信息，严格输出 JSON 格式。
如果某个字段信息不存在，使用 null 或空数组。"""

    @classmethod
    def build_user_prompt(cls, subject: str, body: str, schema: dict | None = None) -> str:
        schema_str = json.dumps(schema, ensure_ascii=False, indent=2) if schema else ""
        return f"""请从以下邮件中提取关键商业信息。

邮件主题: {subject}

邮件正文:
{body[:3000]}

输出 JSON Schema:
{schema_str}

只输出 JSON，不要其他文字。"""

    @classmethod
    def build_messages(cls, subject: str, body: str, schema: dict | None = None) -> list[dict]:
        return [
            {"role": "system", "content": cls.SYSTEM_PROMPT},
            {"role": "user", "content": cls.build_user_prompt(subject, body, schema)},
        ]


class AdvancedPromptGenerator(PromptGenerator):
    """高级 Prompt 生成器"""

    @classmethod
    def build_multi_turn(cls, subject: str, body: str, schema: dict | None = None) -> list[dict]:
        return cls.build_messages(subject, body, schema)
