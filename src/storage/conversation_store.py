"""
Conversation and agent memory storage.

Redis-backed, account-scoped:
- sessions: metadata for each chat session
- messages: ordered chat messages per session
- memory: compact cross-turn user preferences/context
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import redis

from config.settings import get_settings


def _now() -> float:
    return time.time()


@dataclass
class AgentMemory:
    preferences: list[str] = field(default_factory=list)
    pinned_context: list[str] = field(default_factory=list)
    last_topics: list[str] = field(default_factory=list)
    summary: str = ""
    updated_at: float = 0.0

    @classmethod
    def from_json(cls, raw: str | None) -> "AgentMemory":
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
            return cls(
                preferences=list(data.get("preferences") or []),
                pinned_context=list(data.get("pinned_context") or []),
                last_topics=list(data.get("last_topics") or []),
                summary=data.get("summary") or "",
                updated_at=float(data.get("updated_at") or 0),
            )
        except Exception:
            return cls()

    def to_dict(self) -> dict[str, Any]:
        return {
            "preferences": self.preferences[:20],
            "pinned_context": self.pinned_context[:20],
            "last_topics": self.last_topics[:20],
            "summary": self.summary[:1200],
            "updated_at": self.updated_at or _now(),
        }

    def to_prompt(self) -> str:
        parts = []
        if self.preferences:
            parts.append("用户偏好: " + "；".join(self.preferences[:8]))
        if self.pinned_context:
            parts.append("固定上下文: " + "；".join(self.pinned_context[:8]))
        if self.last_topics:
            parts.append("最近关注: " + "；".join(self.last_topics[:8]))
        if self.summary:
            parts.append("会话摘要: " + self.summary[:500])
        return "\n".join(parts)


class ConversationStore:
    def __init__(self, account_id: str | None = None):
        cfg = get_settings()
        self._r = redis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            db=cfg.redis_db,
            password=cfg.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        self._account_id = account_id or "default"
        self._prefix = f"mailgraph:{self._account_id}:conv:"

    @property
    def r(self) -> redis.Redis:
        return self._r

    def _k(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    def create_session(self, title: str = "新对话") -> dict:
        sid = uuid.uuid4().hex[:16]
        now = _now()
        session = {
            "id": sid,
            "title": title or "新对话",
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }
        self.r.hset(self._k("session", sid), mapping={
            k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
            for k, v in session.items()
        })
        self.r.zadd(self._k("sessions"), {sid: now})
        return session

    def list_sessions(self, limit: int = 30) -> list[dict]:
        ids = self.r.zrevrange(self._k("sessions"), 0, max(limit - 1, 0))
        sessions = []
        for sid in ids:
            raw = self.r.hgetall(self._k("session", sid))
            if not raw:
                continue
            sessions.append(self._decode_session(raw))
        return sessions

    def get_session(self, session_id: str) -> dict | None:
        raw = self.r.hgetall(self._k("session", session_id))
        return self._decode_session(raw) if raw else None

    def ensure_session(self, session_id: str | None = None) -> dict:
        if session_id:
            existing = self.get_session(session_id)
            if existing:
                return existing
        sessions = self.list_sessions(limit=1)
        return sessions[0] if sessions else self.create_session()

    def rename_session(self, session_id: str, title: str):
        title = (title or "").strip()[:80] or "新对话"
        self.r.hset(self._k("session", session_id), "title", title)
        self.touch_session(session_id)

    def delete_session(self, session_id: str):
        self.r.delete(self._k("session", session_id))
        self.r.delete(self._k("messages", session_id))
        self.r.zrem(self._k("sessions"), session_id)

    def add_message(self, session_id: str, role: str, content: str,
                    result: dict | None = None):
        now = _now()
        msg = {
            "id": uuid.uuid4().hex[:12],
            "role": role,
            "content": content,
            "result": result or {},
            "created_at": now,
        }
        self.r.rpush(self._k("messages", session_id), json.dumps(msg, ensure_ascii=False))
        self.touch_session(session_id)
        self.r.hincrby(self._k("session", session_id), "message_count", 1)
        if role == "user":
            self._maybe_title_from_message(session_id, content)
        return msg

    def list_messages(self, session_id: str, limit: int = 80) -> list[dict]:
        raw_items = self.r.lrange(self._k("messages", session_id), max(-limit, -1000), -1)
        messages = []
        for raw in raw_items:
            try:
                messages.append(json.loads(raw))
            except Exception:
                pass
        return messages

    def recent_context(self, session_id: str, limit: int = 6) -> list[dict]:
        messages = self.list_messages(session_id, limit=limit)
        return [
            {"role": m.get("role", ""), "content": (m.get("content") or "")[:600]}
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]

    def get_memory(self) -> AgentMemory:
        return AgentMemory.from_json(self.r.get(self._k("memory")))

    def save_memory(self, memory: AgentMemory):
        memory.updated_at = _now()
        self.r.set(self._k("memory"), json.dumps(memory.to_dict(), ensure_ascii=False))

    def update_memory_from_turn(self, question: str, answer: str):
        memory = self.get_memory()
        topic = self._extract_topic(question)
        if topic:
            memory.last_topics = self._prepend_unique(memory.last_topics, topic, 12)
        pref = self._extract_preference(question)
        if pref:
            memory.preferences = self._prepend_unique(memory.preferences, pref, 12)
        if answer:
            memory.summary = self._summarize_locally(memory.summary, question, answer)
        self.save_memory(memory)

    def touch_session(self, session_id: str):
        now = _now()
        self.r.hset(self._k("session", session_id), "updated_at", str(now))
        self.r.zadd(self._k("sessions"), {session_id: now})

    def close(self):
        self._r.close()

    def _decode_session(self, raw: dict) -> dict:
        return {
            "id": raw.get("id", ""),
            "title": raw.get("title", "新对话"),
            "created_at": float(raw.get("created_at") or 0),
            "updated_at": float(raw.get("updated_at") or 0),
            "message_count": int(raw.get("message_count") or 0),
        }

    def _maybe_title_from_message(self, session_id: str, content: str):
        session = self.get_session(session_id)
        if not session or session.get("message_count", 0) > 1:
            return
        title = re_title(content)
        if title:
            self.rename_session(session_id, title)

    @staticmethod
    def _prepend_unique(values: list[str], value: str, limit: int) -> list[str]:
        value = value.strip()
        return [value] + [v for v in values if v != value][:limit - 1]

    @staticmethod
    def _extract_topic(question: str) -> str:
        text = (question or "").strip()
        for marker in ("关于", "提到", "讨论", "查询", "看看", "列出"):
            if marker in text:
                tail = text.split(marker, 1)[1].strip(" 的邮件有哪些多少？?，,。")
                if 1 < len(tail) <= 40:
                    return tail
        return text[:30] if 4 <= len(text) <= 30 else ""

    @staticmethod
    def _extract_preference(question: str) -> str:
        text = (question or "").strip()
        patterns = ("以后", "之后", "下次", "默认", "记住")
        if any(p in text for p in patterns):
            return text[:80]
        return ""

    @staticmethod
    def _summarize_locally(summary: str, question: str, answer: str) -> str:
        line = f"用户问: {question[:80]}；助手答: {answer[:120]}"
        lines = ([line] + [l for l in (summary or "").split("\n") if l.strip()])[:8]
        return "\n".join(lines)


def re_title(content: str) -> str:
    title = re.sub(r"\s+", " ", (content or "").strip())
    title = title.strip("？?。,.，")
    return title[:28] if title else "新对话"
