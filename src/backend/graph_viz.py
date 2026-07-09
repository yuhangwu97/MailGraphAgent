"""
Graph visualization helper using pyvis.
Builds interactive network graphs from RAGFlow GraphRAG data.
"""
import logging
from typing import Optional

from pyvis.network import Network

logger = logging.getLogger(__name__)

# Color scheme for each entity type (business types + RAGFlow native types)
# 石墨中性调色：克制土系（公司=墨绿 / 联系人=古铜 / 项目=砖红 / 内部=石墨）
NODE_COLORS = {
    "Company": "#1F6F5C",
    "Contact": "#C08A3E",
    "Employee": "#4A5568",
    "Project": "#9A3B2E",
    "Email": "#7A756E",
    "Department": "#4A7C8A",
    "Attachment": "#B4791F",
    # RAGFlow GraphRAG native entity types
    "Organization": "#1F6F5C",
    "Person": "#C08A3E",
    "Location": "#4A7C8A",
    "Event": "#B4791F",
    "Entity": "#A8A29E",
}

KNOWN_TYPES = list(NODE_COLORS.keys())
DEFAULT_NODE_COLOR = "#A8A29E"


def build_pyvis_network_from_ragflow(
    ragflow_client,
    entity_types_filter: Optional[list] = None,
    limit: int = 500,
) -> str:
    """Build a pyvis interactive network graph from RAGFlow GraphRAG data.

    Fetches entities and relationships from RAGFlow, applies optional
    type filtering, sizes nodes by degree, and returns an HTML string.

    Args:
        ragflow_client: A RAGFlowClient instance with dataset configured.
        entity_types_filter: Optional list of entity types to include
                             (e.g. ["Company", "Contact"]).
        limit: Max number of entities to fetch.

    Returns:
        An HTML string containing the interactive graph.
    """
    try:
        entities = ragflow_client.get_graph_entities(page_size=limit)
        relationships = ragflow_client.get_graph_relationships(page_size=limit * 2)

        if not entities:
            return (
                '<div style="padding: 40px; text-align: center; color: #94A3B8;">'
                "<h3>图谱为空</h3>"
                "<p>暂无图谱数据。请先导入邮件提取结果到 RAGFlow 知识库。</p>"
                "</div>"
            )

        # Apply entity type filter
        if entity_types_filter:
            entities = [e for e in entities if e.get("type") in entity_types_filter]

        entity_ids = {e["id"] for e in entities}

        # Filter relationships to only include those between filtered entities
        filtered_rels = [
            r for r in relationships
            if r.get("source_id") in entity_ids and r.get("target_id") in entity_ids
        ]

        if not entities:
            return (
                '<div style="padding: 40px; text-align: center; color: #94A3B8;">'
                "<h3>无匹配节点</h3>"
                "<p>当前筛选条件下无匹配的实体节点。</p>"
                "</div>"
            )

        # Calculate node degree
        degree = {}
        for r in filtered_rels:
            src = r.get("source_id", "")
            tgt = r.get("target_id", "")
            degree[src] = degree.get(src, 0) + 1
            degree[tgt] = degree.get(tgt, 0) + 1

        # Build pyvis Network
        net = Network(
            height="600px",
            width="100%",
            directed=True,
            bgcolor="#FFFFFF",
            font_color="#0F172A",
        )

        # Add nodes
        for ent in entities:
            eid = ent["id"]
            etype = ent.get("type", "Entity")
            name = ent.get("name", eid)[:22]
            color = NODE_COLORS.get(etype, DEFAULT_NODE_COLOR)
            node_size = 12 + min(int(degree.get(eid, 0) ** 0.6 * 3), 28)

            net.add_node(
                eid,
                label=name,
                title=(
                    f"<b>类型:</b> {etype}<br>"
                    f"<b>名称:</b> {ent.get('name', eid)}<br>"
                    f"<b>关联数:</b> {degree.get(eid, 0)}<br>"
                    f"<b>描述:</b> {ent.get('description', '')[:100]}"
                ),
                color=color,
                size=node_size,
                group=etype,
            )

        # Add edges
        for rel in filtered_rels:
            rel_type = rel.get("type", "RELATED_TO")
            net.add_edge(
                rel["source_id"],
                rel["target_id"],
                title=rel_type,
                label=rel_type,
                arrows="to",
            )

        # Physics & styling
        net.set_options("""
        {
            "nodes": {
                "font": {"size": 11, "face": "Inter, sans-serif", "color": "#0F172A",
                         "strokeWidth": 2, "strokeColor": "#ffffff"},
                "borderWidth": 2,
                "borderWidthSelected": 3,
                "shape": "dot"
            },
            "edges": {
                "color": {"color": "#D6D3CE", "highlight": "#1F6F5C", "hover": "#1F6F5C"},
                "width": 1.5,
                "selectionWidth": 2,
                "smooth": {"enabled": true, "type": "continuous"},
                "font": {"size": 9, "face": "Inter, sans-serif", "color": "#64748B",
                         "strokeWidth": 2, "strokeColor": "#ffffff"},
                "arrows": {"to": {"enabled": true, "scaleFactor": 0.6}}
            },
            "physics": {
                "enabled": true,
                "stabilization": {"iterations": 150},
                "barnesHut": {
                    "gravitationalConstant": -2000,
                    "springLength": 250,
                    "springConstant": 0.02,
                    "damping": 0.6
                }
            },
            "interaction": {
                "hover": true,
                "tooltipDelay": 100,
                "multiselect": true
            },
            "configure": {
                "enabled": false
            }
        }
        """)

        return net.generate_html()

    except Exception as exc:
        logger.error("Failed to build pyvis network: %s", exc)
        return (
            '<div style="padding: 20px; color: #e74c3c;">'
            "<h3>图谱加载失败</h3>"
            f"<p>{exc}</p>"
            "</div>"
        )
