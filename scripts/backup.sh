#!/bin/bash
# MailGraphAgent 数据备份脚本
# 用法：在 WSL2 内执行，输出到 Windows 可访问的路径
#   bash scripts/backup.sh /mnt/d/backups/mailgraph
# 配合 Windows 计划任务：wsl -d docker-desktop bash ~/MailGraphAgent/scripts/backup.sh /mnt/d/backups/mailgraph
set -euo pipefail

BACKUP_ROOT="${1:-/mnt/d/backups/mailgraph}"
TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TIMESTAMP"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$PROJECT_DIR/data"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] MailGraphAgent backup starting → $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

# ── 1. LightRAG KV（无需停服） ──
if [ -d "$DATA_DIR/lightrag" ]; then
    echo "  backing up lightrag..."
    tar czf "$BACKUP_DIR/lightrag.tar.gz" -C "$DATA_DIR" lightrag/
fi

# ── 2. Redis AOF（无需停服） ──
if [ -f "$DATA_DIR/redis/appendonly.aof" ]; then
    echo "  backing up redis aof..."
    cp "$DATA_DIR/redis/appendonly.aof" "$BACKUP_DIR/appendonly.aof"
fi

# ── 3. Neo4j（建议停服，这里保守处理：直接 tar data 目录） ──
if [ -d "$DATA_DIR/neo4j" ]; then
    echo "  backing up neo4j (may be inconsistent if running)..."
    tar czf "$BACKUP_DIR/neo4j.tar.gz" -C "$DATA_DIR" neo4j/ 2>/dev/null || echo "  WARNING: neo4j backup had errors (likely running)"
fi

# ── 4. Milvus（必须停服才一致，这里仅作提示） ──
if [ -d "$DATA_DIR/milvus" ]; then
    echo "  backing up milvus (may be inconsistent if running)..."
    tar czf "$BACKUP_DIR/milvus.tar.gz" -C "$DATA_DIR" milvus/ 2>/dev/null || echo "  WARNING: milvus backup had errors (likely running)"
fi

# ── 清理 30 天前的旧备份 ──
find "$BACKUP_ROOT" -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \; 2>/dev/null || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup complete → $BACKUP_DIR ($(du -sh "$BACKUP_DIR" | cut -f1))"
