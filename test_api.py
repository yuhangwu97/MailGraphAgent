"""
验证 OpenAI 中转 API 连通性
支持 Chat Completions 和 Responses 两种协议
"""
import os
import sys
import json
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL", "http://43.160.245.179:8080")
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")

print("=" * 60)
print("MailGraphAgent - API 连通性测试")
print("=" * 60)
print(f"Base URL: {BASE_URL}")
print(f"Model:    {MODEL}")
print(f"Key:      {API_KEY[:20]}...{API_KEY[-10:]}")
print()

# ============================================================
# 方式一：标准 OpenAI Chat Completions API
# ============================================================
print("─" * 60)
print("测试 1: Chat Completions API (/v1/chat/completions)")
print("─" * 60)

try:
    from openai import OpenAI

    client = OpenAI(
        api_key=API_KEY,
        base_url=f"{BASE_URL}/v1",
        timeout=60.0,
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": "请用一句话介绍你自己，并说明你是什么模型。",
            },
        ],
        max_tokens=200,
    )

    content = response.choices[0].message.content
    model_used = response.model
    tokens = response.usage

    print("✅ Chat Completions API 联通!")
    print(f"   模型返回: {model_used}")
    print(f"   回复内容: {content}")
    print(f"   Token 消耗: prompt={tokens.prompt_tokens}, completion={tokens.completion_tokens}")
except Exception as e:
    print(f"❌ Chat Completions API 失败: {e}")
    print("   将尝试 Responses API...")

# ============================================================
# 方式二：OpenAI Responses API
# ============================================================
print()
print("─" * 60)
print("测试 2: Responses API (/v1/responses)")
print("─" * 60)

try:
    from openai import OpenAI

    client = OpenAI(
        api_key=API_KEY,
        base_url=f"{BASE_URL}/v1",
        timeout=60.0,
    )

    response = client.responses.create(
        model=MODEL,
        input="请用一句话介绍你自己。",
        max_output_tokens=200,
    )

    print("✅ Responses API 联通!")
    print(f"   模型返回: {response.model}")
    print(f"   回复内容: {response.output_text}")
except Exception as e:
    print(f"❌ Responses API 失败: {e}")

# ============================================================
# 方式三：直接 HTTP 请求（诊断用）
# ============================================================
print()
print("─" * 60)
print("测试 3: 直接 HTTP 探测端点")
print("─" * 60)

import urllib.request
import urllib.error

endpoints = [
    f"{BASE_URL}/v1/models",
    f"{BASE_URL}/v1/chat/completions",
    f"{BASE_URL}/v1/responses",
    f"{BASE_URL}/models",
]

for ep in endpoints:
    try:
        req = urllib.request.Request(ep)
        req.add_header("Authorization", f"Bearer {API_KEY}")
        urllib.request.urlopen(req, timeout=5)
        print(f"✅ {ep} → 可达 (200)")
    except urllib.error.HTTPError as e:
        print(f"⚠️  {ep} → HTTP {e.code} (需要 POST 请求，正常)")
    except Exception as e:
        print(f"❌ {ep} → {e}")

# ============================================================
# 测试 4: JSON 结构化提取能力（核心功能验证）
# ============================================================
print()
print("─" * 60)
print("测试 4: JSON 结构化提取 (核心业务能力)")
print("─" * 60)

TEST_EMAIL = """
发件人: zhangwei@abc-tech.com
收件人: wangfang@ourcompany.com
主题: 关于X智能仓储系统升级项目的进度汇报

王工，您好。

向您汇报一下X智能仓储系统升级项目的最新进展：

1. 本周完成了WMS核心模块的联调测试，系统运行稳定。
2. A公司的张总对当前进度表示满意，但希望我们在下周五前完成移动端的适配。
3. 目前项目整体进度约70%，风险点主要是第三方硬件接口文档迟迟未给，可能影响上线时间。
4. 另外，A公司的技术对接人李经理下周会到我们这边来做UAT测试，需要安排一下会议室。

以上，请知悉。
"""

try:
    from openai import OpenAI

    client = OpenAI(
        api_key=API_KEY,
        base_url=f"{BASE_URL}/v1",
        timeout=120.0,
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是一个企业邮件分析专家。请从邮件中提取结构化信息，严格输出 JSON 格式。",
            },
            {
                "role": "user",
                "content": f"""请从以下邮件中提取关键商业信息，输出 JSON 格式：

邮件内容：
{TEST_EMAIL}

输出 JSON Schema：
{{
  "company": {{"name": "客户公司名称", "aliases": []}},
  "contacts": [{{"name": "外部对接人", "role": "职位"}}],
  "internal_owners": [{{"name": "内部负责人", "role": "职位"}}],
  "projects": [{{"name": "项目名称", "status": "进行中/已完成/停滞", "progress": "进度%", "risk_points": []}}],
  "summary": "邮件摘要"
}}

只输出 JSON，不要其他文字。""",
            },
        ],
        max_tokens=1000,
        temperature=0.1,
    )

    result_text = response.choices[0].message.content
    print("✅ 结构化提取成功!")
    print(f"   原始返回:\n{result_text}")

    # 尝试解析 JSON
    # 处理可能的 markdown 包裹
    if "```json" in result_text:
        result_text = result_text.split("```json")[1].split("```")[0]
    elif "```" in result_text:
        result_text = result_text.split("```")[1].split("```")[0]

    parsed = json.loads(result_text)
    print(f"\n   解析后的结构化数据:")
    print(f"   公司:     {parsed.get('company', {}).get('name', 'N/A')}")
    print(f"   对接人:   {[c['name'] for c in parsed.get('contacts', [])]}")
    print(f"   内部负责人: {[c['name'] for c in parsed.get('internal_owners', [])]}")
    print(f"   项目:     {[p['name'] for p in parsed.get('projects', [])]}")
    print(f"   进度:     {[p.get('progress', 'N/A') for p in parsed.get('projects', [])]}")
    tokens_used = response.usage
    print(f"   Token 消耗: prompt={tokens_used.prompt_tokens}, completion={tokens_used.completion_tokens}")

except json.JSONDecodeError as e:
    print(f"⚠️  JSON 解析失败: {e}")
    print(f"   模型返回了非标准 JSON，需要在 Prompt 里加约束")
except Exception as e:
    print(f"❌ 结构化提取失败: {e}")

print()
print("=" * 60)
print("测试完成")
print("=" * 60)
