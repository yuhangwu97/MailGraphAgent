#!/bin/bash
set -e
echo "=== MailGraphAgent 开发模式 ==="

# 开发姿势：基础设施跑 Docker，app（api / worker / web）本地热重载。
# 基础设施 = compose 里的 5 个服务：
#   redis  — 缓存 / 会话 / LightRAG KV+doc_status
#   neo4j  — 知识图谱（LightRAG 图存储）
#   milvus — 向量存储，compose 里 depends_on etcd + minio（元数据 / 对象存储后端）
# --wait 会按 depends_on 顺序拉起 etcd/minio 再起 milvus，并阻塞到各服务 healthy。
echo "⏳ 启动基础设施并等待就绪（redis / neo4j / etcd / minio / milvus）..."
docker compose up -d --wait redis neo4j etcd minio milvus

echo "✅ 基础设施就绪"
echo "   Neo4j 浏览器: http://localhost:7474   (neo4j / mailgraph2024)"
echo "   Milvus:       localhost:19530"
echo "   Redis:        localhost:6379          (密码 mailgraph2024)"
echo
echo "下一步 · 本地热重载启动 app（各开一个终端）："
echo "   API    : uvicorn src.backend.app:app --reload --port 8000"
echo "   Worker : python -m src.backend.worker"
echo "   Web    : (cd src/web && npm run dev)"
echo
echo "或全 Docker 部署（含 app，需构建镜像）：docker compose up -d --build"
