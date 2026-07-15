"""Job registry routes — persistent job tracking for scan/parse operations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Depends

from src.backend import jobstore
from src.backend.deps import get_cache

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(status: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
    """列出所有 Job（从 Redis 重建，按 created_at 倒序，可按 status 过滤）。"""
    jobs = jobstore.list_jobs(status=status, limit=limit)
    return {"jobs": jobs, "total": len(jobs)}


@router.get("/{job_id}")
def get_job(job_id: str):
    """Job 明细 + 覆盖邮件的逐封状态。"""
    job = jobstore.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    item_ids = jobstore.items(job_id)
    return {"job": job, "item_ids": list(item_ids)[:200]}


@router.post("/{job_id}/pause")
def pause_job(job_id: str):
    """暂停 Job（停止再领取新项）。"""
    job = jobstore.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    jobstore.update(job_id, status="paused")
    return {"status": "paused", "job_id": job_id}


@router.post("/{job_id}/resume")
def resume_job(job_id: str, cache=Depends(get_cache)):
    """续跑 Job：把剩余 / interrupted 项重新入队。"""
    job = jobstore.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    jobstore.set_stage(job_id, job.get("stage", "build-graph"))
    # Re-enqueue remaining items
    mids = jobstore.items(job_id)
    requeued = 0
    for mid in mids:
        state = cache.get_mail_state(mid) if cache else None
        if state and state.get("status") in ("pending", "processing", "failed"):
            cache.requeue_pending(mid)
            requeued += 1

    return {"status": "running", "job_id": job_id, "requeued": requeued}


@router.post("/{job_id}/retry-failed")
def retry_failed(job_id: str, cache=Depends(get_cache)):
    """只重跑失败项。"""
    job = jobstore.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    jobstore.set_stage(job_id, job.get("stage", "build-graph"))
    mids = jobstore.items(job_id)
    retried = 0
    for mid in mids:
        state = cache.get_mail_state(mid) if cache else None
        if state and state.get("status") == "failed":
            cache.requeue_pending(mid)
            retried += 1

    return {"status": "running", "job_id": job_id, "retried": retried}


@router.delete("/{job_id}")
def delete_job(job_id: str):
    """清除 Job 记录（不删邮件）。"""
    job = jobstore.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    jobstore.mark_terminal(job_id, "completed", summary="deleted")
    return {"status": "deleted", "job_id": job_id}
