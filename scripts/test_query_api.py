"""
高点测试用例 — 使用 urllib 无外部依赖
"""
import urllib.request
import urllib.error
import json
import time
import sys
import re

BASE = "http://localhost:5173/api"
PASS, FAIL, WARN = "✅", "❌", "⚠️"

total, passed = 0, 0


def query_sse(question: str, timeout: int = 120) -> dict:
    t0 = time.time()
    result = {
        "question": question, "answer": "", "query_plan": None,
        "trace": [], "rows": [], "error": "", "duration_ms": 0,
        "tokens": "", "entities": [], "chunks": [],
    }
    try:
        data = json.dumps({"question": question}).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE}/query",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = b""
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                buf += chunk
                # Try to parse complete lines
                decoded = buf.decode("utf-8", errors="replace")
                if "\n\n" not in decoded:
                    continue
                parts = decoded.split("\n\n")
                buf = parts.pop(-1).encode("utf-8") if parts else b""
                lines = parts
                for event_str in lines:
                    event_str = event_str.strip()
                    if not event_str:
                        continue
                    # parse event
                    for line in event_str.split("\n"):
                        if line.startswith("data: "):
                            data_str = line[6:]
                            try:
                                d = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            if "token" in d:
                                result["tokens"] += d["token"]
                            if "answer" in d:
                                result["answer"] = d.get("answer", "")
                                result["query_plan"] = d.get("query_plan")
                                result["trace"] = d.get("trace", [])
                                result["rows"] = d.get("rows", [])
                                result["error"] = d.get("error", "")
                                result["entities"] = d.get("entities", [])
                                result["chunks"] = d.get("chunks", [])
    except Exception as e:
        result["error"] = str(e)

    result["duration_ms"] = int((time.time() - t0) * 1000)
    if not result["answer"] and result["tokens"]:
        result["answer"] = result["tokens"].strip()
    return result


