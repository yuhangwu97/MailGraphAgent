"""Knowledge graph endpoints — Neo4j via LightRAG."""
import asyncio
import logging

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/entities")
async def entities(page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=2000)):
    from src.backend.storage.neo4j_client import get_all_entities
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: get_all_entities(limit=page_size))
    return {"entities": result, "page": page, "page_size": page_size}


@router.get("/relationships")
async def relationships(page: int = Query(1, ge=1), page_size: int = Query(1000, ge=1, le=5000)):
    from src.backend.storage.neo4j_client import get_all_relationships
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: get_all_relationships(limit=page_size))
    return {"relationships": result, "page": page, "page_size": page_size}


@router.get("/status")
async def graph_status():
    """知识图谱综合状态：图谱规模 + LightRAG 文档处理进度 + pipeline 实时状态。

    切到 LightRAG 后新增可见的建图侧状态，供工作台状态带展示。所有读取都在
    executor 里跑（Neo4j 计数 / LightRAG 状态均为同步阻塞调用）；任一后端不可用
    时对应字段优雅降级为 0/空闲，不阻塞整体响应。
    """
    def _collect() -> dict:
        from src.backend.storage.neo4j_client import count_graph
        from src.backend.knowledge.lightrag_wrapper import (
            get_doc_status_counts,
            get_pipeline_status,
        )
        return {
            "graph": count_graph(),
            "docs": get_doc_status_counts(),
            "pipeline": get_pipeline_status(),
        }

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _collect)


@router.post("/resolve-entities")
async def resolve_entities(dry_run: bool = Query(False, description="只返回归并预案，不写库")):
    """实体归并（别名消歧）：把跨邮件指向同一现实实体的节点合并。

    LightRAG 只按同名合并，"赵阳/赵工"、"华远/华远物流"会被拆成多个节点。此接口
    用 LLM 在同类型内保守聚类后调 amerge_entities（同步更新图谱 + 向量库）。
    dry_run=true 时只返回预案供预览，不改数据。
    """
    from src.backend.knowledge.lightrag_wrapper import resolve_entity_aliases
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: resolve_entity_aliases(dry_run=dry_run))

