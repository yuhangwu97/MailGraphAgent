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
import uuid

import redis

from config.settings import get_settings

logger = logging.getLogger(__name__)

JOBS_KEY = "mailgraph:jobs"

# worker 支持的任务类型 → Pipeline 方法名
JOB_TYPES = {"fetch", "ingest", "reprocess", "index", "parse_selected"}


def _client() -> redis.Redis:
    cfg = get_settings()
    return redis.Redis(
        host=cfg.redis_host,
        port=cfg.redis_port,
        db=cfg.redis_db,
        password=cfg.redis_password or None,
        decode_responses=True,
    )


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


def pop_job(timeout: int = 5) -> dict | None:
    """阻塞取一个任务（BRPOP，FIFO 配合 LPUSH）；超时返回 None。"""
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
