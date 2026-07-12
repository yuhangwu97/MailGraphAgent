# Dashboard 项目看板优化 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Dashboard 增加分页（20/页）、AI 项目分析（7 维度）、分析报告弹窗、Chat 快捷联动入口。

**Architecture:** 后端新增 Redis-backed 项目分析存储 + projects 路由；前端新增 ProjectReportModal 组件，改造 DashboardPage/ProjectCard/ChatPage 支持分页和分析联动。

**Tech Stack:** Python 3.12+ (FastAPI), Vue 3 (Composition API + TypeScript), Redis, Neo4j

## Global Constraints

- 分页: 20 条/页，使用 `page` + `page_size` query 参数
- Redis key 前缀: `mailgraph:project_analysis:`
- 分析缓存 TTL: 24 小时
- 分析维度 7 项: 概述、阶段、合同、时间节点、人员、公司、动态
- Chat 联动: 创建新会话 → 构建 prompt → 跳转 `/chat?prompt=...`
- 前端 modal 使用 Teleport to body，与现有 Agent Memory modal 风格一致

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/backend/storage/project_analysis_store.py` | Create | Redis CRUD for project analysis JSON |
| `src/backend/schemas.py` | Modify | Add ProjectItem, ProjectAnalysis, ProjectSummary, PaginatedProjects |
| `src/backend/routes/projects.py` | Create | GET /api/projects, POST analyze, GET analysis |
| `src/backend/app.py` | Modify | Register projects router |
| `src/backend/storage/neo4j_client.py` | Modify | Add `get_projects_paginated()` with SKIP/LIMIT |
| `src/web/src/api/index.ts` | Modify | Add projectsApi + types |
| `src/web/src/components/dashboard/ProjectCard.vue` | Modify | AI summary section + action buttons |
| `src/web/src/components/dashboard/ProjectReportModal.vue` | Create | 7-dimension report modal (Teleport) |
| `src/web/src/pages/DashboardPage.vue` | Modify | Pagination + new API integration |
| `src/web/src/pages/ChatPage.vue` | Modify | Read `route.query.prompt`, auto-fill input |

---

### Task 1: Backend — Project Analysis Redis Store

**Files:**
- Create: `src/backend/storage/project_analysis_store.py`

**Interfaces:**
- Produces: `ProjectAnalysisStore` class with methods `get()`, `save()`, `delete()`

- [ ] **Step 1: Create the store file**

```python
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
```

- [ ] **Step 2: Verify the file is syntactically valid**

Run: `python -c "from src.backend.storage.project_analysis_store import ProjectAnalysisStore; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/backend/storage/project_analysis_store.py
git commit -m "feat: add ProjectAnalysisStore for Redis-backed project analysis cache"
```

---

### Task 2: Backend — Paginated Project Query in Neo4j

**Files:**
- Modify: `src/backend/storage/neo4j_client.py` — add `get_projects_paginated()` function

**Interfaces:**
- Produces: `get_projects_paginated(page: int, page_size: int) -> dict` returning `{projects: list[dict], total: int}`

- [ ] **Step 1: Add the paginated query function**

Append to `src/backend/storage/neo4j_client.py`:

```python
def get_projects_paginated(page: int = 1, page_size: int = 20) -> dict:
    """Return paginated project entities with neighbor info.

    Each project dict: {id, name, type, description, people, companies}
    People/companies are derived from DIRECTED relationships.
    Returns: {projects: list[dict], total: int}
    """
    skip = max(0, (page - 1) * page_size)

    # Count total projects
    count_q = (
        "MATCH (n) WHERE n.entity_id IS NOT NULL AND n.entity_type = 'project' "
        "RETURN count(n) AS c"
    )

    # Fetch page of projects
    projects_q = (
        "MATCH (n) WHERE n.entity_id IS NOT NULL AND n.entity_type = 'project' "
        "RETURN n.entity_id AS id, "
        "       coalesce(n.description, '') AS description "
        "ORDER BY toLower(n.entity_id) "
        "SKIP $skip LIMIT $limit"
    )

    driver = _get_driver()
    with driver.session() as session:
        total = session.run(count_q).single()["c"]
        result = session.run(projects_q, skip=skip, limit=int(page_size))
        projects = []
        for r in result:
            eid = r["id"]
            projects.append({
                "id": eid,
                "name": eid,
                "type": "project",
                "description": r["description"],
                "people": [],
                "companies": [],
            })

    if not projects:
        return {"projects": [], "total": int(total)}

    # Fetch relationships for this page's projects only
    project_ids = [p["name"] for p in projects]
    rels_q = (
        "MATCH (a)-[r:DIRECTED]->(b) "
        "WHERE a.entity_id IN $pids AND b.entity_id IS NOT NULL "
        "RETURN a.entity_id AS source_id, "
        "       b.entity_id AS target_id, "
        "       b.entity_type AS target_type, "
        "       coalesce(b.description, '') AS target_desc "
        "UNION "
        "MATCH (a)-[r:DIRECTED]->(b) "
        "WHERE b.entity_id IN $pids AND a.entity_id IS NOT NULL "
        "RETURN b.entity_id AS source_id, "
        "       a.entity_id AS target_id, "
        "       a.entity_type AS target_type, "
        "       coalesce(a.description, '') AS target_desc"
    )

    with driver.session() as session:
        rels = session.run(rels_q, pids=project_ids)
        by_project: dict[str, dict[str, list]] = {}
        for p in projects:
            by_project[p["name"]] = {"people": [], "companies": []}

        seen = set()
        for r in rels:
            proj_name = r["source_id"]
            if proj_name not in by_project:
                proj_name = r["target_id"]
                if proj_name not in by_project:
                    continue
            neighbor = r["target_id"] if r["source_id"] == proj_name else r["source_id"]
            ntype = (r["target_type"] or "").lower()
            key = f"{proj_name}|{neighbor}"
            if key in seen:
                continue
            seen.add(key)

            PEOPLE_TYPES = {"person", "contact", "employee"}
            if ntype in PEOPLE_TYPES:
                by_project[proj_name]["people"].append({"name": neighbor})
            elif ntype == "organization":
                by_project[proj_name]["companies"].append({"name": neighbor})

        for p in projects:
            p["people"] = by_project[p["name"]]["people"]
            p["companies"] = by_project[p["name"]]["companies"]

    return {"projects": projects, "total": int(total)}
