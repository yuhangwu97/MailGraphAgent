# MailGraphAgent — 历史邮件智能分析与知识图谱系统

## 一、需求评估

### 1.1 项目目标

为某企业搭建一套 **15 年以上历史邮件的 AI 分析服务**，自动从海量邮件中提取商业关系，构建 **"客户公司 → 外部对接人 → 项目 → 内部负责人"** 知识图谱，并提供可视化查询与推理能力。

### 1.2 核心需求拆解

| 编号 | 需求 | 优先级 | 复杂度 | 说明 |
|------|------|--------|--------|------|
| R1 | IMAP 邮件抓取 | P0 | 中 | 支持 QQ/Gmail/阿里企业邮箱，需断点续传 |
| R2 | 附件解析 | P0 | 高 | PDF/Word/Excel/Zip，保留表格结构 |
| R3 | AI 实体提取 | P0 | 高 | 用 OpenAI 提取公司、联系人、项目、负责人、执行状态 |
| R4 | 知识图谱构建 | P0 | 高 | Neo4j 存储，全局去重，跨年份实体对齐 |
| R5 | 图谱推理 | P1 | 高 | 基于图谱的关系推理（如：某人关联的所有项目） |
| R6 | 图谱可视化 | P0 | 中 | Web 前端展示可交互的关系网络 |
| R7 | 15 年海量数据处理 | P0 | 中 | 按月分片、限流、断点续传、即用即删 |
| R8 | Token 成本控制 | P0 | 中 | 文本清洗、历史裁剪、预过滤噪音邮件 |

### 1.3 硬件评估

| 配置项 | 最低要求 | 推荐配置 | OP 现有 Mac mini | 客户提供 |
|--------|----------|----------|-------------------|----------|
| 内存 | 16GB | 32GB | 16GB ✅ 刚好够 | 32GB 充裕 |
| 存储 | 256GB+外置 | 1TB | 256GB ⚠️ 小 | 1TB 充裕 |
| CPU | M1+ / i7+ | M2+ / i7+ | M 系列 ✅ | 待确认 |
| GPU | 不需要 | 不需要 | — | — |

**结论**：OP 的 16GB+256GB Mac mini 可以跑通 Demo（需严格控制并发），但正式处理 15 年数据必须用客户的 32GB+1TB 设备。

### 1.4 API 环境

- **API Key**：客户提供的 OpenAI 中转 Key
- **Base URL**：`http://43.160.245.179:8080`（国内中转）
- **Model**：`gpt-5.5`（高推理能力）
- **注意**：中转服务可能不稳定，需加 Retry 机制

---

## 二、技术架构

### 2.1 总体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        MailGraphAgent                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐   ┌──────────────┐   ┌───────────────────────┐    │
│  │ 邮件抓取  │   │  附件解析     │   │  AI 实体提取          │    │
│  │ (IMAP)   │ → │  (RAGFlow    │ → │  (OpenAI gpt-5.5)     │    │
│  │          │   │   DeepDoc)   │   │  Structured Output    │    │
│  └──────────┘   └──────────────┘   └───────────┬───────────┘    │
│                                                 │                │
│                                    ┌────────────▼───────────┐    │
│                                    │  知识图谱 (Neo4j)       │    │
│                                    │  实体去重 · 关系叠加    │    │
│                                    └────────────┬───────────┘    │
│                                                 │                │
│  ┌──────────────────────────────────────────────▼───────────┐    │
│  │              可视化前端 (Streamlit)                       │    │
│  │  图谱交互 · 项目看板 · 联系人统计 · 智能问答               │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  全部组件通过 Docker Compose 管理，一键部署到客户机器              │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 技术选型

| 层次 | 技术 | 选型理由 |
|------|------|----------|
| 语言 | Python 3.11+ | 生态丰富，AI/数据类库完善 |
| 邮件抓取 | `imaplib` + `mail-parser` | 标准库，无需额外依赖 |
| 文档解析 | RAGFlow API (DeepDoc) | 视觉级 PDF/Excel 解析，保留表格拓扑 |
| AI 提取 | OpenAI SDK (`gpt-5.5`) | 客户指定，强推理能力 |
| 图数据库 | Neo4j Community (Docker) | 图查询强，可视化生态好，社区版免费 |
| 前端 | Streamlit + pyvis/streamlit-agraph | 全 Python，快速搭建 |
| 容器化 | Docker Compose | 一键部署，环境复刻 |
| 本地缓存 | SQLite | 记录处理进度，断点续传 |
| 配置管理 | `.env` + Pydantic Settings | 路径参数化，切换环境无忧 |

