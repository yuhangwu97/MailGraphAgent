"""
附件处理模块
附件由 RAGFlow DeepDoc 服务端统一解析，本模块仅保留 RAGFlow 客户端。
"""

from .ragflow_client import RAGFlowClient, get_ragflow_client

__all__ = [
    "RAGFlowClient",
    "get_ragflow_client",
]
