<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { mailsApi, accountsApi, type MailItem } from '@/api'
import { useAccountStore } from '@/stores/account'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'refresh'): void
}>()

// ── Tabs ──
type Tab = 'imap' | 'file'
const tab = ref<Tab>('imap')

const accountStore = useAccountStore()

// ═══════════════════════════════════════════════
// IMAP scan
// ═══════════════════════════════════════════════
const folder = ref('INBOX')
const scanLimit = ref(2)
const scanning = ref(false)
const scanLogs = ref<string[]>([])

// ── IMAP folder preview count ──
const previewCount = ref<number | null>(null)
const previewing = ref(false)

async function doPreview() {
  const acct = accountStore.active
  if (!acct) return
  previewing.value = true
  try {
    const r = await accountsApi.preview(acct.id, folder.value)
    previewCount.value = r.total
  } catch (e: any) {
    previewCount.value = null
    scanLogs.value.push('❌ 查询失败: ' + (e.message || String(e)))
  } finally {
    previewing.value = false
  }
}

async function doImapScan() {
  scanning.value = true
  scanLogs.value = []
  mailsApi.scan('imap', {
    folder: folder.value,
    limit: scanLimit.value,
  }, {
    onProgress(d: any) { scanLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete(d: any) {
      scanLogs.value.push('✅ 扫描完成: ' + JSON.stringify(d))
      scanning.value = false
      page.value = 1
      statusFilter.value = 'pending'
      refreshIndexed()
      emit('refresh')
    },
    onError(m: string) {
      scanLogs.value.push('❌ 错误: ' + m)
      scanning.value = false
    },
  })
}

// ═══════════════════════════════════════════════
// File scan — browser native file picker → upload → scan
// ═══════════════════════════════════════════════
const selectedFiles = ref<File[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

function triggerFileInput() {
  fileInput.value?.click()
}

function handleFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length) {
    selectedFiles.value = Array.from(input.files)
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

async function doFileScan() {
  if (!selectedFiles.value.length) return
  scanning.value = true
  scanLogs.value = []

  try {
    const { paths } = await mailsApi.uploadFiles(selectedFiles.value)
    scanLogs.value.push(`📤 已上传 ${paths.length} 个文件`)

    mailsApi.scan('file', { paths }, {
      onProgress(d: any) { scanLogs.value.push(d.msg || JSON.stringify(d)) },
      onComplete(d: any) {
        scanLogs.value.push('✅ 扫描完成: ' + JSON.stringify(d))
        scanning.value = false
        selectedFiles.value = []
        page.value = 1
        statusFilter.value = 'pending'
        refreshIndexed()
        emit('refresh')
      },
      onError(m: string) {
        scanLogs.value.push('❌ 错误: ' + m)
        scanning.value = false
      },
    })
  } catch (e: any) {
    scanLogs.value.push('❌ 上传失败: ' + (e.message || String(e)))
    scanning.value = false
  }
}

// ═══════════════════════════════════════════════
// Shared: indexed list
// ═══════════════════════════════════════════════
const indexedMails = ref<MailItem[]>([])
const indexedTotal = ref(0)
const page = ref(1)
const pageSize = ref(50)
const totalPages = ref(1)
const search = ref('')
const selectedIds = ref<Set<string>>(new Set())
const allSelected = ref(false)
const statusFilter = ref<'pending' | 'done' | 'all'>('pending')

const statusTabs = [
  { key: 'pending' as const, label: '未处理' },
  { key: 'done' as const, label: '已处理' },
  { key: 'all' as const, label: '全部' },
]

function setStatusFilter(k: 'pending' | 'done' | 'all') {
  if (statusFilter.value === k) return
  statusFilter.value = k
  page.value = 1
  selectedIds.value = new Set()
  allSelected.value = false
  refreshIndexed()
}

const STATUS_META: Record<string, { label: string; cls: string }> = {
  indexed: { label: '未处理', cls: 'st-pending' },
  processing: { label: '处理中', cls: 'st-processing' },
  done: { label: '已处理', cls: 'st-done' },
  failed: { label: '失败', cls: 'st-failed' },
  skipped: { label: '已入队', cls: 'st-skipped' },
}
function statusMeta(s: string) {
  return STATUS_META[s] || { label: s || '未知', cls: 'st-pending' }
}

const filtered = ref<MailItem[]>([])

function updateFiltered() {
  const q = search.value.trim().toLowerCase()
  filtered.value = q
    ? indexedMails.value.filter(m => (m.subject || '').toLowerCase().includes(q))
    : indexedMails.value
}

function toggleAll() {
  if (allSelected.value) {
    selectedIds.value = new Set()
    allSelected.value = false
  } else {
    selectedIds.value = new Set(filtered.value.map(m => m.message_id))
    allSelected.value = true
  }
}
function toggleSelect(id: string) {
  const s = new Set(selectedIds.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selectedIds.value = s
  allSelected.value = filtered.value.length > 0 && filtered.value.every(m => selectedIds.value.has(m.message_id))
}

async function refreshIndexed() {
  try {
    const r = await mailsApi.indexed({ page: page.value, page_size: pageSize.value, status: statusFilter.value })
    indexedMails.value = r.items
    indexedTotal.value = r.total
    totalPages.value = Math.max(1, Math.ceil(r.total / pageSize.value))
    updateFiltered()
  } catch (e) { console.error(e) }
}
function goPage(p: number) {
  if (p < 1 || p > totalPages.value) return
  page.value = p; refreshIndexed()
}

// ═══════════════════════════════════════════════
// Shared: parse selected
// ═══════════════════════════════════════════════
const parsing = ref(false)
const parseLogs = ref<string[]>([])

function doParse() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  parsing.value = true; parseLogs.value = []
  mailsApi.parseSelected(ids, {
    onProgress(d: any) { parseLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete(d: any) {
      parseLogs.value.push('✅ 解析完成: ' + JSON.stringify(d))
      parsing.value = false; selectedIds.value = new Set(); allSelected.value = false
      refreshIndexed(); emit('refresh')
    },
    onError(m: string) { parseLogs.value.push('❌ 错误: ' + m); parsing.value = false },
  })
}

// Source badge
function sourceLabel(t: string | undefined) {
  if (!t) return ''
  const m: Record<string, string> = { imap: '📨 IMAP', pst: '📦 PST', ost: '📦 OST', eml: '📧 EML', msg: '📧 MSG' }
  return m[t] || t.toUpperCase()
}

// Reset state when drawer opens
watch(() => props.open, (now) => {
  if (now) {
    scanLogs.value = []
    parseLogs.value = []
    selectedFiles.value = []

    page.value = 1
    statusFilter.value = 'pending'
    refreshIndexed()
    doPreview()
  }
})

onMounted(() => {
  if (props.open) refreshIndexed()
})
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="open" class="drawer-overlay" @click.self="emit('close')">
        <div class="drawer-panel">
          <!-- Header -->
          <div class="drawer-head">
            <h3>📥 导入邮件</h3>
            <button class="drawer-close" @click="emit('close')">✕</button>
          </div>

          <div class="drawer-body">
            <!-- Tab bar -->
            <div class="tab-bar">
              <button :class="['tab', { active: tab === 'imap' }]" @click="tab = 'imap'">📨 IMAP 邮箱</button>
              <button :class="['tab', { active: tab === 'file' }]" @click="tab = 'file'">📂 本地文件</button>
            </div>

            <!-- ─── IMAP scan ─── -->
            <div v-if="tab === 'imap'" class="scan-section">
              <div class="scan-row">
                <div class="scan-field">
                  <label class="scan-label">文件夹</label>
                  <input v-model="folder" class="scan-input" placeholder="INBOX" />
                </div>
                <div class="scan-field scan-field-sm">
                  <label class="scan-label">数量</label>
                  <input v-model.number="scanLimit" type="number" min="1" max="500" class="scan-input" />
                </div>
              </div>
              <div class="preview-row">
                <button
                  class="btn btn-sm preview-btn"
                  :disabled="previewing"
                  @click="doPreview"
                >
                  {{ previewing ? '查询中…' : '🔄 刷新邮件数' }}
                </button>
                <span v-if="previewCount !== null" class="preview-count">
                  📬 <strong>{{ previewCount }}</strong> 封邮件
                </span>
              </div>
              <button
                class="btn btn-primary scan-btn"
                :disabled="scanning"
                @click="doImapScan"
              >
                {{ scanning ? '扫描中…' : `🔍 扫描表头（最近 ${scanLimit} 封）` }}
              </button>
              <p class="scan-hint">仅拉取主题/发件人/日期，不下载正文。扫完勾选后再解析建图。</p>
            </div>

            <!-- ─── File scan ─── -->
            <div v-if="tab === 'file'" class="scan-section">
              <p class="scan-hint">支持 .eml / .msg / .pst / .ost 格式</p>
              <input
                ref="fileInput"
                type="file"
                multiple
                accept=".eml,.msg,.pst,.ost"
                style="display:none"
                @change="handleFileChange"
              />
              <button class="btn btn-secondary pick-btn" @click="triggerFileInput">
                📄 选择邮件文件…
              </button>

              <div v-if="selectedFiles.length" class="file-list">
                <div v-for="(f, i) in selectedFiles" :key="i" class="file-item">
                  {{ f.name }} <span class="file-size">{{ formatSize(f.size) }}</span>
                </div>
              </div>

              <button
                v-if="selectedFiles.length"
                class="btn btn-primary scan-btn"
                :disabled="scanning"
                @click="doFileScan"
              >
                {{ scanning ? '扫描中…' : `🔍 扫描 ${selectedFiles.length} 个文件` }}
              </button>
            </div>

            <!-- ─── Scan logs ─── -->
            <div v-if="scanLogs.length" class="logs">
              <div v-for="(l, i) in scanLogs" :key="'s'+i" class="log-line">{{ l }}</div>
            </div>

            <!-- ─── Indexed list ─── -->
            <div class="list-section">
              <div class="list-head">
                <span class="list-title">📋 已索引邮件（{{ indexedTotal }}）</span>
                <input v-model="search" class="search-input" placeholder="搜索主题…" @input="updateFiltered" />
              </div>

              <!-- Status tabs + parse button -->
              <div class="action-bar">
                <div class="filter-tabs">
                  <button
                    v-for="t in statusTabs" :key="t.key"
                    :class="['filter-tab', { active: statusFilter === t.key }]"
                    @click="setStatusFilter(t.key)"
                  >{{ t.label }}</button>
                </div>
                <span class="sel-info">已选 {{ selectedIds.size }} 封</span>
                <div class="spacer" />
                <button
                  class="btn btn-primary"
                  :disabled="parsing || selectedIds.size === 0"
                  @click="doParse"
                >
                  {{ parsing ? '解析中…' : `⚡ 解析所选并向量化 (${selectedIds.size})` }}
                </button>
              </div>

              <!-- Parse logs -->
              <div v-if="parseLogs.length" class="logs">
                <div v-for="(l, i) in parseLogs" :key="'p'+i" class="log-line">{{ l }}</div>
              </div>

              <!-- Table -->
              <table v-if="filtered.length" class="mail-table">
                <thead>
                  <tr>
                    <th style="width:32px"><input type="checkbox" :checked="allSelected" @change="toggleAll" /></th>
                    <th style="width:70px">状态</th>
                    <th style="width:60px">来源</th>
                    <th style="width:90px">日期</th>
                    <th>主题</th>
                    <th style="width:140px">发件人</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="m in filtered" :key="m.message_id"
                      :class="{ 'row-selected': selectedIds.has(m.message_id), 'row-done': m.status === 'done' }"
                      @click="toggleSelect(m.message_id)">
                    <td class="center" @click.stop>
                      <input type="checkbox" :checked="selectedIds.has(m.message_id)" @change="toggleSelect(m.message_id)" />
                    </td>
                    <td><span class="badge" :class="statusMeta(m.status).cls">{{ statusMeta(m.status).label }}</span></td>
                    <td class="dim">{{ sourceLabel(m.source_type) }}</td>
                    <td class="dim nowrap">{{ (m.date || '').slice(0, 10) }}</td>
                    <td class="subject-cell">{{ m.subject || '(无主题)' }}</td>
                    <td class="dim">{{ m.from_addr || m.from_name || '' }}</td>
                  </tr>
                </tbody>
              </table>
              <div v-else class="empty">
                {{ statusFilter === 'done' ? '还没有已处理的邮件' : '暂无索引邮件，在上方扫描表头或导入文件' }}
              </div>

              <!-- Pagination -->
              <div class="table-footer" v-if="totalPages > 1">
                <div class="pager">
                  <button class="btn btn-sm" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
                  <span class="pager-info">{{ page }} / {{ totalPages }}</span>
                  <button class="btn btn-sm" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* ── Overlay ── */
.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 17, 15, 0.3);
  z-index: 200;
  display: flex;
  justify-content: flex-end;
}

/* ── Panel ── */
.drawer-panel {
  width: 520px;
  max-width: 92vw;
  height: 100%;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  box-shadow: var(--sh-lg);
}

/* ── Transitions ── */
.drawer-enter-active,
.drawer-leave-active {
  transition: opacity var(--dur) var(--ease);
}
.drawer-enter-active .drawer-panel,
.drawer-leave-active .drawer-panel {
  transition: transform var(--dur) var(--ease-out);
}
.drawer-enter-from,
.drawer-leave-to { opacity: 0; }
.drawer-enter-from .drawer-panel { transform: translateX(100%); }
.drawer-leave-to .drawer-panel { transform: translateX(100%); }

/* ── Head ── */
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.drawer-head h3 { margin: 0; font-size: 1rem; font-weight: 650; color: var(--t1); }
.drawer-close {
  width: 28px; height: 28px;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--r-sm);
  border: 1px solid var(--border);
  background: var(--surface); color: var(--t3);
  cursor: pointer; font-size: 0.85rem;
  transition: all var(--dur-fast) var(--ease);
}
.drawer-close:hover { background: var(--surface-2); color: var(--t1); }