```

- [ ] **Step 2: Verify the import works**

Run: `python -c "from src.backend.storage.neo4j_client import get_projects_paginated; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/backend/storage/neo4j_client.py
git commit -m "feat: add get_projects_paginated() with SKIP/LIMIT and neighbor population"
```

---

### Task 3: Backend — Pydantic Schemas for Projects

**Files:**
- Modify: `src/backend/schemas.py` — append project schemas after line 258 (end of file)

**Interfaces:**
- Produces: `ProjectSummary`, `ProjectReport`, `ProjectAnalysisOut`, `ProjectItem`, `PaginatedProjects`, `AnalyzeRequest`

- [ ] **Step 1: Add project schemas**

Append to `src/backend/schemas.py`:

```python

# ═══════════════════════════════════════════════════════════════
# Project Analysis
# ═══════════════════════════════════════════════════════════════

class ProjectSummary(BaseModel):
    overview: str = ""          # 📌 一句话概述
    stage: str = ""             # 📈 项目阶段/状态
    key_dates: str = ""         # 📅 关键时间节点
    core_people: list[str] = Field(default_factory=list)  # 👥 核心人员


class ProjectReport(BaseModel):
    overview: str = ""          # 📌 一句话概述
    stage: str = ""             # 📈 项目阶段/状态
    contract: str = ""          # 💰 合同与金额
    key_dates: str = ""         # 📅 关键时间节点
    core_people: str = ""       # 👥 核心人员
    companies: str = ""         # 🏢 相关公司/组织
    recent_activity: str = ""   # 📝 近期关键动态


class ProjectAnalysisOut(BaseModel):
    project_name: str
    summary: ProjectSummary | None = None    # cached AI summary (card)
    report: ProjectReport | None = None      # cached full report (modal)
    generated_at: float = 0.0
    cached: bool = True


class ProjectItem(BaseModel):
    name: str
    description: str = ""
    people: list[dict] = Field(default_factory=list)
    companies: list[dict] = Field(default_factory=list)
    ai_summary: ProjectSummary | None = None


class PaginatedProjects(BaseModel):
    projects: list[ProjectItem]
    total: int
    page: int
    page_size: int


class AnalyzeRequest(BaseModel):
    question_override: str | None = None  # optional custom question
```

- [ ] **Step 2: Verify the import**

Run: `python -c "from src.backend.schemas import ProjectSummary, ProjectReport, ProjectAnalysisOut, ProjectItem, PaginatedProjects; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/backend/schemas.py
git commit -m "feat: add ProjectAnalysis Pydantic schemas"
```

---

### Task 4: Backend — Projects API Routes

**Files:**
- Create: `src/backend/routes/projects.py`

**Interfaces:**
- Consumes: `ProjectAnalysisStore` from Task 1, schemas from Task 3, `get_projects_paginated` from Task 2
- Produces: `GET /api/projects`, `POST /api/projects/{name}/analyze`, `GET /api/projects/{name}/analysis`

- [ ] **Step 1: Create the routes file**

```python
"""Project dashboard endpoints — paginated listing + AI analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.backend.schemas import (
    AnalyzeRequest,
    PaginatedProjects,
    ProjectAnalysisOut,
    ProjectItem,
    ProjectReport,
    ProjectSummary,
)
from src.backend.storage.project_analysis_store import ProjectAnalysisStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

# 7-dimension analysis prompt template
ANALYSIS_PROMPT = """请基于知识图谱中的邮件数据，对项目「{project_name}」进行多维度分析。用中文回答，严格按以下7个字段输出JSON格式（不要输出markdown代码块，只输出纯JSON）：

{{
  "overview": "一句话概述该项目的核心内容（50字以内）",
  "stage": "项目当前所处阶段（如：前期沟通/方案设计/开发中/测试/已上线/收尾/暂停/未知）",
  "contract": "合同中提到的金额、签约方等关键信息（如未发现合同信息则填'图谱中未发现合同相关信息'）",
  "key_dates": "关键时间节点（截止日期、里程碑、最近活跃时间等）",
  "core_people": "核心参与人员及其角色",
  "companies": "相关公司/组织及其参与方式",
  "recent_activity": "近期关键动态摘要（最近几封邮件的要点）"
}}

项目名：{project_name}
项目描述：{description}

