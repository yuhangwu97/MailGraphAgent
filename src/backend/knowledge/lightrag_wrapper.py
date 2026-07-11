"""LightRAG 邮件知识图谱 — 增量插入 + Neo4j 持久化。

替换 RAGFlow run_graphrag(全量)，改为逐文档 ainsert(增量)。
查询走 LightRAG mix 模式：图遍历 + 向量搜索 + LLM 回答。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path

try:
    from lightrag import LightRAG, QueryParam
    from lightrag.llm.openai import openai_complete_if_cache, openai_embed
    from lightrag.utils import EmbeddingFunc
except ModuleNotFoundError:  # 本地测试/降级检索允许不安装 LightRAG
    LightRAG = None
    QueryParam = None
    openai_complete_if_cache = None
    openai_embed = None
    EmbeddingFunc = None

from config.settings import get_settings

logger = logging.getLogger(__name__)

_instance: LightRAG | None = None
_instance_lock = threading.Lock()

# 邮件-项目知识图谱的领域实体分类指引，注入 LightRAG 抽取提示（{entity_types_guidance}）。
# 标签用 TitleCase 便于 LLM 理解，但 LightRAG 存储时会统一小写去空格，
# 故下游（看板）须按小写比较：project / person / organization / …
_DOMAIN_ENTITY_GUIDANCE = """Classify each entity using one of the following types. If no type fits, use `Other`.