### 2.3 为什么不直接用 RAGFlow 的 GraphRAG？

| 维度 | RAGFlow 自带 GraphRAG | 本方案 (Neo4j) |
|------|----------------------|----------------|
| 图谱粒度 | 文档级概念片段 | 业务实体（公司/人/项目） |
| 跨文档去重 | ❌ 孤岛式，无法连通 | ✅ MERGE 全局去重 |
| 关系类型 | 通用"提及"关系 | 定制化（属于/负责/推进） |
| 可视化 | 概念网/思维导图 | 精准企业关系网 |
| 推理能力 | 文本摘要型 | 图遍历/多层深度查询 |

**策略**：RAGFlow 只做文档解析（DeepDoc），图谱构建和推理完全由 Neo4j 掌控。

---

## 三、数据流设计

### 3.1 邮件处理 Pipeline

```
Step 1: IMAP 连接 → 按月份 SEARCH 邮件列表
Step 2: 检查 SQLite 缓存 → 跳过已处理 Message-ID
Step 3: FETCH 邮件 (RFC822) → 解析 Header + Body + Attachments
Step 4: 文本清洗:
        - 剥离 HTML 标签 (BeautifulSoup)
        - 裁剪回复历史 (正则匹配 "-----Original Message-----")
        - 过滤签名中的图片附件
Step 5: 附件分类:
        - .pdf/.docx/.xlsx/.txt → 送 RAGFlow DeepDoc 解析
        - .zip/.rar → 解压后递归处理
        - .png/.jpg/.exe → 跳过
Step 6: 组装文本 → 送 OpenAI 提取 JSON
Step 7: JSON → Neo4j MERGE 写入
Step 8: 更新 SQLite 缓存
Step 9: 删除本地临时附件 (即用即删)
```

### 3.2 AI 提取 Prompt 设计（核心）

```json
{
  "company": {
    "name": "归一化的公司名称",
    "aliases": ["曾用名/简称"]
  },
  "contacts": [
    {
      "name": "外部对接人姓名",
      "role": "职位",
      "email": "邮箱"
    }
  ],
  "internal_owners": [
    {
      "name": "内部负责人姓名",
      "role": "职位"
    }
  ],
  "projects": [
    {
      "name": "项目名称/代号",
      "status": "进行中|已完成|停滞|已取消",
      "progress_summary": "当前进度描述",
      "risk_points": ["风险点1", "风险点2"],
      "key_milestones": ["里程碑1", "里程碑2"]
    }
  ],
  "summary": "邮件核心内容摘要（<200字）",
  "date_relevance": "邮件涉及的时间范围"
}
```

### 3.3 Neo4j 图模型 (Schema)

```
节点 (Nodes):
  Company    {name, aliases, industry}
  Contact    {name, email, role}          ← 外部对接人
  Employee   {name, email, role}          ← 内部负责人
  Project    {name, status, summary, timeline}
  Email      {message_id, subject, date, summary}

关系 (Edges):
  (Contact)  -[:BELONGS_TO]->  (Company)      "外部对接人属于某公司"
  (Contact)  -[:PARTICIPATES_IN]-> (Project)   "参与某项目"
  (Employee) -[:MANAGES]->      (Project)      "负责某项目"
  (Employee) -[:CONTACTS]->     (Contact)       "对接某外部联系人"
  (Project)  -[:FOR]->          (Company)       "项目为客户公司服务"
  (Email)    -[:MENTIONS]->     (Project)       "邮件提及某项目"
  (Email)    -[:BETWEEN]->      (Contact|Employee) "邮件往来"
```

---

## 四、项目结构

