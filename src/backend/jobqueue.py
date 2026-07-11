"""进程间任务队列 + 进度中继（Redis 后端）。

拆分 API 与文档处理 worker：重活（fetch/ingest/reprocess/index/parse-selected）
由 API 入队、worker 消费，进度经 Redis pub/sub 回传给 API 的 SSE。

- 任务队列：Redis LIST `mailgraph:jobs`（LPUSH 入队 / BRPOP 消费，FIFO）。
- 进度频道：`mailgraph:progress:{job_id}`，事件形如
  {"event": "progress"|"complete"|"error", "data": {...}}，与 deps.sse_from_queue 对齐。
"""
from __future__ import annotations

import json
import logging
import threading
import uuid

import redis

from config.settings import get_settings

logger = logging.getLogger(__name__)

JOBS_KEY = "mailgraph:jobs"

# BRPOP 的阻塞时长（秒）。必须 < 客户端 socket_timeout，否则 socket 读超时会与
# 服务端阻塞计时同时到点、先抛 TimeoutError（redis-py 8 默认 socket_timeout=5）。
BRPOP_TIMEOUT = 5

# worker 只做一件事：消费 ingest_queue 做 DeepDoc 解析 + 建图。
# 其余动作（fetch 拉取 / index 扫表头 / parse_selected 读源文件 / reprocess 重拉）都属
# IO/轻量「准备」，在 API 进程内跑、把邮件塞进 ingest_queue，再入队 ingest 交给 worker。
JOB_TYPES = {"ingest"}

_client_singleton: redis.Redis | None = None
_client_lock = threading.Lock()


def _client() -> redis.Redis:
    """返回复用的 Redis 客户端（持久连接池）。

    socket_timeout 设为明显大于 BRPOP_TIMEOUT，避免阻塞式 BRPOP 的 socket 读超时
    与服务端阻塞计时打架（这正是 worker 空闲轮询时偶发 TimeoutError 的根因）。
    """
    global _client_singleton
    if _client_singleton is None:
        with _client_lock:
            if _client_singleton is None:
                cfg = get_settings()
                _client_singleton = redis.Redis(
                    host=cfg.redis_host,
                    port=cfg.redis_port,
                    db=cfg.redis_db,
                    password=cfg.redis_password or None,
                    decode_responses=True,
                    socket_timeout=BRPOP_TIMEOUT * 4,      # 20s > BRPOP 5s
                    socket_connect_timeout=10,
                    health_check_interval=30,
                )
    return _client_singleton


def progress_channel(job_id: str) -> str:
    return f"mailgraph:progress:{job_id}"


def new_job_id() -> str:
    return uuid.uuid4().hex


def enqueue_job(job_type: str, account_id: str, params: dict | None = None,
                job_id: str | None = None) -> str:
    """入队一个任务，返回 job_id。

    可传入预生成的 job_id —— API 侧需先订阅进度频道再入队，避免漏掉首帧。
    """
    if job_type not in JOB_TYPES:
        raise ValueError(f"unknown job type: {job_type}")
    job_id = job_id or new_job_id()
    job = {"job_id": job_id, "type": job_type, "account_id": account_id, "params": params or {}}
    _client().lpush(JOBS_KEY, json.dumps(job))
    logger.info("enqueued job %s type=%s account=%s", job_id, job_type, account_id)
    return job_id


def pop_job(timeout: int = BRPOP_TIMEOUT) -> dict | None:
    """阻塞取一个任务（BRPOP，FIFO 配合 LPUSH）；超时返回 None。

    timeout 会被限制在 socket_timeout 以内，避免 socket 读超时先于 BRPOP 触发。
    """
    timeout = min(timeout, BRPOP_TIMEOUT)
    item = _client().brpop(JOBS_KEY, timeout=timeout)
    if not item:
        return None
    _key, raw = item
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("bad job payload dropped: %r", raw)
        return None


def publish_progress(job_id: str, event: str, data: dict | None = None) -> None:
    """worker 侧：向进度频道发布一个事件。"""
    payload = {"event": event, "data": data or {}}
    _client().publish(progress_channel(job_id), json.dumps(payload))


def subscribe_progress(job_id: str) -> "redis.client.PubSub":
    """API 侧：订阅进度频道，返回已订阅的 PubSub（调用方负责消费与关闭）。

    必须在 enqueue_job 之前调用，避免漏掉首帧进度。
    """
    pubsub = _client().pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(progress_channel(job_id))
    return pubsub
