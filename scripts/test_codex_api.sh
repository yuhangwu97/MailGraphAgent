#!/bin/bash
set -e

BASE_URL="http://43.160.245.179:8080"
API_KEY="sk-78fee43b18e08053876fc69723a0f1fb77d2ca5c22cda1a49103f4be4546fea6"

echo "=========================================="
echo "Codex API 连通性测试"
echo "目标: $BASE_URL"
echo "=========================================="

# 1. TCP 连通性
echo ""
echo "[1/4] 测试 TCP 连通性..."
if timeout 5 bash -c "echo >/dev/tcp/43.160.245.179/8080" 2>/dev/null; then
    echo "  ✅ TCP 端口 8080 可达"
else
    echo "  ❌ TCP 端口 8080 不可达！请检查防火墙/网络"
    echo "  尝试 ping..."
    ping -c 2 -W 3 43.160.245.179 2>/dev/null || echo "  ❌ ping 也失败，主机可能不可达"
    exit 1
fi

# 2. HTTP 根路径
echo ""
echo "[2/4] 测试 HTTP 根路径..."
HTTP_CODE=$(curl -s -o /tmp/codex_root.txt -w "%{http_code}" --connect-timeout 5 --max-time 10 "$BASE_URL/" 2>&1 || echo "000")
echo "  HTTP 状态码: $HTTP_CODE"
echo "  响应内容: $(head -c 500 /tmp/codex_root.txt)"

# 3. Models 列表
echo ""
echo "[3/4] 测试 /v1/models 端点..."
MODELS_RESP=$(curl -s -w "\n%{http_code}" --connect-timeout 5 --max-time 10 \
    -H "Authorization: Bearer $API_KEY" \
    "$BASE_URL/v1/models" 2>&1)
MODELS_BODY=$(echo "$MODELS_RESP" | head -n -1)
MODELS_CODE=$(echo "$MODELS_RESP" | tail -n 1)
echo "  HTTP 状态码: $MODELS_CODE"
echo "  模型列表:"
echo "$MODELS_BODY" | python3 -m json.tool 2>/dev/null | head -30 || echo "$MODELS_BODY" | head -30

# 4. Chat Completions 测试
echo ""
echo "[4/4] 测试 /v1/chat/completions 端点..."
CHAT_RESP=$(curl -s -w "\n%{http_code}" --connect-timeout 5 --max-time 30 \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "Say just: hello"}],
        "max_tokens": 20
    }' \
    "$BASE_URL/v1/chat/completions" 2>&1)
CHAT_BODY=$(echo "$CHAT_RESP" | head -n -1)
CHAT_CODE=$(echo "$CHAT_RESP" | tail -n 1)
echo "  HTTP 状态码: $CHAT_CODE"
echo "  响应:"
echo "$CHAT_BODY" | python3 -m json.tool 2>/dev/null || echo "$CHAT_BODY"

echo ""
echo "=========================================="
echo "测试完成"
echo "=========================================="
