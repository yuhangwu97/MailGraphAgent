<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { mailsApi, graphApi, type MailStats, type MailItem, type GraphStatus } from '@/api'
import { useStatusStore } from '@/stores/status'
import TopBar from '@/components/workbench/TopBar.vue'
import MailList from '@/components/workbench/MailList.vue'
import ActivityPanel from '@/components/workbench/ActivityPanel.vue'
import ImportDrawer from '@/components/workbench/ImportDrawer.vue'
import JobCenter from '@/components/workbench/JobCenter.vue'
import type { ActivityEvent } from '@/components/workbench/ActivityPanel.vue'

const statusStore = useStatusStore()

// ── Layout state ──
const drawerMode = ref<'fetch' | 'import' | null>(null)
const searchText = ref('')
const refreshing = ref(false)
const sidePanelVisible = ref(true)

// ── Service health ──
const services = computed(() => statusStore.services)

// ── KPI ──
const kpi = ref<MailStats>({
  total: 0, done: 0, pending: 0, failed: 0, skipped: 0, ingested: 0, indexed: 0,
})

async function refreshStats() {
  try { kpi.value = await mailsApi.stats() } catch (e) { console.error(e) }
}

// ── Graph status ──
const gstatus = ref<GraphStatus>({
  graph: { entities: 0, relationships: 0 },
  docs: { pending: 0, processing: 0, processed: 0, failed: 0, duplicate: 0 },
  pipeline: { busy: false, latest_message: '', job_name: '' },
})

async function refreshGraphStatus() {
  try { gstatus.value = await graphApi.status() } catch (e) { console.error(e) }
}

// ── Mail list ──
type MailFilter = 'all' | 'todo' | 'done'
const filter = ref<MailFilter>('all')
const mails = ref<MailItem[]>([])
const total = ref(0)
const selectedIds = ref<Set<string>>(new Set())
const processing = ref(false)
const processLogs = ref<string[]>([])
const loading = ref(false)

const PAGE_SIZE = 200

async function refreshList() {
  try {
    loading.value = true
    const r = await mailsApi.list({ filter: filter.value, page: 1, page_size: PAGE_SIZE })
    mails.value = r?.items || []
    total.value = r?.total || 0
  } catch (e) { console.error(e) } finally { loading.value = false }
}

// Apply client-side search filter
const visibleMails = computed(() => {
  const q = searchText.value.trim().toLowerCase()
  if (!q) return mails.value
  return mails.value.filter(m =>
    (m.subject || '').toLowerCase().includes(q) ||
    (m.from_addr || '').toLowerCase().includes(q) ||
    (m.from_name || '').toLowerCase().includes(q)
  )
})

