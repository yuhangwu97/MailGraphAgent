# MailGraphAgent 重构蓝图

## 架构决策（已收敛）

**纯 RAGFlow + GraphRAG，不加 Neo4j。** GraphRAG 用 `light` method 单遍建图，
跨文档统一图谱（当前 RAGFlow 版本支持 dataset 级 entity resolution）。
删除入库路径上多余的 OpenAI 结构化提取。

- **建图**：清洗后的邮件文本 + 附件（DeepDoc）→ RAGFlow dataset → GraphRAG 自动建统一图。
- **查询**：自然语言 → RAGFlow 检索/graph_search → LLM 总结（query-time 用 OpenAI 合理保留）。
- **单一事实源**：进度状态 + 临时正文都在 Redis；图谱在 RAGFlow。**废弃 JSON 文件中转。**

## 目标数据流

```
fetch:   IMAP → parse(带 download_dir 提取附件) → clean → 噪音过滤
              → Redis 存正文+元数据(TTL) + 加入 ingest 队列 + 附件落盘

ingest:  遍历 Redis ingest 队列 → 每封:
              upload 清洗后文本(1 文档) + upload 附件文件(DeepDoc)
              → RAGFlow GraphRAG(light) 自动并入统一图
              → mark_ingested + 删除本地附件(即用即删)
         → start_parsing + wait_for_parsing

query:   NL → graph_search + retrieve → LLM 总结 → 表格 + 子图
```

## 逐文件改动

| 文件 | 改动 |
|------|------|
| `config/settings.py` | 加 GraphRAG 配置：method=light、entity_types、resolution、dataset 名、正文 TTL |
| `src/storage/redis_cache.py` | 加正文暂存：`store_mail`/`get_mail`/`list_pending_ingest`/`mark_ingested`（带 TTL + 队列） |
| `src/attachment/ragflow_client.py` | 修 import 崩溃；`create_dataset` 写全 graphrag 配置；`upload_email_extraction`→`upload_email`（存清洗文本，不再拍平 JSON）；附件走 `upload_file`(DeepDoc) |
| `src/pipeline.py` | 重写 `run_fetch`(→Redis) + `run_ingest`(Redis→RAGFlow)，去 OpenAI 提取，接附件 |
| `src/main.py` | CLI 收敛：`fetch` / `ingest` / `full-pipeline` / `web` / `check`；删 `extract`、`import-kb` |
| `src/web/app.py` | 工作台走 pipeline；列表从 Redis 读；去 Extractor；修 `build_result_subgraph` NameError；看板改造 |
| `src/ai/query_engine.py` | 基本保留（query-time OpenAI 合理） |
| 删除 | `src/ai/extractor.py`、`prompts.py`、`extraction_schema.py`、`config/extraction_schema.json`（入库不再用结构化提取） |

## 一个你会看到的取舍：项目看板

原看板的"进行中/已完成/进度条"来自被删掉的 OpenAI 结构化提取字段。
纯 GraphRAG 不产出这种确定性状态字段。所以看板将改为**图谱可如实提供的内容**：
项目 + 关联人员 + LLM 描述，去掉编造的状态/进度条。
（如需状态，后续可加一个只跑状态一个字段的轻量标注，但那会重新引入小规模二次提取。）

curl -s -H "Authorization: Bearer ragflow-TotgCeQwinlwoPP0SDnqbCUM4b5AJxhk9nyJviIhVgk" "http://localhost:9380/api/v1/datasets" | python3 -c "
import sys,json
d=json.load(sys.stdin)
data=d.get('data',{}) or {}
print(f'progress={data.get(\"progress\")}')
print(f'msg={str(data.get(\"progress_msg\",\"\"))[-200:]}')
"