/* ── Body ── */
.drawer-body {
  flex: 1; overflow-y: auto;
  padding: 0.85rem 1.25rem;
  display: flex; flex-direction: column; gap: 0.85rem;
}

/* ── Tab bar ── */
.tab-bar { display: flex; gap: 0; border-bottom: 2px solid var(--border); flex-shrink: 0; }
.tab {
  flex: 1; padding: 0.5rem 0; border: none; background: transparent;
  font-size: 0.82rem; font-weight: 550; color: var(--t4);
  cursor: pointer; font-family: inherit;
  border-bottom: 2px solid transparent; margin-bottom: -2px;
  transition: all 0.12s;
}
.tab:hover { color: var(--t2); }
.tab.active { color: var(--p); border-bottom-color: var(--p); }

/* ── Scan section ── */
.scan-section { display: flex; flex-direction: column; gap: 0.5rem; flex-shrink: 0; }
.scan-row { display: flex; gap: 0.6rem; }
.scan-field { flex: 1; display: flex; flex-direction: column; gap: 0.2rem; }
.scan-field-sm { max-width: 90px; flex: none; }
.scan-label { font-size: 0.68rem; font-weight: 600; color: var(--t4); text-transform: uppercase; letter-spacing: 0.2px; }
.scan-input {
  width: 100%; padding: 0.4rem 0.55rem; font-size: 0.8rem;
  border: 1px solid var(--border); border-radius: var(--r-sm);
  font-family: inherit; background: var(--surface);
}
.scan-btn { margin-top: 0.2rem; }
.scan-hint { font-size: 0.68rem; color: var(--t4); margin: 0; line-height: 1.4; }

