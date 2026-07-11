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

# 任务类型 → Pipeline 方法名；params 的键需与方法形参名一致（folder/limit/message_ids/paths）
_DISPATCH = {
    "fetch": "run_fetch",
    "ingest": "run_ingest",
    "reprocess": "reprocess",
    "index": "run_index_files",
    "parse_selected": "parse_selected",
}


def _handle(job: dict) -> None:
    job_id = job.get("job_id", "")
    jtype = job.get("type", "")
    account_id = job.get("account_id") or None
    params = job.get("params") or {}

    method = _DISPATCH.get(jtype)
    if not method:
        publish_progress(job_id, "error", {"msg": f"unknown job type: {jtype}"})
        return

    def on_log(msg: str) -> None:
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("ingest worker started, waiting for jobs...")
    while True:
        try:
            job = pop_job(timeout=5)
            if job:
                _handle(job)
        except KeyboardInterrupt:
            logger.info("worker stopping")
            break
        except Exception:  # noqa: BLE001 — 单个任务/连接异常不应打死循环
            logger.exception("worker loop error; retrying")
            time.sleep(1)


if __name__ == "__main__":
    main()
