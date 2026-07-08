"""
AI 提取 JSON Schema 定义

定义从邮件中提取结构化信息的目标格式，
供 prompt 生成器和解析器使用。
"""
import json
from typing import Dict, Any

# 邮件提取的目标 JSON Schema
# 所有字段均为可选，缺失时使用 null 或空数组
EXTRACTION_SCHEMA: Dict[str, Any] = {
    "company": {
        "name": "归一化的公司名称",
        "aliases": ["别名列表"],
    },
    "contacts": [
        {
            "name": "外部对接人",
            "role": "职位",
            "email": "邮箱",
        }
    ],
    "internal_owners": [
        {
            "name": "内部负责人",
            "role": "职位",
            "department": "部门",
        }
    ],
    "projects": [
        {
            "name": "项目名称",
            "status": "进行中|已完成|停滞|已取消",
            "progress": "进度描述",
            "risk_points": ["风险点"],
            "key_dates": [{"date": "日期", "event": "事件"}],
        }
    ],
    "summary": "邮件核心摘要",
    "financial_info": {
        "amount": "金额(万元)",
        "currency": "币种",
        "description": "说明",
    },
}

# 简化版 Schema，用于附件文本提取
EXTRACTION_SCHEMA_SIMPLE: Dict[str, Any] = {
    "company": {"name": "归一化的公司名称", "aliases": ["别名列表"]},
    "contacts": [{"name": "外部对接人", "role": "职位", "email": "邮箱"}],
    "projects": [
        {
            "name": "项目名称",
            "status": "进行中|已完成|停滞|已取消",
            "progress": "进度描述",
            "risk_points": ["风险点"],
        }
    ],
    "summary": "附件核心内容摘要",
    "financial_info": {
        "amount": "金额(万元)",
        "currency": "币种",
        "description": "说明",
    },
}


def schema_to_json_str(schema: Dict[str, Any] | None = None) -> str:
    """将 Schema 转为 JSON 字符串，便于嵌入 prompt"""
    target = schema or EXTRACTION_SCHEMA
    return json.dumps(target, ensure_ascii=False, indent=2)


def get_schema_fields(schema: Dict[str, Any] | None = None) -> list[str]:
    """获取 Schema 顶层字段名列表"""
    target = schema or EXTRACTION_SCHEMA
    return list(target.keys())


__all__ = [
    "EXTRACTION_SCHEMA",
    "EXTRACTION_SCHEMA_SIMPLE",
    "schema_to_json_str",
    "get_schema_fields",
]
