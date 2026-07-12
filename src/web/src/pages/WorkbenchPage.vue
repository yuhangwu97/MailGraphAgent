<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { mailsApi, graphApi, type MailStats, type MailItem, type GraphStatus } from '@/api'
import { useStatusStore } from '@/stores/status'
import AccountManager from '@/components/workbench/AccountManager.vue'

const statusStore = useStatusStore()

// ── 服务健康 ──
const services = computed(() => statusStore.services)
const serviceChips = computed(() => [
  { key: 'redis', label: 'Redis', ok: services.value.redis },
  { key: 'neo4j', label: 'Neo4j', ok: services.value.neo4j },
  { key: 'milvus', label: 'Milvus', ok: services.value.milvus },
])

// ── 知识图谱状态（LightRAG 建图侧）──
const gstatus = ref<GraphStatus>({
  graph: { entities: 0, relationships: 0 },
  docs: { pending: 0, processing: 0, processed: 0, failed: 0, duplicate: 0 },
  pipeline: { busy: false, latest_message: '', job_name: '' },
})

const docChips = computed(() => {
  const d = gstatus.value.docs
  const chips = [
    { key: 'processed', label: '已建图', value: d.processed, cls: 'st-done' },
    { key: 'processing', label: '建图中', value: d.processing, cls: 'st-processing' },
    { key: 'pending', label: '排队', value: d.pending, cls: 'st-pending' },
    { key: 'failed', label: '失败', value: d.failed, cls: 'st-failed' },
  ]
  // 重复插入被 LightRAG 标记的记录（非真失败）——仅在存在时淡显，避免误读为失败
  if (d.duplicate) {
    chips.push({ key: 'duplicate', label: '重复(已跳过)', value: d.duplicate, cls: 'st-skipped' })
  }
  return chips
})

async function refreshGraphStatus() {
  try { gstatus.value = await graphApi.status() } catch (e) { console.error(e) }
}

// ── KPI ──
const kpi = ref<MailStats>({ total: 0, done: 0, pending: 0, failed: 0, skipped: 0, ingested: 0, indexed: 0 })

const kpiCards = [
  { key: 'total' as const, value: '邮件总数', color: '#57534E', get: (k: MailStats) => k.total },
  { key: 'indexed' as const, value: '待处理', color: '#3B6EA5', get: (k: MailStats) => (k.indexed || 0) + (k.pending || 0) },
  { key: 'done' as const, value: '已入库', color: '#1F6F5C', get: (k: MailStats) => k.done },
  { key: 'pending' as const, value: '待导入', color: '#B4791F', get: (k: MailStats) => k.pending },
  { key: 'failed' as const, value: '失败', color: '#9A3B2E', get: (k: MailStats) => k.failed },
  { key: 'skipped' as const, value: '已跳过', color: '#A8A29E', get: (k: MailStats) => k.skipped },
]

async function refreshStats() { try { kpi.value = await mailsApi.stats() } catch (e) { console.error(e) } }

// ── IMAP fetch ──
const folder = ref('INBOX')
const limit = ref(20)
const fetching = ref(false)
const fetchLogs = ref<string[]>([])

