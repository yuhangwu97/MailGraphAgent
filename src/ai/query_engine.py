"""
智能查询引擎
============
接收自然语言问题，通过 LLM 意图分类路由到不同后端：

  stat_query     → MailCache（Redis 计数/列表/发件人排名/成功率）
  content_query  → RAGFlow GraphRAG（语义检索 + 图谱 + 生成）

意图由 LLM 做语义槽位提取——自然语言时间描述（"近三天"、"上周"）、
状态描述（"失败"、"待处理"）、发件人过滤等自动转为结构化查询参数。
"""
import json, logging, re, time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Literal
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from config.settings import get_settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 查询计划
# ═══════════════════════════════════════════════════════════════

QUERY_PLAN_SYSTEM_PROMPT = """你是一个生产级邮件查询规划器。分析用户问题，输出严格 JSON。

## 路由判定

**stat**（走数据库）— 以下任一类：
- 计数：问数量、多少、几条、几个
- 列表：列出、有哪些、哪些邮件、谁发了什么
- 排名：谁最多、Top N、发件排名
- 比率：成功率、失败率、比例、占比
- 进度：处理进度、完成情况

**content**（走知识图谱）— 以下任一类：
- 问邮件具体内容、某封邮件讲了什么
- 问项目、客户、人物关系、某个话题的讨论

**hybrid**（先数据库过滤，再语义检索/合成）— 同时包含统计/列表约束和内容主题：
- "上周张三发的关于合同的邮件有哪些"
- "最近三天失败的邮件里哪些提到了预算"

**clarify** — 用户问题缺少必要信息，或 stat/content/hybrid 均不确定。

## 时间解析

当前时间：{current_time}（周{weekday}）。把相对时间转为绝对范围：
- "近/最近/过去 N 天" → 往前推 N 天 00:00 ~ 现在
- "今天" → 当天 00:00 ~ 现在
- "昨天" → 昨天 00:00 ~ 23:59
- "本周/这周" → 本周一 00:00 ~ 现在
- "上周" → 上周一 00:00 ~ 上周日 23:59
- "本月/这个月" → 本月 1 日 00:00 ~ 现在
- 没提时间 → time_range 为 null
- time_range.original 填用户原始时间表达，例如"近三天"；没有则 null

## 状态映射（status 字段）

- "已完成/已处理/入库/处理好/成功了" → done
- "失败/报错/出错/没成功" → failed
- "跳过/过滤/噪音/被过滤了" → skipped
- "待处理/还没处理/未处理/没处理/排队中" → pending
- "处理中/正在处理" → processing
- "收到/收了/收了多少/总共/一共/全部/所有" → statuses 为空数组，不区分状态

注意：如果问题提到多个状态（如"成功和失败的各多少"），statuses 填多个值。

## 发件人识别（sender 字段）

如果问题指定了某人（"张三发的"、"从xxx@xxx来的"、"某人的邮件"），
把识别到的发件人填入 sender 字段（邮箱地址或姓名）。没提则不填。

## 附件过滤（has_attachment 字段）

- "带附件的/有附件的/含附件" → true
- "没有附件的/纯文本" → false
- 没提 → 不填

## 聚合类型（aggregation 字段）

- "多少/几封/数量/统计/计数" → "count"
- "列出/有哪些/哪些邮件/看看/显示" → "list"
- "谁最多/Top/排名/发件最多/哪些人发" → "top_senders"
- "成功率/失败率/比例/占比/百分之" → "rate"

## 主题过滤（topic 字段）

如果问题要求关于某个话题/项目/合同/客户/人物/内容，填 topic；否则 null。
如果是纯统计（例如"近三天多少封失败邮件"），topic 必须为 null。

## 输出格式（纯 JSON，无 markdown 包裹）

stat 示例：
{{
  "route": "stat",
  "aggregation": "count",
  "time_range": {{"original": "近三天", "start": "2026-07-06T00:00:00", "end": "2026-07-09T23:59:59"}},
  "filters": {{
    "statuses": [],
    "sender": null,
    "has_attachment": null,
    "topic": null
  }},
  "limit": 0,
  "confidence": 0.95,
  "clarifying_question": null,
  "reason": "用户询问近三天邮件数量"
}}

content 示例：
{{
  "route": "content",
  "aggregation": null,
  "time_range": null,
  "filters": {{"statuses": [], "sender": null, "has_attachment": null, "topic": "原始问题主题"}},
  "limit": 0,
  "confidence": 0.9,
  "clarifying_question": null,
  "reason": "用户询问邮件内容"
}}

limit 含义：top_senders 时返回 Top N（默认 5），list 时返回最近 N 条（默认 10）。"""


