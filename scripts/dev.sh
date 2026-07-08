#!/bin/bash
set -e
echo "=== MailGraphAgent 开发模式 ==="

docker compose up -d mysql redis minio ragflow
echo "⏳ 等待 RAGFlow..."
until curl -s http://localhost:9380/api/v1/health &>/dev/null 2>&1; do
    sleep 3
done
echo "✅ RAGFlow ready: http://localhost:9380"
echo "   知识库管理: http://localhost:9380"