async function handleFetch() {
  fetching.value = true; fetchLogs.value = []
  mailsApi.fetch(folder.value, limit.value, {
    onProgress(d: any) { fetchLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete() { fetchLogs.value.push('拉取完成'); fetching.value = false; refreshAll() },
    onError(m: string) { fetchLogs.value.push('错误: ' + m); fetching.value = false },
  })
}

// ── File import: pick & index ──
const picking = ref(false)
const pickErr = ref('')
const pickPaths = ref<string[]>([])

async function pickFolder() {
  picking.value = true; pickErr.value = ''
  try {
    const r = await mailsApi.pick('folder')
    if (!r.canceled && r.paths.length) pickPaths.value = r.paths
  } catch (e: any) { pickErr.value = e.message || String(e) }
  finally { picking.value = false }
}
async function pickFiles() {
  picking.value = true; pickErr.value = ''
  try {
    const r = await mailsApi.pick('files')
    if (!r.canceled && r.paths.length) pickPaths.value = r.paths
  } catch (e: any) { pickErr.value = e.message || String(e) }
  finally { picking.value = false }
}

const indexing = ref(false)
const indexLogs = ref<string[]>([])

function doIndex() {
  if (!pickPaths.value.length) return
  indexing.value = true; indexLogs.value = []
  mailsApi.indexFiles(pickPaths.value, {
    onProgress(d: any) { indexLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete(d: any) {
      indexLogs.value.push('扫描完成: ' + JSON.stringify(d))
      indexing.value = false; pickPaths.value = []
      refreshAll()
    },
    onError(m: string) { indexLogs.value.push('错误: ' + m); indexing.value = false },
  })
}

// ── Unified mail list ──
type MailFilter = 'all' | 'todo' | 'done'
const filter = ref<MailFilter>('all')
const search = ref('')

// 统一列表（所有状态，服务端按日期倒序分页）
const mails = ref<MailItem[]>([])
const total = ref(0)
const selectedIds = ref<Set<string>>(new Set())

const PAGE_SIZE = 200

// Status metadata
const STATUS_META: Record<string, { label: string; cls: string }> = {
  indexed: { label: '未处理', cls: 'st-pending' },
  pending: { label: '待导入', cls: 'st-pending' },
  processing: { label: '处理中', cls: 'st-processing' },
  done: { label: '已处理', cls: 'st-done' },
  failed: { label: '失败', cls: 'st-failed' },
  skipped: { label: '已跳过', cls: 'st-skipped' },
}
function statusMeta(s: string) {
  return STATUS_META[s] || { label: s || '未知', cls: 'st-pending' }
}

// Source label
function sourceLabel(t?: string): { label: string; cls: string } {
  if (t === 'pst') return { label: '📦 PST', cls: 'source-pst' }
  if (t === 'eml') return { label: '📧 EML', cls: 'source-eml' }
  if (t === 'msg') return { label: '📧 MSG', cls: 'source-msg' }
  if (t) return { label: `📂 ${t.toUpperCase()}`, cls: 'source-file' }
  return { label: '📨 IMAP', cls: 'source-imap' }
}

// Is a mail from file import?
function isFileMail(m: MailItem): boolean {
  return !!m.source_type && m.source_type !== 'imap'
}

// Is a mail from IMAP?
function isImapMail(m: MailItem): boolean {
  return !m.source_type || m.source_type === 'imap'
}

// 完成态归一：done/skipped = 已完成，其余 = 未完成
function isDoneMail(m: MailItem): boolean {
  return m.status === 'done' || m.status === 'skipped'
}

// 过滤器 chips（计数来自 KPI 统计）
const filterChips = computed(() => {
  const k = kpi.value
  const done = (k.done || 0) + (k.skipped || 0)
  const todo = (k.pending || 0) + (k.indexed || 0) + (k.failed || 0) + (k.processing || 0)
  return [
    { key: 'all' as MailFilter, label: '全部', count: done + todo },
    { key: 'todo' as MailFilter, label: '未完成', count: todo },
    { key: 'done' as MailFilter, label: '已完成', count: done },
  ]
})

function setFilter(f: MailFilter) {
  if (filter.value === f) return
  filter.value = f
  selectedIds.value = new Set()
  refreshList()
}

// 当前可见（在当前页做搜索过滤）
const visibleMails = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return mails.value
  return mails.value.filter(m =>
    (m.subject || '').toLowerCase().includes(q) ||
    (m.from_addr || '').toLowerCase().includes(q) ||
    (m.from_name || '').toLowerCase().includes(q)
  )
})

// Selection
const allSelected = computed(() =>
  visibleMails.value.length > 0 && visibleMails.value.every(m => selectedIds.value.has(m.message_id))
)

