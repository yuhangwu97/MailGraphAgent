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