class TimeRangePlan(BaseModel):
    original: str | None = None
    start: str | None = None
    end: str | None = None


class QueryFilters(BaseModel):
    statuses: list[Literal["done", "failed", "skipped", "processing", "pending"]] = Field(default_factory=list)
    sender: str | None = None
    has_attachment: bool | None = None
    topic: str | None = None

    @field_validator("statuses", mode="before")
    @classmethod
    def normalize_statuses(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("sender", "topic", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None


class QueryPlan(BaseModel):
    route: Literal["stat", "content", "hybrid", "clarify"]
    aggregation: Literal["count", "list", "top_senders", "rate"] | None = None
    time_range: TimeRangePlan | None = None
    filters: QueryFilters = Field(default_factory=QueryFilters)
    limit: int = 0
    confidence: float = 0.0
    clarifying_question: str | None = None
    reason: str = ""

    @field_validator("limit", mode="before")
    @classmethod
    def normalize_limit(cls, value):
        if not isinstance(value, int) or value < 0:
            return 0
        return min(value, 100)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return 0.0

    @model_validator(mode="after")
    def normalize_by_route(self):
        if self.route == "stat" and self.aggregation is None:
            self.aggregation = "count"
        if self.route in ("content", "clarify"):
            self.aggregation = None
        if self.route == "clarify" and not self.clarifying_question:
            self.clarifying_question = "你想查邮件数量/列表，还是邮件内容？"
        return self


QUERY_PLAN_SCHEMA = {
    "name": "mail_query_plan",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "route", "aggregation", "time_range", "filters", "limit",
            "confidence", "clarifying_question", "reason",
        ],
        "properties": {
            "route": {"type": "string", "enum": ["stat", "content", "hybrid", "clarify"]},
            "aggregation": {
                "anyOf": [
                    {"type": "string", "enum": ["count", "list", "top_senders", "rate"]},
                    {"type": "null"},
                ]
            },
            "time_range": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["original", "start", "end"],
                        "properties": {
                            "original": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "start": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "end": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        },
                    },
                    {"type": "null"},
                ]
            },
            "filters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statuses", "sender", "has_attachment", "topic"],
                "properties": {
                    "statuses": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["done", "failed", "skipped", "processing", "pending"],
                        },
                    },
                    "sender": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "has_attachment": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
                    "topic": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
            "limit": {"type": "integer", "minimum": 0, "maximum": 100},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "clarifying_question": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "reason": {"type": "string"},
        },
    },
}


@dataclass
class QueryResult:
    question: str = ""
    answer: str = ""
    entities: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    chunks: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    total_duration_ms: int = 0
    error: str = ""


