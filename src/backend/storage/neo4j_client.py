"""Neo4j 只读客户端 — 读取 LightRAG 写入的知识图谱供前端图谱页展示。

LightRAG Neo4JStorage 的存储约定（见 lightrag.kg.neo4j_impl）：
  - 实体节点带属性 ``entity_id``（实体名，即主键），另有 ``entity_type`` /
    ``description`` / ``source_id`` 等；节点 label 为 workspace（默认 ``base``）。
  - 关系类型统一为 ``:DIRECTED``，带 ``keywords`` / ``description`` /
    ``weight`` / ``source_id`` 等属性。

这里用「节点是否含 entity_id 属性」作过滤，避免与 workspace label 名耦合，
不同 workspace 也能读。使用同步 neo4j driver（graph.py 在 executor 中调用，
status.py 健康检查也是同步）。
"""
from __future__ import annotations

import logging
import threading

from neo4j import GraphDatabase, Driver

from config.settings import get_settings

logger = logging.getLogger(__name__)

_driver: Driver | None = None
_lock = threading.Lock()


def _get_driver() -> Driver:
    """获取（惰性初始化）同步 Neo4j driver 单例。"""
    global _driver
    if _driver is not None:
        return _driver
    with _lock:
        if _driver is None:
            cfg = get_settings()
            _driver = GraphDatabase.driver(
                cfg.resolved_neo4j_uri(),
                auth=(cfg.neo4j_user, cfg.neo4j_password),
            )
            logger.info("Neo4j driver initialized: %s", cfg.resolved_neo4j_uri())
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        try:
            _driver.close()
        finally:
            _driver = None


def get_all_entities(limit: int = 500) -> list[dict]:
    """返回实体节点列表：{id, name, type, description}。"""
    query = (
        "MATCH (n) WHERE n.entity_id IS NOT NULL "
        "RETURN n.entity_id AS id, "
        "       coalesce(n.entity_type, 'Entity') AS type, "
        "       coalesce(n.description, '') AS description "
        "LIMIT $limit"
    )
    driver = _get_driver()
    with driver.session() as session:
        result = session.run(query, limit=int(limit))
        entities = []
        for r in result:
            eid = r["id"]
            entities.append({
                "id": eid,
                "name": eid,
                "type": r["type"],
                "description": r["description"],
            })
    return entities


def count_graph() -> dict:
    """返回知识图谱规模：{entities, relationships}。用 COUNT 聚合，不拉全量数据。

    ── 为什么 count 走直连 Cypher 而非 LightRAG 原生图 API ──
    原生 get_all_labels()/get_all_edges() 会全量拉取节点标签与边（含属性）再取
    len——对每 5s 轮询一次的工作台状态带太重；且 get_all_edges() 用无向 -[r]-
    会把每条有向边算两次（124→~248）。这里用纯 COUNT 聚合：零数据传输、无需
    LightRAG 冷启动、且用有向 :DIRECTED-> 得到真实边数。

    ── 关于 workspace / 账号 ──
    本系统用全局单一知识图谱：LightRAG 单例、单 workspace(base)，所有账号的邮件汇入
    同一张图（跨账号统一检索）。邮件/会话数据（Redis）现在同样全局共享，账号仅作为
    IMAP 登录凭据。因此这里 MATCH (n) WHERE entity_id IS NOT NULL 不按 workspace
    限定是安全的——全局只有一个 workspace。

    连接失败（Neo4j 未起）时返回 0，让前端优雅降级。
    """
    ent_q = "MATCH (n) WHERE n.entity_id IS NOT NULL RETURN count(n) AS c"
    rel_q = (
        "MATCH (a)-[r:DIRECTED]->(b) "
        "WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL "
        "RETURN count(r) AS c"
    )
    try:
        driver = _get_driver()
        with driver.session() as session:
            entities = session.run(ent_q).single()["c"]
            relationships = session.run(rel_q).single()["c"]
        return {"entities": int(entities), "relationships": int(relationships)}
    except Exception as e:
        logger.warning("count_graph failed: %s", e)
        return {"entities": 0, "relationships": 0}


