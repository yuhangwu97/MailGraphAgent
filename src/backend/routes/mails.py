"""Mail fetch, ingest, status queries, and SSE progress streams."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.backend.deps import (
    get_account_id,
    get_cache,
    run_pipeline_with_sse,
    sse_from_queue,
)
from src.backend.schemas import (
    FetchRequest,
    IngestRequest,
    MailDetail,
    MailItem,
    MailQueryRequest,
    MailStats,
    ReprocessRequest,
)

router = APIRouter(prefix="/api/mails", tags=["mails"])


# ── Stats & queries ──


@router.get("/stats", response_model=MailStats)
def mail_stats(cache=Depends(get_cache)):
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    try:
        s = cache.get_stats()
        return MailStats(
            total=sum(s.values()),
            done=s.get("done", 0),
            pending=s.get("pending", 0),
            failed=s.get("failed", 0),
            skipped=s.get("skipped", 0),
            ingested=s.get("ingested", 0),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query")
def mail_query(body: MailQueryRequest, cache=Depends(get_cache)):
    """Multi-dimensional mail stats query (used by QueryEngine stat route)."""
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    try:
        result = cache.query_stats(
            start_time=body.start_time,
            end_time=body.end_time,
            status=body.status,
            sender=body.sender,
            has_attachment=body.has_attachment,
            message_ids=body.message_ids,
            topic=body.topic,
            aggregation=body.aggregation,
            limit=body.limit,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Mail lists ──


@router.get("/pending", response_model=list[MailItem])
def pending_mails(cache=Depends(get_cache)):
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    pending_ids = cache.list_pending_ingest()
    items = []
    for mid in pending_ids[:50]:
        mail = cache.get_mail(mid)
        if mail:
            items.append(_to_mail_item(mail))
    return items


@router.get("/done", response_model=list[MailItem])
def done_mails(limit: int = Query(100, ge=1, le=500), cache=Depends(get_cache)):
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    mails = cache.list_done_mails(limit=limit)
    return [_to_mail_item(m) for m in mails]


@router.get("/recent", response_model=list[MailItem])
def recent_mails(limit: int = Query(50, ge=1, le=200), cache=Depends(get_cache)):
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    mails = cache.list_recent_mails(limit=limit)
    return [_to_mail_item(m) for m in mails]


@router.get("/{message_id}", response_model=MailDetail)
def mail_detail(message_id: str, cache=Depends(get_cache)):
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    mail = cache.get_mail(message_id)
    if not mail:
        raise HTTPException(status_code=404, detail="Mail not found")
    return _to_mail_detail(mail)


# ── SSE operations ──


@router.post("/fetch")
async def fetch_mails(
    body: FetchRequest,
    account_id: str = Depends(get_account_id),
):
    """Pull mails from IMAP → clean → enqueue. SSE progress stream."""
    from src.backend.pipeline import Pipeline

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        # Start pipeline in thread
        task = asyncio.create_task(
            run_pipeline_with_sse(
                Pipeline(account_id).run_fetch,
                body.folder,
                body.limit,
                queue=queue,
            )
        )
        async for event in sse_from_queue(queue):
            yield event
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/ingest")
async def ingest_mails(
    body: IngestRequest,
    account_id: str = Depends(get_account_id),
):
    """Process pending mails → RAGFlow. SSE progress stream."""
    from src.backend.pipeline import Pipeline

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        task = asyncio.create_task(
            run_pipeline_with_sse(
                Pipeline(account_id).run_ingest,
                body.limit,
                queue=queue,
            )
        )
        async for event in sse_from_queue(queue):
            yield event
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/reprocess")
async def reprocess_mails(
    body: ReprocessRequest,
    account_id: str = Depends(get_account_id),
):
    """Force re-process selected mails. SSE progress stream."""
    from src.backend.pipeline import Pipeline

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        task = asyncio.create_task(
            run_pipeline_with_sse(
                Pipeline(account_id).reprocess,
                body.message_ids,
                queue=queue,
            )
        )
        async for event in sse_from_queue(queue):
            yield event
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Helpers ──


def _to_mail_item(mail: dict) -> MailItem:
    return MailItem(
        message_id=mail.get("message_id", ""),
        subject=mail.get("subject", ""),
        from_addr=mail.get("from_addr", ""),
        from_name=mail.get("from_name", ""),
        date=str(mail.get("date", "")),
        status=mail.get("status", "pending"),
        attachment_count=int(mail.get("attachment_count") or 0),
        attachments=mail.get("attachments") or [],
    )


def _to_mail_detail(mail: dict) -> MailDetail:
    return MailDetail(
        message_id=mail.get("message_id", ""),
        subject=mail.get("subject", ""),
        from_addr=mail.get("from_addr", ""),
        from_name=mail.get("from_name", ""),
        date=str(mail.get("date", "")),
        status=mail.get("status", "pending"),
        attachment_count=int(mail.get("attachment_count") or 0),
        attachments=mail.get("attachments") or [],
        body=mail.get("cleaned_body") or mail.get("body") or "",
        to_addrs=mail.get("to_addrs") or [],
        cc_addrs=mail.get("cc_addrs") or [],
    )