class QueryEngine:
    """智能查询引擎 — LLM 意图分类 → 路由分发 → 执行"""

    def __init__(self, ragflow_client=None, account_id: str | None = None):
        cfg = get_settings()
        self.llm = OpenAI(
            api_key=cfg.openai_api_key,
            base_url=f"{cfg.openai_base_url}/v1",
            timeout=60.0,
        )
        self.model = cfg.openai_model
        self.rf = ragflow_client
        self._account_id = account_id
        self._cache = None

    # ═══════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════

    def _get_cache(self):
        if self._cache is None:
            from src.storage.redis_cache import MailCache
            try:
                self._cache = MailCache(self._account_id)
            except Exception as e:
                logger.warning("MailCache 初始化失败: %s", e)
                self._cache = False
        return self._cache if self._cache is not False else None

    def _call_llm(self, prompt: str, max_tokens: int = 500,
                  system: str | None = None) -> str:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = self.llm.chat.completions.create(
                model=self.model, temperature=0,
                messages=messages, max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error("LLM 调用失败: %s", e)
            return "{}"

    def _call_llm_json_schema(self, prompt: str, system: str,
                              schema: dict, max_tokens: int = 600) -> str:
        """Call the model with strict JSON schema when the provider supports it."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        resp = self.llm.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=messages,
            max_tokens=max_tokens,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        return resp.choices[0].message.content or ""

    def _parse_json(self, raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else ""
            if text.endswith("```"):
                text = text[:-3]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
            logger.warning("无法解析意图 JSON: %s", raw[:200])
            return {}

    # ═══════════════════════════════════════════
    # 查询规划
    # ═══════════════════════════════════════════

    _STAT_HINTS = (
        "多少", "几封", "几条", "数量", "统计", "计数", "成功率", "失败率",
        "比例", "占比", "百分", "top", "排名", "谁最多", "发件最多",
        "列出", "有哪些", "哪些邮件", "显示", "看看", "处理进度", "完成情况",
        "有附件", "带附件", "没有附件", "无附件",
    )
    _CONTENT_HINTS = (
        "内容", "讲了什么", "说了什么", "总结", "摘要", "项目", "客户",
        "合同", "预算", "方案", "关系", "讨论", "提到", "关于",
    )

    def _precheck_route(self, question: str) -> str | None:
        q = question.lower()
        has_stat = any(h.lower() in q for h in self._STAT_HINTS)
        has_content = any(h.lower() in q for h in self._CONTENT_HINTS)
        if has_stat and has_content:
            return "hybrid"
        if has_stat:
            return "stat"
        if has_content:
            return "content"
        return None

    def _build_query_plan(self, question: str) -> QueryPlan:
        now = datetime.now()
        weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
        system = QUERY_PLAN_SYSTEM_PROMPT.format(
            current_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            weekday=weekday,
        )
        try:
            raw = self._call_llm_json_schema(
                question, system=system, schema=QUERY_PLAN_SCHEMA, max_tokens=600)
        except Exception as e:
            logger.warning("结构化查询规划失败，降级普通 JSON: %s", e)
            raw = self._call_llm(question, max_tokens=600, system=system)
        parsed = self._parse_json(raw)

        try:
            plan = QueryPlan.model_validate(parsed)
        except ValidationError as e:
            logger.warning("查询计划校验失败，回退 content: %s", e)
            plan = QueryPlan(
                route="content",
                filters=QueryFilters(topic=question),
                confidence=0.3,
                reason="查询计划校验失败，回退内容检索",
            )

        plan = self._repair_plan_with_precheck(question, plan)
        logger.info("查询计划: %s → %s/%s conf=%.2f",
                    question[:60], plan.route, plan.aggregation, plan.confidence)
        return plan

    def _repair_plan_with_precheck(self, question: str, plan: QueryPlan) -> QueryPlan:
        """Use cheap lexical signals to catch obvious route mistakes."""
        hint = self._precheck_route(question)
        if hint and plan.confidence < 0.65:
            plan.route = hint
            if hint == "stat" and plan.aggregation is None:
                plan.aggregation = "count"
            if hint == "content":
                plan.aggregation = None
            plan.reason = f"{plan.reason}；规则预检修正为 {hint}".strip("；")

        if plan.route == "stat" and plan.filters.topic:
            topic_words = re.sub(r"\s+", "", plan.filters.topic)
            if len(topic_words) >= 2 and any(h in question for h in ("关于", "提到", "内容", "讲了什么")):
                plan.route = "hybrid"
                plan.reason = f"{plan.reason}；存在主题过滤，升级为 hybrid".strip("；")

        if plan.route == "hybrid" and plan.aggregation is None:
            plan.aggregation = "list"

        return plan

    # ═══════════════════════════════════════════
    # 统计查询执行
    # ═══════════════════════════════════════════

    _VALID_STATUSES = {"done", "failed", "skipped", "processing", "pending"}

    def _sanitize_plan(self, plan: QueryPlan) -> QueryPlan:
        """Final defensive cleanup after model and Pydantic validation."""
        plan.filters.statuses = [
            s for s in dict.fromkeys(plan.filters.statuses)
            if s in self._VALID_STATUSES
        ]
        if plan.limit < 0:
            plan.limit = 0
        if plan.limit > 100:
            plan.limit = 100
        if plan.route == "stat" and plan.aggregation is None:
            plan.aggregation = "count"
        if plan.route == "hybrid" and plan.aggregation is None:
            plan.aggregation = "list"
        return plan

    def _parse_time_slot(self, tr: TimeRangePlan | None) -> tuple:
        if not tr:
            return None, None
        try:
            start_str = tr.start or ""
            end_str = tr.end or ""
            start_dt = datetime.fromisoformat(start_str) if start_str else None
            end_dt = datetime.fromisoformat(end_str) if end_str else None
            if start_dt and end_dt and start_dt > end_dt:
                return None, None
            return start_dt, end_dt
        except (ValueError, TypeError):
            return None, None

    def _execute_stat_query(self, question: str, plan: QueryPlan,
                            start_time: float, hybrid: bool = False) -> dict:
        """执行统计查询：槽位 → MailCache.query_stats → LLM 格式化。"""
        plan = self._sanitize_plan(plan)
        tr = plan.time_range
        start_dt, end_dt = self._parse_time_slot(tr)

        cache = self._get_cache()
        if cache is None:
            return self._error_response(question, start_time, "Redis 不可用")

        # 决定 group_by
        group_by = None
        if plan.aggregation == "top_senders":
            group_by = "sender"
            if plan.limit == 0:
                plan.limit = 5  # top 5 默认
        elif plan.aggregation == "list" and plan.limit == 0:
            plan.limit = 10

        try:
            result = cache.query_stats(
                start_time=start_dt if start_dt else None,
                end_time=end_dt if end_dt else None,
                statuses=plan.filters.statuses or None,
                group_by=group_by,
                has_attachment=plan.filters.has_attachment,
                limit=plan.limit,
            )
        except Exception as e:
            logger.error("统计查询失败: %s", e)
            return self._error_response(question, start_time, str(e))

        # sender 精确过滤（LLM 给出发件人地址或姓名）
        if plan.filters.sender and group_by is None:
            result = self._filter_by_sender(result, plan.filters.sender)

        # 按聚合类型生成回答
        answer = self._format_stat_answer(question, result, tr, plan)

        total_dur = int((time.time() - start_time) * 1000)
        trace_name = "混合查询过滤（QueryPlan → MailCache）" if hybrid else "统计查询（QueryPlan → MailCache）"
        trace = [{
            "name": trace_name, "icon": "📊",
            "content": self._trace_summary(plan, result),
            "detail": f"匹配 {result['total']} 封",
            "status": "ok", "color": "#10B981",
            "duration_ms": total_dur,
        }]

        return {
            "question": question,
            "answer": answer,
            "entities": [], "relationships": [], "chunks": [],
            "trace": trace,
            "rows": self._stat_rows(result, plan),
            "columns": list(self._stat_rows(result, plan)[0].keys()) if self._stat_rows(result, plan) else [],
            "total_rows": len(self._stat_rows(result, plan)),
            "total_duration_ms": total_dur,
            "error": "",
            "matched_ids": result.get("matched_ids", []),
            "query_plan": plan.model_dump(),
        }

    def _filter_by_sender(self, result: dict, sender: str) -> dict:
        """在 items 中按发件人模糊匹配过滤。"""
        s_lower = sender.lower().strip()
        filtered = [
            i for i in result.get("items", [])
            if s_lower in i.get("from_addr", "").lower()
            or s_lower in i.get("from_name", "").lower()
        ]
        # 重算 by_status
        by_status = {"done": 0, "failed": 0, "skipped": 0, "processing": 0, "pending": 0}
        for i in filtered:
            st = i.get("status", "pending")
            if st in by_status:
                by_status[st] += 1
        return {
            "total": len(filtered),
            "by_status": by_status,
            "by_sender": [],
            "items": filtered,
            "matched_ids": [i["message_id"] for i in filtered],
        }

    def _trace_summary(self, plan: QueryPlan, result: dict) -> str:
        parts = [f"路由: {plan.route}", f"聚合: {plan.aggregation}"]
        if plan.time_range:
            parts.append(f"时间范围: 是")
        if plan.filters.statuses:
            parts.append(f"状态: {','.join(plan.filters.statuses)}")
        if plan.filters.sender:
            parts.append(f"发件人: {plan.filters.sender}")
        if plan.filters.has_attachment is not None:
            parts.append(f"附件: {'有' if plan.filters.has_attachment else '无'}")
        if plan.filters.topic:
            parts.append(f"主题: {plan.filters.topic}")
        parts.append(f"置信度: {plan.confidence:.2f}")
        return "，".join(parts)

    def _stat_rows(self, result: dict, plan: QueryPlan) -> list[dict]:
        """生成前端数据表行。"""
        agg = plan.aggregation

        if agg == "top_senders":
            return [
                {"发件人": s.get("name") or s["addr"],
                 "地址": s["addr"],
                 "数量": str(s["count"])}
                for s in result.get("by_sender", [])
            ]

        if agg == "list":
            return [
                {"日期": (i.get("date") or "")[:19],
                 "主题": (i.get("subject") or "")[:60],
                 "发件人": i.get("from_name") or i.get("from_addr", ""),
                 "地址": i.get("from_addr", ""),
                 "状态": i.get("status", ""),
                 "附件": "📎" if i.get("has_attachment") else ""}
                for i in result.get("items", [])[:20]
            ]

        # count / rate: 状态分布表
        by_status = result.get("by_status", {})
        return [
            {"状态": "已完成", "数量": str(by_status.get("done", 0))},
            {"状态": "待处理", "数量": str(by_status.get("pending", 0))},
            {"状态": "处理中", "数量": str(by_status.get("processing", 0))},
            {"状态": "失败", "数量": str(by_status.get("failed", 0))},
            {"状态": "已跳过", "数量": str(by_status.get("skipped", 0))},
        ]

    # ═══════════════════════════════════════════
    # 自然语言回答生成
    # ═══════════════════════════════════════════

    def _format_stat_answer(self, question: str, result: dict,
                            time_range: TimeRangePlan | None,
                            plan: QueryPlan) -> str:
        """用 LLM 将统计结果格式化为自然语言。"""
        total = result["total"]
        by_status = result.get("by_status", {})
        by_sender = result.get("by_sender", [])
        items = result.get("items", [])
        agg = plan.aggregation or "count"

        # 构建数据上下文
        ctx = [f"匹配邮件总数: {total}"]
        if time_range:
            ctx.append(f"时间范围: {(time_range.start or '')[:10]} ~ {(time_range.end or '')[:10]}")
        if plan.filters.statuses:
            ctx.append(f"状态过滤: {','.join(plan.filters.statuses)}")
        if plan.filters.sender:
            ctx.append(f"发件人过滤: {plan.filters.sender}")
        if plan.filters.has_attachment is not None:
            ctx.append(f"附件过滤: {'仅含附件' if plan.filters.has_attachment else '仅无附件'}")
        if plan.filters.topic:
            ctx.append(f"主题过滤: {plan.filters.topic}")

        ctx.append(f"状态分布: {json.dumps(by_status, ensure_ascii=False)}")

        if by_sender:
            top = [f"{s.get('name') or s['addr']}({s['count']}封)" for s in by_sender[:10]]
            ctx.append(f"发件排名: {', '.join(top)}")

        if items and agg == "list":
            preview = [f"{i.get('date','')[:10]} {i.get('subject','')[:40]} ({(i.get('from_name') or i.get('from_addr',''))[:20]})"
                       for i in items[:8]]
            ctx.append(f"邮件列表(前{len(preview)}): {'; '.join(preview)}")

        # 提示
        hints = {
            "count": "直接给出数字，如有状态分布可补充说明。",
            "list": "列出邮件列表（日期、主题、发件人）。如果数量多，先给总数再列前几条。",
            "top_senders": "按发件量排名展示。",
            "rate": f"计算成功率、失败率等比例。已知 total={total}, by_status={by_status}。成功率 = done/total，失败率 = failed/total。",
        }

        prompt = f"""根据统计数据回答用户问题（中文，简洁，<200字）。

用户问题：{question}

{'；'.join(ctx)}

{hints.get(agg, '')}"""

        answer = self._call_llm(prompt, max_tokens=350)
        if answer and answer != "{}":
            return answer

        return self._template_answer(total, by_status, by_sender, items,
                                     time_range, plan)

    def _template_answer(self, total: int, by_status: dict,
                         by_sender: list, items: list,
                         time_range: TimeRangePlan | None,
                         plan: QueryPlan) -> str:
        """模板兜底回答。"""
        time_str = ""
        if time_range:
            time_str = f"（{(time_range.start or '')[:10]} ~ {(time_range.end or '')[:10]}）"

        if total == 0:
            reason = time_str if time_str else ""
            return f"{reason} 没有匹配的邮件。" if reason else "没有找到匹配的邮件。请先导入邮件数据。"

        agg = plan.aggregation or "count"

        if agg == "top_senders" and by_sender:
            lines = [f"发件量排名{time_str}："]
            for i, s in enumerate(by_sender[:10], 1):
                name = s.get("name") or s["addr"]
                lines.append(f"  {i}. {name} — {s['count']} 封")
            return "\n".join(lines)

        if agg == "list" and items:
            total_info = f"共 {total} 封{time_str}" if time_str else f"共 {total} 封"
            lines = [total_info]
            for i in items[:8]:
                d = (i.get("date", ""))[:10]
                s = (i.get("subject", ""))[:50]
                f = (i.get("from_name") or i.get("from_addr", ""))[:30]
                lines.append(f"  {d} | {s} | {f}")
            return "\n".join(lines)

        if agg == "rate":
            done = by_status.get("done", 0)
            failed = by_status.get("failed", 0)
            success_rate = f"{done/total*100:.1f}%" if total > 0 else "N/A"
            fail_rate = f"{failed/total*100:.1f}%" if total > 0 else "N/A"
            return (f"共 {total} 封邮件{time_str}。"
                    f"成功率 {success_rate}（{done}/{total}），"
                    f"失败率 {fail_rate}（{failed}/{total}）。")

        # count
        labels = {"done": "已完成", "failed": "失败", "skipped": "已跳过",
                  "processing": "处理中", "pending": "待处理"}
        lines = [f"共 {total} 封邮件{time_str}。"]
        for st in ["done", "pending", "processing", "failed", "skipped"]:
            if by_status.get(st, 0) > 0:
                lines.append(f"  {labels[st]}：{by_status[st]} 封")
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 错误降级
    # ═══════════════════════════════════════════

    def _error_response(self, question: str, start_time: float,
                        error: str) -> dict:
        total_dur = int((time.time() - start_time) * 1000)
        return {
            "question": question,
            "answer": f"⚠️ 暂时无法查询邮件统计数据。{error}",
            "entities": [], "relationships": [], "chunks": [],
            "trace": [{
                "name": "统计查询失败", "icon": "⚠️",
                "content": error,
                "status": "fail", "color": "#EF4444",
                "duration_ms": total_dur,
            }],
            "rows": [], "columns": [],
            "total_rows": 0, "total_duration_ms": total_dur, "error": error,
        }

    # ═══════════════════════════════════════════
    # 主入口：路由分发
    # ═══════════════════════════════════════════

    def query(self, question: str) -> dict:
        """主查询入口：LLM 意图分类 → 路由分发。"""
        start_time = time.time()

        intent = self._classify_intent(question)

        if intent.get("intent") == "stat_query":
            return self._execute_stat_query(question, intent, start_time)

        # content_query → RAGFlow
        answer, trace, entities, relationships, chunks = \
            self._native_query(question)
        total_dur = int((time.time() - start_time) * 1000)
        rows = self._to_rows(entities, relationships)
        return {
            "question": question,
            "answer": answer,
            "entities": entities,
            "relationships": relationships,
            "chunks": chunks,
            "trace": trace,
            "rows": rows,
            "columns": list(rows[0].keys()) if rows else [],
            "total_rows": len(rows),
            "total_duration_ms": total_dur,
            "error": "",
        }

    # ═══════════════════════════════════════════
    # RAGFlow 内容查询（不变）
    # ═══════════════════════════════════════════

    def _native_query(self, question: str):
        t0 = time.time()
        res = self.rf.chat_answer(question)
        answer = res.get("answer", "")
        refs = res.get("references", [])

        if not answer:
            fallback_chunks = self.rf.retrieve_chunks(question, top_k=10)
            answer = self._summarize(question, [], [], fallback_chunks)
            trace = [{
                "name": "RAGFlow 原生问答（回退本地总结）", "icon": "✨",
                "content": f"原生无结果，改用 {len(fallback_chunks)} 段检索总结",
                "status": "warning", "color": "#F59E0B",
                "duration_ms": int((time.time() - t0) * 1000),
            }]
            return answer, trace, [], [], fallback_chunks

        trace = [{
            "name": "RAGFlow 原生问答（检索+图谱+生成）", "icon": "✨",
            "content": f"引用 {len(refs)} 段来源",
            "detail": "、".join(r.get("doc_name", "") for r in refs[:5] if r.get("doc_name")),
            "status": "ok", "color": "#8B5CF6",
            "duration_ms": int((time.time() - t0) * 1000),
        }]

        entities, relationships = [], []
        try:
            entities = self.rf.get_graph_entities(page_size=100)
            relationships = self.rf.get_graph_relationships(page_size=500)
        except Exception as e:
            logger.warning("获取图谱数据失败: %s", e)

        return answer, trace, entities, relationships, refs

    def _summarize(self, question: str, entities: list,
                   relationships: list, chunks: list) -> str:
        parts = []
        if entities:
            es = {}
            for e in entities:
                t = e.get("type", "Entity")
                es[t] = es.get(t, 0) + 1
            parts.append(f"图谱匹配: {', '.join(f'{k}×{v}' for k, v in es.items())}")
            names = [e.get("name", "") for e in entities[:10] if e.get("name")]
            if names:
                parts.append(f"实体: {', '.join(names)}")
        if relationships:
            rs = {}
            for r in relationships:
                t = r.get("type", "RELATED")
                rs[t] = rs.get(t, 0) + 1
            parts.append(f"关系: {', '.join(f'{k}×{v}' for k, v in rs.items())}")
        if chunks:
            context = "\n---\n".join(
                c.get("content", "")[:400] for c in chunks[:5])
            summary = self._call_llm(
                f"基于以下信息简洁回答（中文，<200字）：\n问题：{question}\n图谱：{'; '.join(parts)}\n文档：{context}",
                400)
            if summary and summary != "{}":
                return summary
        if entities:
            return f"找到 {len(entities)} 个相关实体：{'、'.join(e.get('name','') for e in entities[:8] if e.get('name'))}"
        return "未找到相关结果，请尝试其他关键词或先导入邮件数据。"

    def _to_rows(self, entities: list, relationships: list) -> list[dict]:
        rows = []
        for e in entities:
            rows.append({
                "名称": e.get("name", ""),
                "类型": e.get("type", "Entity"),
                "描述": e.get("description", "")[:100],
            })
        if not rows and relationships:
            for r in relationships[:50]:
                rows.append({
                    "关系": r.get("type", ""),
                    "来源": r.get("source_id", ""),
                    "目标": r.get("target_id", ""),
                })
        return rows
