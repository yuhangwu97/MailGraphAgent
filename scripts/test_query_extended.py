"""
MailGraphAgent 扩展场景测试
============================
覆盖：时间范围、金额过滤、人员关系、多条件组合、英文/中英混合、
      附件内容、总结摘要、待办事项、对话追问、实体提取等
"""
import urllib.request
import json
import time
import sys

BASE = "http://localhost:8000/api"
PASS, FAIL = "✅", "❌"
total, passed = 0, 0
results_detail = []


def query_sse(question: str, timeout: int = 90) -> dict:
    t0 = time.time()
    result = {
        "question": question, "answer": "", "query_plan": None,
        "trace": [], "rows": [], "error": "", "duration_ms": 0,
        "tokens": "", "chunks": 0,
    }
    try:
        data = json.dumps({"question": question}).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE}/query",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = b""
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                buf += chunk
                decoded = buf.decode("utf-8", errors="replace")
                if "\n\n" not in decoded:
                    continue
                parts = decoded.split("\n\n")
                buf = parts.pop(-1).encode("utf-8") if parts else b""
                for event_str in parts:
                    for line in event_str.strip().split("\n"):
                        if line.startswith("data: "):
                            try:
                                d = json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
                            if "token" in d:
                                result["tokens"] += d["token"]
                            if "answer" in d:
                                result["answer"] = d.get("answer", "")
                                result["query_plan"] = d.get("query_plan") or {}
                                result["trace"] = d.get("trace", [])
                                result["rows"] = d.get("rows", [])
                                result["chunks"] = len(d.get("chunks", []))
                                result["error"] = d.get("error", "")
    except Exception as e:
        result["error"] = str(e)
    result["duration_ms"] = int((time.time() - t0) * 1000)
    if not result["answer"] and result["tokens"]:
        result["answer"] = result["tokens"].strip()
    return result


def check(desc: str, cond: bool, detail: str = "") -> bool:
    s = PASS if cond else FAIL
    msg = f"  {s} {desc}"
    if detail:
        msg += f" → {detail}"
    print(msg)
    return cond


def test(category: str, desc: str, question: str, *assertions):
    global total, passed
    total += 1
    print(f"\n{'─'*55}")
    print(f"[{category}] {desc}")
    print(f"  Q: {question}")
    result = query_sse(question)
    plan = result.get("query_plan") or {}
    route = plan.get("route", "?")
    answer = (result["answer"] or "")[:300]
    print(f"  路由: {route}, chunks: {result['chunks']}, {result['duration_ms']}ms")
    print(f"  A: {answer}")
    if result.get("error"):
        print(f"  {FAIL} 错误: {result['error']}")

    ok = True
    for fn in assertions:
        if not fn(result):
            ok = False
    if ok:
        passed += 1
        print(f"  {PASS} 通过")
    else:
        print(f"  {FAIL} 未通过")
    results_detail.append({"category": category, "desc": desc, "ok": ok, "answer": answer[:150]})
    return result


# ═══════════════════════════════════════════════════════════════
# 一、时间范围查询
# ═══════════════════════════════════════════════════════════════

test("时间", "今天有多少封邮件？",
    "今天有多少封邮件？",
    lambda r: check("路由正确", r.get("query_plan", {}).get("route") in ("stat", "content")),
    lambda r: check("有回答", len(r.get("answer", "")) > 0),
)

test("时间", "昨天有没有新邮件？",
    "昨天有没有新邮件？",
    lambda r: check("有回答", len(r.get("answer", "")) > 0),
    lambda r: check("无错误", not r.get("error")),
)

test("时间", "这周收到了多少封邮件？",
    "这周收到了多少封邮件？",
    lambda r: check("路由为 stat", r.get("query_plan", {}).get("route") == "stat"),
    lambda r: check("回答含数字", any(c.isdigit() for c in r.get("answer", ""))),
)

