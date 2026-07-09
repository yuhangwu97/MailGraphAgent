"""Check GraphRAG status."""
import requests, json

API_KEY = "ragflow-TotgCeQwinlwoPP0SDnqbCUM4b5AJxhk9nyJviIhVgk"
BASE = "http://localhost:9380/api/v1"
DS = "7fe9b7207b8c11f1bfc331dc1fc8a8b4"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

print("=== trace_graphrag ===")
r = requests.get(f"{BASE}/datasets/{DS}/trace_graphrag", headers=HEADERS, timeout=10)
data = r.json().get("data", {}) or {}
print(f"progress={data.get('progress')} msg={data.get('progress_msg', '')[:300]}")

print("\n=== knowledge_graph ===")
r = requests.get(f"{BASE}/datasets/{DS}/knowledge_graph", headers=HEADERS, timeout=10)
d = r.json()
g = d.get("data", {}).get("graph", {}) if d.get("data") else {}
ents = g.get("entities", g.get("nodes", [])) or []
rels = g.get("relationships", g.get("edges", [])) or []
print(f"entities: {len(ents)}, relationships: {len(rels)}")
if ents:
    for e in (ents[:15] if isinstance(ents, list) else []):
        print(f"  - [{e.get('type', e.get('entity_type', '?'))}] {e.get('name', e.get('entity_name', '?'))}")
