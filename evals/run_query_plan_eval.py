#!/usr/bin/env python3
"""Run golden evals for MailGraph query planning.

Default mode is deterministic and calls only local planner repair logic. Use
--live to call the configured LLM and validate the full prompt/schema path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.query_engine import QueryEngine, QueryFilters, QueryPlan, TimeRangePlan  # noqa: E402


class EvalEngine(QueryEngine):
    def __init__(self):
        self.llm = None
        self.model = "eval"
        self.rf = None
        self._account_id = None
        self._cache = None


def load_cases(path: Path) -> list[dict]:
    cases = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {e}") from e
    return cases


def fixture_plan(question: str, expect: dict) -> QueryPlan:
    filters = QueryFilters(
        statuses=expect.get("statuses", []),
        sender=expect.get("sender"),
        has_attachment=expect.get("has_attachment"),
        topic=expect.get("topic"),
    )
    return QueryPlan(
        route=expect["route"],
        aggregation=expect.get("aggregation"),
        time_range=TimeRangePlan(
            original="fixture",
            start="2026-07-01T00:00:00",
            end="2026-07-09T23:59:59",
        ) if expect.get("time_range") is True else None,
        filters=filters,
        limit=expect.get("limit", 0),
        confidence=0.95,
        reason=f"fixture for {question}",
    )


def get_plan(engine: QueryEngine, case: dict, live: bool) -> QueryPlan:
    if live:
        return engine._build_query_plan(case["question"])
    plan = fixture_plan(case["question"], case["expect"])
    return engine._repair_plan_with_precheck(case["question"], plan)


def check_case(plan: QueryPlan, expect: dict) -> list[str]:
    failures = []
    if plan.route != expect["route"]:
        failures.append(f"route expected {expect['route']} got {plan.route}")
    if "aggregation" in expect and plan.aggregation != expect["aggregation"]:
        failures.append(f"aggregation expected {expect['aggregation']} got {plan.aggregation}")
    if "statuses" in expect:
        got = set(plan.filters.statuses)
        want = set(expect["statuses"])
        if got != want:
            failures.append(f"statuses expected {sorted(want)} got {sorted(got)}")
    if "sender" in expect and (plan.filters.sender or "") != expect["sender"]:
        failures.append(f"sender expected {expect['sender']} got {plan.filters.sender}")
    if "has_attachment" in expect and plan.filters.has_attachment != expect["has_attachment"]:
        failures.append(f"has_attachment expected {expect['has_attachment']} got {plan.filters.has_attachment}")
    if "topic" in expect:
        got_topic = plan.filters.topic or ""
        if expect["topic"] not in got_topic and got_topic not in expect["topic"]:
            failures.append(f"topic expected ~{expect['topic']} got {plan.filters.topic}")
    if "limit" in expect and plan.limit != expect["limit"]:
        failures.append(f"limit expected {expect['limit']} got {plan.limit}")
    if expect.get("time_range") is True and plan.time_range is None:
        failures.append("time_range expected present got null")
    if expect.get("time_range") is False and plan.time_range is not None:
        failures.append("time_range expected null got present")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "evals" / "query_golden.jsonl"))
    parser.add_argument("--live", action="store_true", help="Call the configured LLM")
    args = parser.parse_args()

    cases = load_cases(Path(args.cases))
    engine: QueryEngine = QueryEngine() if args.live else EvalEngine()
    failed = []

    for case in cases:
        plan = get_plan(engine, case, args.live)
        failures = check_case(plan, case["expect"])
        if failures:
            failed.append((case, plan, failures))

    passed = len(cases) - len(failed)
    print(f"query-plan eval: {passed}/{len(cases)} passed")
    for case, plan, failures in failed[:20]:
        print(f"\n{case['id']}: {case['question']}")
        print("  " + "\n  ".join(failures))
        print(f"  plan={json.dumps(plan.model_dump(), ensure_ascii=False)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