- Project: A concrete project, engagement, initiative, or system-integration effort (e.g. an ERP integration, a delivery project).
- Person: A named individual — client contacts, colleagues, employees, or any human participant.
- Organization: A company, supplier, customer, department, team, or institution.
- Document: A file, contract, report, spreadsheet, attachment, or other written artifact.
- Task: A task, test case, deliverable, milestone, action item, or to-do.
- System: A software system, module, platform, application, or component.
- Event: A meeting, signing, review, delivery, or other occurrence in time.
- Location: A geographic place, address, or physical/logical site.
- Other: Anything that does not fit the types above."""

# LightRAG 的 Neo4j / Milvus 异步 driver 会绑定到「创建它们的 event loop」。
# 之前 initialize_storages 跑在一个临时 loop 上、随后被 close，而 insert/query
# 又各自新建 loop，导致 driver 的 Future 属于已关闭的 loop →
# "Task ... got Future ... attached to a different loop"。
#
# 解决办法：进程内维护一个常驻后台 loop（独立线程 run_forever），把
# initialize / insert / query 全部通过 run_coroutine_threadsafe 派发到它上面。
# 这样所有 driver 从创建到使用都在同一个 loop，生命周期与进程一致。
# 调用方（run_ingest / query）都跑在 run_in_executor 的 worker 线程里，
# 用 .result() 阻塞等待不会冻住 FastAPI 主 loop。
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """获取（或惰性创建）LightRAG 专用的常驻事件循环。"""
    global _loop
    if _loop is not None and not _loop.is_closed():
        return _loop
    with _loop_lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=loop.run_forever,
            name="lightrag-loop",
            daemon=True,
        ).start()
        _loop = loop
        logger.info("LightRAG dedicated event loop started")
        return _loop


def _run(coro):
    """在 LightRAG 专用 loop 上执行协程并阻塞返回结果。"""
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result()


def _get_llm_func():
    if openai_complete_if_cache is None:
        raise RuntimeError("LightRAG is not installed")
    cfg = get_settings()
    base_url = f"{cfg.openai_base_url}/v1" if not cfg.openai_base_url.endswith("/v1") else cfg.openai_base_url

    async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs):
        return await openai_complete_if_cache(
            model=cfg.openai_model,
            prompt=prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            api_key=cfg.openai_api_key,
            base_url=base_url,
            **kwargs,
        )

    return llm_model_func


def _get_embedding_func() -> EmbeddingFunc:
    if EmbeddingFunc is None or openai_embed is None:
        raise RuntimeError("LightRAG is not installed")
    cfg = get_settings()
    base_url = f"{cfg.openai_base_url}/v1" if not cfg.openai_base_url.endswith("/v1") else cfg.openai_base_url

    # 注意：lightrag.llm.openai.openai_embed 本身已被
    # @wrap_embedding_func_with_attrs(embedding_dim=1536) 装饰，会强制按 1536 维
    # 向 API 请求并对返回结果做校验。DashScope text-embedding-v3 只返回 1024 维，
    # 于是触发 "dimension mismatch: expected 1536"（与外层配置的 1024 无关）。
    # 解决：调用其未装饰的 .func（保留内部 retry/截断），显式传 embedding_dim，
    # 让 API 按 cfg.embedding_dim 维度返回；维度校验交给下方外层 EmbeddingFunc。
    raw_embed = openai_embed.func

    return EmbeddingFunc(
        embedding_dim=cfg.embedding_dim,
        max_token_size=8192,
        func=lambda texts: raw_embed(
            texts=texts,
            model=cfg.embedding_model,
            api_key=cfg.openai_api_key,
            base_url=base_url,
            embedding_dim=cfg.embedding_dim,
        ),
    )


def get_lightrag() -> LightRAG:
    """获取或初始化 LightRAG 单例。"""
    if LightRAG is None:
        raise RuntimeError("LightRAG is not installed")
    global _instance
    if _instance is not None:
        return _instance

    with _instance_lock:
        if _instance is not None:
            return _instance

        cfg = get_settings()
        working_dir = os.path.join(str(cfg.data_dir), "lightrag")
        os.makedirs(working_dir, exist_ok=True)

        # Milvus 环境变量
        os.environ.setdefault("MILVUS_URI", cfg.milvus_uri)
        os.environ.setdefault("MILVUS_DB_NAME", cfg.milvus_db_name)
        # Neo4j 环境变量
        os.environ.setdefault("NEO4J_URI", cfg.resolved_neo4j_uri())
        os.environ.setdefault("NEO4J_USERNAME", cfg.neo4j_user)
        os.environ.setdefault("NEO4J_PASSWORD", cfg.neo4j_password)
        # Redis 环境变量（LightRAG RedisKVStorage / RedisDocStatusStorage 读 REDIS_URI）
        _pw = f":{cfg.redis_password}@" if cfg.redis_password else ""
        os.environ.setdefault(
            "REDIS_URI", f"redis://{_pw}{cfg.redis_host}:{cfg.redis_port}/{cfg.redis_db}")

        rag = LightRAG(
            working_dir=working_dir,
            llm_model_func=_get_llm_func(),
            embedding_func=_get_embedding_func(),
            graph_storage="Neo4JStorage",
            vector_storage="MilvusVectorDBStorage",
            # KV / doc_status 用 Redis 而非本地 JSON 文件：api 与 worker 两个进程
            # 需共享 LightRAG 状态；JsonKVStorage 是本地文件（且查询会写 llm 缓存），
            # 多进程并发会写坏。改 Redis 后全部状态落 Neo4j+Milvus+Redis，进程安全。
            kv_storage="RedisKVStorage",
            doc_status_storage="RedisDocStatusStorage",
            log_file_path=os.path.join(working_dir, "lightrag.log"),
            # 邮件-项目领域实体分类：让抽取产出 Project / Person / Organization /
            # Document / Task / System / Event 等类型，供看板按类统计。
            # 注意 LightRAG 会把类型统一转小写并去空格（operate.py），故看板侧需按
            # 小写比较（project / person / …）。
            # language=Chinese：默认 DEFAULT_SUMMARY_LANGUAGE=English，会把中文邮件的
            # 实体名/描述翻译成英文（如“赵阳”→“Zhao Yang”）；设为中文以保留原文。
            addon_params={
                "entity_types_guidance": _DOMAIN_ENTITY_GUIDANCE,
                "language": "Chinese",
            },
        )

        # 在专用 loop 上初始化存储 —— driver 从此绑定该 loop，
        # 与后续 ainsert/aquery 共用，避免 cross-loop Future。
        _run(_async_init(rag))

        _instance = rag
        logger.info("LightRAG initialized (Neo4j + Milvus)")
        return _instance


async def _async_init(rag: LightRAG):
    """在专用 loop 上完成 LightRAG 的标准初始化序列。"""
    from lightrag.kg.shared_storage import initialize_pipeline_status

    await rag.initialize_storages()
    await initialize_pipeline_status()


# Milvus VARCHAR 主键上限 64 字节且不可截断；LightRAG 的 chunk 主键格式为
# f"{doc_id}-chunk-{order:03d}"，给后缀留足空间（含 >999 chunk 的 4 位序号）。
_DOC_ID_BUDGET = 52


def _bounded_doc_id(mail_id: str) -> str:
    """返回一个长度受限的 doc_id，保证 f"{doc_id}-chunk-NNN" 不超过 Milvus 主键 64 字节。

    附件 doc_id = "<message_id>:<文件名>"，中文文件名 UTF-8 每字 3 字节，极易超限
    （见报错 "primary key ... exceeds 64 bytes"）。超限时改用确定性短哈希——
    同一 mail_id 恒定映射到同一 doc_id，保证 reprocess 幂等（LightRAG 按 doc_id
    resume/purge）。file_paths 仍传完整 mail_id，证据溯源不受影响。
    """
    if len(mail_id.encode("utf-8")) <= _DOC_ID_BUDGET:
        return mail_id
    digest = hashlib.blake2s(mail_id.encode("utf-8")).hexdigest()[:16]
    return f"mail-{digest}"


def _working_dir() -> Path:
    return Path(get_settings().data_dir) / "lightrag"


def _load_json_store(filename: str) -> dict:
    path = _working_dir() / filename
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read LightRAG store %s", path, exc_info=True)
        return {}


def _mail_id_from_file_path(file_path: str) -> str:
    """从 LightRAG file_path/doc_id 还原 message_id，兼容附件 '<mid>:filename'。"""
    value = str(file_path or "")
    if not value:
        return ""
    if value.startswith("<") and ">:" in value:
        return value.split(">:", 1)[0] + ">"
    return value.split(":", 1)[0]


def _query_terms(query: str) -> list[str]:
    """轻量关键词切分，用于本地 JSON store 降级检索。"""
    q = (query or "").strip()
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_.+-]+|[\u4e00-\u9fff]+", q):
        token = token.lower().strip()
        if not token:
            continue
        terms.add(token)
        # 中文短 query 经常是“华远项目风险”这种组合，补 2/3-gram 提高召回。
        if re.search(r"[\u4e00-\u9fff]", token) and len(token) > 2:
            for n in (2, 3):
                for i in range(0, len(token) - n + 1):
                    terms.add(token[i:i + n])
    return sorted(terms, key=len, reverse=True)


def _score_text(query: str, text: str) -> int:
    haystack = (text or "").lower()
    if not haystack:
        return 0
    score = 0
    q = (query or "").lower().strip()
    if q and q in haystack:
        score += 12
    for term in _query_terms(query):
        if len(term) < 2:
            continue
        if term in haystack:
            score += 2 + min(len(term), 6)
    return score


def _normalize_chunk(chunk_id: str, chunk: dict, score: int = 0) -> dict:
    fp = chunk.get("file_path") or chunk.get("doc_id") or chunk.get("full_doc_id") or ""
    mid = _mail_id_from_file_path(fp)
    return {
        "content": chunk.get("content", "") or "",
        "doc_id": mid,
        "file_path": fp,
        "chunk_id": chunk.get("_id") or chunk_id,
        "score": score,
    }


def _dedupe_chunks(chunks: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for chunk in chunks:
        key = chunk.get("chunk_id") or (chunk.get("doc_id"), chunk.get("content", "")[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(chunk)
    return out


def _local_retrieve_chunks(query: str, top_k: int = 20) -> list[dict]:
    """从 LightRAG 本地 JSON KV 兜底检索 chunk，保证证据可归属 message_id。"""
    text_chunks = _load_json_store("kv_store_text_chunks.json")
    entity_chunks = _load_json_store("kv_store_entity_chunks.json")
    full_entities = _load_json_store("kv_store_full_entities.json")

    scored: dict[str, int] = {}

    # 1) 实体倒排：实体名与 query 相互包含时，直接给相关 chunk 加权。
    q_lower = (query or "").lower()
    for name, data in entity_chunks.items():
        n_lower = str(name).lower()
        if n_lower and (n_lower in q_lower or q_lower in n_lower or _score_text(query, n_lower)):
            for cid in data.get("chunk_ids", []) or []:
                scored[cid] = max(scored.get(cid, 0), 18)

    # 2) 文档级实体列表：用于“项目/人名/客户”类 query 的补召回。
    for doc_id, data in full_entities.items():
        names = " ".join(str(n) for n in data.get("entity_names", []) or [])
        score = _score_text(query, names)
        if score:
            for cid, chunk in text_chunks.items():
                if chunk.get("full_doc_id") == doc_id:
                    scored[cid] = max(scored.get(cid, 0), score + 6)

    # 3) 正文直接匹配。
    for cid, chunk in text_chunks.items():
        score = _score_text(query, chunk.get("content", ""))
        if score:
            scored[cid] = max(scored.get(cid, 0), score)

    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    chunks = [
        _normalize_chunk(cid, text_chunks[cid], score)
        for cid, score in ranked
        if cid in text_chunks
    ]
    return _dedupe_chunks(chunks)[:top_k]


def _local_graph_context(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    """根据命中的 chunk/doc，从本地实体/关系摘要补图谱上下文。"""
    doc_ids = {
        c.get("file_path") or c.get("doc_id") or ""
        for c in chunks
    }
    doc_ids.update(c.get("doc_id", "") for c in chunks)

    full_entities = _load_json_store("kv_store_full_entities.json")
    full_relations = _load_json_store("kv_store_full_relations.json")

    entities: dict[str, dict] = {}
    relationships: list[dict] = []
    rel_seen = set()

    for doc_id in list(doc_ids):
        if not doc_id:
            continue
        # 附件 file_path 是 <mid>:filename，而 full_entities 可能用短 doc_id；
        # 所以这里同时尝试完整 file_path 和 message_id。
        candidates = [doc_id, _mail_id_from_file_path(doc_id)]
        for key in candidates:
            data = full_entities.get(key) or {}
            for name in data.get("entity_names", []) or []:
                entities.setdefault(str(name), {
                    "id": str(name),
                    "name": str(name),
                    "type": "Entity",
                    "description": "",
                })
            rels = (full_relations.get(key) or {}).get("relation_pairs", []) or []
            for pair in rels:
                if len(pair) < 2:
                    continue
                src, tgt = str(pair[0]), str(pair[1])
                rel_key = (src, tgt)
                if rel_key in rel_seen:
                    continue
                rel_seen.add(rel_key)
                relationships.append({
                    "source_id": src,
                    "target_id": tgt,
                    "type": "related",
                    "description": "",
                    "weight": 1.0,
                })

    return list(entities.values()), relationships


def insert_mail(text: str, mail_id: str) -> str:
    """增量插入一封邮件/附件到知识图谱（幂等）。

    file_path 恒为完整 mail_id，使检索出的 chunk 能溯源回 message_id（证据归属）；
    doc_id 在过长时降级为确定性短哈希以满足 Milvus 主键长度限制。返回 mail_id。

    幂等跳过：LightRAG 对「同一 file_path 再插一次」不会静默跳过，而是造一条
    dup-FAILED 合成记录（噪音，且会虚增工作台失败数）。所以插入前先查 doc 状态，
    已 PROCESSED/PROCESSING 的直接跳过，从源头不产生 dup。真要重建应走 reprocess
    的 delete→insert，而非重复插入。
    """
    rag = get_lightrag()
    doc_id = _bounded_doc_id(mail_id)
    try:
        existing = _run(rag.doc_status.get_by_id(doc_id))
        if existing:
            status = str(existing.get("status", "")).lower()
            if "processed" in status or "processing" in status:
                logger.info("LightRAG skip already-ingested doc: %s (%s)", mail_id[:50], status)
                return mail_id
    except Exception:
        # 查状态失败不阻断插入，交给 LightRAG 自身去重兜底
        logger.debug("doc status precheck failed for %s", mail_id[:50], exc_info=True)
    _run(rag.ainsert(input=text, ids=doc_id, file_paths=mail_id))
    logger.info("LightRAG inserted: %s", mail_id[:50])
    return mail_id


def get_doc_status_counts() -> dict:
    """返回 LightRAG 文档处理状态计数：{pending, processing, processed, failed, duplicate}。

    只在 LightRAG 已初始化时读取——纯查状态不值得为它冷启动整套
    Neo4j + Milvus 存储。未初始化则返回全 0（此时也确实还没有文档在处理）。

    关键：LightRAG 检测到重复插入（同 file_path 再插一次）时，会造一条 `dup-xxx`
    的合成记录并标成 FAILED（见 lightrag/pipeline.py）。这些不是真失败——原文档已
    PROCESSED，内容也在图里。因此从 failed 里剔除这些 dup 标记，单列到 duplicate，
    让工作台「失败」只反映真正的处理失败。
    """
    empty = {"pending": 0, "processing": 0, "processed": 0, "failed": 0, "duplicate": 0}
    if _instance is None:
        return empty
    try:
        counts = _run(_instance.get_processing_status())
        out = dict(empty)
        for k, v in (counts or {}).items():
            key = str(k).lower()
            if key in out:
                out[key] = int(v)

        # 从 failed 中分离出 dup 合成标记（doc_id 前缀 dup- 或 metadata.is_duplicate）
        if out["failed"]:
            from lightrag.base import DocStatus
            failed_docs = _run(_instance.doc_status.get_docs_by_status(DocStatus.FAILED)) or {}
            dup = 0
            for doc_id, rec in failed_docs.items():
                meta = getattr(rec, "metadata", None) or {}
                if str(doc_id).startswith("dup-") or meta.get("is_duplicate"):
                    dup += 1
            out["duplicate"] = dup
            out["failed"] = max(0, out["failed"] - dup)
        return out
    except Exception:
        logger.warning("get_doc_status_counts failed", exc_info=True)
        return empty


def get_pipeline_status() -> dict:
    """返回 LightRAG 建图 pipeline 的实时状态：{busy, latest_message, job_name}。

    busy=True 表示正在增量建图（抽取实体/关系/向量化）。未初始化视为空闲。
    """
    idle = {"busy": False, "latest_message": "", "job_name": ""}
    if _instance is None:
        return idle
    try:
        from lightrag.kg.shared_storage import get_namespace_data

        data = _run(get_namespace_data("pipeline_status"))
        if not data:
            return idle
        history = data.get("history_messages") or []
        return {
            "busy": bool(data.get("busy", False)),
            "latest_message": str(data.get("latest_message") or (history[-1] if history else "")),
            "job_name": str(data.get("job_name") or ""),
        }
    except Exception:
        logger.warning("get_pipeline_status failed", exc_info=True)
        return idle


def query_mail(question: str, mode: str = "mix", top_k: int = 20,
               user_prompt: str | None = None) -> str:
    """查询邮件知识图谱。

    mode: local(邻居遍历) | global(社区摘要) | mix(混合) | naive(纯向量)
    user_prompt: 附加作答指令（注入回答模板的 Additional Instructions），
                 用于引导语气/侧重，如"正面陈述此人相关邮件而非强调其非发件人"。
    """
    rag = get_lightrag()
    result = _run(rag.aquery(
        question,
        param=QueryParam(mode=mode, top_k=top_k, only_need_context=False,
                         user_prompt=user_prompt),
    ))
    return result


def retrieve_mail_sources(topic: str, mode: str = "mix", top_k: int = 20) -> list[dict]:
    """按主题检索命中的邮件 chunk，返回带来源的证据列表（不生成回答）。

    因插入时 file_paths=mail_id，命中 chunk 的 file_path 即 message_id，
    可直接用于 hybrid 查询的 topic 命中 → message_id 求交。

    返回: [{"content": str, "doc_id": message_id, "file_path": message_id}, ...]
    """
    return retrieve_mail_graph(topic, mode=mode, top_k=top_k)["chunks"]


def retrieve_mail_graph(question: str, mode: str = "mix", top_k: int = 20) -> dict:
    """结构化检索：一次返回 {entities, relationships, chunks}（检索层，未经 LLM）。

    复用 LightRAG aquery_data（与 mix 相同检索路径：向量 + 图遍历 + 高低层关键词
    双路召回）。retrieve_mail_sources 早先只取了 chunks，把 entities/relationships
    丢了；content 路径需要这三样一并返回前端做图谱证据展示与来源溯源。

    字段归一化为与 neo4j_client / 前端一致的形状：
      entities:      {id, name, type, description}
      relationships: {source_id, target_id, type, description, weight}
      chunks:        {content, doc_id(message_id), file_path}
    """
    empty = {"entities": [], "relationships": [], "chunks": []}
    try:
        rag = get_lightrag()
        data = _run(rag.aquery_data(
            question,
            param=QueryParam(mode=mode, top_k=top_k, only_need_context=True),
        ))
    except Exception:
        logger.warning("retrieve_mail_graph failed for %s; using local KV fallback",
                       question[:50], exc_info=True)
        chunks = _local_retrieve_chunks(question, top_k=top_k)
        entities, relationships = _local_graph_context(chunks)
        return {"entities": entities, "relationships": relationships, "chunks": chunks}

    payload = (data or {}).get("data", {}) or {}

    entities: list[dict] = []
    for e in payload.get("entities", []) or []:
        name = e.get("entity_name") or e.get("name") or ""
        if not name:
            continue
        entities.append({
            "id": name,
            "name": name,
            "type": e.get("entity_type") or "Entity",
            "description": e.get("description", "") or "",
        })

    relationships: list[dict] = []
    for r in payload.get("relationships", []) or []:
        src = r.get("src_id") or r.get("source_id") or ""
        tgt = r.get("tgt_id") or r.get("target_id") or ""
        if not src or not tgt:
            continue
        relationships.append({
            "source_id": src,
            "target_id": tgt,
            "type": r.get("keywords") or "related",
            "description": r.get("description", "") or "",
            "weight": r.get("weight", 1.0),
        })

    chunks: list[dict] = []
    for c in payload.get("chunks", []) or []:
        # file_path 即插入时写入的 mail_id（message_id）；兼容附件 "<mid>:<filename>"
        fp = c.get("file_path") or c.get("doc_id") or ""
        mid = _mail_id_from_file_path(fp)
        chunks.append({
            "content": c.get("content", "") or "",
            "doc_id": mid,
            "file_path": fp,
            "chunk_id": c.get("_id") or c.get("id") or "",
            "score": c.get("score", 0),
        })

    # 某些 LightRAG 版本的 aquery_data 只返回实体/关系，不带可溯源 chunks；
    # 这种情况下补一次本地 KV 检索，保证前端和 hybrid 都有证据归属。
    if not chunks:
        chunks = _local_retrieve_chunks(question, top_k=top_k)
    if chunks and not entities:
        entities, local_rels = _local_graph_context(chunks)
        relationships = relationships or local_rels

    return {
        "entities": entities,
        "relationships": relationships,
        "chunks": _dedupe_chunks(chunks)[:top_k],
    }


def expand_entity_neighbors(names: list[str], max_nodes: int = 60) -> dict:
    """二跳邻居扩展：对首轮命中的实体取其一跳邻域，走 LightRAG 原生 get_knowledge_graph。

    为什么用原生 API 而非手写 Cypher：LightRAG 的 Neo4JStorage 会自动按 workspace
    label 限定（`MATCH (n:`base`)`）、用无类型 `-[r]-` 匹配，不耦合关系类型名。
    手写 `:DIRECTED` 是对内部 schema 的硬编码，LightRAG 换实现即失效——这才是
    「LightRAG 模式下读图的正确姿势」。

    注意原生 KG 的坑：node.id / edge.source / edge.target 是子图内部序号（"0"/"106"），
    实体名在 node.labels / node.properties["entity_id"]，故必须建 id→实体名映射再翻译边。
    跨多个种子调用时同一实体的内部序号会变，因此一律按实体名去重。

    LightRAG 未初始化或图为空时返回空。
    返回: {entities:[{id,name,type,description}], relationships:[{source_id,target_id,type,description,weight}]}
    """
    empty = {"entities": [], "relationships": []}
    seeds = [n for n in (names or []) if n]
    if _instance is None or not seeds:
        return empty

    storage = _instance.chunk_entity_relation_graph
    ent_by_name: dict = {}
    rels: list = []
    rel_seen: set = set()

    for name in seeds:
        try:
            kg = _run(storage.get_knowledge_graph(name, max_depth=1, max_nodes=max_nodes))
        except Exception:
            logger.warning("get_knowledge_graph failed for %s", str(name)[:40], exc_info=True)
            continue

        # id → 实体名映射（本次子图内有效）
        id2name: dict = {}
        for node in kg.nodes or []:
            props = node.properties or {}
            ename = props.get("entity_id") or (node.labels[0] if node.labels else "") or node.id
            if not ename:
                continue
            id2name[node.id] = ename
            if ename not in ent_by_name:
                ent_by_name[ename] = {
                    "id": ename, "name": ename,
                    "type": props.get("entity_type") or "Entity",
                    "description": props.get("description", "") or "",
                }

        for edge in kg.edges or []:
            src = id2name.get(edge.source, edge.source)
            tgt = id2name.get(edge.target, edge.target)
            key = (str(src), str(tgt))
            if not src or not tgt or key in rel_seen:
                continue
            rel_seen.add(key)
            props = edge.properties or {}
            rels.append({
                "source_id": src, "target_id": tgt,
                "type": props.get("keywords") or edge.type or "related",
                "description": props.get("description", "") or "",
                "weight": props.get("weight", 1.0),
            })

    return {"entities": list(ent_by_name.values()), "relationships": rels}


# ── 实体归并（别名消歧）────────────────────────────────────────────────
# LightRAG 只按「实体名完全相同」合并，跨邮件的同一实体会被拆成多个节点
# （赵阳/赵工、华远/华远物流/华远项目）。这里用 LLM 在【同一类型】内保守聚类，
# 再调 amerge_entities（同时更新图谱 + 向量库）把别名并到规范名。

_ALIAS_SYSTEM = "你是知识图谱实体消歧助手，判断哪些实体名称指向同一个现实实体。"

_ALIAS_PROMPT = """下面是类型为「{etype}」的实体，请找出其中指向【同一现实实体】的别名分组。