请开始分析："""


def _clean(s: str) -> str:
    return (s or "").replace("<[^>]*>", "")


def _parse_analysis_response(text: str) -> dict | None:
    """Try to extract JSON from LLM response, handling markdown fences."""
    # Strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = re.sub(r"^```\w*\s*", "", text)
        # Remove closing fence
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        m = re.search(r"\{[^{}]*\"overview\"[^{}]*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None


def _build_summary(report: dict) -> dict:
    """Extract card-level summary from full report."""
    return {
        "overview": report.get("overview", ""),
        "stage": report.get("stage", ""),
        "key_dates": report.get("key_dates", ""),
        "core_people": report.get("core_people", ""),
    }


@router.get("", response_model=PaginatedProjects)
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List projects with pagination, including cached AI summaries."""
    from src.backend.storage.neo4j_client import get_projects_paginated

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: get_projects_paginated(page=page, page_size=page_size)
    )

    store = ProjectAnalysisStore()
    try:
        projects = []
        for p in result["projects"]:
            cached = store.get(p["name"])
            ai_summary = None
            if cached and cached.get("summary"):
                ai_summary = cached["summary"]

            projects.append(ProjectItem(
                name=_clean(p["name"]),
                description=_clean(p.get("description", ""))[:200],
                people=p.get("people", []),
                companies=p.get("companies", []),
                ai_summary=ProjectSummary(**ai_summary) if ai_summary else None,
            ))

        return PaginatedProjects(
            projects=projects,
            total=result["total"],
            page=page,
            page_size=page_size,
        )
    finally:
        store.close()


@router.get("/{name:path}/analysis", response_model=ProjectAnalysisOut)
async def get_analysis(name: str):
    """Get cached project analysis. Returns 404 if not cached."""
    store = ProjectAnalysisStore()
    try:
        cached = store.get(name)
        if not cached:
            raise HTTPException(status_code=404, detail="No cached analysis for this project")

        return ProjectAnalysisOut(
            project_name=name,
            summary=ProjectSummary(**cached["summary"]) if cached.get("summary") else None,
            report=ProjectReport(**cached["report"]) if cached.get("report") else None,
            generated_at=cached.get("generated_at", 0),
            cached=True,
        )
    finally:
        store.close()


@router.post("/{name:path}/analyze")
async def analyze_project(name: str, body: AnalyzeRequest = AnalyzeRequest()):
    """Generate AI analysis for a project (SSE streaming), cache to Redis.

    Events:
      - progress: status messages during generation
      - result: full report JSON
      - done: final complete signal
      - error: error message
    """
    from src.backend.ai.query_engine import QueryEngine

    # Get project description from Neo4j
    from src.backend.storage.neo4j_client import _get_driver

    description = ""
    driver = _get_driver()
    with driver.session() as session:
        r = session.run(
            "MATCH (n) WHERE n.entity_id = $name AND n.entity_type = 'project' "
            "RETURN coalesce(n.description, '') AS desc",
            name=name,
        ).single()
        if r:
            description = _clean(r["desc"] or "")[:300]

    prompt = body.question_override or ANALYSIS_PROMPT.format(
        project_name=name,
        description=description or "无额外描述",
    )

    queue: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        loop = asyncio.get_running_loop()

        def _emit(event: str, data: dict):
            loop.call_soon_threadsafe(queue.put_nowait, {"event": event, "data": data})

        def _run():
            try:
                _emit("progress", {"msg": f"🔍 正在分析项目「{name}」…"})
                engine = QueryEngine(account_id="default")
                result = engine.query(prompt, context={})

                answer = result.get("answer", "")
                _emit("progress", {"msg": "📊 正在解析分析结果…"})

                parsed = _parse_analysis_response(answer)
                if not parsed:
                    _emit("error", {"msg": "AI 返回格式异常，请重试"})
                    _emit("done", {})
                    return

                # Build structured report
                report = {
                    "overview": parsed.get("overview", ""),
                    "stage": parsed.get("stage", ""),
                    "contract": parsed.get("contract", ""),
                    "key_dates": parsed.get("key_dates", ""),
                    "core_people": parsed.get("core_people", ""),
                    "companies": parsed.get("companies", ""),
                    "recent_activity": parsed.get("recent_activity", ""),
                }
                summary = _build_summary(report)

                # Cache to Redis
                store = ProjectAnalysisStore()
                try:
                    store.save(name, summary, report)
                finally:
                    store.close()

                _emit("result", {
                    "project_name": name,
                    "summary": summary,
                    "report": report,
                })
            except Exception as exc:
                logger.exception("Project analysis failed")
                _emit("error", {"msg": str(exc)})
            finally:
                _emit("done", {})
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.ensure_future(
            asyncio.get_running_loop().run_in_executor(None, _run)
        )

        while True:
            item = await queue.get()
            if item is None:
                break
            event_type = item.get("event", "progress")
            data = item.get("data", item)
            yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 2: Verify the module imports**

Run: `python -c "from src.backend.routes.projects import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/backend/routes/projects.py
git commit -m "feat: add /api/projects routes — list, analyze (SSE), get analysis"
```

---

### Task 5: Backend — Register Projects Router

**Files:**
- Modify: `src/backend/app.py` — add import and router registration

- [ ] **Step 1: Register the router**

In `src/backend/app.py`, change line 18 from:
```python
from src.backend.routes import accounts, conversations, graph, mails, query, status
```
to:
```python
from src.backend.routes import accounts, conversations, graph, mails, projects, query, status
```

Add after line 82 (`app.include_router(graph.router)`):
```python
app.include_router(projects.router)
```

- [ ] **Step 2: Verify app starts without errors**

Run: `python -c "from src.backend.app import app; print('App loaded OK, routes:', len(app.routes))"`
Expected: `App loaded OK, routes: <number>`

- [ ] **Step 3: Commit**

```bash
git add src/backend/app.py
git commit -m "feat: register projects router in FastAPI app"
```

