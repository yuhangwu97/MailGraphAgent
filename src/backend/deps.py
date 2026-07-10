"""FastAPI dependency injection — provides settings, clients, and stores."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import Depends, Header, HTTPException, Request

from config.settings import get_settings, Settings
from src.backend.storage.account_store import AccountStore

logger = logging.getLogger(__name__)

# ── module-level caches (replaces @st.cache_resource) ──
_ragflow_clients: dict[str, object] = {}
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


async def get_ragflow(
    account_id: str = Depends(get_account_id),
):
    """Return a cached RAGFlowClient for the active account."""
    from src.backend.attachment.ragflow_client import get_ragflow_client

    if account_id not in _ragflow_clients:
        try:
            client = get_ragflow_client(account_id)
            client.get_or_create_dataset(_dataset_name(account_id))
            _ragflow_clients[account_id] = client
        except Exception as e:
            logger.warning("RAGFlow unavailable: %s", e)
            return None
    return _ragflow_clients[account_id]


async def get_query_engine(account_id: str):
    """Return a cached QueryEngine for the active account.

    Resolves RAGFlow internally (not via FastAPI Depends) so callers can
    invoke this directly with a concrete account_id.
    """
    if account_id not in _query_engines:
        # Resolve RAGFlow client for this account
        from src.backend.attachment.ragflow_client import get_ragflow_client

        if account_id not in _ragflow_clients:
            try:
                client = get_ragflow_client(account_id)
                client.get_or_create_dataset(_dataset_name(account_id))
                _ragflow_clients[account_id] = client
            except Exception as e:
                logger.warning("RAGFlow unavailable: %s", e)
                return None

        ragflow = _ragflow_clients.get(account_id)
        if ragflow is not None:
            from src.backend.ai.query_engine import QueryEngine
            _query_engines[account_id] = QueryEngine(ragflow, account_id=account_id)

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


def _dataset_name(account_id: str | None) -> str:
    base = get_settings().ragflow_dataset_name
    return f"{base}-{account_id}" if account_id else base


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
