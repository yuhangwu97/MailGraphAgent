"""Chat session / message / agent memory CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from src.backend.deps import get_account_id, get_conversation_store
from src.backend.schemas import (
    AgentMemoryOut,
    ChatMessage,
    ChatMessageCreate,
    ConvCreateRequest,
    ConvRenameRequest,
    ConvSessionOut,
    RecentContext,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _get_store(account_id: str = Depends(get_account_id)):
    store = get_conversation_store(account_id=account_id)
    if store is None:
        raise HTTPException(status_code=503, detail="Conversation store unavailable")
    return store


# ── Memory (must be before /{session_id} to avoid route conflict) ──


@router.get("/memory", response_model=AgentMemoryOut)
def get_memory(store=Depends(_get_store)):
    try:
        mem = store.get_memory()
        return AgentMemoryOut(
            preferences=mem.preferences,
            pinned_context=mem.pinned_context,
            last_topics=mem.last_topics,
            summary=mem.summary,
            updated_at=mem.updated_at,
        )
    finally:
        store.close()


@router.post("/memory")
def update_memory(question: str = Query(""), answer: str = Query(""), store=Depends(_get_store)):
    try:
        store.update_memory_from_turn(question, answer)
        return {"ok": True}
    finally:
        store.close()


# ── Sessions ──


@router.get("", response_model=list[ConvSessionOut])
def list_sessions(store=Depends(_get_store)):
    try:
        sessions = store.list_sessions()
        return [_to_session_out(s) for s in sessions]
    finally:
        store.close()


@router.post("", response_model=ConvSessionOut, status_code=201)
def create_session(body: ConvCreateRequest = ConvCreateRequest(), store=Depends(_get_store)):
    try:
        session = store.create_session(title=body.title)
        return _to_session_out(session)
    finally:
        store.close()


@router.get("/{session_id}", response_model=ConvSessionOut)
def get_session(session_id: str, store=Depends(_get_store)):
    try:
        session = store.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return _to_session_out(session)
    finally:
        store.close()


@router.patch("/{session_id}", response_model=ConvSessionOut)
def rename_session(session_id: str, body: ConvRenameRequest, store=Depends(_get_store)):
    try:
        store.rename_session(session_id, body.title)
        session = store.get_session(session_id)
        return _to_session_out(session)
    finally:
        store.close()


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str, store=Depends(_get_store)):
    try:
        store.delete_session(session_id)
    finally:
        store.close()


# ── Messages ──


@router.get("/{session_id}/messages", response_model=list[ChatMessage])
def list_messages(session_id: str, limit: int = 80, store=Depends(_get_store)):
    try:
        msgs = store.list_messages(session_id, limit=limit)
        return [
            ChatMessage(
                id=m.get("id", ""),
                role=m.get("role", "user"),
                content=m.get("content", ""),
                result=m.get("result"),
                created_at=m.get("created_at", 0.0),
            )
            for m in msgs
        ]
    finally:
        store.close()


@router.post("/{session_id}/messages", response_model=ChatMessage, status_code=201)
def add_message(session_id: str, body: ChatMessageCreate, store=Depends(_get_store)):
    try:
        msg = store.add_message(session_id, body.role, body.content, body.result)
        return ChatMessage(
            id=msg.get("id", ""),
            role=msg.get("role", "user"),
            content=msg.get("content", ""),
            result=msg.get("result"),
            created_at=msg.get("created_at", 0.0),
        )
    finally:
        store.close()


@router.get("/{session_id}/context", response_model=list[RecentContext])
def recent_context(session_id: str, limit: int = 6, store=Depends(_get_store)):
    try:
        ctx = store.recent_context(session_id, limit=limit)
        return [RecentContext(role=c["role"], content=c["content"]) for c in ctx]
    finally:
        store.close()


# ── Helpers ──


def _to_session_out(s: dict) -> ConvSessionOut:
    return ConvSessionOut(
        id=s.get("id", ""),
        title=s.get("title", "新对话"),
        created_at=s.get("created_at", 0.0),
        updated_at=s.get("updated_at", 0.0),
        message_count=s.get("message_count", 0),
    )
