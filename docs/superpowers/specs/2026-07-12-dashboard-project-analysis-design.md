# Dashboard 项目看板优化 — AI 分析 + 分页 + Chat 联动

**日期**: 2026-07-12
**分支**: feat/lightrag-worker-split

## 目标

优化 `/dashboard` 项目看板，增加 AI 分析能力、分页、Chat 联动入口。

## 功能清单

1. **分页** — 项目列表 20 个/页，前后端分页
2. **AI 摘要** — 每个项目卡片展示缓存的 AI 简要分析（4 个维度）
3. **详细报告** — 点击"查看报告"弹出完整 7 维度分析报告（Modal）
4. **Chat 快捷入口** — 报告弹窗底部按钮，创建新会话并跳转 `/chat?prompt=...`
5. **分析缓存** — Redis 持久化，`mailgraph:project_analysis:{project_name}`

## 分析维度

| # | 维度 | 卡片 | 报告 |
|---|------|:---:|:---:|
| 📌 | 一句话概述 | ✅ | ✅ |
| 📈 | 项目阶段/状态 | ✅ | ✅ |
| 💰 | 合同与金额 | ❌ | ✅ |
| 📅 | 关键时间节点 | ✅ | ✅ |
| 👥 | 核心人员 | ✅ | ✅ |
| 🏢 | 相关公司/组织 | ❌ | ✅ |
| 📝 | 近期关键动态 | ❌ | ✅ |

## 技术设计

### 后端

**新增路由** `src/backend/routes/projects.py`：

- `GET /api/projects` — 分页返回项目列表
  - Query: `page=1`, `page_size=20`
  - 从 Neo4j 获取 project 类型实体，通过关系边关联人员/公司
  - 附带 Redis 中缓存的 AI 摘要（如有）
  - Response: `{ projects: ProjectItem[], total: number, page: number, page_size: number }`

- `POST /api/projects/{name}/analyze` — 触发 AI 分析
  - 调用 QueryEngine.query() 生成 7 维度报告
  - 结果存入 Redis，key: `mailgraph:project_analysis:{project_name}`
  - 返回完整报告（SSE 流式，复用现有 query 流式模式）
  - TTL: 24h（分析结果可过期重新生成）

- `GET /api/projects/{name}/analysis` — 读取缓存
  - 有缓存直接返回
  - 无缓存返回 404，前端触发 analyze

**Redis 数据结构**：
```json
{
  "summary": {
    "overview": "一句话概述",
    "stage": "项目阶段",
    "key_dates": "关键时间节点",
    "core_people": ["人员1", "人员2"]
  },
  "report": {
    "overview": "...",
    "stage": "...",
    "contract": "...",
    "key_dates": "...",
    "core_people": "...",
    "companies": "...",
    "recent_activity": "..."
  },
  "generated_at": 1720771200.0
}
```

**注册路由**：`app.py` 中挂载 `projects.router`

### 前端

**新增组件**：
- `src/web/src/components/dashboard/ProjectReportModal.vue` — 报告弹窗（Teleport to body）
  - 7 维度分节展示
  - 底部 "在 Chat 中深度分析 →" 按钮

**修改文件**：

| 文件 | 变更 |
|------|------|
| `pages/DashboardPage.vue` | 分页逻辑、调用 `/api/projects`、分页组件 |
| `components/dashboard/ProjectCard.vue` | 新增 props: `aiSummary`、`hasReport`；按钮："查看报告" / "Chat 分析" |
| `api/index.ts` | 新增 `ProjectItem`、`ProjectAnalysis` 类型；`projectsApi` |
| `pages/ChatPage.vue` | 读取 `route.query.prompt`，自动填入输入框 |
| `App.vue` | 无需变动 |

**Chat 联动流程**：
1. 用户点击 "在 Chat 中深度分析"
2. 创建新会话 `POST /api/conversations`
3. 构建 prompt：`"请帮我深入分析项目「{name}」。项目概述：{overview}。请从图谱中提取更多细节，包括邮件往来、合同金额、时间线、风险点等。"`
4. `router.push({ path: '/chat', query: { prompt } })`
5. ChatPage 读取 query 参数填入输入框

**分页组件**：内联实现，简洁的前一页/后一页 + 页码显示

### 数据流

```
DashboardPage (mounted)
  → GET /api/projects?page=1&page_size=20
  → 渲染 ProjectCard（含缓存 AI 摘要）

用户点击"查看报告"
  → GET /api/projects/{name}/analysis
  → 有缓存 → 立即展示 Modal
  → 无缓存 → POST /api/projects/{name}/analyze（SSE 流式）
    → QueryEngine 分析图谱数据
    → 写入 Redis
    → 展示 Modal

用户点击"Chat 深度分析"
  → 构建 prompt
  → 创建会话
  → 跳转 /chat?prompt=...
```

## 影响范围

- **后端新增**: 1 个路由文件 + 1 个存储类 + app.py 注册
- **前端新增**: 1 个 Modal 组件
- **前端修改**: 3 个页面/组件 + api/index.ts
- **不涉及**: Neo4j schema、LightRAG、Pipeline、Worker

## 验收标准

- [ ] Dashboard 按 20 个/页分页展示
- [ ] 每张卡片展示 AI 摘要（4 维度）
- [ ] 点击"查看报告"弹出完整 Modal
- [ ] Modal 内 7 个维度全部展示
- [ ] "Chat 深度分析"按钮创建新会话并跳转
- [ ] ChatPage 自动填入 prompt
- [ ] 分析结果缓存至 Redis，二次查看不重复调用
- [ ] 无缓存时流式生成报告