.preview-row {
  display: flex; align-items: center; gap: 0.5rem;
}
.preview-btn {
  flex-shrink: 0;
}
.preview-count {
  font-size: 0.78rem; color: var(--t2);
}
.preview-count strong {
  color: var(--p); font-size: 0.85rem;
}

.pick-btn { width: 100%; }
.err-msg { font-size: 0.7rem; color: var(--red); padding: 0.25rem 0.4rem; background: var(--red-bg); border-radius: var(--r-sm); }

.file-list {
  max-height: 100px; overflow-y: auto;
  border: 1px solid var(--border); border-radius: var(--r-sm);
  padding: 0.3rem; background: var(--surface-2);
}
.file-item {
  font-size: 0.7rem; color: var(--t2); padding: 0.15rem 0.3rem;
  border-radius: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.file-size { font-size: 0.62rem; color: var(--t4); margin-left: 0.3rem; }

/* ── List section ── */
.list-section { flex: 1; display: flex; flex-direction: column; gap: 0.5rem; min-height: 0; }
.list-head {
  display: flex; align-items: center; gap: 0.5rem;
  padding-top: 0.5rem; border-top: 1px solid var(--border-light);
}
.list-title { font-size: 0.78rem; font-weight: 600; color: var(--t2); white-space: nowrap; }
.search-input {
  margin-left: auto; width: 150px; padding: 0.25rem 0.45rem;
  border: 1px solid var(--border); border-radius: var(--r-sm);
  font-size: 0.72rem; font-family: inherit;
}

/* ── Action bar ── */
.action-bar {
  display: flex; align-items: center; gap: 0.5rem;
  padding: 0.4rem 0.55rem;
  background: var(--surface-2); border: 1px solid var(--border-light);
  border-radius: var(--r-sm); flex-shrink: 0;
}
.sel-info { font-size: 0.72rem; color: var(--t3); font-weight: 500; white-space: nowrap; }
.spacer { flex: 1; }

.filter-tabs {
  display: inline-flex; gap: 2px; padding: 2px;
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-sm);
}
.filter-tab {
  padding: 0.2rem 0.5rem; border: none; background: transparent;
  border-radius: calc(var(--r-sm) - 2px); font-size: 0.7rem; font-weight: 500;
  color: var(--t4); cursor: pointer; font-family: inherit; transition: all 0.12s;
}
.filter-tab:hover { color: var(--t2); }
.filter-tab.active { background: var(--p); color: #fff; }

/* ── Buttons ── */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 0.3rem;
  padding: 0.4rem 0.85rem; border-radius: var(--r-sm);
  font-size: 0.78rem; font-weight: 500; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface);
  color: var(--t2); transition: all 0.12s; font-family: inherit;
}
.btn:hover:not(:disabled) { background: var(--surface-2); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-primary { background: var(--p); border-color: var(--p); color: #fff; }
.btn-primary:hover:not(:disabled) { background: var(--p-hover); }
.btn-secondary { background: var(--surface-2); }
.btn-sm { padding: 0.25rem 0.55rem; font-size: 0.72rem; }

/* ── Mail table ── */
.mail-table { width: 100%; border-collapse: collapse; font-size: 0.75rem; flex: 1; }
.mail-table thead { border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 1; }
.mail-table th {
  padding: 0.35rem 0.5rem; font-size: 0.64rem; font-weight: 600; color: var(--t4);
  text-transform: uppercase; letter-spacing: 0.2px; text-align: left;
  white-space: nowrap; background: var(--surface-2);
}
.mail-table td {
  padding: 0.3rem 0.5rem; border-bottom: 1px solid var(--border-light);
  color: var(--t2); vertical-align: top; word-break: break-word;
}
.mail-table td.nowrap { white-space: nowrap; }
.mail-table td.subject-cell { color: var(--t1); line-height: 1.4; }
.mail-table td.center { text-align: center; }
.mail-table tbody tr { transition: background 0.08s; cursor: pointer; }
.mail-table tbody tr:hover { background: var(--surface-2); }
.mail-table tbody tr.row-selected { background: var(--p-light); }
.mail-table tbody tr.row-done td:not(:first-child):not(:nth-child(2)):not(:nth-child(3)) { opacity: 0.5; }
.mail-table tbody tr:last-child td { border-bottom: none; }
.mail-table input[type="checkbox"] { width: 13px; height: 13px; accent-color: var(--p); cursor: pointer; }
.dim { color: var(--t4); font-size: 0.7rem; }

/* ── Badges ── */
.badge {
  display: inline-block; padding: 1px 6px; border-radius: 9999px;
  font-size: 0.62rem; font-weight: 600; white-space: nowrap;
}
.st-pending { background: #E7EEF7; color: #3B6EA5; }
.st-processing { background: #FBF0DD; color: #B4791F; }
.st-done { background: #E1F0EA; color: #1F6F5C; }
.st-failed { background: #F6E3DF; color: #9A3B2E; }
.st-skipped { background: var(--surface-2); color: #A8A29E; }

/* ── Logs ── */
.logs {
  background: #1a1a1a; color: #c0c0c0;
  font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 0.64rem; padding: 0.4rem 0.6rem; border-radius: var(--r-sm);
  max-height: 180px; overflow-y: auto; line-height: 1.6; flex-shrink: 0;
}
.log-line { word-break: break-all; }

/* ── Empty ── */
.empty { padding: 1.5rem; text-align: center; color: var(--t4); font-size: 0.8rem; flex-shrink: 0; }

/* ── Footer / pager ── */
.table-footer { padding: 0.4rem 0 0; display: flex; align-items: center; flex-shrink: 0; }
.pager { display: flex; align-items: center; gap: 0.4rem; }
.pager-info { font-size: 0.7rem; color: var(--t4); }
</style>
