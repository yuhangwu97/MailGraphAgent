"""Trigger GraphRAG and check status for the current dataset."""
import requests, json, time, sys

API_KEY = "ragflow-TotgCeQwinlwoPP0SDnqbCUM4b5AJxhk9nyJviIhVgk"
BASE = "http://localhost:9380/api/v1"
DS = "7fe9b7207b8c11f1bfc331dc1fc8a8b4"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# 1. Check current knowledge graph
print("=== 当前 knowledge_graph ===")
r = requests.get(f"{BASE}/datasets/{DS}/graph", headers=HEADERS, timeout=10)
d = r.json()
g = d.get("data", {}).get("graph", {}) if d.get("data") else {}
ents = g.get("nodes", g.get("entities", [])) or []
rels = g.get("edges", g.get("relationships", [])) or []
print(f"entities: {len(ents)}, relationships: {len(rels)}")

if ents:
    for e in (ents[:5] if isinstance(ents, list) else []):
        print(f"  - {e.get('name', e.get('entity_name', '?'))} ({e.get('type', e.get('entity_type', '?'))})")

# 2. Trigger GraphRAG
print("\n=== 触发 GraphRAG ===")
r = requests.post(f"{BASE}/datasets/{DS}/index?type=graph", headers=HEADERS, timeout=30)
d = r.json()
print(f"code: {d.get('code')}, task_id: {d.get('data', {}).get('task_id', 'N/A')}")

if d.get("code") != 0:
    print(f"message: {d.get('message')}")
    sys.exit(1)

# 3. Poll for completion
print("\n=== 等待 GraphRAG 完成 ===")
start = time.time()
while time.time() - start < 300:
    r = requests.get(f"{BASE}/datasets/{DS}/index?type=graph", headers=HEADERS, timeout=10)
    d = r.json()
    data = d.get("data", {}) or {}
    progress = data.get("progress")
    if progress is not None:
        try:
            p = float(progress)
        except (TypeError, ValueError):
            p = None
        msg = data.get("progress_msg", "")
        print(f"  progress={p:.2f} {msg}")
        if p is not None:
            if p >= 1.0:
                print("GraphRAG 完成!")
                break
            if p < 0:
                print(f"GraphRAG 失败: {msg}")
                break
    else:
        print(f"  (no progress yet) {json.dumps(data, ensure_ascii=False)[:200]}")
    time.sleep(3)
else:
    print("超时 (300s)")

# 4. Final check
print("\n=== 最终 knowledge_graph ===")
r = requests.get(f"{BASE}/datasets/{DS}/graph", headers=HEADERS, timeout=10)
d = r.json()
g = d.get("data", {}).get("graph", {}) if d.get("data") else {}
ents = g.get("nodes", g.get("entities", [])) or []
rels = g.get("edges", g.get("relationships", [])) or []
print(f"entities: {len(ents)}, relationships: {len(rels)}")
if ents:
    for e in (ents[:10] if isinstance(ents, list) else []):
        print(f"  - {e.get('name', e.get('entity_name', '?'))} ({e.get('type', e.get('entity_type', '?'))})")