// ── Selection ──
function toggleSelect(id: string) {
  const s = new Set(selectedIds.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selectedIds.value = s
}

function toggleAll() {
  const allSel = visibleMails.value.length > 0 && visibleMails.value.every(m => selectedIds.value.has(m.message_id))
  if (allSel) {
    const visibleIds = new Set(visibleMails.value.map(m => m.message_id))
    selectedIds.value = new Set([...selectedIds.value].filter(id => !visibleIds.has(id)))
  } else {
    const s = new Set(selectedIds.value)
    visibleMails.value.forEach(m => s.add(m.message_id))
    selectedIds.value = s
  }
}

// ── Process selected ──
function isFileMail(m: MailItem): boolean {
  return !!m.source_type && m.source_type !== 'imap'
}

function isImapMail(m: MailItem): boolean {
  return !m.source_type || m.source_type === 'imap'
}

function isTerminal(m: MailItem): boolean {
  return ['done', 'skipped', 'failed'].includes(m.status)
}

async function handleProcess() {
  const ids = [...selectedIds.value]
  if (!ids.length) return

  const byId = (id: string) => mails.value.find(x => x.message_id === id)
  // 只有 failed / skipped 才重处理，done 不再动
  const reprocessIds = ids.filter(id => {
    const m = byId(id); return !!m && (m.status === 'failed' || m.status === 'skipped')
  })
  const fileIds = ids.filter(id => { const m = byId(id); return !!m && !isTerminal(m) && isFileMail(m) })
  // 未完成的 IMAP 邮件分两路：
  //   - pending: 正文未存（噪音过滤掉的），走 reprocess 重拉 IMAP → 入队
  //   - indexed/processing: 正文已在 Redis，补回 ingest 队列即可
  const pendingImapIds = ids.filter(id => {
    const m = byId(id); return !!m && m.status === 'pending' && isImapMail(m)
  })
  const otherImapIds = ids.filter(id => {
    const m = byId(id); return !!m && m.status !== 'pending' && !isTerminal(m) && isImapMail(m)
  })

  processing.value = true
  processLogs.value = []
  const promises: Promise<void>[] = []

  if (pendingImapIds.length) {
    promises.push(new Promise<void>((resolve) => {
      mailsApi.reprocess(pendingImapIds, {
        onProgress(d: any) { processLogs.value.push('[重拉] ' + (d.msg || JSON.stringify(d))) },
        onComplete() { resolve() },
        onError(m: string) { processLogs.value.push('[重拉] 错误: ' + m); resolve() },
      })
    }))
  }

  if (otherImapIds.length) {
    promises.push(new Promise<void>((resolve) => {
      mailsApi.ingest(null, {
        onProgress(d: any) { processLogs.value.push('[IMAP] ' + (d.msg || JSON.stringify(d))) },
        onComplete() { resolve() },
        onError(m: string) { processLogs.value.push('[IMAP] 错误: ' + m); resolve() },
      }, otherImapIds)
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

// ── Drawer ──
function openDrawer(mode: 'fetch' | 'import') {
  drawerMode.value = mode
}

function closeDrawer() {
  drawerMode.value = null
}

// ── Data refresh ──
async function refreshAll() {
  await Promise.all([refreshStats(), refreshList(), refreshGraphStatus(), statusStore.refresh()])
}

async function handleRefresh() {
  refreshing.value = true
  try { await refreshAll() } finally { refreshing.value = false }
}

// 操作完成后刷新：自动切回"全部"让用户看到结果
async function handleDrawerRefresh() {
  filter.value = 'all'
  selectedIds.value = new Set()
  await refreshAll()
}

// ── Activity stream (SSE) ──
const activities = ref<ActivityEvent[]>([])
const MAX_ACTIVITIES = 50
let activityIdCounter = 0
let eventSource: EventSource | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
const useSSE = ref(false)

function addActivity(ev: Omit<ActivityEvent, 'id'>) {
  activities.value.unshift({ ...ev, id: `ev-${++activityIdCounter}` })
  if (activities.value.length > MAX_ACTIVITIES) {
    activities.value = activities.value.slice(0, MAX_ACTIVITIES)
  }
}

function connectSSE() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }

  try {
    const es = new EventSource('/api/events/stream')
    eventSource = es

    es.addEventListener('mail_processed', (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        addActivity({
          type: d.status === 'done' ? 'processed' : d.status === 'failed' ? 'failed' : 'skipped',
          message_id: d.message_id || '',
          subject: d.subject || '',
          detail: d.detail || '',
          timestamp: Date.now(),
        })
        // Auto-refresh list and stats when a mail is processed
        refreshList()
        refreshStats()
        refreshGraphStatus()
      } catch {}
    })

    es.addEventListener('pipeline_tick', (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        gstatus.value.pipeline = { ...gstatus.value.pipeline, ...d }
      } catch {}
    })

    es.addEventListener('docs_changed', (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        gstatus.value.docs = { ...gstatus.value.docs, ...d }
      } catch {}
    })

    es.addEventListener('graph_changed', (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        gstatus.value.graph = { ...gstatus.value.graph, ...d }
      } catch {}
    })

    es.addEventListener('processing_started', (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        addActivity({
          type: 'processing',
          message_id: d.message_id || '',
          subject: d.subject || '',
          detail: d.detail || '',
          timestamp: Date.now(),
        })
      } catch {}
    })

    es.onopen = () => {
      useSSE.value = true
      // Stop polling when SSE is active
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
    }

    es.onerror = () => {
      es.close()
      eventSource = null
      useSSE.value = false
      // Fallback to polling
      if (!pollTimer) {
        pollTimer = setInterval(() => {
          refreshGraphStatus()
          refreshStats()
          statusStore.refresh()
        }, 5000)
      }
      // Retry after 10 seconds
      reconnectTimer = setTimeout(connectSSE, 10000)
    }
  } catch {
    useSSE.value = false
  }
}

function disconnectSSE() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// ── Busy state ──
const isBusy = computed(() =>
  processing.value || gstatus.value.pipeline.busy
)

// ── Lifecycle ──
onMounted(() => {
  refreshAll()
  connectSSE()
  // Fallback polling starts immediately, SSE will stop it on connect
  pollTimer = setInterval(() => {
    refreshGraphStatus()
    if (isBusy.value) { refreshStats(); statusStore.refresh() }
  }, 5000)
})

