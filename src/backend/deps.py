"""FastAPI dependency injection — provides settings, clients, and stores."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import AsyncGenerator

from fastapi import Depends, Header, HTTPException, Request

from config.settings import get_settings, Settings
from src.backend.storage.account_store import AccountStore

logger = logging.getLogger(__name__)

_query_engines: dict[str, object] = {}


def get_settings_dep() -> Settings:
    return get_settings()


async def get_account_id(
    request: Request,
    x_account_id: str | None = Header(None, alias="X-Account-Id"),
) -> str:
    """Resolve active account from header, falling back to first available."""
    if x_account_id:
        return x_account_id
    # fallback: first account in store
    store = AccountStore()
    try:
        default = store.default_id()
    finally:
        store.close()
    if default:
        return default
    raise HTTPException(status_code=400, detail="No email account configured")


async def get_cache(
    request: Request,
    account_id: str = Depends(get_account_id),
):
    """Return a MailCache for the active account."""
    from src.backend.storage.redis_cache import MailCache

    try:
        return MailCache(account_id)
    except Exception as e:
        logger.warning("MailCache unavailable: %s", e)
        return None


async def get_query_engine(account_id: str):
    """Return a cached QueryEngine for the active account."""
    if account_id not in _query_engines:
        from src.backend.ai.query_engine import QueryEngine
        _query_engines[account_id] = QueryEngine(account_id=account_id)
    return _query_engines.get(account_id)


def get_account_store() -> AccountStore:
    return AccountStore()


def get_conversation_store(
    account_id: str = Depends(get_account_id),
) -> object:
    """Return a ConversationStore for the active account."""
    from src.backend.storage.conversation_store import ConversationStore

    try:
        return ConversationStore(account_id)
    except Exception as e:
        logger.warning("ConversationStore unavailable: %s", e)
        return None


# ── SSE helpers ──


async def sse_from_queue(queue: asyncio.Queue) -> AsyncGenerator[str, None]:
    """Yield SSE events from an async queue until a 'done' sentinel arrives."""
    while True:
        item = await queue.get()
        if item is None:  # sentinel
            break
        event_type = item.get("event", "progress")
        data = json.dumps(item.get("data", item), ensure_ascii=False)
        yield f"event: {event_type}\ndata: {data}\n\n"


async def enqueue_job_and_stream(
    job_type: str, account_id: str | None, params: dict | None = None,
    max_wait: int = 3600,
) -> AsyncGenerator[str, None]:
    """把重活入队给 worker，并把 worker 经 Redis pub/sub 回传的进度转成 SSE。

    与 run_pipeline_with_sse 同构（同样的事件契约），但处理跑在独立 worker 进程，
    API 只订阅+转发，不被 DeepDoc 解析阻塞。先订阅再入队，避免漏首帧。
    """
    from src.backend import jobqueue

    main_loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    job_id = jobqueue.new_job_id()
    pubsub = jobqueue.subscribe_progress(job_id)          # 1) 先订阅
    jobqueue.enqueue_job(job_type, account_id or "", params or {}, job_id=job_id)  # 2) 再入队

    def _reader() -> None:
        deadline = time.time() + max_wait
        try:
            while time.time() < deadline:
                msg = pubsub.get_message(timeout=1.0)
                if not msg or msg.get("type") != "message":
                    continue
                try:
                    payload = json.loads(msg["data"])
                except Exception:
                    continue
                main_loop.call_soon_threadsafe(queue.put_nowait, payload)
                if payload.get("event") in ("complete", "error"):
                    return
            main_loop.call_soon_threadsafe(
                queue.put_nowait,
                {"event": "error", "data": {"msg": "处理超时或 worker 无响应"}},
            )
        finally:
            main_loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel
            try:
                pubsub.close()
            except Exception:
                pass

    threading.Thread(target=_reader, name=f"sse-{job_id[:8]}", daemon=True).start()

    async for event in sse_from_queue(queue):
        yield event


async def prep_then_ingest_stream(
    prep_fn, prep_args: tuple, account_id: str | None, ingest_params: dict | None = None,
) -> AsyncGenerator[str, None]:
    """先在 API 进程内跑「准备」(prep_fn，只入队不建图)，再入队 ingest 交给 worker 建图。

    prep_fn 是 Pipeline 的准备方法（parse_selected / reprocess），在 API 线程池里跑（读源
    文件 / 重拉 IMAP，属 IO 型），只把邮件塞进 ingest_queue。准备阶段只发 progress 事件，
    最终的 complete 由 worker 的 ingest 建图阶段发出，前端看到的是一条连续的 SSE。
    """
    main_loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _DONE = object()

    def _prep() -> None:
        def on_log(msg: str) -> None:
            main_loop.call_soon_threadsafe(
                queue.put_nowait, {"event": "progress", "data": {"msg": msg}})
        err = None
        try:
            prep_fn(*prep_args, on_log=on_log)
        except Exception as e:  # noqa: BLE001
            err = str(e)
        main_loop.call_soon_threadsafe(queue.put_nowait, (_DONE, err))

    threading.Thread(target=_prep, name="prep", daemon=True).start()

    # 阶段 1：准备进度（只 progress；准备失败则发 error 并终止）
    while True:
        item = await queue.get()
        if isinstance(item, tuple) and item[0] is _DONE:
            err = item[1]
            if err:
                yield f"event: error\ndata: {json.dumps({'msg': err}, ensure_ascii=False)}\n\n"
                return
            break
        data = json.dumps(item.get("data", {}), ensure_ascii=False)
        yield f"event: {item.get('event', 'progress')}\ndata: {data}\n\n"

    # 阶段 2：入队 ingest 给 worker，转发建图进度（含最终 complete）
    async for event in enqueue_job_and_stream("ingest", account_id, ingest_params or {}):
        yield event



async def run_in_thread(fn, *args, **kwargs):
    """Run a synchronous function in a thread pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


async def run_pipeline_with_sse(fn, *args, queue: asyncio.Queue):
    """Run a Pipeline method in a thread, pushing log messages to an SSE queue.

    Capture the running loop *before* spawning the worker thread — inside the
    thread there is no current event loop (asyncio.get_event_loop() raises on
    Python 3.12+), so we must hold a reference to hand items back thread-safely.
    """
    main_loop = asyncio.get_running_loop()

    def _put(item):
        try:
            main_loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception:
            pass

    def _target():
        def on_log(msg: str):
            _put({"event": "progress", "data": {"msg": msg}})

        try:
            result = fn(on_log=on_log, *args)
            _put({
                "event": "complete",
                "data": result if isinstance(result, dict) else {"result": str(result)},
            })
        except Exception as exc:
            _put({"event": "error", "data": {"msg": str(exc)}})
        finally:
            _put(None)  # sentinel

    await main_loop.run_in_executor(None, _target)