def get_all_relationships(limit: int = 1000) -> list[dict]:
    """返回关系列表：{source_id, target_id, type, description, weight}。

    用有向匹配 ``-[r:DIRECTED]->`` 避免无向匹配把每条边返回两次。
    """
    query = (
        "MATCH (a)-[r:DIRECTED]->(b) "
        "WHERE a.entity_id IS NOT NULL AND b.entity_id IS NOT NULL "
        "RETURN a.entity_id AS source_id, "
        "       b.entity_id AS target_id, "
        "       coalesce(r.keywords, '') AS type, "
        "       coalesce(r.description, '') AS description, "
        "       coalesce(r.weight, 1.0) AS weight "
        "LIMIT $limit"
    )
    driver = _get_driver()
    with driver.session() as session:
        result = session.run(query, limit=int(limit))
        rels = []
        for r in result:
            rels.append({
                "source_id": r["source_id"],
                "target_id": r["target_id"],
                "type": r["type"] or "related",
                "description": r["description"],
                "weight": r["weight"],
            })
    return rels


def get_projects_paginated(page: int = 1, page_size: int = 20) -> dict:
    """Return paginated project entities with neighbor info.

    Each project dict: {id, name, type, description, people, companies}
    People/companies are derived from DIRECTED relationships.
    Returns: {projects: list[dict], total: int}
    """
    skip = max(0, (page - 1) * page_size)

    # Count total projects
    count_q = (
        "MATCH (n) WHERE n.entity_id IS NOT NULL AND n.entity_type = 'project' "
        "RETURN count(n) AS c"
    )

    # Fetch page of projects
    projects_q = (
        "MATCH (n) WHERE n.entity_id IS NOT NULL AND n.entity_type = 'project' "
        "RETURN n.entity_id AS id, "
        "       coalesce(n.description, '') AS description "
        "ORDER BY toLower(n.entity_id) "
        "SKIP $skip LIMIT $limit"
    )

    driver = _get_driver()
    with driver.session() as session:
        total = session.run(count_q).single()["c"]
        result = session.run(projects_q, skip=skip, limit=int(page_size))
        projects = []
        for r in result:
            eid = r["id"]
            projects.append({
                "id": eid,
                "name": eid,
                "type": "project",
                "description": r["description"],
                "people": [],
                "companies": [],
            })

    if not projects:
        return {"projects": [], "total": int(total)}

    # Fetch relationships for this page's projects only
    project_ids = [p["name"] for p in projects]
    rels_q = (
        "MATCH (a)-[r:DIRECTED]->(b) "
        "WHERE a.entity_id IN $pids AND b.entity_id IS NOT NULL "
        "RETURN a.entity_id AS source_id, "
        "       b.entity_id AS target_id, "
        "       b.entity_type AS target_type, "
        "       coalesce(b.description, '') AS target_desc "
        "UNION "
        "MATCH (a)-[r:DIRECTED]->(b) "
        "WHERE b.entity_id IN $pids AND a.entity_id IS NOT NULL "
        "RETURN b.entity_id AS source_id, "
        "       a.entity_id AS target_id, "
        "       a.entity_type AS target_type, "
        "       coalesce(a.description, '') AS target_desc"
    )

    with driver.session() as session:
        rels = session.run(rels_q, pids=project_ids)
        by_project: dict[str, dict[str, list]] = {}
        for p in projects:
            by_project[p["name"]] = {"people": [], "companies": []}

        seen = set()
        for r in rels:
            proj_name = r["source_id"]
            if proj_name not in by_project:
                proj_name = r["target_id"]
                if proj_name not in by_project:
                    continue
            neighbor = r["target_id"] if r["source_id"] == proj_name else r["source_id"]
            ntype = (r["target_type"] or "").lower()
            key = f"{proj_name}|{neighbor}"
            if key in seen:
                continue
            seen.add(key)

            PEOPLE_TYPES = {"person", "contact", "employee"}
            if ntype in PEOPLE_TYPES:
                by_project[proj_name]["people"].append({"name": neighbor})
            elif ntype == "organization":
                by_project[proj_name]["companies"].append({"name": neighbor})

        for p in projects:
            p["people"] = by_project[p["name"]]["people"]
            p["companies"] = by_project[p["name"]]["companies"]

    return {"projects": projects, "total": int(total)}
