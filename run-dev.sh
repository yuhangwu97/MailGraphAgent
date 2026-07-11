#!/usr/bin/env bash
# 本地一键启动：后端 API + 文档处理 worker + 前端 Vite。
# 基础设施（redis / neo4j / milvus 等）仍由 docker compose 提供，需已在运行。
#
#   用法：  bash run-dev.sh        （Ctrl+C 停止全部）
#
# 说明：重活端点（ingest/reprocess/parse 等）已改为「入队给 worker」，
# 所以必须同时起 worker，否则这些操作会一直挂起。
set -uo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
UVICORN=".venv/bin/uvicorn"

# 清理可能残留的旧进程，避免端口占用（脚本可重复运行）
echo "· 清理旧进程…"
pkill -f "uvicorn src.backend.app:app" 2>/dev/null || true
pkill -f "src.backend.worker"          2>/dev/null || true
lsof -ti tcp:5173 2>/dev/null | xargs kill 2>/dev/null || true
sleep 1

pids=()
cleanup() {
  echo ""
  echo "· 停止所有服务…"
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  pkill -f "src.backend.worker" 2>/dev/null || true
  lsof -ti tcp:5173 2>/dev/null | xargs kill 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "▶ 后端 API   http://localhost:8000"
"$UVICORN" src.backend.app:app --reload --host 0.0.0.0 --port 8000 &
pids+=($!)

echo "▶ Worker（文档解析 + 建图，独立进程，不阻塞 API）"
"$PY" -m src.backend.worker &
pids+=($!)

echo "▶ 前端 Vite  http://localhost:5173"
( cd src/web && npm run dev ) &
pids+=($!)

echo ""
echo "全部已启动 →  前端 http://localhost:5173   后端 http://localhost:8000"
echo "Ctrl+C 停止全部。"
wait