function toggleAll() {
  if (allSelected.value) {
    const visibleIds = new Set(visibleMails.value.map(m => m.message_id))
    selectedIds.value = new Set([...selectedIds.value].filter(id => !visibleIds.has(id)))
  } else {
    const s = new Set(selectedIds.value)
    visibleMails.value.forEach(m => s.add(m.message_id))
    selectedIds.value = s
  }
}
function toggleSelect(id: string) {
  const s = new Set(selectedIds.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selectedIds.value = s
}

// ── Actions ──
const processing = ref(false)
const processLogs = ref<string[]>([])

async function handleProcess() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  const byId = (id: string) => mails.value.find(x => x.message_id === id)
  const isTerminal = (m: MailItem) => ['done', 'skipped', 'failed'].includes(m.status)

  // 已完成/已跳过/失败 → 重新处理；未完成的文件邮件 → 解析入库；
  // 未完成的 IMAP → ingest（处理整个待入库队列）
  const reprocessIds = ids.filter(id => { const m = byId(id); return !!m && isTerminal(m) })
  const fileIds = ids.filter(id => { const m = byId(id); return !!m && !isTerminal(m) && isFileMail(m) })
  const imapTodo = ids.some(id => { const m = byId(id); return !!m && !isTerminal(m) && isImapMail(m) })

  processing.value = true; processLogs.value = []
  const promises: Promise<void>[] = []

  if (imapTodo) {
    promises.push(new Promise<void>((resolve) => {
      mailsApi.ingest(null, {
        onProgress(d: any) { processLogs.value.push('[IMAP] ' + (d.msg || JSON.stringify(d))) },
        onComplete() { resolve() },
        onError(m: string) { processLogs.value.push('[IMAP] 错误: ' + m); resolve() },
      })
    }))
  }
  if (fileIds.length) {
    promises.push(new Promise<void>((resolve) => {
      mailsApi.parseSelected(fileIds, {
        onProgress(d: any) { processLogs.value.push('[文件] ' + (d.msg || JSON.stringify(d))) },
        onComplete() { resolve() },
        onError(m: string) { processLogs.value.push('[文件] 错误: ' + m); resolve() },
      })
    }))
  }
  if (reprocessIds.length) {
    promises.push(new Promise<void>((resolve) => {
      mailsApi.reprocess(reprocessIds, {
        onProgress(d: any) { processLogs.value.push('[重处理] ' + (d.msg || JSON.stringify(d))) },
        onComplete() { resolve() },
        onError(m: string) { processLogs.value.push('[重处理] 错误: ' + m); resolve() },
      })
    }))
  }

  await Promise.all(promises)
  processing.value = false
  selectedIds.value = new Set()
  refreshAll()
}

// ── Data fetching ──
async function refreshList() {
  try {
    const r = await mailsApi.list({ filter: filter.value, page: 1, page_size: PAGE_SIZE })
    mails.value = r?.items || []
    total.value = r?.total || 0
  } catch (e) { console.error(e) }
}

async function refreshAll() {
  await Promise.all([refreshStats(), refreshList(), refreshGraphStatus(), statusStore.refresh()])
}

// 手动刷新整个界面数据（KPI / 图谱状态 / 邮件列表 / 服务健康）
const refreshing = ref(false)
async function handleRefresh() {
  refreshing.value = true
  try { await refreshAll() } finally { refreshing.value = false }
}

// 状态带轮询：建图/拉取/解析进行中时加速刷新，空闲时低频刷新，避免长期占用后端
let pollTimer: ReturnType<typeof setInterval> | null = null
const isBusy = computed(() =>
  fetching.value || indexing.value || processing.value || gstatus.value.pipeline.busy
)