onUnmounted(() => {
  disconnectSSE()
})
</script>

<template>
  <div class="wb-layout">
    <!-- Top Bar -->
    <TopBar
      v-model:search-text="searchText"
      @open-drawer="openDrawer"
      @search="() => {}"
    />

    <!-- Body: dual column -->
    <div class="wb-body">
      <!-- Main: mail list -->
      <div class="wb-main">
        <!-- Process logs -->
        <div v-if="processLogs.length" class="wb-process-logs">
          <div class="wb-pl-head">
            <span>处理日志</span>
            <button class="wb-pl-dismiss" @click="processLogs = []">✕</button>
          </div>
          <div v-for="(l, i) in processLogs" :key="'pl'+i" class="wb-pl-line">{{ l }}</div>
        </div>

        <MailList
          :mails="visibleMails"
          :total="total"
          :filter="filter"
          :selected-ids="selectedIds"
          :kpi="kpi"
          :processing="processing"
          @update:filter="(f: MailFilter) => { filter = f; selectedIds = new Set(); refreshList() }"
          @toggle-select="toggleSelect"
          @toggle-all="toggleAll"
          @process="handleProcess"
        />

        <!-- Bottom bar -->
        <div class="wb-bottom">
          <span class="wb-bottom-info">
            共 {{ total.toLocaleString() }} 封
            <template v-if="!useSSE"> · 轮询模式</template>
            <template v-else> · 实时推送</template>
          </span>
          <button class="btn btn-secondary btn-sm wb-refresh-btn" :disabled="refreshing" @click="handleRefresh">
            {{ refreshing ? '刷新中…' : '手动刷新' }}
          </button>
          <button
            class="wb-toggle-panel"
            @click="sidePanelVisible = !sidePanelVisible"
            :title="sidePanelVisible ? '隐藏面板' : '显示面板'"
          >
            {{ sidePanelVisible ? '▸' : '◂' }}
          </button>
        </div>
      </div>

      <!-- Side: activity panel + job center -->
      <Transition name="panel-slide">
        <div v-if="sidePanelVisible" class="wb-side">
          <JobCenter />
          <ActivityPanel
            :activities="activities"
            :kpi="kpi"
            :graph-status="gstatus"
            :services="services"
          />
        </div>
      </Transition>
    </div>

    <!-- Import drawer -->
    <ImportDrawer
      :open="drawerMode !== null"
      :mode="drawerMode"
      @close="closeDrawer"
      @refresh="handleDrawerRefresh"
    />
  </div>
</template>

<style scoped>
.wb-layout {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 3.75rem);
  min-height: 0;
}

/* ── Body: dual column ── */
.wb-body {
  display: flex;
  gap: 0.75rem;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.wb-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

/* ── Process logs ── */
.wb-process-logs {
  background: #1a1a1a;
  color: #c0c0c0;
  font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  padding: 0.5rem 0.7rem;
  border-radius: var(--r-sm);
  max-height: 140px;
  overflow-y: auto;
  line-height: 1.5;
  flex-shrink: 0;
}

.wb-pl-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.25rem;
  font-weight: 600;
  color: #e0e0e0;
}

.wb-pl-dismiss {
  background: none;
  border: none;
  color: #888;
  cursor: pointer;
  font-size: 0.75rem;
}

.wb-pl-line {
  word-break: break-all;
}

/* ── Bottom bar ── */
.wb-bottom {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.4rem 0;
  flex-shrink: 0;
}

.wb-bottom-info {
  font-size: 0.68rem;
  color: var(--t4);
}

.wb-refresh-btn {
  margin-left: auto;
}

.wb-side {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  width: 320px;
  flex-shrink: 0;
  overflow-y: auto;
}

.wb-toggle-panel {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--t3);
  cursor: pointer;
  font-size: 0.7rem;
  transition: all var(--dur-fast) var(--ease);
}

.wb-toggle-panel:hover {
  background: var(--surface-2);
  color: var(--t1);
}

/* ── Panel slide transition ── */
.panel-slide-enter-active,
.panel-slide-leave-active {
  transition: all var(--dur) var(--ease);
}

.panel-slide-enter-from,
.panel-slide-leave-to {
  opacity: 0;
  transform: translateX(20px);
}

.panel-slide-enter-to,
.panel-slide-leave-from {
  opacity: 1;
  transform: translateX(0);
}
</style>