---

### Task 6: Frontend — API Types and Functions

**Files:**
- Modify: `src/web/src/api/index.ts` — append projects API after line 308 (before closing)

**Interfaces:**
- Consumes: none (standalone)
- Produces: `ProjectSummary`, `ProjectReport`, `ProjectAnalysis`, `ProjectItem`, `PaginatedProjects`, `projectsApi`

- [ ] **Step 1: Add projects API section**

Append to `src/web/src/api/index.ts` before the final export block:

```typescript
// ═══════════════════════════════════════════════════════════════
// Project API
// ═══════════════════════════════════════════════════════════════

export interface ProjectSummary {
  overview: string
  stage: string
  key_dates: string
  core_people: string[]
}

export interface ProjectReport {
  overview: string
  stage: string
  contract: string
  key_dates: string
  core_people: string
  companies: string
  recent_activity: string
}

export interface ProjectAnalysis {
  project_name: string
  summary: ProjectSummary | null
  report: ProjectReport | null
  generated_at: number
  cached: boolean
}

export interface ProjectItem {
  name: string
  description: string
  people: { name: string }[]
  companies: { name: string }[]
  ai_summary: ProjectSummary | null
}

export interface PaginatedProjects {
  projects: ProjectItem[]
  total: number
  page: number
  page_size: number
}

export const projectsApi = {
  list: (page = 1, pageSize = 20) =>
    request<PaginatedProjects>(`/projects?page=${page}&page_size=${pageSize}`),

  getAnalysis: (name: string) =>
    request<ProjectAnalysis>(`/projects/${encodeURIComponent(name)}/analysis`),

  analyze: (name: string, handlers: Parameters<typeof sseStream>[2]) =>
    sseStream(`/projects/${encodeURIComponent(name)}/analyze`, {}, handlers),
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd src/web && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No new errors (existing errors may exist from other files)

- [ ] **Step 3: Commit**

```bash
git add src/web/src/api/index.ts
git commit -m "feat: add projectsApi with types for dashboard project analysis"
```

---

### Task 7: Frontend — Update ProjectCard Component

**Files:**
- Modify: `src/web/src/components/dashboard/ProjectCard.vue` — add AI summary section + action buttons

**Interfaces:**
- Consumes: `ProjectSummary` type from Task 6
- Produces: emits `view-report`, `chat-analyze` events

- [ ] **Step 1: Rewrite ProjectCard.vue**

Replace the entire file content:

```vue
<script setup lang="ts">
import SvgIcon from '@/components/SvgIcon.vue'
import type { ProjectSummary } from '@/api'

defineProps<{
  name: string
  description: string
  people: { name: string }[]
  companies: { name: string }[]
  aiSummary: ProjectSummary | null
}>()

defineEmits<{
  'view-report': [name: string]
  'chat-analyze': [name: string]
}>()
</script>

<template>
  <div class="project-card">
    <div class="pc-header">
      <div class="pc-icon">
        <SvgIcon name="project" :size="16" />
      </div>
      <h3 class="pc-name">{{ name }}</h3>
    </div>

    <!-- AI Summary section -->
    <div v-if="aiSummary" class="pc-ai-summary">
      <div class="ai-summary-row">
        <span class="ai-label">📌</span>
        <span class="ai-text">{{ aiSummary.overview }}</span>
      </div>
      <div class="ai-summary-row" v-if="aiSummary.stage">
        <span class="ai-label">📈</span>
        <span class="ai-stage-badge">{{ aiSummary.stage }}</span>
      </div>
      <div class="ai-summary-row" v-if="aiSummary.key_dates">
        <span class="ai-label">📅</span>
        <span class="ai-text">{{ aiSummary.key_dates }}</span>
      </div>
      <div class="ai-summary-row" v-if="aiSummary.core_people?.length">
        <span class="ai-label">👥</span>
        <span class="ai-text">{{ aiSummary.core_people.join('、') }}</span>
      </div>
    </div>

    <!-- Fallback description when no AI summary -->
    <p v-else class="pc-desc">{{ description || '图谱中暂无该项目的描述信息。' }}</p>

    <!-- People / Companies -->
    <div class="pc-meta">
      <div class="pc-meta-item" :class="{ empty: !people.length }">
        <SvgIcon name="user" :size="13" />
        <span>{{ people.map(p => p.name).join('、') || '暂无关联人员' }}</span>
      </div>
      <div v-if="companies.length" class="pc-meta-item">
        <SvgIcon name="building" :size="13" />
        <span>{{ companies.map(c => c.name).join('、') }}</span>
      </div>
    </div>

    <!-- Action buttons -->
    <div class="pc-actions">
      <button class="pc-btn pc-btn-primary" @click="$emit('view-report', name)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
        </svg>
        {{ aiSummary ? '查看报告' : 'AI 分析' }}
      </button>
      <button class="pc-btn pc-btn-ghost" @click="$emit('chat-analyze', name)" title="在 Chat 中深度分析">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        Chat 分析
      </button>
    </div>
  </div>
</template>

<style scoped>
.project-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 1.15rem 1.25rem;
  transition: box-shadow 0.15s, border-color 0.15s, transform 0.12s;
  display: flex;
  flex-direction: column;
}

.project-card:hover {
  box-shadow: var(--sh-md);
  border-color: var(--t5);
  transform: translateY(-1px);
}

.pc-header {
  display: flex; align-items: center; gap: 0.55rem;
  margin-bottom: 0.5rem;
}