规则：
- 极其保守：只有高度确定是同一实体（同一个人/组织/项目/系统/文档）时才归为一组；宁可不合并，绝不错合。
- 可合并示例：同一人的全名/简称/职称（赵阳/赵工/赵先生）、同一机构的全称/简称（华远物流/华远）、同一项目的不同叫法。
- 不可合并：不同实体；不同版本/型号；上下位或包含但不等同的（如「报表模块」与「系统」）；日期与事件。
- canonical 取组内最完整规范的名称；aliases 列出该组其余名称。
- 只输出至少含 1 个别名的分组；没有可合并的就返回空数组。

实体列表：
{lines}

只输出 JSON（不要解释、不要代码块）：{{"groups":[{{"canonical":"...","aliases":["...","..."]}}]}}"""

_VERIFY_SYSTEM = "你严格判断两个实体是否为同一个现实实体，不确定一律判否。"

_VERIFY_PROMPT = """实体A：{a}
A的描述：{da}

实体B：{b}
B的描述：{db}

它们是否指向【同一个现实实体】（同一个人／组织／项目／系统／文档）？
判否（回答 no）的情形：
- 不同的人（如不同姓氏的「孙总/张总」；泛指「新同事」无法确定对应哪个具体人时也判否）
- 不同版本/型号（如「U8 13.0」与「U8+ 16.0」）
- 上下位/包含但不等同（如「报表模块」与「核心系统」「WMS」）
- 日期与事件、部分与整体
只有高度确定是同一实体才回答 yes。只输出一个词：yes 或 no。"""


def _llm_complete(prompt: str, system: str = "") -> str:
    """用配置的对话模型做一次补全（走专用 loop）。"""
    cfg = get_settings()
    base_url = f"{cfg.openai_base_url}/v1" if not cfg.openai_base_url.endswith("/v1") else cfg.openai_base_url
    return _run(openai_complete_if_cache(
        model=cfg.openai_model,
        prompt=prompt,
        system_prompt=system or None,
        history_messages=[],
        api_key=cfg.openai_api_key,
        base_url=base_url,
    ))


def _parse_json_obj(raw: str) -> dict:
    """从 LLM 输出里稳健地抠出 JSON 对象。

    容忍三种常见 LLM 噪声：```json 代码块围栏、对象前后的解释性文字，以及
    模型把结果拆成【多个并列 JSON 对象】输出——早先用贪婪正则 `\\{.*\\}` +
    json.loads 正是踩在这里：贪婪匹配从第一个 `{` 一路吃到最后一个 `}`，把
    多个对象连成一段喂给 json.loads → "Extra data"。

    改用 JSONDecoder.raw_decode 从每个 `{` 起逐个解析（只吃一个完整对象、
    忽略其后尾巴），扫描全串收集所有对象，最后合并它们的 groups。
    """
    import re
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()

    decoder = json.JSONDecoder()
    objs: list[dict] = []
    i = 0
    while i < len(s):
        j = s.find("{", i)
        if j == -1:
            break
        try:
            obj, end = decoder.raw_decode(s, j)
        except json.JSONDecodeError:
            i = j + 1  # 此 `{` 不是合法对象开头，跳过继续找下一个
            continue
        if isinstance(obj, dict):
            objs.append(obj)
        i = end

    if not objs:
        raise json.JSONDecodeError("no JSON object found", s, 0)
    if len(objs) == 1:
        return objs[0]

    # 多个并列对象：合并各对象的 groups；兼容模型直接吐裸 group 对象的情况。
    merged: dict = {"groups": []}
    for o in objs:
        groups = o.get("groups")
        if isinstance(groups, list):
            merged["groups"].extend(groups)
        elif "canonical" in o:
            merged["groups"].append(o)
    return merged


def _llm_yes(raw: str) -> bool:
    """把 LLM 的 yes/no 判定稳健归一为布尔——与 _parse_json_obj 同思路的"容忍脏输出"。

    对抗式校验默认判否：空串、纯噪声、含任何否定信号、或既无肯定也无否定，
    一律 False；只有检测到明确肯定且无否定时才 True。兼容中英文
    （yes/no、是/否、相同/不同）、代码块/引号/markdown 包裹，以及模型在结论
    前后夹带解释文字的情况。
    """
    s = (raw or "").strip().lower().strip("`*_\"'“”「」 \t\r\n")
    if not s:
        return False
    # 去掉疑问词「是否 / whether」，避免其中的「否」被误当成否定信号。
    s = s.replace("是否", "").replace("whether", "")
    # 否定优先——出现任何明确否定即判否（保守，宁可不合并）。
    if any(t in s for t in ("不是", "不同", "非同一", "不属于", "不为", "并非")):
        return False
    if re.search(r"\b(no|not|different|distinct)\b", s):
        return False
    if "否" in s:  # 已剔除「是否」，此处的「否」是真否定
        return False
    # 肯定信号。
    if any(t in s for t in ("同一", "相同", "是的", "确是", "是")):
        return True
    if re.search(r"\b(yes|same|identical)\b", s):
        return True
    return False


def _verify_same_entity(a: str, b: str, da: str, db: str) -> bool:
    """对抗式二次校验：严格判断 a、b 是否同一现实实体，默认判否。"""
    try:
        raw = _llm_complete(
            _VERIFY_PROMPT.format(a=a, b=b, da=(da or "")[:120], db=(db or "")[:120]),
            system=_VERIFY_SYSTEM,
        )
    except Exception:
        logger.warning("verify same-entity failed for %s / %s", a, b, exc_info=True)
        return False
    return _llm_yes(raw)


def resolve_entity_aliases(dry_run: bool = False, on_log=None) -> dict:
    """检测并归并指向同一现实实体的别名节点。

    两道保险确保精确率：
      1) 丢弃歧义名（在多个候选组出现、或既当 canonical 又当 source）；
      2) 对每个 (canonical, source) 做对抗式二次校验，严判为同一实体才通过。
    仅在同一 entity_type 内、由 LLM 保守聚类；dry_run=True 只返回预案不写库。
    返回: {dry_run, groups, merged_groups, merged_entities, rejected}
    """
    from collections import Counter, defaultdict
    from src.backend.storage.neo4j_client import get_all_entities

    log = on_log or (lambda m: logger.info(m))
    entities = get_all_entities(limit=10000)
    existing = {e["name"] for e in entities}
    desc_of = {e["name"]: (e.get("description") or "") for e in entities}

    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in entities:
        t = (e.get("type") or "").lower()
        if t in ("", "other"):
            continue  # other/未分类不参与归并，避免误合
        by_type[t].append(e)

    # ── Pass 1：按类型 LLM 聚类，收集候选 (type, canonical, source) ──
    candidates: list[tuple[str, str, str]] = []
    for etype, ents in by_type.items():
        if len(ents) < 2:
            continue
        lines = "\n".join(f"- {e['name']}：{(e.get('description') or '')[:60]}" for e in ents)
        try:
            raw = _llm_complete(_ALIAS_PROMPT.format(etype=etype, lines=lines), system=_ALIAS_SYSTEM)
            groups = _parse_json_obj(raw).get("groups", []) or []
        except Exception:
            logger.warning("alias clustering failed for type=%s", etype, exc_info=True)
            continue
        for g in groups:
            canonical = (g.get("canonical") or "").strip()
            aliases = [str(a).strip() for a in (g.get("aliases") or []) if str(a).strip()]
            names = list(dict.fromkeys(n for n in ([canonical] + aliases) if n in existing))
            if canonical not in existing:
                if not names:
                    continue
                canonical = names[0]
            for s in names:
                if s != canonical:
                    candidates.append((etype, canonical, s))

    # ── 保险 1：丢弃歧义名（source 出现多次，或既是某组 canonical 又在别处当 source）──
    src_count = Counter(s for _, _, s in candidates)
    canon_set = {c for _, c, _ in candidates}
    filtered = [(t, c, s) for (t, c, s) in candidates
                if src_count[s] == 1 and s not in canon_set]
    report = {"dry_run": dry_run, "groups": [], "merged_groups": 0,
              "merged_entities": 0, "rejected": len(candidates) - len(filtered)}

    # ── 保险 2：对抗式二次校验，逐对严判 ──
    verified: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (t, c, s) in filtered:
        if _verify_same_entity(c, s, desc_of.get(c, ""), desc_of.get(s, "")):
            verified[(t, c)].append(s)
        else:
            report["rejected"] += 1

    # ── 执行归并 ──
    rag = None if dry_run else get_lightrag()
    for (etype, canonical), sources in verified.items():
        sources = [s for s in sources if s in existing and s != canonical]
        if not sources:
            continue
        entry = {"type": etype, "canonical": canonical, "merged": sources}
        report["groups"].append(entry)
        report["merged_groups"] += 1
        report["merged_entities"] += len(sources)
        log(f"  归并[{etype}] {sources} → {canonical}")
        if not dry_run:
            try:
                _run(rag.amerge_entities(
                    source_entities=sources,
                    target_entity=canonical,
                    merge_strategy={"description": "concatenate", "entity_type": "keep_first"},
                ))
                for s in sources:
                    existing.discard(s)  # 已并走，避免后续组重复引用
            except Exception as e:
                logger.error("amerge_entities failed %s→%s: %s", sources, canonical, e)
                entry["error"] = str(e)
                report["merged_groups"] -= 1
                report["merged_entities"] -= len(sources)

    log(f"实体归并完成：{report['merged_groups']} 组、{report['merged_entities']} 个别名"
        + f"，拒绝 {report['rejected']} 个候选"
        + ("（dry-run，未写入）" if dry_run else "已合并"))
    return report