onMounted(() => {
  refreshAll()
  pollTimer = setInterval(() => {
    refreshGraphStatus()
    if (isBusy.value) { refreshStats(); statusStore.refresh() }
  }, 5000)
})
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<template>
  <div class="workbench">
    <div class="wb-head">
      <h2>邮件工作台</h2>
      <button class="btn btn-secondary btn-sm refresh-btn" :disabled="refreshing" @click="handleRefresh">
        🔄 {{ refreshing ? '刷新中…' : '界面刷新' }}
      </button>
    </div>

    <!-- KPI -->
    <div class="kpi-row">
      <div v-for="card in kpiCards" :key="card.key" class="kpi-item">
        <span class="kpi-num" :style="{ color: card.color }">{{ card.get(kpi).toLocaleString() }}</span>
        <span class="kpi-text">{{ card.value }}</span>
      </div>
    </div>

    <!-- ── 知识图谱状态带（LightRAG 建图侧）── -->
    <div class="status-strip">
      <!-- 图谱规模 -->
      <div class="ss-group">
        <span class="ss-label">知识图谱</span>
        <span class="ss-metric"><b>{{ gstatus.graph.entities.toLocaleString() }}</b> 实体</span>
        <span class="ss-sep">·</span>
        <span class="ss-metric"><b>{{ gstatus.graph.relationships.toLocaleString() }}</b> 关系</span>
      </div>

      <div class="ss-divider" />

      <!-- 文档处理状态 -->
      <div class="ss-group">
        <span class="ss-label">建图进度</span>
        <span v-for="d in docChips" :key="d.key" class="badge" :class="d.cls">
          {{ d.label }} {{ d.value }}
        </span>
      </div>

      <div class="ss-divider" />

      <!-- Pipeline 实时状态 -->
      <div class="ss-group">
        <span class="ss-label">Pipeline</span>
        <span class="ss-pill" :class="gstatus.pipeline.busy ? 'ss-pill--busy' : 'ss-pill--idle'">
          <span class="dot" />
          {{ gstatus.pipeline.busy ? '建图中' : '空闲' }}
        </span>
        <span v-if="gstatus.pipeline.busy && gstatus.pipeline.latest_message"
              class="ss-msg" :title="gstatus.pipeline.latest_message">
          {{ gstatus.pipeline.latest_message }}
        </span>
      </div>

      <div class="ss-spacer" />

      <!-- 服务健康 -->
      <div class="ss-group">
        <span v-for="s in serviceChips" :key="s.key" class="ss-svc" :class="{ 'ss-svc--down': !s.ok }">
          <span class="dot" />{{ s.label }}
        </span>
      </div>
    </div>

    <!-- Account Manager -->
    <div class="toolbar">
      <div class="toolbar-account">
        <AccountManager />
      </div>
    </div>

    <!-- ── Action Area: IMAP + File import side by side ── -->
    <div class="action-area">
      <!-- IMAP -->
      <div class="action-card">
        <div class="action-card-title">📨 邮箱拉取</div>
        <div class="action-card-body">
          <select v-model="folder" class="tb-select">
            <option value="INBOX">INBOX</option>
            <option value="[Gmail]/Sent Mail">[Gmail]/Sent Mail</option>
          </select>
          <input v-model.number="limit" type="number" min="1" max="200" class="tb-num" title="拉取数量" />
          <button class="tb-btn tb-btn--primary" :disabled="fetching" @click="handleFetch">
            {{ fetching ? '拉取中…' : '拉取' }}
          </button>
        </div>
        <div v-if="fetchLogs.length" class="log-box">
          <div v-for="(l, i) in fetchLogs" :key="'f' + i">{{ l }}</div>
        </div>
      </div>

      <!-- File import -->
      <div class="action-card">
        <div class="action-card-title">📂 文件导入</div>
        <div class="action-card-body">
          <button class="tb-btn" :disabled="picking" @click="pickFiles">
            📄 选择文件…
          </button>
          <button
            class="tb-btn tb-btn--primary"
            :disabled="indexing || !pickPaths.length"
            @click="doIndex"
          >
            {{ indexing ? '导入中…' : `导入所选 (${pickPaths.length})` }}
          </button>
        </div>
        <div v-if="pickPaths.length && !indexing" class="picker-info">
          已选 {{ pickPaths.length }} 项: {{ pickPaths[0] }}{{ pickPaths.length > 1 ? ' …等' : '' }}
        </div>
        <div v-if="pickErr" class="hint hint--err">{{ pickErr }}</div>
        <div v-if="indexLogs.length" class="log-box">
          <div v-for="(l, i) in indexLogs" :key="'ix' + i">{{ l }}</div>
        </div>
      </div>
    </div>

    <!-- ── Unified Mail List ── -->
    <div class="list-panel">
      <!-- Panel header: 状态过滤器 + search -->
      <div class="list-panel-header">
        <div class="tabs">
          <button
            v-for="c in filterChips" :key="c.key"
            :class="['tab', { active: filter === c.key }]"
            @click="setFilter(c.key)"
          >
            {{ c.label }}
            <span class="tab-count">{{ c.count }}</span>
          </button>
        </div>
        <input
          v-model="search"
          class="search-input"
          placeholder="搜索…"
        />
        <span v-if="search" class="search-hint">
          {{ visibleMails.length }} / {{ mails.length }}
        </span>
      </div>

      <!-- Action bar -->
      <div v-if="visibleMails.length" class="action-bar">
        <span v-if="selectedIds.size" class="sel-info">{{ selectedIds.size }} 封已选</span>
        <div class="spacer" />
        <button
          class="tb-btn tb-btn--primary"
          :disabled="processing || selectedIds.size === 0"
          @click="handleProcess"
        >
          {{ processing ? '处理中…' : '处理所选' }}
          <span v-if="selectedIds.size" class="btn-count">{{ selectedIds.size }}</span>
        </button>
      </div>

      <!-- Process logs -->
      <div v-if="processLogs.length" class="log-box">
        <div v-for="(l, i) in processLogs" :key="'pl' + i">{{ l }}</div>
      </div>

      <!-- Table -->
      <table v-if="visibleMails.length" class="data-table">
        <thead>
          <tr>
            <th style="width:36px">
              <input type="checkbox" :checked="allSelected" @change="toggleAll" />
            </th>
            <th style="width:80px">状态</th>
            <th style="width:90px">来源</th>
            <th style="width:100px">日期</th>
            <th>主题</th>
            <th style="width:180px">发件人</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in visibleMails" :key="m.message_id"
              :class="{ 'row-selected': selectedIds.has(m.message_id) }"
              @click="toggleSelect(m.message_id)">
            <td class="center" @click.stop>
              <input type="checkbox" :checked="selectedIds.has(m.message_id)" @change="toggleSelect(m.message_id)" />
            </td>
            <td>
              <span class="badge" :class="statusMeta(m.status).cls">{{ statusMeta(m.status).label }}</span>
            </td>
            <td>
              <span class="source-badge" :class="sourceLabel(m.source_type).cls">
                {{ sourceLabel(m.source_type).label }}
              </span>
            </td>
            <td class="dim nowrap">{{ (m.date || '').slice(0, 10) }}</td>
            <td class="subject-cell">{{ m.subject || '(无主题)' }}</td>
            <td class="dim">{{ m.from_addr || m.from_name || '' }}</td>
          </tr>
        </tbody>
      </table>

      <div v-else class="empty">
        {{ search ? '没有匹配的邮件'
           : filter === 'todo' ? '暂无未完成邮件。请拉取邮箱或导入文件。'
           : filter === 'done' ? '暂无已完成邮件。'
           : '暂无邮件。请拉取邮箱或导入文件。' }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.workbench { max-width: 100%; }

.wb-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 0.6rem; margin-bottom: 0.75rem;
}
.wb-head h2 { margin: 0; }
.refresh-btn { flex-shrink: 0; white-space: nowrap; }

