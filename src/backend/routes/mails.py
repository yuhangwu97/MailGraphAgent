"""Mail fetch, ingest, status queries, and SSE progress streams."""

from __future__ import annotations

import asyncio
import json
import logging

import os as _os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from src.backend.deps import (
    get_account_id,
    get_cache,
    enqueue_job_and_stream,
    multi_job_stream,
    prep_then_ingest_stream,
    run_pipeline_with_sse,
    sse_from_queue,
)
from src.backend.schemas import (
    BrowseDir,
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
    PickResponse,
    ReprocessRequest,
    ScanRequest,
)

router = APIRouter(prefix="/api/mails", tags=["mails"])

logger = logging.getLogger(__name__)


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
            processing=s.get("processing", 0),
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


@router.get("/pending", response_model=PaginatedMailResponse)
def pending_mails(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    cache=Depends(get_cache),
):
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    pending_ids = list(cache.list_pending_ingest())
    total = len(pending_ids)
    offset = (page - 1) * page_size
    ids_slice = pending_ids[offset:offset + page_size]
    items = []
    for mid in ids_slice:
        mail = cache.get_mail(mid)
        if mail:
            items.append(_to_mail_item(mail))
    return PaginatedMailResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/list", response_model=PaginatedMailResponse)
def list_all_mails(
    filter: str = Query("all", pattern="^(all|todo|done)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    cache=Depends(get_cache),
):
    """统一邮件列表：所有状态合并、按日期倒序、分页。

    filter: all=全部 | todo=未完成(未处理/待入库/处理中/失败) | done=已完成(已入库/已跳过)。
    """
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    offset = (page - 1) * page_size
    mails, total = cache.list_mails(filter=filter, limit=page_size, offset=offset)
    return PaginatedMailResponse(
        items=[_to_indexed_item(m) for m in mails],
        total=total,
        page=page,
        page_size=page_size,
    )


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


@router.get("/pick", response_model=PickResponse)
def pick_path(mode: str = Query("folder", pattern="^(folder|files)$")):
    """打开本机原生文件对话框（macOS Finder），返回所选绝对路径。

    mode=folder 选一个文件夹；mode=files 可多选 .pst/.ost/.eml/.msg 文件。
    仅在后端与用户同机（本地）时可用；对话框会弹在运行后端的机器屏幕上。
    """
    import subprocess
    import sys

    if sys.platform != "darwin":
        raise HTTPException(
            status_code=501,
            detail="原生文件对话框目前仅支持 macOS，请在路径框手动输入或点文件夹逐级浏览",
        )

    if mode == "folder":
        script = 'POSIX path of (choose folder with prompt "选择邮件文件夹")'
    else:
        script = (
            'set theFiles to choose file with prompt "选择邮件文件（可多选）" '
            'of type {"pst","ost","eml","msg"} with multiple selections allowed\n'
            'set AppleScript\'s text item delimiters to linefeed\n'
            'set out to ""\n'
            'repeat with f in theFiles\n'
            '  set out to out & POSIX path of f & linefeed\n'
            'end repeat\n'
            'return out'
        )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="选择超时")
    except FileNotFoundError:
        raise HTTPException(status_code=501, detail="未找到 osascript")

    if proc.returncode != 0:
        err = proc.stderr or ""
        if "-128" in err or "User canceled" in err or "cancel" in err.lower():
            return PickResponse(paths=[], canceled=True)
        raise HTTPException(status_code=500, detail=err.strip() or "打开文件对话框失败")

    paths = [p.rstrip("/") if p.rstrip("/") else p
             for p in (proc.stdout or "").splitlines() if p.strip()]
    return PickResponse(paths=paths, canceled=False)


@router.post("/upload-files")
async def upload_files(
    files: list[UploadFile] = File(..., description="邮件文件 (.eml/.msg/.pst/.ost)"),
    account_id: str = Depends(get_account_id),
):
    """接收浏览器端上传的邮件文件，保存到服务端临时目录，返回绝对路径列表。

    前端用 <input type=file> 选择文件后上传，然后调用 POST /mails/scan (source=file)
    用返回的 paths 扫描表头。
    """
    data_dir = _os.environ.get("DATA_DIR", "./data")
    upload_dir = Path(data_dir) / "uploads" / account_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    for f in files:
        safe_name = Path(f.filename or "unknown").name
        dest = upload_dir / safe_name
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            i = 1
            while dest.exists():
                dest = upload_dir / f"{stem}_{i}{suffix}"
                i += 1
        with open(dest, "wb") as buf:
            shutil.copyfileobj(f.file, buf)
        saved_paths.append(str(dest.resolve()))
        logger.info("upload: saved %s → %s", safe_name, dest)

    return {"paths": saved_paths, "count": len(saved_paths)}


