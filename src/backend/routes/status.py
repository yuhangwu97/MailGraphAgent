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

    # Neo4j check
    neo4j_ok = False
    try:
        from src.backend.storage.neo4j_client import _get_driver
        _get_driver().verify_connectivity()
        neo4j_ok = True
    except Exception:
        pass

    # Milvus check
    # Milvus 的 HTTP /healthz 在 9091（metrics 端口），而 compose 只映射了 19530
    # （gRPC 代理端口）；19530 上无 /healthz，v1 RESTful 又不稳定（间歇 502）。
    # 用一次 TCP 连接探测代理端口是否在监听作为存活信号——快（~10ms）且可靠。
    milvus_ok = False
    try:
        import socket
        from urllib.parse import urlparse
        from config.settings import get_settings

        u = urlparse(get_settings().milvus_uri)
        with socket.create_connection((u.hostname, u.port or 19530), timeout=3):
            milvus_ok = True
    except Exception:
        pass

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
        services=ServiceStatus(redis=redis_ok, neo4j=neo4j_ok, milvus=milvus_ok),
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
        "neo4j": "mailgraph-neo4j",
        "redis": "mailgraph-redis",
        "milvus": "mailgraph-milvus",
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
