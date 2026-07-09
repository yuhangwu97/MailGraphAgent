"""GraphRAG knowledge graph endpoints."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.backend.deps import get_account_id, get_ragflow, sse_from_queue
from src.backend.schemas import GraphBuildRequest, GraphVisualizeRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/entities")
async def entities(
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=2000),
    account_id: str = Depends(get_account_id),
    rf=Depends(get_ragflow),
):
    """List graph entities with pagination."""
    if rf is None:
        raise HTTPException(status_code=503, detail="RAGFlow unavailable")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: rf.get_graph_entities(page=page, page_size=page_size)
    )
    return {"entities": result, "page": page, "page_size": page_size}


@router.get("/relationships")
async def relationships(
    page: int = Query(1, ge=1),
    page_size: int = Query(1000, ge=1, le=5000),
    account_id: str = Depends(get_account_id),
    rf=Depends(get_ragflow),
):
    """List graph relationships with pagination."""
    if rf is None:
        raise HTTPException(status_code=503, detail="RAGFlow unavailable")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: rf.get_graph_relationships(page=page, page_size=page_size)
    )
    return {"relationships": result, "page": page, "page_size": page_size}


@router.post("/build")
async def build_graph(
    body: GraphBuildRequest = GraphBuildRequest(),
    account_id: str = Depends(get_account_id),
    rf=Depends(get_ragflow),
):
    """Trigger GraphRAG knowledge graph build. SSE progress stream."""
    if rf is None:
        raise HTTPException(status_code=503, detail="RAGFlow unavailable")

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        loop = asyncio.get_running_loop()

        def _run():
            try:
                msgs = []

                def on_progress(msg: str):
                    msgs.append(msg)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"event": "progress", "data": {"msg": msg}},
                    )

                result = rf.build_graph(timeout=body.timeout, on_progress=on_progress)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"event": "complete", "data": result},
                )
            except Exception as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"event": "error", "data": {"msg": str(exc)}},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        import asyncio as _asyncio
        task = _asyncio.create_task(loop.run_in_executor(None, _run))
        async for event in sse_from_queue(queue):
            yield event
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/visualize")
async def visualize(
    body: GraphVisualizeRequest = GraphVisualizeRequest(),
    rf=Depends(get_ragflow),
):
    """Generate pyvis graph HTML for the given entity type filter."""
    if rf is None:
        raise HTTPException(status_code=503, detail="RAGFlow unavailable")

    loop = asyncio.get_running_loop()

    def _build():
        from src.backend.graph_viz import build_pyvis_network_from_ragflow

        return build_pyvis_network_from_ragflow(
            rf, entity_types_filter=body.entity_types if body.entity_types else None
        )

    html = await loop.run_in_executor(None, _build)
    return {"html": html}
