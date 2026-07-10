"""Pydantic request / response models for the MailGraph API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# Account
# ═══════════════════════════════════════════════════════════════

class AccountCreate(BaseModel):
    label: str = ""
    imap_server: str
    imap_port: int = 993
    email_user: str
    email_pass: str
    provider: str = ""


class AccountOut(BaseModel):
    id: str
    label: str
    imap_server: str
    imap_port: int
    email_user: str
    provider: str


# ═══════════════════════════════════════════════════════════════
# Mail
# ═══════════════════════════════════════════════════════════════

class MailStats(BaseModel):
    total: int
    done: int
    pending: int
    failed: int
    skipped: int
    ingested: int = 0
    indexed: int = 0


class MailQueryRequest(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    status: str | None = None
    sender: str | None = None
    has_attachment: bool | None = None
    message_ids: list[str] | None = None
    topic: str | None = None
    aggregation: Literal["count", "list", "rate", "top_senders"] | None = None
    limit: int = 20


class MailItem(BaseModel):
    message_id: str = ""
    subject: str = ""
    from_addr: str = ""
    from_name: str = ""
    date: str = ""
    status: str = "pending"
    attachment_count: int = 0
    attachments: list[dict] = Field(default_factory=list)
    folder: str = ""
    source_type: str = ""


class PaginatedMailResponse(BaseModel):
    items: list[MailItem]
    total: int
    page: int
    page_size: int


class MailDetail(MailItem):
    body: str = ""
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)


class FetchRequest(BaseModel):
    folder: str = "INBOX"
    limit: int = 20


class IngestRequest(BaseModel):
    limit: int | None = None


class ReprocessRequest(BaseModel):
    message_ids: list[str]


# ── 文件邮件导入（本地 .eml/.msg/.pst/.ost）──

class IndexFilesRequest(BaseModel):
    """Step 1：扫描给定本地文件/目录路径的邮件表头。"""
    paths: list[str]


class ParseSelectedRequest(BaseModel):
    """Step 2：解析并向量化选中的 indexed 邮件。"""
    message_ids: list[str]


class BrowseFile(BaseModel):
    path: str
    name: str
    size: int = 0
    ext: str = ""


class BrowseResponse(BaseModel):
    dir: str
    files: list[BrowseFile] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# Conversation
# ═══════════════════════════════════════════════════════════════

class ConvSessionOut(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float
    message_count: int


class ConvCreateRequest(BaseModel):
    title: str = "新对话"


class ConvRenameRequest(BaseModel):
    title: str


class ChatMessage(BaseModel):
    id: str = ""
    role: Literal["user", "assistant"]
    content: str
    result: dict | None = None
    created_at: float = 0.0


class ChatMessageCreate(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    result: dict | None = None


class RecentContext(BaseModel):
    role: str
    content: str


class AgentMemoryOut(BaseModel):
    preferences: list[str] = Field(default_factory=list)
    pinned_context: list[str] = Field(default_factory=list)
    last_topics: list[str] = Field(default_factory=list)
    summary: str = ""
    updated_at: float = 0.0


# ═══════════════════════════════════════════════════════════════
# Query
# ═══════════════════════════════════════════════════════════════

class QueryRequest(BaseModel):
    question: str
    session_id: str | None = None
    account_id: str | None = None


class QueryResult(BaseModel):
    question: str
    answer: str
    entities: list[dict] = Field(default_factory=list)
    relationships: list[dict] = Field(default_factory=list)
    chunks: list[dict] = Field(default_factory=list)
    rows: list[dict] | None = None
    columns: list[str] | None = None
    total_rows: int = 0
    trace: list[dict] = Field(default_factory=list)
    error: str | None = None
    query_plan: dict | None = None
    total_duration_ms: int = 0


# ═══════════════════════════════════════════════════════════════
# Graph
# ═══════════════════════════════════════════════════════════════

class GraphEntity(BaseModel):
    id: str
    name: str = ""
    type: str = "Entity"
    description: str = ""


class GraphRelationship(BaseModel):
    source_id: str
    target_id: str
    type: str = ""
    description: str = ""


class GraphBuildRequest(BaseModel):
    timeout: int = 300


class GraphVisualizeRequest(BaseModel):
    entity_types: list[str] | None = None


# ═══════════════════════════════════════════════════════════════
# Status
# ═══════════════════════════════════════════════════════════════

class ServiceStatus(BaseModel):
    ragflow: bool
    redis: bool
    mysql: bool = True
    minio: bool = True


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class StatusResponse(BaseModel):
    services: ServiceStatus
    active_account_id: str | None = None
    accounts: list[AccountOut] = Field(default_factory=list)


class SSEEvent(BaseModel):
    event: str  # "progress" | "complete" | "error"
    data: str