test("时间", "7月8号和7月9号各有多少封邮件？",
    "7月8号和7月9号各有多少封邮件？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

# ═══════════════════════════════════════════════════════════════
# 二、金额/数字查询
# ═══════════════════════════════════════════════════════════════

test("金额", "合同金额超过100万的项目有哪些？",
    "合同金额超过100万的项目有哪些？",
    lambda r: check("提到华远或速达", ("华远" in r.get("answer", "") or "速达" in r.get("answer", "") or "350" in r.get("answer", ""))),
)

test("金额", "哪个项目金额最大？",
    "哪个项目金额最大？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("金额", "华远项目首付款是多少？",
    "华远项目首付款是多少？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
    lambda r: check("含百分比或金额", "%" in r.get("answer", "") or "万" in r.get("answer", "") or "30" in r.get("answer", "")),
)

test("金额", "所有项目的总合同金额加起来是多少？",
    "所有项目的总合同金额加起来是多少？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

# ═══════════════════════════════════════════════════════════════
# 三、人员/关系查询
# ═══════════════════════════════════════════════════════════════

test("人员", "张明和王芳谁发的邮件更多？",
    "张明和王芳谁发的邮件更多？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("人员", "孙总和张总是一个人吗？",
    "孙总和张总是一个人吗？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("人员", "哪些邮件是发给王芳的？",
    "哪些邮件是发给王芳的？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("人员", "李经理是谁？在项目中扮演什么角色？",
    "李经理是谁？在项目中扮演什么角色？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

# ═══════════════════════════════════════════════════════════════
# 四、多条件组合查询
# ═══════════════════════════════════════════════════════════════

test("组合", "张明发的带附件的邮件",
    "张明发的带附件的邮件有哪些？",
    lambda r: check("路由正确", r.get("query_plan", {}).get("route") in ("hybrid", "stat")),
    lambda r: check("rows 有数据", len(r.get("rows", [])) > 0),
)

test("组合", "本周关于华远和速达两个项目的邮件",
    "本周关于华远项目和速达项目的邮件有哪些？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("组合", "孙总发的关于合同的非风险类邮件",
    "孙总发的关于合同但不是风险的邮件",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("组合", "最近邮件中提到的所有人名和公司名",
    "列出最近邮件中提到的所有公司和联系人",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

# ═══════════════════════════════════════════════════════════════
# 五、英文/中英混合查询
# ═══════════════════════════════════════════════════════════════

test("语言", "How many emails are there in total?",
    "How many emails are there in total?",
    lambda r: check("有回答", len(r.get("answer", "")) > 0),
    lambda r: check("无错误", not r.get("error")),
)

test("语言", "What is the contract amount for the Huayuan WMS project?",
    "What is the contract amount for the Huayuan WMS project?",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("语言", "ERP系统对接 UAT testing 进度",
    "ERP系统对接的UAT testing进度如何？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

# ═══════════════════════════════════════════════════════════════
# 六、总结/摘要查询
# ═══════════════════════════════════════════════════════════════

test("总结", "总结一下这两天邮件的重点内容",
    "总结一下这两天邮件的重点内容",
    lambda r: check("有回答", len(r.get("answer", "")) > 50),
)

test("总结", "本周最重要的三件事是什么？",
    "本周最重要的三件事是什么？",
    lambda r: check("有回答", len(r.get("answer", "")) > 30),
)

test("总结", "项目整体健康状况如何？",
    "项目整体健康状况如何？",
    lambda r: check("有回答", len(r.get("answer", "")) > 30),
)

# ═══════════════════════════════════════════════════════════════
# 七、待办/行动项提取
# ═══════════════════════════════════════════════════════════════

test("行动", "有哪些待办事项或需要我跟进的？",
    "有哪些待办事项或需要我跟进的？",
    lambda r: check("有回答", len(r.get("answer", "")) > 20),
)

test("行动", "最近邮件中有哪些截止日期？",
    "最近邮件中有哪些截止日期或deadline？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("行动", "王芳接下来需要做什么？",
    "王芳接下来需要做什么？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

# ═══════════════════════════════════════════════════════════════
# 八、附件内容查询
# ═══════════════════════════════════════════════════════════════

test("附件", "PDF附件里有什么内容？",
    "PDF附件里有什么内容？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("附件", "Excel报价单里有哪些项目？",
    "Excel报价单里有哪些项目？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("附件", "附件中的CSV数据包含哪些SKU？",
    "附件中的CSV测试数据包含哪些SKU？",
    lambda r: check("有回答", len(r.get("answer", "")) > 0),
    lambda r: check("无错误", not r.get("error")),
)

# ═══════════════════════════════════════════════════════════════
# 九、供应商/外部合作方查询
# ═══════════════════════════════════════════════════════════════

test("外部", "邮件中提到了哪些外部公司？",
    "邮件中提到了哪些外部公司？",
    lambda r: check("提到华远", "华远" in r.get("answer", "")),
    lambda r: check("至少提到2家外部公司", len([c for c in ["华远", "速达", "鹏程", "凯联", "越秀"] if c in r.get("answer", "")]) >= 2),
)

test("外部", "用友ERP的对接方案是什么？",
    "用友ERP的对接方案是什么？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("外部", "鹏程电子的MES系统是什么版本？",
    "鹏程电子的MES系统是什么版本？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

# ═══════════════════════════════════════════════════════════════
# 十、对话式追问（模拟连续对话）
# ═══════════════════════════════════════════════════════════════

test("追问", "华远项目的负责人是谁？",
    "华远项目的负责人是谁？",
    lambda r: check("提到王芳或张明", any(n in r.get("answer", "") for n in ["王芳", "张明", "孙总"])),
)

test("追问", "他们各自负责什么？",
    "他们各自负责什么？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

test("追问", "那二期项目呢？谁来负责？",
    "华远二期项目谁来负责？",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

# ═══════════════════════════════════════════════════════════════
# 十一、特殊边界用例
# ═══════════════════════════════════════════════════════════════

test("边界", "最近一封邮件是谁发的？",
    "最近一封邮件是谁发的？",
    lambda r: check("有回答", len(r.get("answer", "")) > 0),
)

test("边界", "空的查询会怎样？",
    "",
    lambda r: check("无错误", not r.get("error") or "validation" in str(r.get("error", "")).lower()),
)

test("边界", "只问一个词：WMS",
    "WMS",
    lambda r: check("有回答", len(r.get("answer", "")) > 10),
)

# ═══════════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"扩展场景测试完成: {passed}/{total} 通过 ({passed/total*100:.0f}%)")
print(f"{'='*60}")

# 打印失败详情
failed = [r for r in results_detail if not r["ok"]]
if failed:
    print(f"\n{FAIL} 失败用例 ({len(failed)}):")
    for f in failed:
        print(f"  [{f['category']}] {f['desc']}")
        print(f"    A: {f['answer'][:120]}")

sys.exit(0 if passed == total else 1)
