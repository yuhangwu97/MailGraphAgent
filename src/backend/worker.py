"""文档处理 worker：消费 Redis 任务队列，跑 Pipeline 重活（解析/建图）。

与 API 进程分离，让 DeepDoc（CPU 密集、持 GIL）不再阻塞 FastAPI 事件循环。
进度经 Redis pub/sub 回传给 API 的 SSE（见 jobqueue / deps.enqueue_job_and_stream）。

启动：python -m src.backend.worker
"""
from __future__ import annotations

import logging
import time

from src.backend.jobqueue import pop_job, publish_progress

logger = logging.getLogger("mailgraph.worker")

# worker 只消费 ingest：SPOP 领取 ingest_queue 里的邮件 → DeepDoc 解析 → 建图。
# fetch/index/parse_selected/reprocess 都在 API 侧做「准备+入队」，不进 worker。
_DISPATCH = {
    "ingest": "run_ingest",
    "ingest_one": "run_ingest_one",
}


def _heartbeat_logger(job_record_id, client=None, publish=None):
    """Return an on_log(msg) that publishes progress AND heartbeats the durable job."""
    from src.backend import jobstore
    from src.backend.jobqueue import publish_progress

    pub = publish or publish_progress

    def on_log(msg: str) -> None:
        if job_record_id:
            try:
                jobstore.heartbeat(job_record_id, client=client)
            except Exception:
                pass
        pub  # keep ref; actual publish below when wired with job_id
    return on_log


def _handle(job: dict) -> None:
    job_id = job.get("job_id", "")
    jtype = job.get("type", "")
    account_id = job.get("account_id") or None
    params = job.get("params") or {}

    method = _DISPATCH.get(jtype)
    if not method:
        publish_progress(job_id, "error", {"msg": f"unknown job type: {jtype}"})
        return

    job_record_id = params.pop("job_record_id", "")

    def on_log(msg: str) -> None:
        if job_record_id:
            try:
                from src.backend import jobstore
                jobstore.heartbeat(job_record_id)
            except Exception:
                pass
        publish_progress(job_id, "progress", {"msg": msg})

    logger.info("job %s start type=%s account=%s", job_id, jtype, account_id)
    try:
        from src.backend.pipeline import Pipeline

        result = getattr(Pipeline(account_id), method)(on_log=on_log, **params)
        data = result if isinstance(result, dict) else {"result": result}
        publish_progress(job_id, "complete", data)
        logger.info("job %s complete", job_id)
    except Exception as e:  # noqa: BLE001 — 任何失败都要回传给前端，不能吞
        logger.exception("job %s failed", job_id)
        publish_progress(job_id, "error", {"msg": str(e)})


def _reap(client=None, cache_factory=None, stale_seconds: float = 60) -> int:
    """Mark stale running jobs interrupted and requeue in-flight mails.

    Returns the number of jobs marked interrupted.
    """
    from src.backend import jobstore

    # 1) requeue stale in-flight mails (account-agnostic pool)
    if cache_factory is None:
        from src.backend.storage.redis_cache import MailCache
        cache_factory = lambda: MailCache(None)
    cache = cache_factory()
    try:
        cache.reap_inflight(stale_seconds=stale_seconds)
    finally:
        try:
            cache.close()
        except Exception:
            pass

    # 2) mark stale running jobs interrupted
    stale_jobs = jobstore.find_stale_running(stale_seconds=stale_seconds, client=client)
    for job in stale_jobs:
        jobstore.mark_terminal(job["job_id"], "interrupted",
                               summary="worker heartbeat lost", client=client)
    return len(stale_jobs)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("ingest worker started, waiting for jobs...")
    try:
        _reap()  # startup recovery sweep
    except Exception:
        logger.exception("startup reap failed")
    last_reap = time.time()
    while True:
        try:
            job = pop_job(timeout=5)
            if job:
                _handle(job)
            if time.time() - last_reap > 30:
                _reap()
                last_reap = time.time()
        except KeyboardInterrupt:
            logger.info("worker stopping")
            break
        except Exception:  # noqa: BLE001 — 单个任务/连接异常不应打死循环
            logger.exception("worker loop error; retrying")
            time.sleep(1)


if __name__ == "__main__":
    main()
