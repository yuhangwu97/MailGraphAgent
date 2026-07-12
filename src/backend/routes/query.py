"""Natural-language query with SSE streaming answer."""

from __future__ import annotations

import asyncio
import json
import logging
import math

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from src.backend.deps import get_account_id, get_cache, get_conversation_store, get_query_engine
from src.backend.schemas import QueryRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["query"])


def _sanitize_json(obj):
    """Recursively replace NaN/Infinity float values with None (→ null in JSON)."""
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


@router.post("/query")
async def run_query(
    body: QueryRequest,
    account_id: str = Depends(get_account_id),
):
    """Run a natural-language query and stream the answer via SSE.

    Events:
      - progress: token chunks
      - trace: query pipeline steps
      - result: structured data (entities, relationships, chunks, rows)
      - done: final complete signal
      - error: error message
    """
    engine = await get_query_engine(account_id=account_id)

    if engine is None:
        raise HTTPException(status_code=503, detail="Query engine unavailable")

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        # Resolve conversation context
        store = get_conversation_store(account_id=account_id)
        memory_prompt = ""
        history = []
        if store:
            try:
                if body.session_id:
                    history = store.recent_context(body.session_id, limit=8)
                mem = store.get_memory()
                memory_prompt = mem.to_prompt() if mem else ""
            finally:
                store.close()

        context = {"memory": memory_prompt, "history": history}

        # Run query in thread
        loop = asyncio.get_running_loop()

        def _emit(event: str, data: dict):
            loop.call_soon_threadsafe(queue.put_nowait, {"event": event, "data": data})

        def _run():
            try:
                _emit("progress", {"msg": "🔍 正在分析问题…"})

                def _on_progress(msg: str):
                    _emit("progress", {"msg": msg})

                result = engine.query(body.question, context=context,
                                      progress_cb=_on_progress)

                # Emit query plan as a progress step
                plan = result.get("query_plan")
                if plan:
                    route = plan.get("route", "")
                    route_labels = {"stat": "📊 统计查询", "content": "✨ 内容检索", "hybrid": "🔀 混合查询", "clarify": "❔ 澄清"}
                    _emit("progress", {"msg": f"{route_labels.get(route, route)} — {plan.get('reason', '')[:80]}"})

                # Emit trace steps as progress
                for step in result.get("trace") or []:
                    icon = step.get("icon", "")
                    name = step.get("name", "")
                    detail = step.get("detail", "")
                    status = step.get("status", "ok")
                    msg = f"{icon} {name}"
                    if detail:
                        msg += f"：{detail}"
                    _emit("progress", {"msg": msg})

                # Stream tokens one by one (supports CJK + Latin text)
                answer = result.get("answer") or "未找到相关信息。"
                import re, time

                # Split: CJK chars individually, Latin words as groups, whitespace preserved
                tokens = []
                i = 0
                while i < len(answer):
                    ch = answer[i]
                    if ch in ' \t\n\r':
                        tokens.append(ch)
                        i += 1
                    elif '一' <= ch <= '鿿' or '　' <= ch <= '〿' or '＀' <= ch <= '￯':
                        # CJK character — emit individually for visible streaming
                        tokens.append(ch)
                        i += 1
                    else:
                        # Latin/numbers/punctuation — group until next CJK or whitespace
                        j = i
                        while j < len(answer):
                            cj = answer[j]
                            if cj in ' \t\n\r' or '一' <= cj <= '鿿' or '　' <= cj <= '〿' or '＀' <= cj <= '￯':
                                break
                            j += 1
                        tokens.append(answer[i:j])
                        i = j

                for tok in tokens:
                    _emit("progress", {"token": tok})
                    time.sleep(0.025)

                # Send structured result (includes chunks → source references)
                _emit("result", result)
            except Exception as exc:
                logger.exception("Query failed")
                _emit("error", {"msg": str(exc)})
            finally:
                _emit("done", {})
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

        import asyncio as _asyncio

        # ensure_future works with both coroutines (Python <3.12) and
        # Futures (Python ≥3.12/3.14) returned by run_in_executor.
        task = _asyncio.ensure_future(
            _asyncio.get_running_loop().run_in_executor(None, _run)
        )

        while True:
            item = await queue.get()
            if item is None:
                break
            event_type = item.get("event", "progress")
            data = item.get("data", item)
            # Sanitize float values to ensure valid JSON (no NaN/Infinity)
            safe = _sanitize_json(data)
            yield f"event: {event_type}\ndata: {json.dumps(safe, ensure_ascii=False, default=str)}\n\n"

        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/knowledge/retrieve")
async def lightrag_retrieve(body: dict):
    """LightRAG knowledge graph retrieval."""
    from src.backend.knowledge.lightrag_wrapper import query_mail
    query = body.get("query", "")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: query_mail(query, mode="mix"))
    return {"result": result}


@router.post("/knowledge/chat")
async def lightrag_chat(body: dict):
    """LightRAG chat completion."""
    from src.backend.knowledge.lightrag_wrapper import query_mail
    query = body.get("query", body.get("question", ""))
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: query_mail(query))
    return {"answer": result}
