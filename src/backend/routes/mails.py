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
    BrowseFile,
    BrowseResponse,
    FetchRequest,
    IndexFilesRequest,
    IngestRequest,
    MailDetail,
    MailItem,
    MailQueryRequest,
    MailStats,
    PaginatedMailResponse,
    ParseSelectedRequest,
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
            indexed=s.get("indexed", 0),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query")
def mail_query(body: MailQueryRequest, cache=Depends(get_cache)):
    """Multi-dimensional mail stats query (used by QueryEngine stat route)."""
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    try:
        # Map aggregation to group_by for MailCache.query_stats
        group_by = None
        if body.aggregation == "top_senders":
            group_by = "sender"

        result = cache.query_stats(
            start_time=body.start_time,
            end_time=body.end_time,
            status=body.status,
            sender=body.sender,
            has_attachment=body.has_attachment,
            message_ids=body.message_ids,
            group_by=group_by,
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


@router.get("/done", response_model=PaginatedMailResponse)
def done_mails(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    cache=Depends(get_cache),
):
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    offset = (page - 1) * page_size
    mails = cache.list_done_mails(limit=page_size, offset=offset)
    total = cache.count_done_mails()
    return PaginatedMailResponse(
        items=[_to_mail_item(m) for m in mails],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/recent", response_model=PaginatedMailResponse)
def recent_mails(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    cache=Depends(get_cache),
):
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    offset = (page - 1) * page_size
    mails = cache.list_recent_mails(limit=page_size, offset=offset)
    total = cache.count_recent_mails()
    return PaginatedMailResponse(
        items=[_to_mail_item(m) for m in mails],
        total=total,
        page=page,
        page_size=page_size,
    )


# ── File import: browse + indexed list ──


@router.get("/browse", response_model=BrowseResponse)
def browse_files(dir: str = Query(..., description="本地目录或文件的绝对路径")):
    """列出本地目录下受支持的邮件文件（.eml/.msg/.pst/.ost），供前端选文件。只读。"""
    from pathlib import Path

    from src.backend.mail.sources import EXTENSIONS, expand_paths

    p = Path(dir).expanduser()
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {dir}")

    files = expand_paths([str(p)])[:500]
    items = [
        BrowseFile(
            path=str(f),
            name=f.name,
            size=(f.stat().st_size if f.exists() else 0),
            ext=f.suffix.lower(),
        )
        for f in files
    ]
    return BrowseResponse(dir=str(p), files=items)


@router.get("/indexed", response_model=PaginatedMailResponse)
def indexed_mails(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    cache=Depends(get_cache),
):
    """列出已扫描表头、待解析(indexed)的文件邮件。"""
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    offset = (page - 1) * page_size
    mails = cache.list_indexed(limit=page_size, offset=offset)
    total = cache.count_indexed()
    return PaginatedMailResponse(
        items=[_to_indexed_item(m) for m in mails],
        total=total,
        page=page,
        page_size=page_size,
    )


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


@router.post("/index")
async def index_files(
    body: IndexFilesRequest,
    account_id: str = Depends(get_account_id),
):
    """Step 1: scan local mail files → extract headers into `indexed`. SSE progress."""
    from src.backend.pipeline import Pipeline

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        task = asyncio.create_task(
            run_pipeline_with_sse(
                Pipeline(account_id).run_index_files,
                body.paths,
                queue=queue,
            )
        )
        async for event in sse_from_queue(queue):
            yield event
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/parse-selected")
async def parse_selected_mails(
    body: ParseSelectedRequest,
    account_id: str = Depends(get_account_id),
):
    """Step 2: read bodies of selected indexed mails → RAGFlow vectorize. SSE progress."""
    from src.backend.pipeline import Pipeline

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        task = asyncio.create_task(
            run_pipeline_with_sse(
                Pipeline(account_id).parse_selected,
                body.message_ids,
                queue=queue,
            )
        )
        async for event in sse_from_queue(queue):
            yield event
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Helpers ──


def _to_indexed_item(mail: dict) -> MailItem:
    try:
        att = int(mail.get("attachment_count") or 0)
    except (TypeError, ValueError):
        att = 0
    return MailItem(
        message_id=mail.get("message_id", ""),
        subject=mail.get("subject", ""),
        from_addr=mail.get("from_addr", ""),
        from_name=mail.get("from_name", ""),
        date=str(mail.get("date", "")),
        status=mail.get("status", "indexed"),
        attachment_count=att,
        attachments=[],
        folder=mail.get("folder", ""),
        source_type=mail.get("source_type", ""),
    )


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