.pc-icon {
  width: 28px; height: 28px;
  border-radius: 7px;
  background: #FEF2F2;
  color: #9A3B2E;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}

.pc-name {
  font-size: 0.92rem; font-weight: 620; color: var(--t1);
  line-height: 1.3; margin: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* AI Summary */
.pc-ai-summary {
  background: var(--p-bg);
  border: 1px solid color-mix(in srgb, var(--p) 15%, transparent);
  border-radius: 8px;
  padding: 0.55rem 0.65rem;
  margin-bottom: 0.65rem;
  flex: 1;
}

.ai-summary-row {
  display: flex;
  gap: 4px;
  font-size: 0.73rem;
  line-height: 1.45;
  margin-bottom: 0.15rem;
}
.ai-summary-row:last-child { margin-bottom: 0; }

.ai-label {
  flex-shrink: 0;
  font-size: 0.7rem;
}

.ai-text {
  color: var(--t2);
}

.ai-stage-badge {
  display: inline-block;
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--p-text);
  background: color-mix(in srgb, var(--p) 15%, transparent);
  padding: 0.05rem 0.45rem;
  border-radius: 999px;
}

/* Description fallback */
.pc-desc {
  font-size: 0.78rem; color: var(--t3); line-height: 1.55;
  margin-bottom: 0.7rem;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden;
  flex: 1;
}

/* Meta */
.pc-meta {
  display: flex; flex-direction: column; gap: 0.3rem;
  padding-top: 0.55rem;
  border-top: 1px solid var(--border-light);
}

.pc-meta-item {
  display: flex; align-items: center; gap: 5px;
  font-size: 0.73rem; color: var(--t3);
}

.pc-meta-item.empty {
  color: var(--t4);
}

/* Actions */
.pc-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.6rem;
  padding-top: 0.55rem;
  border-top: 1px solid var(--border-light);
}

.pc-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.73rem;
  font-weight: 520;
  padding: 0.3rem 0.7rem;
  border-radius: 7px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
  line-height: 1.3;
}

.pc-btn-primary {
  background: var(--p);
  color: #fff;
  border-color: var(--p);
  flex: 1;
  justify-content: center;
}

.pc-btn-primary:hover {
  background: var(--p-hover);
  border-color: var(--p-hover);
}

.pc-btn-ghost {
  background: var(--surface);
  color: var(--t3);
}

.pc-btn-ghost:hover {
  background: var(--surface-2);
  border-color: var(--p);
  color: var(--p);
}
</style>
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd src/web && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No new errors related to ProjectCard

- [ ] **Step 3: Commit**

```bash
git add src/web/src/components/dashboard/ProjectCard.vue
git commit -m "feat: add AI summary + action buttons to ProjectCard"
```

---

### Task 8: Frontend — ProjectReportModal Component

**Files:**
- Create: `src/web/src/components/dashboard/ProjectReportModal.vue`

**Interfaces:**
- Consumes: `ProjectReport`, `ProjectSummary` from Task 6
- Produces: emits `close`, `chat-analyze` events; props: `visible`, `projectName`, `report`, `summary`, `loading`

- [ ] **Step 1: Create the modal component**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import type { ProjectReport, ProjectSummary } from '@/api'

const props = defineProps<{
  visible: boolean
  projectName: string
  report: ProjectReport | null
  summary: ProjectSummary | null
  loading: boolean
}>()

defineEmits<{
  close: []
  'chat-analyze': [name: string]
}>()

