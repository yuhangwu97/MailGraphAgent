# 邮件工作台重设计 — 设计文档

- 日期：2026-07-14
- 状态：待用户 review
- 方案：Redis 原生任务注册表 + 流水线阶段拆分 + 双来源统一两步流

---

## 1. 目标与痛点

用户反馈现有邮件工作台"流程难用、中途断了没处理、解析那里有问题"。定位到三类根因：

1. **流程难用**
   - "处理所选"一个按钮隐藏 4 条后端路径（`reprocess` / `ingest` / `parse-selected`），
     依据 `source_type` + `status` 隐式决定，用户看不出点下去会发生什么
     （[WorkbenchPage.vue:106-172](../src/web/src/pages/WorkbenchPage.vue#L106-L172)）。
   - 状态标签反直觉：`indexed`=“未处理”、`pending`=“待导入”、`skipped`=“已完成”，字段名与标签对不上
     （[MailList.vue:34-41](../src/web/src/components/workbench/MailList.vue#L34-L41)）。
   - IMAP 拉取与文件导入是两条割裂的流程：IMAP 是"一把梭"（拉取即解析正文+附件+噪音过滤+入队），
     文件是"扫表头→选→解析"两步。两者心智模型不一致。

2. **中途断了没处理（无断点恢复）**
   - 进度只活在 `mailgraph:progress:{job_id}` pub/sub 频道 + 前端内存数组里，
     **没有持久化 Job 记录**。刷新 / 断网 / API 重启 → 进度归零，无从恢复。
   - `claim_pending_mail()` 用 SPOP 把邮件弹出 `ingest_queue`
     （[redis_cache.py:545-558](../src/backend/storage/redis_cache.py#L545-L558)）；
     worker 建图中途崩溃 → 该邮件既不在队列、又永远卡在 `processing`，无 reaper 回收。

3. **解析问题**（用户确认全部命中）
   - 附件 DeepDoc 解析同步内联在 `run_ingest` 循环里
     （[pipeline.py:291-310](../src/backend/pipeline.py#L291-L310)）：无超时、无进度、卡住拖死整封邮件。
   - DeepDoc 模型缺失（LFS 上 7 个 onnx 全部 404）时静默回落 pypdf，`logger.debug` 吞掉，
     用户不知道解析质量已降级（[pipeline.py:623-672](../src/backend/pipeline.py#L623-L672)）。
   - 解析与建图深度耦合，无法单独重试解析、也看不清它是独立一步。

### 用户明确的流程期望（2026-07-14 补充）

> pull 邮件那里，可以显示总共多少邮件，然后可以自定义拉数据，拉下来后，可以看到清单，
> 然后勾选去解析，倒入文件也是一样的。

即：**IMAP 与文件导入走完全一致的两步流** —— 先看到来源总量、自定义范围、拉回一份**只有表头的清单**，
再从清单**勾选**要处理的邮件去**解析建图**。

---

## 2. 核心设计理念

把 IMAP 变成 sources 注册表里的又一个"源"，让两个来源统一走：

```
   扫表头(轻量)          →   indexed 清单   →   勾选   →   解析建图(重活)
 ┌─────────────┐          ┌────────────┐              ┌────────────────────┐
 │ IMAP: 只拉  │          │ 统一清单    │   用户勾选   │ parse-attachments  │
 │ ENVELOPE/头 │  ──────▶ │ 主题/发件人 │  ─────────▶ │ + build-graph      │
 │ File: scan  │          │ /日期/来源  │              │ (worker + Job)     │
 └─────────────┘          └────────────┘              └────────────────────┘
   Stage A (index)          单一事实源                    Stage B (parse)
```

IMAP 端的可行性已确认（`imap_client.py`）：
- 总量：`select_folder()` 返回 `(status, count)`。
- 自定义范围：`search_uids(folder, since, before)` + `list_folders()`。
- 只拉表头：新增 `fetch_headers()`（`FETCH ... ENVELOPE` / `BODY.PEEK[HEADER.FIELDS (...)]`，只取元数据）。
- 回读整封：`fetch_by_uid(uid, folder)` 已存在。

---

## 3. 后端设计

### 3.1 持久化 Job 模型（新增 `src/backend/jobstore.py`）

每次扫描 / 解析 / 重处理都建一条持久 Job 记录，刷新 / 重启后可完整重建。

```
mailgraph:job:{job_id}            (hash)
  job_id
  type        scan | parse | reprocess          # 用户视角的动作
  source      imap | file                        # 仅 scan 用
  stage       acquire | parse-body | parse-attachments | build-graph
  status      queued | running | paused | completed | failed | interrupted | partial
  total / done / failed / skipped / att_failed   # 计数器
  cursor      断点游标（已处理到第几封 / 最后 message_id）
  params      JSON（folder / since / before / limit / paths / message_ids）
  error / summary
  created_at / updated_at / heartbeat_at
mailgraph:job:{job_id}:items      (set)     本 job 覆盖的 message_id
mailgraph:jobs:index              (zset by created_at)  列表与清理用
```

`jobstore.py` 提供：`create / get / list / update_progress / set_stage / heartbeat /
mark_done / mark_failed / requeue_items / reap_stale`。全部走现有 Redis 连接（`decode_responses=True`）。

### 3.2 路由（新增 `src/backend/routes/jobs.py`）

- `GET  /api/jobs` — 列出所有 Job（从 Redis 重建，按 created_at 倒序，可按 status 过滤）。
- `GET  /api/jobs/{id}` — Job 明细 + 覆盖邮件的逐封状态（含附件逐项）。
- `POST /api/jobs/{id}/pause` — 暂停（停止再领取新项）。
- `POST /api/jobs/{id}/resume` — 续跑（把剩余 / interrupted 项重新入队）。
- `POST /api/jobs/{id}/retry-failed` — 只重跑失败项。
- `DELETE /api/jobs/{id}` — 清除记录（不删邮件）。

### 3.3 流水线阶段拆分（改 `src/backend/pipeline.py`）

保留**两个用户阶段**（入库 / 建图），内部拆成 4 个显式阶段。关键是把**附件解析从建图剥离**成独立阶段：

| 用户阶段 | 内部阶段 | 说明 | 归属进程 |
|---------|---------|------|---------|
| 入库 Stage A | `acquire` | IMAP 拉表头 / 文件扫表头 → `indexed` | API |
| 入库 Stage A | `parse-body` | 勾选后回读整封 → 解析正文 + 清洗 → 暂存入队 | API（IO） |
| 建图 Stage B | `parse-attachments` | 附件解析（独立、逐项、超时、可单独重试） | worker |
| 建图 Stage B | `build-graph` | 正文 + 附件文本入 LightRAG | worker |

`scan`（新，替代 IMAP 一把梭的 `run_fetch`）：
- `run_scan(source, params)` → 对 IMAP 走 `fetch_headers`、对文件走现有 `scan_file`，
  统一产 `HeaderRecord` 写入 `indexed`（`store_indexed` 已支持 locator）。
- IMAP 的 `HeaderRecord.locator = {"source_type": "imap", "folder": ..., "uid": ..., "account_id": ...}`。
- **噪音过滤不再在扫描时自动跳过**（用户显式勾选即视为想要）；改为清单里的可选"疑似噪音"标记/过滤器。

`parse`（复用并增强现有 `parse_selected`）：
- 对勾选的 `indexed` 邮件按 `(source_type, path/folder)` 归组、回读整封、解析正文入队，
  再入队 `ingest` 交 worker。IMAP 与文件共用此路径。

`parse-attachments`（新，从 `run_ingest` 剥离）：
- 每个附件记 `pending / parsing / parsed / failed / degraded`，存 `mailgraph:mail:{id}:atts`（hash）。
- **超时保护**：每附件解析设可配超时（`settings.attachment_parse_timeout`，默认 120s），
  卡住的附件标 `failed` 并跳过，不拖死整封。
- **模型缺失显式化**：DeepDoc 模型不可用时标 `degraded`（已回落 pypdf/docx），
  记录原因；**不再 `logger.debug` 静默吞掉**，明确上报到 Job 与 UI。
- **独立重试**：可只重跑附件解析，不重新拉整封邮件。

### 3.4 崩溃恢复 / 断点续跑（改 `src/backend/worker.py` + `redis_cache.py` + `jobqueue.py`）

- **inflight 追踪**：领取邮件时不再纯 SPOP。新增 `claim_pending_mail_tracked()`：
  SPOP 出队的同时把 message_id 写入 `mailgraph:inflight`（zset，score=claimed_at，member=`{worker_id}:{mid}`）。
  建图完成后从 inflight 移除。
- **heartbeat**：worker 每 ~10s 更新当前 Job 的 `heartbeat_at` 与 inflight 时间戳。
- **reaper**：worker 主循环每 N 秒扫描 —— Job `status=running` 但 `heartbeat_at` 过期（>60s）
  → 标 `interrupted`；其 inflight 邮件通过 `requeue_pending()` 放回 `ingest_queue`，
  并把邮件状态从 `processing` 复位为 `pending`。僵尸邮件（`processing` 且不在 inflight/队列）同样回收。
- **启动恢复 sweep**：worker 启动先跑一次 reaper，把上次崩溃遗留的 inflight 全部归队。
- **resume 动作**：Job 记录持久，UI 永远能重建"62%、3 封失败、可续跑"；resume 只重新入队剩余 / interrupted 项。
- `jobqueue.JOB_TYPES` 扩展：`scan` / `parse-body` 属 IO 轻量仍在 API 侧跑（保持现状），
  但产出的 Job 记录纳入统一恢复；`parse-attachments` + `build-graph` 在 worker，受 reaper 保护。

### 3.5 状态词表理顺

内部 status 值**不变**（避免数据迁移），只改 UI 映射并新增 `stage` 字段：

| 内部 status/stage | 新 UI 标签 |
|-------------------|-----------|
| `indexed`         | 待入库 |
| `pending`         | 待重拉 |
| `processing` + stage=`parse-attachments` | 解析附件中 |
| `processing` + stage=`build-graph`       | 建图中 |
| `done`            | 已建图 |
| `failed`          | 失败·可重试 |
| `skipped`         | 已跳过·噪音 |

---

## 4. 前端设计

### 4.1 统一入口（新增 `IntakeDialog.vue`，替代 `TopBar` 两按钮 + `ImportDrawer` 两 mode）

一个「添加邮件」对话框，Tab 切「IMAP 拉取 / 文件导入」，两条路都产出一个 `scan` Job：

- **IMAP 拉取 Tab**：
  - 文件夹下拉（`list_folders`），选中即显示该文件夹**总量**（`select_folder` count）。
  - 自定义范围：日期起止 + 可选数量上限。
  - 「扫描」→ 建 `scan` Job → 拉回表头 → 进 indexed 清单。
- **文件导入 Tab**：
  - 选文件（复用现有原生对话框 / 浏览），显示将扫描的文件与预计邮件数。
  - 「扫描」→ 建 `scan` Job → 进 indexed 清单。

### 4.2 邮件清单（改 `MailList.vue`）

- 清单即"单一事实源"，展示两来源的 `indexed` + 其它状态邮件，来源列区分 IMAP / 文件类型。
- 勾选 → 「解析所选」（单一明确动作，替代隐藏 4 路径的"处理所选"）→ 建 `parse` Job。
- 状态标签按 §3.5 理顺；`processing` 行显示当前子阶段（解析附件中 / 建图中）。
- 行内可展开看附件逐项解析状态 + 原因（尤其 `degraded` 模型缺失提示）。

### 4.3 任务中心（新增 `JobCenter.vue` + `JobCard.vue` + `JobDetail.vue`，吸收 `ActivityPanel`）

替代易失的 `processLogs` 数组 + 抽屉内联日志：

```
┌─ 任务中心 ────────────────────────┐
│ ● 扫描 INBOX · 200 封     完成      │
│ ● 解析所选 · 124 封      运行中 62%│
│   附件 ▰▰▰▱ · 建图 ▰▰▱▱ 76/124    │
│   ⚠ 3 附件降级(模型缺失)           │
│   [暂停] [明细]                    │
│ ⚠ 解析 · 已中断(worker 崩溃)       │
│   [续跑 剩余 45] [重跑失败 2][明细]│
└───────────────────────────────────┘
```

- 每 Job 一张卡：阶段进度条、计数、`暂停 / 续跑 / 重跑失败 / 明细 / 清除`。
- 明细逐封展开，含附件逐项状态 + 原因。
- 加载时 `GET /api/jobs` 从 Redis 重建；实时更新继续走现有 SSE 事件通道。
- 新增 `stores/jobs.ts` 管理 Job 状态与轮询/SSE 合并。

---

## 5. 迁移、兼容与测试

- **迁移**：内部 status 值不变；Job 记录、`inflight`、`atts`、`stage` 均为新增键，纯增量。
  旧的卡死 `processing` 邮件由 reaper 首次运行时自动归队。
- **兼容**：保留现有 `list_mails` / `stats` / `store_indexed` / `parse_selected` 主体，
  IMAP 作为新源接入 registry；旧 `run_fetch` 标记 `@deprecated` 但保留实现，
  内部改为委托 `run_scan` + `parse` 的组合，保证既有调用点（如 `run_full`）不破。
- **测试**：
  - `jobstore` CRUD + `reap_stale` 归队单测。
  - IMAP `fetch_headers` → `HeaderRecord` → `read_message(locator)` 往返单测。
  - 附件解析超时 → `failed`、模型缺失 → `degraded` 单测。
  - **集成测试**：模拟 worker 建图中途崩溃（heartbeat 过期）→ reaper 归队 → resume 建完。

---

## 6. 实施范围与顺序

1. **后端骨架（核心）**：`jobstore.py` + reaper + inflight 追踪 + 阶段拆分（§3.1、3.3、3.4）。
2. **来源统一**：IMAP 接入 sources registry + `run_scan`（§2、§3.3）。
3. **路由**：`routes/jobs.py` + `routes/mails.py` 改造产 Job（§3.2）。
4. **前端**：`IntakeDialog` + `JobCenter` + `MailList` 改造 + `stores/jobs.ts`（§4）。
5. **收尾**：状态词表、迁移 sweep、测试（§3.5、§5）。

第 1–2 步是解决三个痛点的核心；可先落地后端再做前端。

---

## 7. 缺失的 onnx 模型（设计外运维项）

本设计让"模型缺失"变得可见且优雅降级（标 `degraded`），但要恢复满血解析质量，
仍需单独把 7 个 DeepDoc onnx 模型补回（从 HuggingFace `InfiniFlow/deepdoc` 下载，
或由仓库 owner 重推 git LFS）。当前本地已设 `lfs.skipdownloaderrors` + `skip-smudge`，
它们是 132 字节的指针占位符。
