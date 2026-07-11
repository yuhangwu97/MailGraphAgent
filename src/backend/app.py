"""
MailGraphAgent FastAPI application.

Start with:  uvicorn src.backend.app:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config.settings import get_settings
from src.backend.routes import accounts, conversations, graph, mails, query, status

logger = logging.getLogger(__name__)

settings = get_settings()

# Resolve Vue dist dir
_VUE_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
_PROD_MODE = _VUE_DIST.exists() and _VUE_DIST.is_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    logger.info("MailGraph API starting (prod=%s) ...", _PROD_MODE)
    yield
    # ── Shutdown: close pooled connections ──
    logger.info("MailGraph API shutting down — cleaning up connections ...")

    # Close Neo4j driver
    try:
        from src.backend.storage.neo4j_client import close_driver
        close_driver()
        logger.info("Neo4j driver closed")
    except Exception as e:
        logger.warning("Failed to close Neo4j driver: %s", e)

    # Close QueryEngine MailCache instances
    try:
        from src.backend.deps import _query_engines
        for aid, engine in list(_query_engines.items()):
            try:
                if hasattr(engine, 'close'):
                    engine.close()
            except Exception:
                pass
        _query_engines.clear()
        logger.info("QueryEngine caches closed")
    except Exception as e:
        logger.warning("Failed to close QueryEngine caches: %s", e)


app = FastAPI(
    title="MailGraph Agent API",
    version="3.0.0",
    description="企业邮件关系分析平台 — REST + SSE API",
    lifespan=lifespan,
)

# CORS — allow Vue dev server in development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ──
app.include_router(status.router)
app.include_router(accounts.router)
app.include_router(mails.router)
app.include_router(conversations.router)
app.include_router(query.router)
app.include_router(graph.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "3.0.0"}


# ── Production: serve Vue SPA ──
if _PROD_MODE:
    app.mount("/", StaticFiles(directory=str(_VUE_DIST), html=True), name="spa")
    logger.info("Serving Vue SPA from %s", _VUE_DIST)
