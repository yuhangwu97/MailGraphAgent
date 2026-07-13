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
        key = self._k(project_name)

        # Before overwriting, push current to history
        current_raw = self._r.get(key)
        if current_raw:
            try:
                current = json.loads(current_raw)
                current["project_name"] = project_name
                history_key = self._k(project_name) + ":history"
                self._r.lpush(history_key, json.dumps(current, ensure_ascii=False))
                # Keep last 20 history entries
                self._r.ltrim(history_key, 0, 19)
                # Set TTL on history list (same as main TTL)
                self._r.expire(history_key, self._ttl)
            except Exception:
                pass

        self._r.setex(
            key,
            self._ttl,
            json.dumps(data, ensure_ascii=False),
        )
        return data

    def get_history(self, project_name: str) -> list[dict]:
        """Return all historical analyses for a project (newest first)."""
        history_key = self._k(project_name) + ":history"
        try:
            items = self._r.lrange(history_key, 0, -1)
            return [json.loads(i) for i in items if i]
        except Exception:
            return []

    def delete_history(self, project_name: str):
        """Delete history for a project."""
        self._r.delete(self._k(project_name) + ":history")

    def delete(self, project_name: str):
        self._r.delete(self._k(project_name))

    def close(self):
        self._r.close()
