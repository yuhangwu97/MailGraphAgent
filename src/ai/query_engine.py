"""
智能查询引擎
============
接收自然语言问题，通过 RAGFlow GraphRAG 执行图谱检索 + 语义搜索，
LLM 生成自然语言回答。
"""
import logging, time
from dataclasses import dataclass, field
from openai import OpenAI

from config.settings import get_settings

logger = logging.getLogger(__name__)

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
        """主查询入口：RAGFlow 原生一体化问答（检索+图谱+生成+引用）。"""
        start_time = time.time()
        answer, trace, entities, relationships, chunks = self._native_query(question)
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

    def _native_query(self, question: str):
        """RAGFlow 原生问答：一次调用完成检索+图谱+生成，trace 由响应的 references 重建。"""
        t0 = time.time()
        res = self.rf.chat_answer(question)
        answer = res.get("answer", "")
        refs = res.get("references", [])

        # 原生失败兜底：本地检索 + OpenAI 总结
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

        # trace 直接反映真实检索：引用了哪些来源
        trace = [{
            "name": "RAGFlow 原生问答（检索+图谱+生成）", "icon": "✨",
            "content": f"引用 {len(refs)} 段来源",
            "detail": "、".join(r.get("doc_name", "") for r in refs[:5] if r.get("doc_name")),
            "status": "ok", "color": "#8B5CF6",
            "duration_ms": int((time.time() - t0) * 1000),
        }]
        # references 即答案依据的文本块；实体/关系子图归“关系图谱”页展示，此处不再全图 dump
        return answer, trace, [], [], refs

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
