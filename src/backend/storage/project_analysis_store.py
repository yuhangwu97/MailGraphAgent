"""
Project analysis cache — Redis-backed, globally shared.

Key: mailgraph:project_analysis:{project_name} → JSON
TTL: 24 hours (86400 seconds)
"""

from __future__ import annotations

import json
import time

import redis

from config.settings import get_settings


def _now() -> float:
    return time.time()


class ProjectAnalysisStore:
    def __init__(self):
        cfg = get_settings()
        self._r = redis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            db=cfg.redis_db,
            password=cfg.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        self._prefix = "mailgraph:project_analysis:"
        self._ttl = 86400  # 24h

    def _k(self, project_name: str) -> str:
        return self._prefix + project_name

    def get(self, project_name: str) -> dict | None:
        raw = self._r.get(self._k(project_name))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def save(self, project_name: str, summary: dict, report: dict) -> dict:
        data = {
            "summary": summary,
            "report": report,
            "generated_at": _now(),
        }
        self._r.setex(
            self._k(project_name),
            self._ttl,
            json.dumps(data, ensure_ascii=False),
        )
        return data

    def delete(self, project_name: str):
        self._r.delete(self._k(project_name))

    def close(self):
        self._r.close()