const sections = computed(() => {
  if (!props.report) return []
  return [
    { icon: '📌', label: '一句话概述', content: props.report.overview },
    { icon: '📈', label: '项目阶段/状态', content: props.report.stage },
    { icon: '💰', label: '合同与金额', content: props.report.contract },
    { icon: '📅', label: '关键时间节点', content: props.report.key_dates },
    { icon: '👥', label: '核心人员', content: props.report.core_people },
    { icon: '🏢', label: '相关公司/组织', content: props.report.companies },
    { icon: '📝', label: '近期关键动态', content: props.report.recent_activity },
  ].filter(s => s.content)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="report-modal">
      <div v-if="visible" class="report-overlay" @click.self="$emit('close')">
        <div class="report-modal">
          <!-- Header -->
          <div class="report-header">
            <div class="report-title">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
              </svg>
              <span>{{ projectName }} — AI 分析报告</span>
            </div>
            <button class="report-close" @click="$emit('close')">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>

          <!-- Body -->
          <div class="report-body">
            <!-- Loading state -->
            <div v-if="loading" class="report-loading">
              <div class="loading-spinner"></div>
              <p>AI 正在分析项目「{{ projectName }}」…</p>
              <p class="loading-hint">正在从知识图谱中提取邮件、人员、合同等关键信息</p>
            </div>

            <!-- Report content -->
            <div v-else-if="report">
              <div v-for="s in sections" :key="s.label" class="report-section">
                <div class="report-section-title">
                  <span class="rs-icon">{{ s.icon }}</span>
                  <span>{{ s.label }}</span>
                </div>
                <p class="report-section-content">{{ s.content }}</p>
              </div>

              <!-- Empty state if no sections -->
              <div v-if="!sections.length" class="report-empty">
                报告内容为空，请重新生成。
              </div>
            </div>
          </div>

          <!-- Footer -->
          <div class="report-footer" v-if="!loading && report">
            <p class="footer-hint">不满意这份报告？可以在 Chat 中进行更深入的对话分析。</p>
            <button class="footer-chat-btn" @click="$emit('chat-analyze', projectName)">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
              在 Chat 中深度分析 →
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Overlay */
.report-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

/* Modal */
.report-modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  box-shadow: var(--sh-lg);
  width: 100%;
  max-width: 680px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Header */
.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.report-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.95rem;
  font-weight: 650;
  color: var(--t1);
}
.report-title svg { opacity: 0.7; color: var(--p); }

.report-close {
  width: 32px; height: 32px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--t3);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.report-close:hover { background: var(--border); color: var(--t1); }

/* Body */
.report-body {
  flex: 1;
  overflow-y: auto;
  padding: 1.25rem;
}

/* Loading */
.report-loading {
  text-align: center;
  padding: 2.5rem 0;
}

.loading-spinner {
  width: 36px; height: 36px;
  border: 3px solid var(--border-light);
  border-top-color: var(--p);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 1rem;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.report-loading p {
  color: var(--t2);
  font-size: 0.88rem;
  margin: 0;
}

.loading-hint {
  color: var(--t4) !important;
  font-size: 0.78rem !important;
  margin-top: 0.35rem !important;
}

/* Sections */
.report-section {
  margin-bottom: 1.1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border-light);
}
.report-section:last-child { border-bottom: none; margin-bottom: 0; }

.report-section-title {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.84rem;
  font-weight: 650;
  color: var(--t1);
  margin-bottom: 0.4rem;
}

.rs-icon { font-size: 0.85rem; }

.report-section-content {
  font-size: 0.84rem;
  color: var(--t2);
  line-height: 1.65;
  margin: 0;
  white-space: pre-wrap;
}

.report-empty {
  text-align: center;
  color: var(--t4);
  padding: 2rem 0;
}

/* Footer */
.report-footer {
  padding: 0.9rem 1.25rem;
  border-top: 1px solid var(--border);
  background: var(--surface-2);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.footer-hint {
  font-size: 0.76rem;
  color: var(--t4);
  margin: 0;
}

.footer-chat-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 0.8rem;
  font-weight: 550;
  padding: 0.4rem 0.9rem;
  border-radius: 8px;
  border: 1px solid var(--p);
  background: var(--p-bg);
  color: var(--p);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
}

.footer-chat-btn:hover {
  background: var(--p);
  color: #fff;
}

/* Transitions */
.report-modal-enter-active { transition: opacity 0.2s ease; }
.report-modal-leave-active { transition: opacity 0.15s ease; }
.report-modal-enter-from,
.report-modal-leave-to { opacity: 0; }
.report-modal-enter-from .report-modal { transform: scale(0.96); transition: transform 0.2s ease; }
.report-modal-leave-to .report-modal { transform: scale(0.96); transition: transform 0.15s ease; }
</style>
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd src/web && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No new errors

- [ ] **Step 3: Commit**

```bash
git add src/web/src/components/dashboard/ProjectReportModal.vue
git commit -m "feat: add ProjectReportModal — 7-dimension AI analysis report"
```

---

### Task 9: Frontend — Update DashboardPage (Pagination + Integration)

**Files:**
- Modify: `src/web/src/pages/DashboardPage.vue` — replace entire script and template

**Interfaces:**
- Consumes: `projectsApi`, `ProjectItem`, `ProjectReport`, `ProjectSummary` from Task 6; `ProjectCard` from Task 7; `ProjectReportModal` from Task 8
- Produces: renders paginated dashboard with analysis flow

- [ ] **Step 1: Rewrite DashboardPage.vue**

Replace the entire file:

```vue
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { mailsApi, graphApi, projectsApi, type MailStats, type ProjectItem, type ProjectReport, type ProjectSummary } from '@/api'
import { useChatStore } from '@/stores/chat'
import SvgIcon from '@/components/SvgIcon.vue'
import KpiCards from '@/components/dashboard/KpiCards.vue'
import ProjectCard from '@/components/dashboard/ProjectCard.vue'
import ProjectReportModal from '@/components/dashboard/ProjectReportModal.vue'

const router = useRouter()
const chatStore = useChatStore()

const PAGE_SIZE = 20

const kpi = ref<MailStats>({ total: 0, done: 0, pending: 0, failed: 0, skipped: 0, ingested: 0, indexed: 0 })
const graphNodes = ref(0)
const projectCount = ref(0)
const contactCount = ref(0)
const projects = ref<ProjectItem[]>([])
const totalProjects = ref(0)
const currentPage = ref(1)
const search = ref('')
const loading = ref(true)

// Report modal state
const modalVisible = ref(false)
const modalProjectName = ref('')
const modalReport = ref<ProjectReport | null>(null)
const modalSummary = ref<ProjectSummary | null>(null)
const modalLoading = ref(false)

const totalPages = computed(() => Math.max(1, Math.ceil(totalProjects.value / PAGE_SIZE)))

const PEOPLE_TYPES = ['person', 'contact', 'employee']
const etype = (e: any) => String(e?.type || '').toLowerCase()

onMounted(async () => {
  await loadDashboard()
})

async function loadDashboard() {
  loading.value = true
  try {
    const [stats, entRes] = await Promise.all([
      mailsApi.stats(),
      graphApi.entities(1, 500),
    ])
    kpi.value = stats
    const entities = entRes.entities || []
    graphNodes.value = entities.length
    projectCount.value = entities.filter((e: any) => etype(e) === 'project').length
    contactCount.value = entities.filter((e: any) => PEOPLE_TYPES.includes(etype(e))).length

    await loadProjects(currentPage.value)
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function loadProjects(page: number) {
  try {
    const res = await projectsApi.list(page, PAGE_SIZE)
    projects.value = res.projects
    totalProjects.value = res.total
    currentPage.value = res.page
  } catch (e) {
    console.error('Failed to load projects:', e)
  }
}

async function goToPage(page: number) {
  if (page < 1 || page > totalPages.value || page === currentPage.value) return
  await loadProjects(page)
}

const filtered = computed(() => {
  if (!search.value) return projects.value
  const q = search.value.toLowerCase()
  return projects.value.filter((p: ProjectItem) => p.name.toLowerCase().includes(q))
})

// ── Report modal ──

async function handleViewReport(name: string) {
  modalProjectName.value = name
  modalVisible.value = true

  // Try cached first
  try {
    const cached = await projectsApi.getAnalysis(name)
    if (cached.cached && cached.report) {
      modalReport.value = cached.report
      modalSummary.value = cached.summary
      modalLoading.value = false
      return
    }
  } catch {
    // Not cached — proceed to generate
  }

  // Generate new analysis via SSE
  modalLoading.value = true
  modalReport.value = null
  modalSummary.value = null

  projectsApi.analyze(name, {
    onProgress(data: any) {
      // Could show progress messages if desired
    },
    onComplete(data: any) {
      if (data.report) {
        modalReport.value = data.report as ProjectReport
      }
      if (data.summary) {
        modalSummary.value = data.summary as ProjectSummary
      }
      modalLoading.value = false
      // Refresh current page to get updated ai_summary on cards
      loadProjects(currentPage.value)
    },
    onError(msg: string) {
      console.error('Analysis failed:', msg)
      modalLoading.value = false
    },
  })
}

function handleCloseModal() {
  modalVisible.value = false
  modalReport.value = null
  modalSummary.value = null
  modalLoading.value = false
}

// ── Chat deep analysis ──

async function handleChatAnalyze(name: string) {
  const project = projects.value.find(p => p.name === name)
  const overview = project?.ai_summary?.overview || project?.description || ''

  const prompt = `请帮我深入分析项目「${name}」。\n\n项目概述：${overview}\n\n请从知识图谱中提取更多细节，包括邮件往来、合同金额、时间线、风险点等。`

  // Create a new conversation and navigate
  try {
    const { conversationsApi } = await import('@/api')
    const session = await conversationsApi.create(`分析: ${name}`)
    // Navigate with prefill prompt
    router.push({ path: '/chat', query: { prompt, session: session.id } })
  } catch {
    // Fallback: just navigate with prompt
    router.push({ path: '/chat', query: { prompt } })
  }
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2>项目看板</h2>
        <p class="page-desc">基于邮件内容自动识别的项目、人员与组织关系</p>
      </div>
      <div class="head-actions">
        <div class="search-wrap" v-if="projects.length > 0">
          <SvgIcon name="search" :size="15" class="search-icon" />
          <input
            v-model="search"
            type="text"
            placeholder="搜索项目..."
            class="search-input-inline"
          />
        </div>
        <button class="btn btn-secondary btn-sm refresh-btn" :disabled="loading" @click="loadDashboard">
          🔄 {{ loading ? '刷新中…' : '界面刷新' }}
        </button>
      </div>
    </div>

    <KpiCards
      :total-mails="kpi.total"
      :done-mails="kpi.done"
      :graph-nodes="graphNodes"
      :projects="projectCount"
      :contacts="contactCount"
    />

    <!-- Loading skeleton -->
    <div v-if="loading" class="skeleton-grid">
      <div v-for="i in 6" :key="i" class="skeleton-card">
        <div class="sk-line sk-title"></div>
        <div class="sk-line sk-body"></div>
        <div class="sk-line sk-body short"></div>
        <div class="sk-line sk-meta"></div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else-if="!projects.length" class="empty-state">
      <div class="empty-icon">
        <SvgIcon name="inbox" :size="32" />
      </div>
      <h3>暂无项目数据</h3>
      <p>请先在「邮件工作台」拉取邮件并导入到知识图谱，系统将自动识别项目信息。</p>
    </div>

    <!-- Project grid + Pagination -->
    <template v-else>
      <div class="project-grid">
        <ProjectCard
          v-for="p in filtered()"
          :key="p.name"
          :name="p.name"
          :description="p.description"
          :people="p.people"
          :companies="p.companies"
          :ai-summary="p.ai_summary"
          @view-report="handleViewReport"
          @chat-analyze="handleChatAnalyze"
        />
      </div>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="pagination">
        <button
          class="page-btn"
          :disabled="currentPage <= 1"
          @click="goToPage(currentPage - 1)"
        >
          ← 上一页
        </button>
        <span class="page-info">
          {{ currentPage }} / {{ totalPages }}
          <span class="page-total">（共 {{ totalProjects }} 个项目）</span>
        </span>
        <button
          class="page-btn"
          :disabled="currentPage >= totalPages"
          @click="goToPage(currentPage + 1)"
        >
          下一页 →
        </button>
      </div>
    </template>

    <p v-if="!loading && projects.length > 0 && !filtered().length" class="text-muted" style="text-align:center;margin-top:2rem;">
      没有匹配「{{ search }}」的项目
    </p>

    <!-- Report Modal -->
    <ProjectReportModal
      :visible="modalVisible"
      :project-name="modalProjectName"
      :report="modalReport"
      :summary="modalSummary"
      :loading="modalLoading"
      @close="handleCloseModal"
      @chat-analyze="(name: string) => { handleCloseModal(); handleChatAnalyze(name) }"
    />
  </div>
</template>

<style scoped>
/* Reuse existing styles, add pagination */
.page-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 1.25rem; gap: 1rem; flex-wrap: wrap;
}

.page-desc {
  color: var(--t3); font-size: 0.82rem; margin-top: 0.15rem;
}

.search-wrap {
  position: relative;
  flex-shrink: 0;
}

.head-actions {
  display: flex; align-items: center; gap: 0.6rem;
  flex-shrink: 0;
}
.refresh-btn { flex-shrink: 0; white-space: nowrap; }

.search-icon {
  position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  color: var(--t4); pointer-events: none;
}

.search-input-inline {
  padding-left: 2rem !important;
  max-width: 240px;
  font-size: 0.8rem !important;
  height: 34px;
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 0.85rem;
}

/* Pagination */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  margin-top: 1.5rem;
  padding-top: 1rem;
}

.page-btn {
  font-size: 0.8rem;
  padding: 0.35rem 0.85rem;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--t2);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}

.page-btn:hover:not(:disabled) {
  border-color: var(--p);
  color: var(--p);
  background: var(--p-bg);
}

.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.page-info {
  font-size: 0.82rem;
  color: var(--t2);
  font-weight: 520;
}

.page-total {
  font-weight: 400;
  color: var(--t4);
}

/* Skeleton loading */
.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 0.85rem;
}

.skeleton-card {
  background: var(--surface);
  border: 1px solid var(--border-light);
  border-radius: var(--r);
  padding: 1.15rem 1.25rem;
}

.sk-line {
  height: 12px; border-radius: 4px;
  background: var(--border-light);
  margin-bottom: 0.55rem;
  animation: shimmer 1.6s infinite;
}

.sk-title { width: 55%; height: 15px; }
.sk-body { width: 90%; }
.sk-body.short { width: 65%; }
.sk-meta { width: 40%; height: 10px; }

@keyframes shimmer {
  0% { opacity: 0.5; }
  50% { opacity: 1; }
  100% { opacity: 0.5; }
}

/* Empty state */
.empty-state {
  text-align: center; padding: 3rem 1.5rem;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r);
}

.empty-icon {
  width: 56px; height: 56px; border-radius: 14px;
  background: var(--surface-2); color: var(--t4);
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto 1rem;
}

.empty-state h3 {
  color: var(--t2); margin-bottom: 0.35rem;
}

.empty-state p {
  color: var(--t4); font-size: 0.82rem; max-width: 380px;
  margin: 0 auto; line-height: 1.5;
}
</style>
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd src/web && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No new errors

- [ ] **Step 3: Commit**

```bash
git add src/web/src/pages/DashboardPage.vue
git commit -m "feat: add pagination, AI analysis flow, report modal integration to DashboardPage"
```

---

### Task 10: Frontend — ChatPage Query Param Support

**Files:**
- Modify: `src/web/src/pages/ChatPage.vue` — read `route.query.prompt` on mount

**Interfaces:**
- Consumes: Vue Router `useRoute()` for query params
- Produces: auto-fills ChatInput when navigated with prompt param

- [ ] **Step 1: Add query param handling**

In `src/web/src/pages/ChatPage.vue`, add the route import at line 2 (after the `import { onMounted...` line):

```typescript
import { useRoute } from 'vue-router'
```

Add after the existing variable declarations (after line 13, before `const hasMessages`):

```typescript
const route = useRoute()
```

In the `onMounted` block (starting at line 44), modify to handle the prompt param:

```typescript
onMounted(async () => {
  await chatStore.fetchSessions()

  // Check if arriving from dashboard with a prefill prompt
  const prefillPrompt = route.query.prompt as string | undefined
  const targetSession = route.query.session as string | undefined

  if (targetSession) {
    chatStore.activeSessionId = targetSession
    await chatStore.fetchSessions()
    await chatStore.loadMessages(targetSession)
  } else {
    await chatStore.ensureSession()
  }

  if (chatStore.activeSessionId && !targetSession) {
    await chatStore.loadMessages(chatStore.activeSessionId)
  } else if (chatStore.activeSessionId) {
    // Session already loaded via targetSession above
  } else {
    await chatStore.ensureSession()
    if (chatStore.activeSessionId) {
      await chatStore.loadMessages(chatStore.activeSessionId)
    }
  }

  await chatStore.loadMemory()

  // Auto-fill the input with prefill prompt
  if (prefillPrompt) {
    await nextTick()
    // Small delay to ensure ChatInput is mounted
    setTimeout(() => {
      chatInputRef.value?.setText(prefillPrompt)
    }, 200)
  }
})
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd src/web && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: No new errors

- [ ] **Step 3: Commit**

```bash
git add src/web/src/pages/ChatPage.vue
git commit -m "feat: support prefill prompt via ?prompt= query param in ChatPage"
```

---

### Task 11: End-to-End Verification

- [ ] **Step 1: Start the backend and verify new endpoints**

Run: `curl -s http://localhost:8000/api/projects?page=1&page_size=2 | python -m json.tool | head -20`
Expected: JSON response with `projects`, `total`, `page`, `page_size` fields

- [ ] **Step 2: Verify analysis cache read/write**

Run: `curl -s -X POST http://localhost:8000/api/projects/TestProject/analyze | head -5`
Expected: SSE stream starting with `event: progress` or `event: error`

- [ ] **Step 3: Verify frontend builds**

Run: `cd src/web && npm run build 2>&1 | tail -5`
Expected: Build succeeds

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: final verification of dashboard project analysis feature"
```
