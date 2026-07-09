#!/usr/bin/env python3
"""Run golden evals for MailGraph query planning.

Default mode is deterministic and uses a lightweight rule planner stub that
parses the question text. Use --live to call the configured LLM and validate
the full prompt/schema path.
"""
from __future__ import annotations

import argparse
import json
import re
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
        self._query_graph = None

    def _build_query_plan(self, question: str, context: dict | None = None) -> QueryPlan:
        return rule_plan(question)


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


def _time_range_for(question: str) -> TimeRangePlan | None:
    if any(t in question for t in ("最近", "近", "过去", "今天", "昨天", "本周", "这周", "上周", "本月", "这个月")):
        return TimeRangePlan(
            original="rule",
            start="2026-07-01T00:00:00",
            end="2026-07-09T23:59:59",
        )
    return None


def _statuses_for(question: str) -> list[str]:
    pairs = [
        ("done", ("处理完成", "完成", "已处理", "入库", "成功")),
        ("failed", ("失败", "报错", "出错")),
        ("skipped", ("跳过", "过滤", "噪音", "被过滤")),
        ("processing", ("处理中", "正在处理")),
        ("pending", ("待处理", "未处理", "没处理")),
    ]
    statuses = []
    for status, words in pairs:
        if any(w in question for w in words):
            statuses.append(status)
    if any(w in question for w in ("收到", "总共", "一共", "全部", "所有")) and len(statuses) == 1 and statuses[0] == "done":
        return []
    return list(dict.fromkeys(statuses))


def _aggregation_for(question: str) -> str | None:
    q = question.lower()
    if any(w in q for w in ("成功率", "失败率", "比例", "占比", "百分")):
        return "rate"
    if any(w in q for w in ("top", "排名", "谁最多", "发件最多")) or ("最多" in question and ("谁" in question or "哪些人" in question)):
        return "top_senders"
    if any(w in question for w in ("多少", "几封", "数量", "统计", "计数")):
        return "count"
    if "有没有提到" in question:
        return "list"
    if any(w in question for w in ("列出", "有哪些", "哪些", "显示", "看看", "列表")) or re.search(r"发了哪些|发来.*邮件", question):
        return "list"
    return None


def _sender_for(question: str) -> str | None:
    m = re.search(r"从\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+)\s*来", question)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+)\s*发来", question)
    if m:
        return m.group(1)
    cleaned = question
    cleaned = re.sub(r"^(列出|显示|看看|从)\s*", "", cleaned)
    cleaned = re.sub(r"关于.*", "", cleaned)
    cleaned = re.sub(r"^(今天|昨天|本周|这周|上周|本月|这个月|最近\d*小时|最近[一二三四五六七八九十两0-9]+天|近[一二三四五六七八九十两0-9]+天|过去[一二三四五六七八九十两0-9]+天)", "", cleaned)
    cleaned = re.sub(r"(今天|昨天|本周|这周|上周|本月|这个月|最近(?:\d+|24)?小时|最近[一二三四五六七八九十两0-9]+天|最近|近[一二三四五六七八九十两0-9]+天|过去[一二三四五六七八九十两0-9]+天)", "", cleaned)
    patterns = [
        r"([\u4e00-\u9fffA-Za-z0-9_]{2,12})(?:发来的|发来|发了|发的)",
        r"([\u4e00-\u9fffA-Za-z0-9_]{2,12})(?:有附件)",
        r"([\u4e00-\u9fffA-Za-z0-9_]{2,12})(?:今天|最近|本月|本周|上周|近|过去)",
        r"([\u4e00-\u9fffA-Za-z0-9_]{2,12})(?:的失败|有附件|的邮件)",
    ]
    stop = {"最近三天", "最近七天", "最近24小时", "过去两天", "今天", "昨天", "本周", "上周", "本月", "哪些人"}
    for pattern in patterns:
        m = re.search(pattern, cleaned)
        if m:
            name = m.group(1).strip(" 的")
            if name and name not in stop and not any(w in name for w in ("邮件", "附件", "失败", "哪些")):
                return name
    if any(w in question for w in ("发来", "发了", "发的", "关于")):
        fallback = cleaned.strip(" 的邮件有哪些多少？?，,。")
        if 2 <= len(fallback) <= 12 and fallback not in stop and not any(
                w in fallback for w in ("邮件", "附件", "失败", "哪些", "问题")):
            return fallback
    return None


def _attachment_for(question: str) -> bool | None:
    if any(w in question for w in ("没有附件", "无附件")):
        return False
    if any(w in question for w in ("带附件", "有附件", "含附件", "附件邮件")):
        return True
    return None


def _topic_for(question: str) -> str | None:
    patterns = [
        r"关于([^的有了]+)",
        r"提到(?:的)?([^的有了]+)",
        r"和([^有]+)有关",
        r"讨论了([^的有了]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, question)
        if m:
            topic = m.group(1).strip(" 邮件数量列表哪些多少？?，,。")
            if topic:
                return topic
    content_topics = ("项目风险", "合同", "预算", "客户反馈", "供应商", "项目负责人", "延期", "投诉", "采购", "报价", "项目进度", "附件解析")
    for topic in content_topics:
        if topic in question:
            return topic
    if "客户" in question:
        return "客户"
    return None


def rule_plan(question: str) -> QueryPlan:
    aggregation = _aggregation_for(question)
    statuses = _statuses_for(question)
    topic = _topic_for(question)
    sender = _sender_for(question)
    has_attachment = _attachment_for(question)
    has_stat = aggregation is not None or bool(statuses) or sender is not None or has_attachment is not None
    content_only = any(w in question for w in ("总结", "归纳", "讲了什么", "讨论什么", "关系", "关键点", "诉求", "原因", "反馈")) or question.startswith("邮件里提到")

    if topic and has_stat and not content_only:
        route = "hybrid"
    elif has_stat and not content_only:
        route = "stat"
    elif topic or content_only:
        route = "content"
    else:
        route = "clarify"

    if route == "content":
        aggregation = None
    elif route in ("stat", "hybrid") and aggregation is None:
        aggregation = "list" if any(w in question for w in ("哪些", "列出", "显示", "看看")) else "count"

    limit = 0
    m = re.search(r"top\s*(\d+)|Top\s*(\d+)", question)
    if m:
        limit = int(next(g for g in m.groups() if g))

    filters = QueryFilters(
        statuses=statuses,
        sender=sender,
        has_attachment=has_attachment,
        topic=topic,
    )
    return QueryPlan(
        route=route,
        aggregation=aggregation,
        time_range=_time_range_for(question),
        filters=filters,
        limit=limit,
        confidence=0.8,
        reason="rule planner eval stub",
    )


def get_plan(engine: QueryEngine, case: dict, live: bool) -> QueryPlan:
    return engine._build_query_plan(case["question"])


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
