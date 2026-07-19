"""文档处理 worker：消费 Redis 任务队列，跑 Pipeline 重活（解析/建图）。

与 API 进程分离，让 DeepDoc（CPU 密集、持 GIL）不再阻塞 FastAPI 事件循环。
进度经 Redis pub/sub 回传给 API 的 SSE（见 jobqueue / deps.enqueue_job_and_stream）。

启动：python -m src.backend.worker

批量模式：ingest_one job 默认攒到 10 封后一次 ainsert（单次拿锁），
吞吐量相比逐封插入提升约 5-10 倍。
"""
from __future__ import annotations

import logging
import os
import time

from src.backend.jobqueue import pop_job, publish_progress

logger = logging.getLogger("mailgraph.worker")

INGEST_BATCH_SIZE = int(os.getenv("INGEST_BATCH_SIZE", "30"))
INGEST_BATCH_WAIT = float(os.getenv("INGEST_BATCH_WAIT", "10"))

# worker 只消费 ingest：SPOP 领取 ingest_queue 里的邮件 → DeepDoc 解析 → 建图。
# fetch/index/parse_selected/reprocess 都在 API 侧做「准备+入队」，不进 worker。
# ingest_one → 批量模式（攒批后跑 run_ingest_batch）
# ingest     → 单封模式（兼容旧 API）
_DISPATCH = {
    "ingest": "run_ingest",
    "ingest_one": "run_ingest_batch",
}


def _handle_one(job: dict) -> None:
    """处理单封邮件（ingest 类型，非批量）。"""
    job_id = job.get("job_id", "")
    jtype = job.get("type", "")
    account_id = job.get("account_id") or None
    params = job.get("params") or {}

    method = "run_ingest"
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
    except Exception as e:
        logger.exception("job %s failed", job_id)
        publish_progress(job_id, "error", {"msg": str(e)})


def _handle_batch(jobs: list[dict]) -> None:
    """批量处理 ingest_one job：收集 message_id 后一次 run_ingest_batch。"""
    if not jobs:
        return

    account_id = jobs[0].get("account_id") or None
    message_ids = []
    job_ids = []
    for j in jobs:
        mid = (j.get("params") or {}).get("message_id", "")
        if mid:
            message_ids.append(mid)
            job_ids.append(j.get("job_id", ""))

    if not message_ids:
        return

    # 取第一个 job 的 job_record_id 做心跳
    first_params = jobs[0].get("params") or {}
    job_record_id = first_params.pop("job_record_id", "")

    def on_log(msg: str) -> None:
        if job_record_id:
            try:
                from src.backend import jobstore
                jobstore.heartbeat(job_record_id)
            except Exception:
                pass
        # 向所有 job 广播进度
        for jid in job_ids:
            publish_progress(jid, "progress", {"msg": msg})

    logger.info("batch start: %d jobs, %d mails account=%s",
                len(jobs), len(message_ids), account_id)
    try:
        from src.backend.pipeline import Pipeline
        result = Pipeline(account_id).run_ingest_batch(message_ids, on_log=on_log)
        data = result if isinstance(result, dict) else {"result": result}
        for jid in job_ids:
            publish_progress(jid, "complete", data)
        logger.info("batch complete: %d jobs, uploaded=%s",
                    len(jobs), data.get("uploaded", "?"))
    except Exception as e:
        logger.exception("batch failed: %d jobs", len(jobs))
        for jid in job_ids:
            publish_progress(jid, "error", {"msg": str(e)})


def _reap(client=None, cache_factory=None, stale_seconds: float = 60) -> int:
    """Mark stale running jobs interrupted and requeue in-flight mails."""
    from src.backend import jobstore

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
    logger.info("ingest worker started (batch=%d, wait=%.0fs), waiting for jobs...",
                INGEST_BATCH_SIZE, INGEST_BATCH_WAIT)
    try:
        _reap()
    except Exception:
        logger.exception("startup reap failed")
    last_reap = time.time()

    batch: list[dict] = []
    last_batch_time = time.time()

    while True:
        try:
            # 有攒批时缩短 pop 超时，及时 flush
            pop_timeout = 1.0 if batch else 5.0
            job = pop_job(timeout=pop_timeout)

            if job and job.get("type") == "ingest_one":
                batch.append(job)
                if not job:
                    continue  # pop 超时返回 None，走 flush 检查
            elif job and job.get("type") == "ingest":
                # ingest 类型不进批量，直接处理
                _handle_one(job)

            # 批量满或超时 → flush
            should_flush = (
                len(batch) >= INGEST_BATCH_SIZE
                or (batch and time.time() - last_batch_time >= INGEST_BATCH_WAIT)
            )
            if should_flush:
                _handle_batch(batch)
                batch = []
                last_batch_time = time.time()

            if time.time() - last_reap > 30:
                _reap()
                last_reap = time.time()

        except KeyboardInterrupt:
            # 退出前 flush 残留
            if batch:
                logger.info("flushing %d remaining jobs before exit...", len(batch))
                _handle_batch(batch)
            logger.info("worker stopping")
            break
        except Exception:
            logger.exception("worker loop error; retrying")
            time.sleep(1)


if __name__ == "__main__":
    main()