/* ── KPI ── */
.kpi-row {
  display: flex; gap: 0; margin-bottom: 1rem;
  border: 1px solid var(--border); border-radius: var(--r);
  overflow: hidden; background: var(--surface);
}
.kpi-item {
  flex: 1; text-align: center; padding: 0.65rem 0.5rem;
  border-right: 1px solid var(--border-light);
}
.kpi-item:last-child { border-right: none; }
.kpi-num { display: block; font-size: 1.25rem; font-weight: 700; line-height: 1.2; }
.kpi-text { display: block; font-size: 0.68rem; color: var(--t4); margin-top: 0.1rem; }

/* ── 知识图谱状态带 ── */
.status-strip {
  display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;
  padding: 0.5rem 0.85rem; margin-bottom: 1rem;
  border: 1px solid var(--border); border-radius: var(--r);
  background: var(--surface); font-size: 0.74rem;
}
.ss-group { display: flex; align-items: center; gap: 0.4rem; }
.ss-label { font-size: 0.66rem; font-weight: 600; color: var(--t4); text-transform: uppercase; letter-spacing: 0.3px; }
.ss-metric { color: var(--t2); }
.ss-metric b { color: var(--t1); font-weight: 700; font-size: 0.85rem; }
.ss-sep { color: var(--t5); }
.ss-divider { width: 1px; height: 18px; background: var(--border-light); }
.ss-spacer { flex: 1 1 auto; }

