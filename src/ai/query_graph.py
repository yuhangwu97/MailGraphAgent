"""Optional LangGraph orchestration for QueryEngine.

The core QueryEngine remains usable without LangGraph installed. This module
builds the production workflow when the dependency is available.
"""
from __future__ import annotations

import time
from typing import Any, TypedDict


class QueryState(TypedDict, total=False):
    engine: Any
    question: str
    context: dict
    start_time: float
    plan: Any
    result: dict


def _plan(state: QueryState) -> QueryState:
    engine = state["engine"]
    state["plan"] = engine._build_query_plan(
        state["question"], state.get("context") or {})
    return state


def _validate(state: QueryState) -> QueryState:
    engine = state["engine"]
    state["plan"] = engine._sanitize_plan(state["plan"])
    return state


def _route(state: QueryState) -> str:
    return state["plan"].route


def _stat(state: QueryState) -> QueryState:
    engine = state["engine"]
    state["result"] = engine._execute_stat_query(
        state["question"], state["plan"], state["start_time"],
        context=state.get("context") or {})
    return state


def _content(state: QueryState) -> QueryState:
    engine = state["engine"]
    state["result"] = engine._execute_content_query(
        state["question"], state["plan"], state["start_time"],
        context=state.get("context") or {})
    return state


def _hybrid(state: QueryState) -> QueryState:
    engine = state["engine"]
    state["result"] = engine._execute_hybrid_query(
        state["question"], state["plan"], state["start_time"],
        context=state.get("context") or {})
    return state


def _clarify(state: QueryState) -> QueryState:
    engine = state["engine"]
    state["result"] = engine._clarify_response(
        state["question"], state["plan"], state["start_time"])
    return state


def _verify(state: QueryState) -> QueryState:
    result = state.get("result") or {}
    result.setdefault("error", "")
    result.setdefault("trace", [])
    result.setdefault("rows", [])
    result.setdefault("columns", [])
    result.setdefault("entities", [])
    result.setdefault("relationships", [])
    result.setdefault("chunks", [])
    result.setdefault("total_rows", len(result.get("rows") or []))
    result.setdefault("total_duration_ms", int((time.time() - state["start_time"]) * 1000))
    state["result"] = result
    return state


def build_query_graph():
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(QueryState)
    graph.add_node("plan", _plan)
    graph.add_node("validate", _validate)
    graph.add_node("stat", _stat)
    graph.add_node("content", _content)
    graph.add_node("hybrid", _hybrid)
    graph.add_node("clarify", _clarify)
    graph.add_node("verify", _verify)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "validate")
    graph.add_conditional_edges(
        "validate",
        _route,
        {
            "stat": "stat",
            "content": "content",
            "hybrid": "hybrid",
            "clarify": "clarify",
        },
    )
    for node in ("stat", "content", "hybrid", "clarify"):
        graph.add_edge(node, "verify")
    graph.add_edge("verify", END)
    return graph.compile()


def run_query_graph(engine, question: str) -> dict:
    graph = build_query_graph()
    state = graph.invoke({
        "engine": engine,
        "question": question,
        "context": {},
        "start_time": time.time(),
    })
    return state["result"]
