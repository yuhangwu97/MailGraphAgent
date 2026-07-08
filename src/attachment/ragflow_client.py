"""
RAGFlow API 客户端
==================
作为整个系统的核心引擎：文档解析 + GraphRAG 知识图谱 + 语义检索。
邮件正文 + AI 提取结果 → RAGFlow 知识库 → GraphRAG 图谱 + 语义检索。
"""
import json, logging, requests, time
from pathlib import Path
from typing import Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)

# RAGFlow GraphRAG 实体类型 → 业务实体类型映射
ENTITY_TYPE_MAP = {
    "organization": "Company",
    "company": "Company",
    "corporation": "Company",
    "person": "Contact",
    "contact": "Contact",
    "employee": "Employee",
    "manager": "Employee",
    "project": "Project",
    "initiative": "Project",
    "document": "Email",
    "email": "Email",
    "department": "Department",
}

# 反向映射：业务类型 → RAGFlow 类型提示
BUSINESS_TYPE_HINTS = {
    "Company": "organization",
    "Contact": "person",
    "Employee": "person",
    "Project": "project",
    "Email": "document",
    "Department": "organization",
}


class RAGFlowClient:
    """RAGFlow API 客户端 — 知识库管理 + 文档解析 + 语义检索"""

    def __init__(self):
        cfg = get_settings()
        self.base_url = cfg.ragflow_base_url.rstrip("/")  # http://localhost:9380
        self.api_key = cfg.ragflow_api_key or ""
        self.dataset_id: Optional[str] = None

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _api(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}/api/v1{path}"
        kwargs.setdefault("timeout", 30)
        try:
            resp = requests.request(method, url, headers=self.headers, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.ConnectionError:
            logger.warning("RAGFlow 未启动: %s", url)
            return {"code": -1, "message": "RAGFlow not running"}
        except Exception as e:
            logger.error("RAGFlow API 失败 %s %s: %s", method, path, e)
            return {"code": -1, "message": str(e)}

    # ═══════════════════════════════════════
    # 知识库管理
    # ═══════════════════════════════════════

    def create_dataset(self, name: str = "MailGraph", description: str = "邮件分析知识库") -> Optional[str]:
        """创建知识库，返回 dataset_id。

        GraphRAG 通过 parser_config.graphrag.use_graphrag 开启（chunk_method 用 naive）。
        embedding_model 为空时省略，使用 RAGFlow 租户默认 embedding 模型。
        """
        payload = {
            "name": name,
            "description": description,
            "chunk_method": "naive",
            "parser_config": {
                "chunk_token_num": 1024,
                "delimiter": "\n",
                "graphrag": {"use_graphrag": True},
            },
        }
        cfg = get_settings()
        if cfg.ragflow_embedding_model:
            payload["embedding_model"] = cfg.ragflow_embedding_model
        resp = self._api("POST", "/datasets", json=payload)
        if resp.get("code") == 0:
            self.dataset_id = resp["data"]["id"]
            logger.info("RAGFlow 知识库已创建: %s", self.dataset_id)
            return self.dataset_id
        logger.warning("创建知识库失败: %s", resp.get("message", ""))
        return None

    def get_or_create_dataset(self, name: str = "MailGraph") -> Optional[str]:
        """获取或创建知识库"""
        resp = self._api("GET", "/datasets", params={"name": name, "page": 1, "page_size": 10})
        if resp.get("code") == 0:
            for ds in resp.get("data", []):
                if ds.get("name") == name:
                    self.dataset_id = ds["id"]
                    logger.info("使用已有知识库: %s", self.dataset_id)
                    return self.dataset_id
        return self.create_dataset(name)

    # ═══════════════════════════════════════
    # 文档上传与解析
    # ═══════════════════════════════════════

    def upload_document(self, content: str, filename: str, metadata: dict = None) -> Optional[str]:
        """上传文档（纯文本）到知识库，返回 document_id。

        将文本内容写入临时文件后通过 multipart 上传，确保 RAGFlow 正确解析。
        """
        if not self.dataset_id:
            logger.warning("未设置知识库 ID，请先调用 get_or_create_dataset()")
            return None

        import tempfile, os
        suffix = os.path.splitext(filename)[1] or ".md"
        with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp_path = f.name

        try:
            url = f"{self.base_url}/api/v1/datasets/{self.dataset_id}/documents"
            with open(tmp_path, "rb") as f:
                files = {"file": (filename, f, "text/plain")}
                headers = {"Authorization": f"Bearer {self.api_key}"}
                resp = requests.post(url, headers=headers, files=files, timeout=60)
                data = resp.json()
                if data.get("code") == 0:
                    # RAGFlow returns data as list for multipart upload
                    result = data["data"]
                    if isinstance(result, list) and result:
                        doc_id = result[0]["id"]
                    else:
                        doc_id = result.get("id", "") if isinstance(result, dict) else ""
                    if doc_id:
                        logger.info("文档已上传: %s → %s", filename, doc_id)
                        return doc_id
                logger.warning("上传文档失败: %s — %s", filename, data.get("message", ""))
        except Exception as e:
            logger.error("上传文档异常: %s", e)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return None

    def upload_file(self, file_path: str, filename: str = None) -> Optional[str]:
    def start_parsing(self, document_ids: list[str]) -> bool:
        """触发文档解析（上传后需要手动触发）"""
        if not self.dataset_id or not document_ids:
            return False
        try:
            resp = self._api("POST", f"/datasets/{self.dataset_id}/chunks",
                             json={"document_ids": document_ids})
            ok = resp.get("code") == 0
            if ok:
                logger.info("已触发 %d 个文档的解析", len(document_ids))
            return ok
        except Exception as e:
            logger.error("触发解析失败: %s", e)
            return False

    def upload_file(self, file_path: str, filename: str = None) -> Optional[str]:
        """上传物理文件（PDF/Word/Excel 等）到 RAGFlow 解析"""
        if not self.dataset_id:
            logger.warning("未设置知识库 ID")
            return None

        path = Path(file_path)
        if not path.exists():
            logger.warning("文件不存在: %s", file_path)
            return None

        fname = filename or path.name
        # RAGFlow 文件上传接口（multipart）
        url = f"{self.base_url}/api/v1/datasets/{self.dataset_id}/documents"
        try:
            with open(path, "rb") as f:
                resp = requests.post(url, headers={"Authorization": f"Bearer {self.api_key}"},
                                     files={"file": (fname, f)}, timeout=60)
                data = resp.json()
                if data.get("code") == 0:
                    doc_id = data["data"]["id"]
                    logger.info("文件已上传: %s → %s", fname, doc_id)
                    return doc_id
                logger.warning("上传文件失败: %s", data.get("message", ""))
        except Exception as e:
            logger.error("上传文件异常: %s", e)
        return None

    def wait_for_parsing(self, document_ids: list[str], timeout: int = 120) -> bool:
        """等待文档解析完成。

        RAGFlow 状态码: 1=处理中, 2=已完成, -1=失败。
        """
        if not self.dataset_id or not document_ids:
            return False
        ids_set = set(document_ids)
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = self._api("GET", f"/datasets/{self.dataset_id}/documents",
                                 params={"page": 1, "page_size": 100})
                if resp.get("code") != 0:
                    time.sleep(2)
                    continue
                docs = resp.get("data", {}).get("docs", [])
                if not isinstance(docs, list):
                    docs = resp.get("data", [])
                all_done = True
                for doc in docs:
                    doc_id = doc.get("id", "")
                    if doc_id not in ids_set:
                        continue
                    status = doc.get("status")
                    # status: 1=processing, 2=completed, others=error
                    if status != 2 and status != "2" and status != "completed":
                        all_done = False
                        break
                if all_done:
                    logger.info("所有文档解析完成 (%d docs)", len(ids_set))
                    return True
            except Exception as e:
                logger.warning("检查解析状态异常: %s", e)
            time.sleep(2)
        logger.warning("文档解析超时 (%ds)", timeout)
        return False

    # ═══════════════════════════════════════
    # 语义检索
    # ═══════════════════════════════════════

    def retrieve_chunks(self, query: str, top_k: int = 10) -> list[dict]:
        """语义检索 — 返回最相关的文本块"""
        if not self.dataset_id:
            return []

        resp = self._api("POST", f"/datasets/{self.dataset_id}/chunks",
                         json={"keywords": query, "top_k": top_k})
        if resp.get("code") == 0:
            chunks = []
            for c in resp.get("data", {}).get("chunks", []):
                chunks.append({
                    "content": c.get("content", ""),
                    "doc_name": c.get("doc_name", ""),
                    "score": c.get("similarity", 0),
                    "positions": c.get("positions", []),
                })
            logger.info("RAGFlow 检索: '%s' → %d 结果", query[:50], len(chunks))
            return chunks
        return []

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """语义搜索 + 返回结构化结果"""
        return self.retrieve_chunks(query, top_k)

    def generate_answer(self, query: str) -> str:
        """基于知识库的 RAG 回答"""
        if not self.dataset_id:
            return "RAGFlow 未就绪"

        resp = self._api("POST", f"/datasets/{self.dataset_id}/completions",
                         json={"question": query})
        if resp.get("code") == 0:
            return resp.get("data", {}).get("answer", "")
        return ""

    # ═══════════════════════════════════════
    # GraphRAG — 知识图谱管理
    # ═══════════════════════════════════════

    def enable_graphrag(self) -> bool:
        """确认/启用 GraphRAG（parser_config.graphrag.use_graphrag）"""
        if not self.dataset_id:
            return False
        try:
            resp = self._api("PUT", f"/datasets/{self.dataset_id}",
                             json={"parser_config": {"graphrag": {"use_graphrag": True}}})
            if resp.get("code") == 0:
                logger.info("GraphRAG 已启用: %s", self.dataset_id)
                return True
            logger.warning("启用 GraphRAG 失败: %s", resp.get("message", ""))
            return False
        except Exception as e:
            logger.error("启用 GraphRAG 异常: %s", e)
            return False

    def _get_knowledge_graph(self) -> dict:
        """获取 RAGFlow 知识图谱（实体 + 关系 + 思维导图）"""
        if not self.dataset_id:
            return {}
        try:
            resp = self._api("GET", f"/datasets/{self.dataset_id}/knowledge_graph")
            if resp.get("code") == 0:
                return resp.get("data", {}).get("graph", {})
        except Exception as e:
            logger.warning("获取知识图谱失败: %s", e)
        return {}

    def get_graph_entities(self, page: int = 1, page_size: int = 500) -> list[dict]:
        """获取知识图谱中的所有实体节点"""
        if not self.dataset_id:
            return []

        all_entities = []
        graph = self._get_knowledge_graph()
        raw_entities = graph.get("entities", graph.get("nodes", []))
        for ent in (raw_entities or []):
            etype = ent.get("type", ent.get("entity_type", ""))
            mapped_type = ENTITY_TYPE_MAP.get(etype.lower(), etype or "Entity")
            all_entities.append({
                "id": ent.get("id", ent.get("entity_id", "")),
                "name": ent.get("name", ent.get("entity_name", "")),
                "type": mapped_type,
                "description": ent.get("description", ""),
                "properties": {k: v for k, v in ent.items()
                              if k not in ("id", "entity_id", "name", "entity_name", "type", "entity_type", "description")},
            })
        logger.info("GraphRAG 实体: %d 个", len(all_entities))
        return all_entities

    def get_graph_relationships(self, page: int = 1, page_size: int = 1000) -> list[dict]:
        """获取知识图谱中的所有关系边"""
        if not self.dataset_id:
            return []

        graph = self._get_knowledge_graph()
        raw_rels = graph.get("relationships", graph.get("edges", []))
        all_rels = []
        for rel in (raw_rels or []):
            all_rels.append({
                "source_id": rel.get("source_id", rel.get("src_id", "")),
                "target_id": rel.get("target_id", rel.get("tgt_id", "")),
                "type": rel.get("type", rel.get("relation_type", "RELATED_TO")),
                "properties": {k: v for k, v in rel.items()
                              if k not in ("source_id", "src_id", "target_id", "tgt_id", "type", "relation_type")},
            })
        logger.info("GraphRAG 关系: %d 条", len(all_rels))
        return all_rels

    def graph_search(self, query: str, top_k: int = 20) -> dict:
        """GraphRAG 语义图检索：chunk 检索 + 全知识图谱数据"""
        result = {
            "entities": [],
            "relationships": [],
            "chunks": [],
            "answer": "",
        }

        if not self.dataset_id:
            return result

        # 1. 获取全知识图谱
        graph = self._get_knowledge_graph()
        result["entities"] = self._parse_entities(
            graph.get("entities", graph.get("nodes", [])))
        result["relationships"] = self._parse_relations(
            graph.get("relationships", graph.get("edges", [])))

        # 2. 语义搜索
        chunks = self.retrieve_chunks(query, top_k)
        result["chunks"] = chunks

        # 3. 生成回答
        if chunks:
            result["answer"] = self.generate_answer(query)

        return result

    def _parse_entities(self, raw_entities: list) -> list[dict]:
        """解析并映射实体类型"""
        parsed = []
        for ent in (raw_entities or []):
            etype = ent.get("type", ent.get("entity_type", ""))
            mapped_type = ENTITY_TYPE_MAP.get(etype.lower(), etype or "Entity")
            parsed.append({
                "id": ent.get("id", ent.get("entity_id", "")),
                "name": ent.get("name", ent.get("entity_name", "")),
                "type": mapped_type,
                "description": ent.get("description", ""),
                "properties": ent.get("properties", {}),
            })
        return parsed

    def _parse_relations(self, raw_rels: list) -> list[dict]:
        """解析关系数据"""
        parsed = []
        for rel in (raw_rels or []):
            parsed.append({
                "source_id": rel.get("source_id", rel.get("src_id", "")),
                "target_id": rel.get("target_id", rel.get("tgt_id", "")),
                "type": rel.get("type", rel.get("relation_type", "RELATED_TO")),
                "properties": rel.get("properties", {}),
            })
        return parsed

    # ═══════════════════════════════════════
    # 知识库导入 — 邮件提取结果
    # ═══════════════════════════════════════

    def upload_email_extraction(self, metadata: dict, extraction: dict) -> Optional[str]:
        """上传 AI 提取结果到 RAGFlow 知识库（作为结构化文档）

        Args:
            metadata: 邮件元数据 {message_id, subject, from_addr, date, ...}
            extraction: OpenAI 提取的 JSON 结果

        Returns:
            document_id 或 None
        """
        if not self.dataset_id:
            logger.warning("未设置知识库 ID")
            return None

        # 构建结构化文档内容 — 让 RAGFlow GraphRAG 更好地识别实体和关系
        parts = []
        mid = metadata.get("message_id", "")
        subject = metadata.get("subject", "")
        from_addr = metadata.get("from_addr", "")
        date_str = metadata.get("date", "")

        parts.append(f"# 邮件: {subject}")
        parts.append(f"发件人: {from_addr}")
        parts.append(f"日期: {date_str}")
        parts.append(f"Message-ID: {mid}")

        company = extraction.get("company", {}) or {}
        if company.get("name"):
            parts.append(f"\n## 客户公司")
            parts.append(f"公司名称: {company['name']}")
            if company.get("aliases"):
                parts.append(f"别名: {', '.join(company['aliases'])}")

        contacts = extraction.get("contacts", [])
        if contacts:
            parts.append(f"\n## 外部对接人")
            for c in contacts:
                parts.append(f"- {c.get('name','')} ({c.get('role','')}, {c.get('email','')})")

        owners = extraction.get("internal_owners", [])
        if owners:
            parts.append(f"\n## 内部负责人")
            for o in owners:
                parts.append(f"- {o.get('name','')} ({o.get('role','')}, {o.get('department','')})")

        projects = extraction.get("projects", [])
        if projects:
            parts.append(f"\n## 项目")
            for p in projects:
                parts.append(f"- {p.get('name','')} (状态: {p.get('status','')})")
                if p.get("progress"):
                    parts.append(f"  进度: {p['progress']}")
                if p.get("risk_points"):
                    parts.append(f"  风险: {', '.join(p['risk_points'])}")

        if extraction.get("summary"):
            parts.append(f"\n## 摘要\n{extraction['summary']}")

        content = "\n".join(parts)
        filename = f"mail_{mid[:32]}.md"

        # 上传为结构化文档
        return self.upload_document(content, filename, metadata={
            "message_id": mid,
            "subject": subject,
            "from_addr": from_addr,
            "type": "email_extraction",
        })

    def upload_batch_extractions(self, extractions: list[dict]) -> list[str]:
        """批量上传提取结果

        Args:
            extractions: [{metadata: {...}, extraction: {...}}, ...]

        Returns:
            成功上传的 document_id 列表
        """
        doc_ids = []
        for item in extractions:
            doc_id = self.upload_email_extraction(
                item.get("metadata", {}),
                item.get("extraction", {}),
            )
            if doc_id:
                doc_ids.append(doc_id)
        logger.info("批量上传完成: %d/%d", len(doc_ids), len(extractions))
        if doc_ids:
            self.start_parsing(doc_ids)
        return doc_ids


# 全局单例
_client: Optional[RAGFlowClient] = None


def get_ragflow_client() -> RAGFlowClient:
    global _client
    if _client is None:
        _client = RAGFlowClient()
    return _client