@router.get("/browse", response_model=BrowseResponse)
def browse_files(dir: str | None = Query(None, description="要浏览的目录绝对路径；留空则从用户主目录开始")):
    """浏览服务端本地目录：返回子目录 + 受支持的邮件文件 + 上级目录，供前端逐级点选。只读。"""
    from pathlib import Path

    from src.backend.mail.sources import EXTENSIONS

    base = Path(dir).expanduser() if dir else Path.home()
    if not base.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {dir}")
    # 指到文件时，浏览其所在目录
    if base.is_file():
        base = base.parent

    dirs: list[BrowseDir] = []
    files: list[BrowseFile] = []
    try:
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith("."):  # 跳过隐藏项
                continue
            try:
                if entry.is_dir():
                    dirs.append(BrowseDir(path=str(entry), name=entry.name))
                elif entry.suffix.lower() in EXTENSIONS:
                    files.append(BrowseFile(
                        path=str(entry), name=entry.name,
                        size=entry.stat().st_size, ext=entry.suffix.lower(),
                    ))
            except (OSError, PermissionError):
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"无权限读取: {base}")

    parent = str(base.parent) if base.parent != base else None
    return BrowseResponse(dir=str(base), parent=parent, dirs=dirs[:1000], files=files[:1000])


@router.get("/indexed", response_model=PaginatedMailResponse)
def indexed_mails(
    status: str = Query("pending", pattern="^(pending|done|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    cache=Depends(get_cache),
):
    """列出已索引邮件（IMAP 表头扫描 + 文件扫描统一入口）。

    status: pending(未处理) | done(已处理) | all(全部)。
    """
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    offset = (page - 1) * page_size
    mails = cache.list_indexed(limit=page_size, offset=offset, status=status)
    total = cache.count_indexed(status=status)
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


@router.post("/scan")
async def scan_mails(
    body: ScanRequest,
    account_id: str = Depends(get_account_id),
):
    """统一扫描入口：IMAP 拉表头 / 文件扫表头 → indexed 清单。

    source="imap":  只拉 ENVELOPE 级元数据（主题/发件人/日期），不下载正文。
    source="file":  扫描本地 .eml/.msg/.pst/.ost 文件的邮件表头。
    产出 indexed 邮件后，用户从清单勾选 → POST /mails/parse-selected 建图。
    """
    from src.backend.pipeline import Pipeline

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        task = asyncio.create_task(
            run_pipeline_with_sse(
                Pipeline(account_id).run_scan,
                body.source,
                body.params,
                queue=queue,
            )
        )
        async for event in sse_from_queue(queue):
            yield event
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/fetch")
async def fetch_mails(
    body: FetchRequest,
    account_id: str = Depends(get_account_id),
):
    """Pull mails from IMAP → clean → enqueue. 在 API 进程内跑（IMAP 属 IO 型，
    不做 DeepDoc 解析，不会阻塞事件循环；也不占用只管解析的 worker）。

    DEPRECATED: 新代码请用 POST /mails/scan (source=imap) + POST /mails/parse-selected
    两步流程，表头扫描与正文解析分离。
    """
    from src.backend.pipeline import Pipeline

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
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
    """建图：每封邮件一个 ingest_one job，worker 逐个处理。

    如果指定 message_ids：先补回 ingest 队列（处理 reset 后 / 崩 worker 后不在队列的邮件），
    然后逐封入队给 worker。
    """
    from src.backend.storage.redis_cache import MailCache
    from src.backend import jobqueue

    cache = MailCache(account_id)
    mids: list[str] = []

    try:
        if body.message_ids:
            # 补回队列
            for mid in body.message_ids:
                if cache.get_mail(mid):
                    cache.r.sadd(cache._k("ingest_queue"), mid)
                    mids.append(mid)
            if mids:
                logger.info("ingest: requeued %d mails for account %s", len(mids), account_id)
        else:
            # 未指定 → 取队列中所有
            mids = cache.list_pending_ingest()
    finally:
        cache.close()

    if not mids:
        async def _empty():
            yield "event: progress\ndata: {\"msg\": \"ingest 队列为空\"}\n\n"
            yield "event: complete\ndata: {}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    # 逐封入队 ingest_one job，一个 SSE 流跟踪所有 job
    return StreamingResponse(
        multi_job_stream("ingest_one", account_id, mids),
        media_type="text/event-stream",
    )


@router.post("/reprocess")
async def reprocess_mails(
    body: ReprocessRequest,
    account_id: str = Depends(get_account_id),
):
    """强制重处理：API 侧重置+重拉入队（IO），再入队 ingest 交 worker 建图。"""
    from src.backend.pipeline import Pipeline
    pipe = Pipeline(account_id)
    return StreamingResponse(
        prep_then_ingest_stream(pipe.reprocess, (body.message_ids,), account_id),
        media_type="text/event-stream",
    )


@router.post("/index")
async def index_files(
    body: IndexFilesRequest,
    account_id: str = Depends(get_account_id),
):
    """Step 1：扫描本地邮件文件表头 → indexed。纯扫描不建图，在 API 进程内跑。"""
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
    """Step 2：API 侧读选中邮件源文件 + 抽附件入队（IO），再入队 ingest 交 worker 建图。"""
    from src.backend.pipeline import Pipeline
    pipe = Pipeline(account_id)
    return StreamingResponse(
        prep_then_ingest_stream(pipe.parse_selected, (body.message_ids,), account_id),
        media_type="text/event-stream",
    )


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
        folder=mail.get("folder", ""),
        source_type=mail.get("source_type", ""),
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
        folder=mail.get("folder", ""),
        source_type=mail.get("source_type", ""),
        body=mail.get("cleaned_body") or mail.get("body") or "",
        to_addrs=mail.get("to_addrs") or [],
        cc_addrs=mail.get("cc_addrs") or [],
    )
