"""Persistent job registry (Redis-backed).

One hash per job at mailgraph:job:{id}; a zset mailgraph:jobs:index (score=created_at)
for listing; a set mailgraph:job:{id}:items for the message_ids a job covers.
Every public function accepts an optional `client` so tests can inject a fake.
"""
from __future__ import annotations

import json
import time
import uuid

JOB_PREFIX = "mailgraph:job:"
JOBS_INDEX = "mailgraph:jobs:index"

_TERMINAL = {"completed", "failed", "interrupted", "partial"}


def _client(client=None):
    if client is not None:
        return client
    from src.backend.jobqueue import _client as jq_client
    return jq_client()


def _job_key(job_id: str) -> str:
    return f"{JOB_PREFIX}{job_id}"


def _items_key(job_id: str) -> str:
    return f"{JOB_PREFIX}{job_id}:items"


def create_job(job_type: str, source: str = "", params: dict | None = None,
               item_ids: list[str] | None = None, client=None) -> str:
    r = _client(client)
    job_id = uuid.uuid4().hex
    now = time.time()
    r.hset(_job_key(job_id), mapping={
        "job_id": job_id,
        "type": job_type,
        "source": source,
        "stage": "",
        "status": "queued",
        "total": len(item_ids or []),
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "att_failed": 0,
        "cursor": "",
        "params": json.dumps(params or {}, ensure_ascii=False),
        "error": "",
        "summary": "",
        "created_at": now,
        "updated_at": now,
        "heartbeat_at": now,
    })
    r.zadd(JOBS_INDEX, {job_id: now})
    if item_ids:
        r.sadd(_items_key(job_id), *item_ids)
    return job_id


def get_job(job_id: str, client=None) -> dict | None:
    r = _client(client)
    data = r.hgetall(_job_key(job_id))
    return data or None


def _touch(r, job_id: str) -> None:
    r.hset(_job_key(job_id), mapping={"updated_at": time.time()})


def set_stage(job_id: str, stage: str, client=None) -> None:
    r = _client(client)
    r.hset(_job_key(job_id), mapping={"stage": stage, "status": "running"})
    _touch(r, job_id)


def incr(job_id: str, field: str, amount: float = 1, client=None) -> None:
    r = _client(client)
    r.hincrbyfloat(_job_key(job_id), field, amount)
    _touch(r, job_id)


def heartbeat(job_id: str, client=None) -> None:
    r = _client(client)
    r.hset(_job_key(job_id), mapping={"heartbeat_at": time.time()})


def update(job_id: str, client=None, **fields) -> None:
    r = _client(client)
    if fields:
        r.hset(_job_key(job_id), mapping=fields)
    _touch(r, job_id)


def list_jobs(status: str | None = None, limit: int = 50, client=None) -> list[dict]:
    r = _client(client)
    job_ids = r.zrevrange(JOBS_INDEX, 0, limit - 1)
    out = []
    for jid in job_ids:
        data = r.hgetall(_job_key(jid))
        if not data:
            continue
        if status and data.get("status") != status:
            continue
        out.append(data)
    return out


def mark_terminal(job_id: str, status: str, summary: str = "",
                  error: str = "", client=None) -> None:
    if status not in _TERMINAL:
        raise ValueError(f"not a terminal status: {status}")
    r = _client(client)
    r.hset(_job_key(job_id), mapping={
        "status": status,
        "summary": summary,
        "error": error,
        "updated_at": time.time(),
        "heartbeat_at": time.time(),
    })


def find_stale_running(stale_seconds: float = 60, limit: int = 500, client=None) -> list[dict]:
    r = _client(client)
    cutoff = time.time() - stale_seconds
    out = []
    for jid in r.zrevrange(JOBS_INDEX, 0, limit - 1):
        data = r.hgetall(_job_key(jid))
        if not data or data.get("status") != "running":
            continue
        try:
            hb = float(data.get("heartbeat_at", 0))
        except (TypeError, ValueError):
            hb = 0
        if hb < cutoff:
            out.append(data)
    return out


def items(job_id: str, client=None) -> set[str]:
    r = _client(client)
    return set(r.smembers(_items_key(job_id)))
