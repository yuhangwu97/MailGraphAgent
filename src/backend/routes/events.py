"""SSE 实时事件流 —— 推送邮件处理、建图进度、图谱变化等全局事件给前端。

前端 EventSource 连接 GET /api/events/stream，不再依赖轮询。
事件经 Redis pub/sub 频道 mailgraph:events 广播，API 订阅后以 SSE 转发。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.backend.jobqueue import _client, EVENTS_CHANNEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["events"])

# 前端 SSR 事件帧格式：event: <type>\ndata: <json>\n\n
# 注释行（冒号开头）作为心跳帧，不触发前端事件 listener


@router.get("/events/stream")
async def event_stream(request: Request):
    """SSE 实时事件流。

    事件类型：
      - mail_processed  : 邮件处理完成/失败/跳过
      - processing_started : 邮件开始处理
      - pipeline_tick   : Pipeline 状态变化
      - docs_changed    : 文档处理数量变化
      - graph_changed   : 实体/关系数量变化
    """

    pubsub = _client().pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(EVENTS_CHANNEL)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop_evt = threading.Event()

    def _reader() -> None:
        """在 daemon 线程里同步阻塞读 Redis pub/sub，推到 asyncio 队列。"""
        try:
            while not stop_evt.is_set():
                try:
                    msg = pubsub.get_message(timeout=1.0)
                except (OSError, ConnectionError):
                    break  # socket closed — client disconnected
                if msg is None:
                    try:
                        loop.call_soon_threadsafe(queue.put_nowait, None)
                    except asyncio.QueueFull:
                        pass
                    continue
                if msg.get("type") != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"event": data.get("event", "message"), "data": data.get("data", {})},
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.debug("events reader thread exiting: %s", e)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()

    async def _generator():
        try:
            # 初始连接帧
            yield "event: connected\ndata: {}\n\n"

            while True:
                if await request.is_disconnected():
                    break

                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue

                if item is None:
                    # 心跳哨兵
                    yield ": heartbeat\n\n"
                    continue

                event_type = item.get("event", "message")
                event_data = json.dumps(item.get("data", {}), ensure_ascii=False)
                yield f"event: {event_type}\ndata: {event_data}\n\n"
        finally:
            stop_evt.set()
            try:
                pubsub.close()
            except Exception:
                pass
            logger.debug("events SSE connection closed")

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