def check(description: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    msg = f"  {status} {description}"
    if detail:
        from urllib.parse import quote
        msg += f" → {detail}"
    print(msg)
    return condition


def test(description: str, question: str, *assertions):
    global total, passed
    total += 1
    print(f"\n{'='*60}")
    print(f"[{total}] {description}")
    print(f"    问题: {question}")
    result = query_sse(question)
    plan = result.get("query_plan") or {}
    print(f"    路由: {plan.get('route', 'N/A')}")
    print(f"    聚合: {plan.get('aggregation', 'N/A')}")
    print(f"    耗时: {result['duration_ms']}ms")
    answer = (result["answer"] or "")[:250]
    print(f"    回答: {answer}")
    if result.get("chunks"):
        print(f"    证据: {len(result['chunks'])} chunks")
    if result.get("entities"):
        print(f"    实体: {len(result['entities'])} entities")
    if result.get("rows"):
        print(f"    数据行: {len(result['rows'])} rows")

    if result.get("error"):
        print(f"  {WARN} 错误: {result['error']}")

    ok = True
    for fn in assertions:
        if not fn(result):
            ok = False
    if ok:
        passed += 1
        print(f"  {PASS} 通过")
    return result


# ═══════════════════════════════════════════════════════
# 一、统计查询 (stat)
# ═══════════════════════════════════════════════════════

print("\n" + "█" * 60)
print("一、统计查询 (stat) — 计数/列表/排名/比率")
print("█" * 60)

test(
    "1.1 邮件总数统计",
    "一共有多少封邮件？",
    lambda r: check("路由为 stat", (r.get("query_plan") or {}).get("route") == "stat"),
    lambda r: check("回答包含数字", any(c.isdigit() for c in r.get("answer", ""))),
)

test(
    "1.2 按发件人排名",
    "谁发的邮件最多？",
    lambda r: check("路由为 stat", (r.get("query_plan") or {}).get("route") == "stat"),
    lambda r: check("聚合为 top_senders", (r.get("query_plan") or {}).get("aggregation") == "top_senders"),
    lambda r: check("rows 不为空", len(r.get("rows", [])) > 0),
)

test(
    "1.3 查询张明发的邮件",
    "张明发了哪些邮件？",
    lambda r: check("路由为 stat/hybrid",
                 (r.get("query_plan") or {}).get("route") in ("stat", "hybrid")),
    lambda r: check("提到张明", "张明" in str(r) or "zhangming" in str(r).lower()),
)

test(
    "1.4 带附件的邮件统计",
    "有多少封带附件的邮件？",
    lambda r: check("路由为 stat", (r.get("query_plan") or {}).get("route") == "stat"),
    lambda r: check("has_attachment=true",
                 (r.get("query_plan") or {}).get("filters", {}).get("has_attachment") == True),
)

test(
    "1.5 处理成功率",
    "邮件处理的成功率是多少？",
    lambda r: check("路由为 stat", (r.get("query_plan") or {}).get("route") == "stat"),
    lambda r: check("回答中有百分比", "%" in r.get("answer", "") or "率" in r.get("answer", "")),
)

test(
    "1.6 列表查询",
    "列出最近的邮件",
    lambda r: check("路由正确", (r.get("query_plan") or {}).get("route") in ("stat", "hybrid")),
    lambda r: check("rows 有数据", len(r.get("rows", [])) > 0),
)

# ═══════════════════════════════════════════════════════
# 二、内容查询 (content)
# ═══════════════════════════════════════════════════════

print("\n" + "█" * 60)
print("二、内容查询 (content) — 项目/人员/话题")
print("█" * 60)

test(
    "2.1 华远 WMS 项目进展",
    "华远物流的X智能仓储系统项目进展如何？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "2.2 速达 ERP 项目",
    "速达供应链ERP对接项目的情况？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "2.3 鹏程电子项目",
    "鹏程电子智能制造平台的项目规模？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "2.4 越秀贸易竞争分析",
    "越秀跨境物流系统有哪些竞争对手？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "2.5 人员关系",
    "王芳和张明在哪些项目上合作？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "2.6 合同金额",
    "各项目的合同金额是多少？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "2.7 项目风险",
    "目前项目有哪些风险？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "2.8 UAT 时间",
    "华远项目的UAT测试什么时候开始？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

# ═══════════════════════════════════════════════════════
# 三、混合查询 (hybrid)
# ═══════════════════════════════════════════════════════

print("\n" + "█" * 60)
print("三、混合查询 (hybrid)")
print("█" * 60)

test(
    "3.1 时间+合同",
    "关于合同的邮件有哪些？",
    lambda r: check("路由正确", (r.get("query_plan") or {}).get("route") in ("hybrid", "content", "stat")),
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test(
    "3.2 发件人+风险",
    "孙总发的关于项目风险的邮件",
    lambda r: check("路由正确", (r.get("query_plan") or {}).get("route") in ("hybrid", "stat", "content")),
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test(
    "3.3 报价预算查询",
    "有哪些邮件提到了报价和预算？",
    lambda r: check("路由正确", (r.get("query_plan") or {}).get("route") in ("hybrid", "content", "stat")),
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test(
    "3.4 王芳+技术方案",
    "王芳发的关于技术方案的邮件有哪些？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

# ═══════════════════════════════════════════════════════
# 四、边界/高点用例
# ═══════════════════════════════════════════════════════

print("\n" + "█" * 60)
print("四、边界 & 高点用例")
print("█" * 60)

test(
    "4.1 模糊澄清",
    "邮件？",
    lambda r: check("路由为 clarify", (r.get("query_plan") or {}).get("route") == "clarify"),
    lambda r: check("澄清问题非空", bool((r.get("query_plan") or {}).get("clarifying_question"))),
)

test(
    "4.2 无匹配",
    "关于火星探测项目的邮件有哪些？",
    lambda r: check("有回答（无崩溃）", len(r.get("answer", "")) > 0),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "4.3 中英混合",
    "WMS系统的 UAT 测试进度",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "4.4 极短关键词",
    "合同",
    lambda r: check("有回答", len(r.get("answer", "")) > 0),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "4.5 多意图",
    "统计邮件数量并按项目分类",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "4.6 三合一查询",
    "孙总发的关于华远项目的邮件",
    lambda r: check("有回答", len(r.get("answer", "")) > 0),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "4.7 长查询",
    "我想了解一下华远物流项目中关于第三方硬件接口延期这个风险的处理方案，"
    "以及备选供应商的评估情况",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "4.8 数字查询（金额汇总）",
    "华远物流项目的总金额是多少？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "4.9 时间节点查询",
    "7月份有哪些重要的时间节点和里程碑？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

test(
    "4.10 多项目对比",
    "对比华远和速达两个项目的情况",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("无错误", not r.get("error")),
)

# ═══════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"测试完成: {passed}/{total} 通过 ({passed/total*100:.0f}%)")
print(f"{'='*60}")

sys.exit(0 if passed == total else 1)
