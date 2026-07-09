"""Health-check, token usage, and service logs."""

from __future__ import annotations

import subprocess

from fastapi import APIRouter, Depends, HTTPException, Query

from src.backend.deps import get_account_store, get_account_id, get_cache
from src.backend.schemas import ServiceStatus, StatusResponse, TokenUsage

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status", response_model=StatusResponse)
def health_check(
    account_id: str = Depends(get_account_id),
    cache=Depends(get_cache),
):
    """Service health + active account info."""
    redis_ok = False
    if cache:
        try:
            cache.get_stats()
            redis_ok = True
        except Exception:
            pass

    # RAGFlow check
    from src.backend.deps import _ragflow_clients

    ragflow_ok = account_id in _ragflow_clients and _ragflow_clients[account_id] is not None

    store = get_account_store()
    try:
        accounts_raw = store.list()
        from src.backend.schemas import AccountOut

        accounts = [
            AccountOut(
                id=a["id"],
                label=a.get("label") or a.get("email_user", ""),
                imap_server=a.get("imap_server", ""),
                imap_port=a.get("imap_port", 993),
                email_user=a.get("email_user", ""),
                provider=a.get("provider", ""),
            )
            for a in accounts_raw
        ]
    finally:
        store.close()

    return StatusResponse(
        services=ServiceStatus(ragflow=ragflow_ok, redis=redis_ok),
        active_account_id=account_id,
        accounts=accounts,
    )


@router.get("/usage/tokens", response_model=TokenUsage)
def token_usage(cache=Depends(get_cache)):
    """Get total API token usage."""
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    try:
        t = cache.get_total_tokens()
        return TokenUsage(
            prompt_tokens=t.get("prompt_tokens", 0),
            completion_tokens=t.get("completion_tokens", 0),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/{service}")
def service_logs(
    service: str,
    lines: int = Query(50, ge=10, le=500),
):
    """Tail Docker service logs."""
    allowed = {
        "ragflow": "mailgraph-ragflow",
        "mysql": "mailgraph-mysql",
        "redis": "mailgraph-redis",
    }
    container = allowed.get(service)
    if not container:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service}")
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), container],
            capture_output=True, text=True, timeout=10,
        )
        return {"service": service, "lines": lines, "log": (result.stderr or result.stdout or "(no logs)")[-20000:]}
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Docker not installed")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Log fetch timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
