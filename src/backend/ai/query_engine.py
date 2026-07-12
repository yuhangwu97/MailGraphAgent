"""
智能查询引擎
============
接收自然语言问题，通过 LLM 意图分类路由到不同后端：

  stat_query     → MailCache（Redis 计数/列表/发件人排名/成功率）
  content_query  → LightRAG（语义检索 + 图谱 + 生成）

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


def _safe_doc_token(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", (s or "").strip("<>"))
    return s.strip("_")[:40]


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
            "limit": {"type": "integer"},
            "confidence": {"type": "number"},
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

    def __init__(self, account_id: str | None = None):
        cfg = get_settings()
        self.llm = OpenAI(
            api_key=cfg.openai_api_key,
            base_url=f"{cfg.openai_base_url}/v1",
            timeout=60.0,
        )
        self.model = cfg.openai_model
        self._account_id = account_id
        self._cache = None
        self._query_graph = None
        self._progress_cb = None

    def _emit_progress(self, msg: str):
        """Thread-safe: call the progress callback if set."""
        if self._progress_cb:
            try:
                self._progress_cb(msg)
            except Exception:
                pass

    # ═══════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════

    def _get_cache(self):
        if self._cache is None:
            from src.backend.storage.redis_cache import MailCache
            try:
                self._cache = MailCache(self._account_id)
            except Exception as e:
                logger.warning("MailCache 初始化失败: %s", e)
                self._cache = False
        return self._cache if self._cache is not False else None

    def close(self):
        """关闭 MailCache 连接。"""
        if self._cache and self._cache is not False:
            try:
                self._cache.close()
            except Exception:
                pass
            self._cache = None
        self._query_graph = None

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

    def _context_prompt(self, context: dict | None) -> str:
        if not context:
            return ""
        parts = []
        memory = context.get("memory")
        if memory:
            parts.append(f"Agent memory:\n{str(memory)[:1200]}")
        history = context.get("history") or []
        if history:
            lines = []
            for m in history[-6:]:
                role = m.get("role", "")
                content = (m.get("content") or "")[:400]
                if role and content:
                    lines.append(f"{role}: {content}")
            if lines:
                parts.append("Recent chat:\n" + "\n".join(lines))
        return "\n\n".join(parts)

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

    def _build_query_plan(self, question: str, context: dict | None = None) -> QueryPlan:
        now = datetime.now()
        weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
        system = QUERY_PLAN_SYSTEM_PROMPT.format(
            current_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            weekday=weekday,
        )
        self._emit_progress("🤔 正在识别查询意图…")
        context_text = self._context_prompt(context)
        prompt = question if not context_text else (
            f"{context_text}\n\n当前用户问题：{question}\n"
            "只根据当前用户问题规划查询；历史和 memory 仅用于补全省略指代。"
        )
        try:
            raw = self._call_llm_json_schema(
                prompt, system=system, schema=QUERY_PLAN_SCHEMA, max_tokens=600)
        except Exception as e:
            logger.warning("结构化查询规划失败，降级普通 JSON: %s", e)
            raw = self._call_llm(prompt, max_tokens=600, system=system)
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
                            start_time: float, hybrid: bool = False,
                            context: dict | None = None) -> dict:
        """执行统计查询：槽位 → MailCache.query_stats → LLM 格式化。"""
        plan = self._sanitize_plan(plan)
        tr = plan.time_range
        start_dt, end_dt = self._parse_time_slot(tr)

        cache = self._get_cache()
        if cache is None:
            return self._error_response(question, start_time, "Redis 不可用")

        self._emit_progress("📊 正在查询邮件统计数据…")

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
                sender=plan.filters.sender,
            )
        except Exception as e:
            logger.error("统计查询失败: %s", e)
            return self._error_response(question, start_time, str(e))

        # sender 精确过滤（LLM 给出发件人地址或姓名）
        if plan.filters.sender and group_by is None and not result.get("_sender_filtered"):
            result = self._filter_by_sender(result, plan.filters.sender)

        # sender 命中 0：发件人索引只覆盖 from_addr/from_name，此人可能是
        # 收件人或正文里提及/协作的人。不要断言"此人未发送/负责任何邮件"，
        # 回落到语义检索（content/hybrid），从图谱/正文里找出与此人相关的邮件。
        if plan.filters.sender and group_by is None and result.get("total", 0) == 0:
            fallback = self._fallback_sender_to_semantic(
                question, plan, start_time, context)
            if fallback is not None:
                return fallback

        # 按聚合类型生成回答
        answer = self._format_stat_answer(question, result, tr, plan, context)

        total_dur = int((time.time() - start_time) * 1000)
        trace_name = "混合查询过滤（QueryPlan → MailCache）" if hybrid else "统计查询（QueryPlan → MailCache）"
        trace = [{
            "name": trace_name, "icon": "📊",
            "content": self._trace_summary(plan, result),
            "detail": f"匹配 {result['total']} 封",
            "status": "ok", "color": "#10B981",
            "duration_ms": total_dur,
        }]

        rows = self._stat_rows(result, plan)
        return {
            "question": question,
            "answer": answer,
            "entities": [], "relationships": [], "chunks": [],
            "trace": trace,
            "rows": rows,
            "columns": list(rows[0].keys()) if rows else [],
            "total_rows": len(rows),
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

    def _fallback_sender_to_semantic(self, question: str, plan: QueryPlan,
                                     start_time: float,
                                     context: dict | None = None) -> dict | None:
        """stat+sender 命中 0 时的兜底：把人名当正文实体走语义检索。

        发件人索引只覆盖 from_addr/from_name，收件人和正文里提及/协作的人
        统计不到。此时不应断言"此人未发送/负责任何邮件"，而是把 sender 挪到
        topic，回落到 content（无其他约束）或 hybrid（尚有时间/状态/附件约束），
        让 LightRAG 从图谱/正文里找出与此人相关的邮件。返回 None 表示放弃兜底。
        """
        person = plan.filters.sender
        semantic = plan.model_copy(deep=True)
        semantic.filters.sender = None
        semantic.filters.topic = person

        has_other_constraints = bool(
            semantic.filters.statuses
            or semantic.filters.has_attachment is not None
            or semantic.time_range
        )
        note = {
            "name": "发件人零命中，回落语义检索",
            "icon": "🔄",
            "content": f"「{person}」不在发件人中，可能是收件人或正文提及的人；改用语义检索",
            "status": "warning", "color": "#F59E0B",
            "duration_ms": int((time.time() - start_time) * 1000),
        }
        try:
            if has_other_constraints:
                semantic.route = "hybrid"
                semantic.aggregation = semantic.aggregation or "list"
                result = self._execute_hybrid_query(
                    question, semantic, start_time, context=context)
            else:
                semantic.route = "content"
                semantic.aggregation = None
                # 原问题（"X发了哪些邮件"）暗示发送，会把 LightRAG 引向"X不是发件人"。
                # 重述成以人物为中心的问法，让语义召回/合成聚焦此人的相关往来。
                reframed = (
                    f"「{person}」在邮件往来中涉及哪些事项、扮演什么角色、"
                    f"与谁协作？请列出与「{person}」相关的邮件要点。"
                )
                result = self._execute_content_query(
                    question, semantic, start_time, context=context,
                    retrieval_question=reframed)
        except Exception as e:
            logger.warning("发件人零命中回落语义检索失败: %s", e)
            return None

        # 把回落说明放到 trace 最前面，让用户看到路由从 stat 改成了语义检索
        result["trace"] = [note] + list(result.get("trace", []))
        return result

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
                            plan: QueryPlan, context: dict | None = None) -> str:
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

{self._context_prompt(context)}

用户问题：{question}

{'；'.join(ctx)}

{hints.get(agg, '')}"""

        self._emit_progress("✍️ 正在整理统计结果…")
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

    def _clarify_response(self, question: str, plan: QueryPlan,
                          start_time: float) -> dict:
        total_dur = int((time.time() - start_time) * 1000)
        answer = plan.clarifying_question or "你想查邮件数量/列表，还是邮件内容？"
        return {
            "question": question,
            "answer": answer,
            "entities": [], "relationships": [], "chunks": [],
            "trace": [{
                "name": "查询需要澄清", "icon": "❔",
                "content": self._trace_summary(plan, {"total": 0}),
                "detail": plan.reason,
                "status": "warning", "color": "#F59E0B",
                "duration_ms": total_dur,
            }],
            "rows": [], "columns": [],
            "total_rows": 0,
            "total_duration_ms": total_dur,
            "error": "",
            "query_plan": plan.model_dump(),
        }

    def _execute_hybrid_query(self, question: str, plan: QueryPlan,
                              start_time: float,
                              context: dict | None = None) -> dict:
        """Resolve topic evidence to message ids, then run metadata stats on the intersection."""
        topic = plan.filters.topic or question
        self._emit_progress("🧠 正在检索相关邮件主题…")
        chunks, topic_ids = self._retrieve_topic_message_ids(topic)
        if not topic_ids:
            # hybrid 的核心是“主题命中 message_id ∩ 元数据过滤”。
            # 若主题没有可归属 message_id，不能把主题条件静默放宽成“所有邮件”。
            stat_result = self._execute_stat_query_with_message_ids(
                question, plan, start_time, message_ids=set(),
                hybrid=True, context=context)
            stat_result["chunks"] = chunks
            stat_result["answer"] = self._format_hybrid_answer(
                question, topic, stat_result, chunks, context)
            chunk_count = len(chunks)
            trace_content = (
                f"主题：{topic}，检索到 {chunk_count} 段证据但未能归属到 message_id"
                if chunk_count else
                f"主题：{topic}，未检索到相关证据"
            )
            stat_result["trace"].append({
                "name": "混合查询主题命中（LightRAG chunks → message_id）",
                "icon": "✨",
                "content": trace_content,
                "status": "warning" if chunk_count else "fail",
                "color": "#F59E0B" if chunk_count else "#EF4444",
                "duration_ms": int((time.time() - start_time) * 1000),
            })
            return stat_result

        stat_result = self._execute_stat_query_with_message_ids(
            question, plan, start_time, message_ids=topic_ids,
            hybrid=True, context=context)
        if stat_result.get("error"):
            return stat_result

        matched_ids = set(stat_result.get("matched_ids", []))
        chunks = self._filter_chunks_by_message_ids(chunks, matched_ids)
        items = stat_result.get("rows", [])
        answer = self._format_hybrid_answer(question, topic, stat_result, chunks, context)

        total_dur = int((time.time() - start_time) * 1000)
        trace = list(stat_result.get("trace", []))
        trace.append({
            "name": "混合查询主题命中（LightRAG chunks → message_id）",
            "icon": "✨",
            "content": f"主题命中 {len(topic_ids)} 封，元数据交集 {len(matched_ids)} 封，证据 {len(chunks)} 段",
            "status": "ok" if matched_ids else "warning",
            "color": "#8B5CF6",
            "duration_ms": total_dur,
        })

        stat_result.update({
            "answer": answer,
            "trace": trace,
            "chunks": chunks,
            "total_duration_ms": total_dur,
            "rows": items,
            "columns": list(items[0].keys()) if items else [],
        })
        return stat_result

    def _execute_stat_query_with_message_ids(self, question: str, plan: QueryPlan,
                                             start_time: float, message_ids: set[str] | None,
                                             hybrid: bool = False,
                                             context: dict | None = None) -> dict:
        plan = self._sanitize_plan(plan)
        tr = plan.time_range
        start_dt, end_dt = self._parse_time_slot(tr)
        cache = self._get_cache()
        if cache is None:
            return self._error_response(question, start_time, "Redis 不可用")

        group_by = None
        if plan.aggregation == "top_senders":
            group_by = "sender"
            if plan.limit == 0:
                plan.limit = 5
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
                message_ids=message_ids,
                sender=plan.filters.sender,
            )
        except Exception as e:
            logger.error("混合统计查询失败: %s", e)
            return self._error_response(question, start_time, str(e))

        answer = self._format_stat_answer(question, result, tr, plan, context)
        total_dur = int((time.time() - start_time) * 1000)
        trace = [{
            "name": "混合查询过滤（topic message_id ∩ QueryPlan → MailCache）" if hybrid else "统计查询（QueryPlan → MailCache）",
            "icon": "📊",
            "content": self._trace_summary(plan, result),
            "detail": f"匹配 {result['total']} 封",
            "status": "ok", "color": "#10B981",
            "duration_ms": total_dur,
        }]
        rows = self._stat_rows(result, plan)
        return {
            "question": question,
            "answer": answer,
            "entities": [], "relationships": [], "chunks": [],
            "trace": trace,
            "rows": rows,
            "columns": list(rows[0].keys()) if rows else [],
            "total_rows": len(rows),
            "total_duration_ms": total_dur,
            "error": "",
            "matched_ids": result.get("matched_ids", []),
            "query_plan": plan.model_dump(),
        }

    def _retrieve_topic_message_ids(self, topic: str) -> tuple[list[dict], set[str]]:
        # LightRAG 结构化检索：返回带来源（file_path=message_id）的 chunk
        try:
            from src.backend.knowledge.lightrag_wrapper import retrieve_mail_sources
            chunks = retrieve_mail_sources(topic, mode="mix", top_k=20)
            if not chunks:
                return [], set()
        except Exception:
            return [], set()

        cache = self._get_cache()
        doc_ids = set()
        mids = set()
        for chunk in chunks:
            doc_id = self._chunk_doc_id(chunk)
            if doc_id:
                doc_ids.add(doc_id)
            mid = self._chunk_message_id(chunk)
            if mid:
                mids.add(mid)
        # 优先用 file_path/metadata 得到的 message_id；doc_id 可能是 LightRAG
        # 为 Milvus 主键生成的短哈希，只有 Redis 能确认或形态像 Message-ID 时才采信。
        for doc_id in doc_ids:
            if doc_id.startswith("<") and ">" in doc_id:
                mids.add(doc_id)
            elif cache is not None:
                try:
                    if cache.get_mail_state(doc_id):
                        mids.add(doc_id)
                except Exception:
                    pass
        if cache is not None and doc_ids:
            mids.update(cache.message_ids_for_docs(list(doc_ids)))
        return chunks, mids

    def _diversify_chunks(self, chunks: list[dict], max_per_doc: int = 3) -> list[dict]:
        """限制每个文档的 chunk 数量，保证结果多样性。"""
        seen: dict[str, int] = {}
        diverse = []
        for c in chunks:
            doc_id = self._chunk_doc_id(c) or c.get("doc_name", c.get("document_id", ""))
            count = seen.get(doc_id, 0)
            if count < max_per_doc:
                diverse.append(c)
                seen[doc_id] = count + 1
        return diverse

    def _retrieve_hybrid_chunks(self, topic: str, matched_ids: list[str]) -> list[dict]:
        chunks, _ = self._retrieve_topic_message_ids(topic)
        return self._filter_chunks_by_message_ids(chunks, set(matched_ids))

    def _filter_chunks_by_message_ids(self, chunks: list[dict], matched_ids: set[str]) -> list[dict]:
        if not chunks or not matched_ids:
            return []
        wanted = set()
        for mid in matched_ids:
            wanted.add(mid)
            wanted.add(_safe_doc_token(mid))

        filtered = []
        for c in chunks:
            haystack = " ".join([
                str(c.get("doc_id", "")),
                str(c.get("file_path", "")),
                str(c.get("chunk_id", "")),
                str(c.get("document_id", "")),
                str(c.get("doc_name", "")),
                str(c.get("content", ""))[:500],
            ])
            if any(token and token in haystack for token in wanted):
                filtered.append(c)
        return filtered

    def _chunk_doc_id(self, chunk: dict) -> str:
        for key in ("doc_id", "document_id", "document_id_id", "full_doc_id"):
            value = chunk.get(key)
            if value:
                return str(value)
        return ""

    def _chunk_message_id(self, chunk: dict) -> str:
        for key in ("message_id", "mail_id"):
            value = chunk.get(key)
            if value:
                return str(value)
        for key in ("file_path",):
            value = str(chunk.get(key) or "")
            if value.startswith("<") and ">:" in value:
                return value.split(">:", 1)[0] + ">"
            if value and not value.startswith("mail-"):
                return value.split(":", 1)[0]
        doc_id = str(chunk.get("doc_id") or "")
        if doc_id.startswith("<") and ">" in doc_id:
            return doc_id
        metadata = chunk.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("message_id"):
            return str(metadata["message_id"])
        return ""

    def _format_hybrid_answer(self, question: str, topic: str,
                              stat_result: dict, chunks: list[dict],
                              context: dict | None = None) -> str:
        rows = stat_result.get("rows", [])[:10]
        matched_count = len(stat_result.get("matched_ids", []))

        # 有 chunks 但 rows 为空：主题检索命中了正文，但未能映射到具体邮件元数据
        # 直接用 chunk 内容生成回答，不要丢弃已有的检索证据
        if not rows and chunks:
            evidence = "\n---\n".join(
                (c.get("content", "") or "")[:600] for c in chunks[:5]
            )
            prompt = f"""根据检索到的邮件正文片段回答用户问题（中文，简洁，<250字）。

{self._context_prompt(context)}

用户问题：{question}
主题：{topic}

LightRAG 检索到 {len(chunks)} 段与"{topic}"相关的邮件正文：
{evidence}

请根据以上内容直接回答用户问题。列出相关邮件及其要点（日期、发件人、关键信息）。
不要说"没有找到"——正文中已有相关邮件，请如实总结。"""
            answer = self._call_llm(prompt, max_tokens=500)
            if answer and answer != "{}":
                return answer
            # LLM 失败时给出模板兜底
            doc_names = list({c.get("doc_name", "") for c in chunks[:8] if c.get("doc_name")})
            if doc_names:
                return f"检索到 {len(chunks)} 段与'{topic}'相关的邮件正文（{'; '.join(doc_names[:5])}），但未能精确匹配过滤条件。"
            return f"检索到 {len(chunks)} 段与'{topic}'相关的邮件正文，但未能精确匹配过滤条件。"

        if not rows:
            return f"没有找到同时满足过滤条件并与'{topic}'相关的邮件。"

        preview = "\n".join(
            f"- {r.get('日期','')} | {r.get('主题','')} | {r.get('发件人','')}"
            for r in rows
        )
        evidence = "\n---\n".join(
            (c.get("content", "") or "")[:600] for c in chunks[:5]
        )
        prompt = f"""根据已过滤出的邮件候选回答用户问题（中文，简洁，<220字）。

{self._context_prompt(context)}

用户问题：{question}
主题：{topic}
候选邮件总数：{matched_count}
候选邮件预览：
{preview}

LightRAG chunk 证据：
{evidence or "无"}

如果 chunk 证据为空或无法确认来自候选邮件，请明确说这是基于候选邮件元数据的判断；不要编造正文细节。"""
        answer = self._call_llm(prompt, max_tokens=420)
        if answer and answer != "{}":
            return answer
        suffix = "，并检索到相关正文片段。" if chunks else "，但没有检索到可引用的正文片段。"
        return f"找到 {matched_count} 封候选邮件与'{topic}'相关{suffix}"

    # ═══════════════════════════════════════════
    # 主入口：路由分发
    # ═══════════════════════════════════════════

    def query(self, question: str, context: dict | None = None,
              progress_cb=None) -> dict:
        """主查询入口：查询规划 → 校验 → 路由分发。

        progress_cb: optional callable(msg: str) for real-time progress updates.
        """
        prev_cb = self._progress_cb
        self._progress_cb = progress_cb
        try:
            graph_result = self._try_query_graph(question, context)
            if graph_result is not None:
                return graph_result

            start_time = time.time()

            plan = self._build_query_plan(question, context)

            if plan.route == "clarify":
                return self._clarify_response(question, plan, start_time)

            if plan.route == "stat":
                return self._execute_stat_query(question, plan, start_time, context=context)

            if plan.route == "hybrid":
                return self._execute_hybrid_query(question, plan, start_time, context=context)

            return self._execute_content_query(question, plan, start_time, context=context)
        finally:
            self._progress_cb = prev_cb

    def _try_query_graph(self, question: str, context: dict | None = None) -> dict | None:
        try:
            from src.backend.ai.query_graph import build_query_graph
        except Exception:
            return None
        try:
            if self._query_graph is None:
                self._query_graph = build_query_graph()
            state = self._query_graph.invoke({
                "engine": self,
                "question": question,
                "context": context or {},
                "start_time": time.time(),
            })
            return state["result"]
        except Exception as e:
            logger.warning("LangGraph 编排失败，回落顺序执行: %s", e)
            return None

    def _execute_content_query(self, question: str, plan: QueryPlan,
                               start_time: float,
                               context: dict | None = None,
                               retrieval_question: str | None = None) -> dict:
        # content_query → 宽域检索 + LLM 总结
        # 注意：检索只用原始问题，避免 memory/history 污染语义搜索。
        # retrieval_question：可选的重述问题，用于语义召回/合成（如把"王芳发了哪些邮件"
        # 重述为以人物为中心的问法），显示层仍保留用户原始 question。
        context_text = self._context_prompt(context)
        answer, trace, entities, relationships, chunks = \
            self._native_query(retrieval_question or question, context_text)
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
            "query_plan": plan.model_dump(),
        }

    # ═══════════════════════════════════════════
    # LightRAG 内容查询
    # ═══════════════════════════════════════════

    # 语义回答语气：人物/主题类问题要正面陈述其相关邮件，而不是强调"此人非发件人"。
    _SEMANTIC_ANSWER_STYLE = (
        "回答的第一句必须直接陈述该人物/主题在邮件中参与的事项或相关事实，"
        "严禁以否定判断开头（如“某人并未发送任何邮件”“未被记录为发件人”“没有相关记录”）。"
        "用户关心的是这个人物/主题涉及了什么，而非其是否为发件人——即使此人只是收件人或仅被提及，"
        "也要正面、具体地陈述与其相关的邮件内容、承担的任务与协作关系。"
        "只有当上下文里确实找不到任何相关信息时，才说明缺少信息。"
    )

    def _native_query(self, question: str, context_text: str = ""):
        t0 = time.time()
        entities: list = []
        relationships: list = []
        chunks: list = []
        hop2_added = 0
        trace: list = []

        # 1) 结构化召回：语义（向量）+ 图谱（遍历）一体，取回 entities/relationships/chunks
        t1 = time.time()
        self._emit_progress("🧠 正在检索知识图谱…")
        try:
            from src.backend.knowledge.lightrag_wrapper import retrieve_mail_graph
            retrieved = retrieve_mail_graph(question, mode="mix", top_k=20)
            entities = retrieved.get("entities", [])
            relationships = retrieved.get("relationships", [])
            chunks = retrieved.get("chunks", [])
        except Exception:
            logger.warning("结构化召回失败", exc_info=True)
        dur1 = int((time.time() - t1) * 1000)

        trace.append({
            "name": "知识图谱检索", "icon": "🧠",
            "content": f"向量 + 图遍历召回 {len(chunks)} 段证据、{len(entities)} 个实体",
            "detail": f"证据 {len(chunks)} · 实体 {len(entities)} · 关系 {len(relationships)}",
            "status": "ok" if chunks or entities else "warning",
            "color": "#8B5CF6" if chunks or entities else "#F59E0B",
            "duration_ms": dur1,
        })

        # 2) 二跳扩展：沿命中实体的一跳邻居再拉一圈，补充图谱上下文。
        if entities:
            t2 = time.time()
            try:
                from src.backend.knowledge.lightrag_wrapper import expand_entity_neighbors
                seed_ids = [e["name"] for e in entities[:8] if e.get("name")]
                neigh = expand_entity_neighbors(seed_ids, max_nodes=60)
                entities, relationships, hop2_added = self._merge_graph(
                    entities, relationships, neigh)
                dur2 = int((time.time() - t2) * 1000)
                trace.append({
                    "name": "实体邻居扩展", "icon": "🔗",
                    "content": f"二跳邻居 +{hop2_added} 个实体",
                    "detail": f"扩展后共 {len(entities)} 实体 · {len(relationships)} 关系",
                    "status": "ok" if hop2_added else "info",
                    "color": "#6366F1" if hop2_added else "#94A3B8",
                    "duration_ms": dur2,
                })
            except Exception:
                logger.warning("二跳邻居扩展失败", exc_info=True)

        # 3) 为 chunks 补充 doc_name（邮件主题），让前端来源面板可读
        self._enrich_chunk_doc_names(chunks)

        # 4) LLM 合成答案（LightRAG mix：图遍历 + 向量 + 生成）
        answer = ""
        self._emit_progress("✍️ 正在生成回答…")
        t3 = time.time()
        try:
            from src.backend.knowledge.lightrag_wrapper import query_mail
            answer = query_mail(question, mode="mix",
                                user_prompt=self._SEMANTIC_ANSWER_STYLE)
        except Exception:
            logger.warning("LightRAG 合成失败", exc_info=True)
        dur3 = int((time.time() - t3) * 1000)

        trace.append({
            "name": "AI 答案合成", "icon": "✍️",
            "content": f"基于 {len(chunks)} 段证据生成回答",
            "detail": f"回答长度 {len(answer)} 字",
            "status": "ok" if answer and len(answer) > 20 else "warning",
            "color": "#10B981" if answer and len(answer) > 20 else "#F59E0B",
            "duration_ms": dur3,
        })

        # 兜底：合成不可用但已有检索证据 → 用 chunks/实体自行总结
        if (not answer or len(answer) <= 20) and (chunks or entities):
            self._emit_progress("✍️ 正在整理检索结果…")
            t4 = time.time()
            answer = self._summarize(question, entities, relationships, chunks)
            dur4 = int((time.time() - t4) * 1000)
            trace.append({
                "name": "本地总结（兜底）", "icon": "📝",
                "content": f"LLM 合成不可用，基于检索证据本地总结",
                "detail": f"回答长度 {len(answer)} 字",
                "status": "warning", "color": "#F59E0B",
                "duration_ms": dur4,
            })

        total_dur = int((time.time() - t0) * 1000)

        if not answer or len(answer) <= 20:
            trace.append({
                "name": "无结果", "icon": "⚠️",
                "content": "知识图谱中未找到相关信息",
                "status": "fail", "color": "#EF4444",
                "duration_ms": total_dur,
            })
            return "未找到相关信息，请先导入邮件数据。", trace, entities, relationships, chunks

        return answer, trace, entities, relationships, chunks

    def _enrich_chunk_doc_names(self, chunks: list[dict]):
        """为 chunks 补充 doc_name（邮件主题），让前端来源面板可读。"""
        if not chunks:
            return
        cache = self._get_cache()
        if cache is None:
            # 无 cache 时用 message_id 作 doc_name
            for c in chunks:
                if not c.get("doc_name"):
                    mid = c.get("doc_id") or c.get("file_path", "")
                    c["doc_name"] = mid[:60] if mid else "未知来源"
            return

        # 收集所有 message_id，批量查主题
        mids = list({c.get("doc_id") or c.get("file_path", "")
                      for c in chunks if c.get("doc_id") or c.get("file_path")})
        subjects: dict[str, str] = {}
        for mid in mids:
            if not mid:
                continue
            try:
                state = cache.get_mail_state(mid)
                if state:
                    subj = state.get("subject") or ""
                    sender = state.get("from_name") or state.get("from_addr") or ""
                    if subj:
                        label = subj[:50]
                        if sender:
                            label += f" — {sender[:15]}"
                        subjects[mid] = label
                        continue
                # 无状态或空主题：用 message_id 截断
                subjects[mid] = mid[:60]
            except Exception:
                subjects[mid] = mid[:60]

        for c in chunks:
            if c.get("doc_name"):
                continue
            mid = c.get("doc_id") or c.get("file_path", "")
            c["doc_name"] = subjects.get(mid) or (mid[:60] if mid else "未知来源")

    def _merge_graph(self, entities: list, relationships: list, neigh: dict) -> tuple:
        """合并二跳邻居实体/关系并去重。返回 (entities, relationships, 新增实体数)。"""
        ent_by_id: dict = {}
        for e in entities:
            key = str(e.get("id") or e.get("name") or "")
            if key:
                ent_by_id.setdefault(key, e)

        added = 0
        for e in neigh.get("entities", []):
            key = str(e.get("id") or e.get("name") or "")
            if key and key not in ent_by_id:
                ent_by_id[key] = e
                added += 1

        rel_seen = {
            (str(r.get("source_id")), str(r.get("target_id")))
            for r in relationships
        }
        merged_rels = list(relationships)
        for r in neigh.get("relationships", []):
            k = (str(r.get("source_id")), str(r.get("target_id")))
            if k not in rel_seen:
                rel_seen.add(k)
                merged_rels.append(r)

        return list(ent_by_id.values()), merged_rels, added

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
                c.get("content", "")[:400] for c in chunks[:10])
            system_prompt = (
                "你是一个精准的信息提取助手。请仔细阅读提供的邮件文档，"
                "提取与用户问题直接相关的所有信息。即使信息分散在多封邮件中，"
                "也要逐一列出。如果有具体数字（金额、日期、百分比），务必引用。"
                "不要说'未找到'除非你真的逐条检查了所有文档。"
            )
            summary = self._call_llm(
                f"问题：{question}\n\n邮件文档：\n{context}",
                max_tokens=800, system=system_prompt)
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