```
MailGraphAgent/
├── docker-compose.yml          # Neo4j + RAGFlow + App
├── .env.example                # 环境变量模板
├── requirements.txt            # Python 依赖
├── pyproject.toml              # 项目元数据
├── README.md
│
├── config/
│   └── settings.py             # Pydantic Settings 配置管理
│
├── src/
│   ├── __init__.py
│   ├── main.py                 # CLI 入口，编排整体流程
│   │
│   ├── mail/
│   │   ├── __init__.py
│   │   ├── imap_client.py      # IMAP 连接、搜索、获取
│   │   ├── parser.py           # 邮件解析（Header/Body/Attachment）
│   │   └── cleaner.py          # 文本清洗（HTML/历史/噪音）
│   │
│   ├── attachment/
│   │   ├── __init__.py
│   │   ├── ragflow_client.py   # RAGFlow API 客户端
│   │   └── extractor.py        # 附件解压与分类
│   │
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── openai_client.py    # OpenAI API 调用 + Retry
│   │   └── prompts.py          # Prompt 模板管理
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── neo4j_client.py     # Neo4j 连接与写入
│   │   ├── schema.py           # 图模型定义
│   │   └── queries.py          # 常用 Cypher 查询
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   └── sqlite_cache.py     # 处理进度缓存
│   │
│   └── web/
│       ├── __init__.py
│       └── app.py              # Streamlit 前端
│
├── tests/
│   ├── test_imap_client.py
│   ├── test_cleaner.py
│   ├── test_ai_extract.py
│   └── test_neo4j.py
│
├── scripts/
│   ├── setup_db.sh             # 初始化 Neo4j
│   └── dev.sh                  # 开发环境一键启动
│
└── data/                       # (gitignore) 运行时数据
    ├── attachments/            # 临时附件
    ├── cache.db                # SQLite 缓存
    └── logs/                   # 运行日志
```

---

## 五、实施计划

### Phase 0：环境验证（今晚）

- [ ] 验证 OpenAI 中转 API 可用性（`gpt-5.5` 模型）
- [ ] 验证 QQ/Gmail IMAP 连通性（获取授权码）
- [ ] Docker Desktop 确认可用

### Phase 1：最小 Demo（第 1-3 天）

- [ ] **Day 1**：IMAP 邮件抓取模块
  - 拉取最近 100 封邮件正文和附件
  - SQLite 缓存 Message-ID
- [ ] **Day 2**：AI 实体提取
  - 邮件清洗（HTML 剥离、历史裁剪）
  - Prompt 调试，确保稳定输出 JSON
  - 接入 OpenAI API + Retry
- [ ] **Day 3**：Neo4j + 可视化
  - Docker 启动 Neo4j
  - JSON → Cypher MERGE 写入
  - Streamlit + pyvis 图谱展示

### Phase 2：附件解析增强（第 4-5 天）

- [ ] Docker 部署 RAGFlow
- [ ] RAGFlow DeepDoc API 接入
- [ ] PDF/Excel/Word 附件 → 结构化文本
- [ ] 压缩包递归解析

### Phase 3：全量处理优化（第 6-7 天）

- [ ] 按月分片拉取
- [ ] 限流控制（随机 delay）
- [ ] 噪音邮件预过滤
- [ ] 全量跑批脚本

### Phase 4：图谱推理与完善（第 8-10 天）

- [ ] 图谱推理查询（多层关系遍历）
- [ ] 项目执行情况统计
- [ ] 前端看板完善
- [ ] Docker Compose 一键部署

---

## 六、风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 阿里邮箱限流封 IP | 邮件抓取中断 | 按月分片 + 随机 delay + 断点续传 |
| 中转 API 不稳定 | AI 提取失败 | Retry 3 次 + 指数退避 |
| 附件总量超预期 | 256G 硬盘爆满 | 即用即删策略 + 外置 SSD |
| 16GB 内存 OOM | Neo4j 崩溃 | Docker 限制 JVM 最大 4GB，单线程处理 |
| RAGFlow 解析失败 | 附件文本丢失 | Fallback 到本地 pypdf/docx 解析 |
| Token 消耗过大 | 成本失控 | 预过滤噪音 + 长文本裁剪 + 历史记录去重 |

---

## 七、下一步行动

1. **今晚**：验证 API 和 IMAP 连通性
2. **明天**：开始写 IMAP 抓取模块代码
3. 有任何报错随时反馈，即时 Debug