.ss-pill {
  display: inline-flex; align-items: center; gap: 0.3rem;
  padding: 1px 8px; border-radius: 9999px; font-size: 0.68rem; font-weight: 600;
}
.ss-pill .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.ss-pill--idle { background: var(--surface-2); color: #A8A29E; }
.ss-pill--busy { background: #FBF0DD; color: #B4791F; }
.ss-pill--busy .dot { animation: ss-pulse 1s ease-in-out infinite; }
@keyframes ss-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
.ss-msg {
  max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--t4); font-size: 0.7rem;
}

.ss-svc {
  display: inline-flex; align-items: center; gap: 0.3rem;
  color: var(--t3); font-size: 0.7rem;
}
.ss-svc .dot { width: 6px; height: 6px; border-radius: 50%; background: #1F6F5C; }
.ss-svc--down { color: var(--t5); }
.ss-svc--down .dot { background: #9A3B2E; }

@media (max-width: 800px) {
  .ss-divider { display: none; }
  .ss-spacer { flex-basis: 100%; height: 0; }
}

/* ── Toolbar (account) ── */
.toolbar {
  border: 1px solid var(--border); border-radius: var(--r);
  background: var(--surface); margin-bottom: 1rem;
}
.toolbar-account {
  padding: 0.65rem 0.85rem;
}

/* ── Action area ── */
.action-area {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
@media (max-width: 800px) {
  .action-area { grid-template-columns: 1fr; }
}

.action-card {
  border: 1px solid var(--border); border-radius: var(--r);
  background: var(--surface); padding: 0.75rem 0.85rem;
}
.action-card-title {
  font-size: 0.78rem; font-weight: 650; color: var(--t2);
  margin-bottom: 0.55rem;
}
.action-card-body {
  display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;
}
.picker-info {
  font-size: 0.72rem; color: var(--t4); margin-top: 0.4rem;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.hint { font-size: 0.72rem; margin-top: 0.3rem; }
.hint--err { color: #9A3B2E; }

/* ── List panel ── */
.list-panel {
  border: 1px solid var(--border); border-radius: var(--r);
  background: var(--surface); overflow: hidden;
}
.list-panel-header {
  display: flex; align-items: center; gap: 0.75rem;
  padding: 0 0.85rem;
  border-bottom: 1px solid var(--border);
}

/* ── Tabs ── */
.tabs { display: flex; gap: 0; }
.tab {
  padding: 0.55rem 0.9rem; font-size: 0.82rem; font-weight: 500;
  background: none; border: none; border-bottom: 2px solid transparent;
  color: var(--t4); cursor: pointer; transition: all 0.12s;
  display: flex; align-items: center; gap: 0.4rem; font-family: inherit;
}
.tab:hover { color: var(--t2); }
.tab.active { color: var(--t1); border-bottom-color: var(--p); }
.tab-count {
  font-size: 0.68rem; font-weight: 600; padding: 0.05rem 0.35rem;
  border-radius: 9999px; background: var(--surface-2); color: var(--t4);
}
.tab.active .tab-count { background: var(--p-light); color: var(--p); }

.search-input {
  width: 180px; padding: 0.3rem 0.5rem; margin-left: auto;
  border: 1px solid var(--border); border-radius: var(--r-sm);
  font-size: 0.74rem; font-family: inherit;
}
.search-input:focus { outline: none; border-color: var(--p); box-shadow: 0 0 0 2px var(--p-ring); }
.search-hint { font-size: 0.7rem; color: var(--t4); white-space: nowrap; }

/* ── Action bar ── */
.action-bar {
  display: flex; align-items: center; gap: 0.5rem;
  padding: 0.5rem 0.85rem;
  background: var(--surface-2); border-bottom: 1px solid var(--border-light);
}
.sel-info { font-size: 0.74rem; color: var(--t3); font-weight: 500; }
.sel-info--hint { color: var(--t5); font-weight: 400; }
.spacer { flex: 1; }
.btn-count {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; padding: 0 4px;
  border-radius: 999px; background: rgba(255,255,255,0.25);
  font-size: 0.68rem; font-weight: 700;
}

/* ── Table ── */
.data-table {
  width: 100%; border-collapse: collapse;
  background: var(--surface); font-size: 0.8rem;
}
.data-table thead { border-bottom: 1px solid var(--border); }
.data-table th {
  padding: 0.45rem 0.75rem; font-size: 0.68rem; font-weight: 600;
  color: var(--t4); text-transform: uppercase; letter-spacing: 0.3px;
  text-align: left; white-space: nowrap; background: var(--surface-2);
}
.data-table td {
  padding: 0.4rem 0.75rem; border-bottom: 1px solid var(--border-light);
  color: var(--t2); vertical-align: top;
}
.data-table td.nowrap { white-space: nowrap; }
.data-table td.subject-cell { color: var(--t1); line-height: 1.45; word-break: break-word; }

.data-table tbody tr { transition: background 0.08s; cursor: pointer; }
.data-table tbody tr:hover { background: var(--surface-2); }
.data-table tbody tr.row-selected { background: var(--p-light); }
.data-table tbody tr:last-child td { border-bottom: none; }
.data-table input[type="checkbox"] { width: 14px; height: 14px; accent-color: var(--p); cursor: pointer; }
.dim { color: var(--t4); font-size: 0.73rem; }
.center { text-align: center; }

/* ── Badges ── */
.badge {
  display: inline-block; padding: 1px 8px; border-radius: 9999px;
  font-size: 0.64rem; font-weight: 600; white-space: nowrap;
}
.st-pending { background: #E7EEF7; color: #3B6EA5; }
.st-processing { background: #FBF0DD; color: #B4791F; }
.st-done { background: #E1F0EA; color: #1F6F5C; }
.st-failed { background: #F6E3DF; color: #9A3B2E; }
.st-skipped { background: var(--surface-2); color: #A8A29E; }

.source-badge {
  display: inline-block; padding: 1px 8px; border-radius: 9999px;
  font-size: 0.64rem; font-weight: 600; white-space: nowrap;
}
.source-imap  { background: #E7EEF7; color: #3B6EA5; }
.source-pst   { background: #FBF0DD; color: #B4791F; }
.source-eml   { background: #E1F0EA; color: #1F6F5C; }
.source-msg   { background: #E1F0EA; color: #1F6F5C; }
.source-file  { background: #F6E3DF; color: #9A3B2E; }

/* ── Buttons ── */
.tb-btn {
  display: inline-flex; align-items: center; gap: 0.3rem;
  padding: 0.35rem 0.75rem; border-radius: var(--r-sm);
  font-size: 0.78rem; font-weight: 500; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface);
  color: var(--t2); transition: all 0.12s; font-family: inherit;
}
.tb-btn:hover:not(:disabled) { background: var(--surface-2); }
.tb-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.tb-btn--primary { background: var(--p); border-color: var(--p); color: #fff; }
.tb-btn--primary:hover:not(:disabled) { background: var(--p-hover); }

.tb-num { width: 56px; padding: 0.35rem 0.4rem; border: 1px solid var(--border); border-radius: var(--r-sm); font-size: 0.78rem; font-family: inherit; }
.tb-select { padding: 0.35rem 0.4rem; border: 1px solid var(--border); border-radius: var(--r-sm); font-size: 0.78rem; font-family: inherit; background: var(--surface); }

/* ── Empty ── */
.empty {
  padding: 2.5rem; text-align: center; color: var(--t4);
  font-size: 0.82rem;
}

/* ── Log ── */
.log-box {
  background: #1a1a1a; color: #c0c0c0; font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 0.68rem; padding: 0.5rem 0.7rem; border-radius: var(--r-sm);
  max-height: 180px; overflow-y: auto; margin-top: 0.5rem; line-height: 1.5;
}
</style>
