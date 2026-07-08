"""
智能查询引擎
============
接收自然语言问题，通过 RAGFlow GraphRAG 执行图谱检索 + 语义搜索，
LLM 生成自然语言回答。
"""
import json, logging, time
from dataclasses import dataclass, field
from openai import OpenAI

from config.settings import get_settings

logger = logging.getLogger(__name__)

QUERY_ROUTER_PROMPT = """你是查询路由器。分析用户问题，决定用哪种查询方式。

问题: {question}

图谱可查的业务实体:
  节点: 客户公司(Company)、外部对接人(Contact)、内部负责人(Employee)、项目(Project)、邮件(Email)、部门(Department)
  关系: 对接人属于公司、员工对接联系人、员工管理项目、联系人参与项目、项目服务于公司

全文可查: 邮件正文、附件内容、项目摘要等文本信息

返回 JSON: {{"type": "graph|search|both", "reason": "理由"}}"""


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
    """智能查询引擎 — 自然语言 → RAGFlow GraphRAG → LLM 总结"""

    def __init__(self, ragflow_client=None):
        cfg = get_settings()
        self.llm = OpenAI(
            api_key=cfg.openai_api_key,
            base_url=f"{cfg.openai_base_url}/v1",
            timeout=60.0,
        )
        self.model = cfg.openai_model
        self.rf = ragflow_client

    def query(self, question: str) -> dict:
        """主查询入口：路由 → 图谱/搜索 → 总结"""
        start_time = time.time()
        trace = []

        # Step 1: 路由决策
        t0 = time.time()
        route = self._call_llm(QUERY_ROUTER_PROMPT.format(question=question))
        try:
            route_data = json.loads(route)
        except Exception:
            route_data = {"type": "both", "reason": "fallback"}
        trace.append({
            "name": "意图分析", "icon": "🧠",
            "content": f"路由: {route_data.get('type', 'both')}",
            "detail": route_data.get("reason", ""),
            "status": "ok", "color": "#8B5CF6",
            "duration_ms": int((time.time() - t0) * 1000),
        })

        # Step 2a: 图谱检索 (GraphRAG)
        entities, relationships, chunks = [], [], []
        if route_data.get("type") in ("graph", "both") and self.rf:
            t1 = time.time()
            try:
                graph_result = self.rf.graph_search(question)
                entities = graph_result.get("entities", [])
                relationships = graph_result.get("relationships", [])
                chunks = graph_result.get("chunks", [])
                trace.append({
                    "name": "图谱检索 (GraphRAG)", "icon": "🔗",
                    "content": f"实体 {len(entities)} 个, 关系 {len(relationships)} 条",
                    "status": "ok" if entities else "warning",
                    "color": "#3B82F6" if entities else "#F59E0B",
                    "duration_ms": int((time.time() - t1) * 1000),
                })
            except Exception as e:
                logger.warning("GraphRAG 检索失败: %s", e)
                trace.append({
                    "name": "图谱检索 (GraphRAG)", "icon": "🔗",
                    "content": f"失败: {e}",
                    "status": "fail", "color": "#EF4444",
                    "duration_ms": int((time.time() - t1) * 1000),
                })

        # Step 2b: 语义搜索（仅在需要时且 chunks 为空）
        if route_data.get("type") in ("search", "both") and self.rf and not chunks:
            t2 = time.time()
            try:
                chunks = self.rf.retrieve_chunks(question, top_k=10)
                trace.append({
                    "name": "语义搜索", "icon": "🔍",
                    "content": f"{len(chunks)} 个相关文本块",
                    "status": "ok" if chunks else "warning",
                    "color": "#10B981" if chunks else "#F59E0B",
                    "duration_ms": int((time.time() - t2) * 1000),
                })
            except Exception as e:
                logger.warning("语义搜索失败: %s", e)
                trace.append({
                    "name": "语义搜索", "icon": "🔍",
                    "content": f"失败: {e}",
                    "status": "fail", "color": "#EF4444",
                    "duration_ms": int((time.time() - t2) * 1000),
                })

        # Step 3: LLM 总结
        t3 = time.time()
        answer = self._summarize(question, entities, relationships, chunks)
        trace.append({
            "name": "LLM 总结", "icon": "✨",
            "content": answer[:80] + ("…" if len(answer) > 80 else ""),
            "status": "ok",
            "color": "#8B5CF6",
            "duration_ms": int((time.time() - t3) * 1000),
        })

        total_dur = int((time.time() - start_time) * 1000)

        # 转换为兼容前端的格式
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

    def _call_llm(self, prompt: str, max_tokens: int = 500) -> str:
        try:
            resp = self.llm.chat.completions.create(
                model=self.model, temperature=0,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error("LLM 调用失败: %s", e)
            return "{}"

    def _summarize(self, question: str, entities: list, relationships: list, chunks: list) -> str:
        """基于图谱实体 + 文本块生成自然语言回答"""
        parts = []

        if entities:
            entity_summary = {}
            for e in entities:
                t = e.get("type", "Entity")
                entity_summary[t] = entity_summary.get(t, 0) + 1
            parts.append(f"图谱匹配: {', '.join(f'{k}×{v}' for k, v in entity_summary.items())}")
            names = [e.get("name", "") for e in entities[:10] if e.get("name")]
            if names:
                parts.append(f"实体: {', '.join(names)}")

        if relationships:
            rel_summary = {}
            for r in relationships:
                t = r.get("type", "RELATED")
                rel_summary[t] = rel_summary.get(t, 0) + 1
            parts.append(f"关系: {', '.join(f'{k}×{v}' for k, v in rel_summary.items())}")

        if chunks:
            context = "\n---\n".join(
                c.get("content", "")[:400] for c in chunks[:5]
            )
            summary = self._call_llm(
                f"基于以下信息简洁回答（中文，<200字）：\n问题：{question}\n图谱：{'; '.join(parts)}\n文档：{context}",
                400,
            )
            if summary and summary != "{}":
                return summary

        if entities:
            return f"找到 {len(entities)} 个相关实体：{'、'.join(e.get('name','') for e in entities[:8] if e.get('name'))}"

        return "未找到相关结果，请尝试其他关键词或先导入邮件数据。"

    def _to_rows(self, entities: list, relationships: list) -> list[dict]:
        """将实体数据转为表格行（兼容前端 data frame 显示）"""
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
