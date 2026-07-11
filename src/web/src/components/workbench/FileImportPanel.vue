<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { mailsApi, type MailItem } from '@/api'

const emit = defineEmits<{ changed: [] }>()

// ── Step 1: 选择文件/文件夹 ──
const picking = ref(false)
const pickErr = ref('')
const pickPaths = ref<string[]>([])

async function pickFolder() {
  picking.value = true; pickErr.value = ''
  try {
    const r = await mailsApi.pick('folder')
    if (!r.canceled && r.paths.length) pickPaths.value = r.paths
  } catch (e: any) {
    pickErr.value = e.message || String(e)
  } finally { picking.value = false }
}
async function pickFiles() {
  picking.value = true; pickErr.value = ''
  try {
    const r = await mailsApi.pick('files')
    if (!r.canceled && r.paths.length) pickPaths.value = r.paths
  } catch (e: any) {
    pickErr.value = e.message || String(e)
  } finally { picking.value = false }
}

// ── Step 2: 导入（扫描表头）──
const indexing = ref(false)
const indexLogs = ref<string[]>([])

function doIndex() {
  if (!pickPaths.value.length) return
  indexing.value = true; indexLogs.value = []
  mailsApi.indexFiles(pickPaths.value, {
    onProgress(d: any) { indexLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete(d: any) {
      indexLogs.value.push('扫描完成: ' + JSON.stringify(d))
      indexing.value = false; page.value = 1; refreshIndexed(); emit('changed')
    },
    onError(m: string) { indexLogs.value.push('错误: ' + m); indexing.value = false },
  })
}

// ── Step 3: 主题列表 ──
const indexedMails = ref<MailItem[]>([])
const indexedTotal = ref(0)
const page = ref(1)
const pageSize = ref(50)
const search = ref('')
const selectedIds = ref<Set<string>>(new Set())
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
  skipped: { label: '已跳过', cls: 'st-skipped' },
}
function statusMeta(s: string) {
  return STATUS_META[s] || { label: s || '未知', cls: 'st-pending' }
}

const totalPages = ref(1)

const filtered = ref<MailItem[]>([])

function updateFiltered() {
  const q = search.value.trim().toLowerCase()
  filtered.value = q
    ? indexedMails.value.filter(m => (m.subject || '').toLowerCase().includes(q))
    : indexedMails.value
}

const allSelected = ref(false)

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

// ── Step 4: 解析所选（向量化）──
const parsing = ref(false)
const parseLogs = ref<string[]>([])

function doParse() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  parsing.value = true; parseLogs.value = []
  mailsApi.parseSelected(ids, {
    onProgress(d: any) { parseLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete(d: any) {
      parseLogs.value.push('解析完成: ' + JSON.stringify(d))
      parsing.value = false; selectedIds.value = new Set()
      refreshIndexed(); emit('changed')
    },
    onError(m: string) { parseLogs.value.push('错误: ' + m); parsing.value = false },
  })
}

onMounted(() => { refreshIndexed() })
</script>

<template>
  <div class="file-import">
    <!-- Step 1 + 2: 选择 & 扫描 -->
    <div class="panel">
      <div class="panel-title">① 选择本地邮件文件（.pst / .ost / .eml / .msg）并导入</div>

      <div class="picker-bar">
        <button class="tb-btn tb-btn--primary" :disabled="picking" @click="pickFolder">
          📂 {{ picking ? '打开中…' : '选择文件夹…' }}
        </button>
        <button class="tb-btn" :disabled="picking" @click="pickFiles">
          📄 选择文件…（可多选）
        </button>
        <button
          class="tb-btn tb-btn--primary"
          :disabled="indexing || !pickPaths.length"
          @click="doIndex"
        >
          {{ indexing ? '导入中…' : `导入所选 (${pickPaths.length})` }}
        </button>
        <span v-if="pickPaths.length" class="picker-info">
          已选 {{ pickPaths.length }} 项: {{ pickPaths[0] }}{{ pickPaths.length > 1 ? ` …等` : '' }}
        </span>
      </div>
      <div v-if="pickErr" class="hint hint--err">{{ pickErr }}</div>

      <div v-if="indexLogs.length" class="log-box">
        <div v-for="(l, i) in indexLogs" :key="'ix' + i">{{ l }}</div>
      </div>
    </div>

    <!-- Step 3 + 4: 主题列表 & 解析 -->
    <div class="panel">
      <div class="panel-title">
        ② 主题列表（{{ indexedTotal }}）
        <input v-model="search" class="search-input" placeholder="搜索主题…" @input="updateFiltered" />
      </div>

      <!-- 解析按钮置顶，避免长列表下拉找不到 -->
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
        <button class="tb-btn tb-btn--primary" :disabled="parsing || selectedIds.size === 0" @click="doParse">
          {{ parsing ? '解析中…' : `解析所选并向量化 (${selectedIds.size})` }}
        </button>
      </div>

      <div v-if="parseLogs.length" class="log-box">
        <div v-for="(l, i) in parseLogs" :key="'ps' + i">{{ l }}</div>
      </div>

      <table v-if="filtered.length" class="data-table">
        <thead>
          <tr>
            <th style="width:36px"><input type="checkbox" :checked="allSelected" @change="toggleAll" /></th>
            <th style="width:82px">状态</th>
            <th style="width:100px">日期</th>
            <th>主题</th>
            <th style="width:150px">文件夹</th>
            <th style="width:180px">发件人</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in filtered" :key="m.message_id"
              :class="{ 'row-selected': selectedIds.has(m.message_id), 'row-done': m.status === 'done' }"
              @click="toggleSelect(m.message_id)">
            <td class="center" @click.stop><input type="checkbox" :checked="selectedIds.has(m.message_id)" @change="toggleSelect(m.message_id)" /></td>
            <td><span class="badge" :class="statusMeta(m.status).cls">{{ statusMeta(m.status).label }}</span></td>
            <td class="dim nowrap">{{ (m.date || '').slice(0, 10) }}</td>
            <td class="subject-cell">{{ m.subject || '(无主题)' }}</td>
            <td class="dim">{{ m.folder || '-' }}</td>
            <td class="dim">{{ m.from_addr || m.from_name || '' }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">
        {{ statusFilter === 'done' ? '还没有已处理的邮件' : '暂无邮件，先在上方选择文件并「导入」' }}
      </div>

      <div class="table-footer">
        <div class="pager" v-if="totalPages > 1">
          <button class="tb-btn" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
          <span class="pager-info">{{ page }} / {{ totalPages }}</span>
          <button class="tb-btn" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.file-import { display: flex; flex-direction: column; gap: 1rem; }
.panel {
  border: 1px solid var(--border); border-radius: var(--r);
  background: var(--surface); padding: 0.85rem 1rem;
}
.panel-title {
  font-size: 0.8rem; font-weight: 600; color: var(--t2);
  margin-bottom: 0.7rem; display: flex; align-items: center; gap: 0.75rem;
}

.picker-bar { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.picker-info { font-size: 0.75rem; color: var(--t4); }

.search-input {
  margin-left: auto; width: 180px; padding: 0.25rem 0.5rem;
  border: 1px solid var(--border); border-radius: var(--r-sm);
  font-size: 0.75rem; font-family: inherit; font-weight: 400;
}
.hint { font-size: 0.75rem; margin-top: 0.4rem; }
.hint--err { color: #9A3B2E; }

.spacer { flex: 1; }

.action-bar {
  display: flex; align-items: center; gap: 0.5rem;
  padding: 0.5rem 0.65rem; margin-bottom: 0.4rem;
  background: var(--surface-2); border: 1px solid var(--border-light);
  border-radius: var(--r-sm);
}
.sel-info { font-size: 0.75rem; color: var(--t3); font-weight: 500; }

/* 状态筛选标签 */
.filter-tabs { display: inline-flex; gap: 2px; padding: 2px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-sm); }
.filter-tab {
  padding: 0.25rem 0.6rem; border: none; background: transparent;
  border-radius: calc(var(--r-sm) - 2px); font-size: 0.74rem; font-weight: 500;
  color: var(--t4); cursor: pointer; font-family: inherit; transition: all 0.12s;
}
.filter-tab:hover { color: var(--t2); }
.filter-tab.active { background: var(--p); color: #fff; }

/* 状态徽章 */
.badge {
  display: inline-block; padding: 1px 8px; border-radius: 9999px;
  font-size: 0.66rem; font-weight: 600; white-space: nowrap;
}
.st-pending { background: #E7EEF7; color: #3B6EA5; }
.st-processing { background: #FBF0DD; color: #B4791F; }
.st-done { background: #E1F0EA; color: #1F6F5C; }
.st-failed { background: #F6E3DF; color: #9A3B2E; }
.st-skipped { background: var(--surface-2); color: #A8A29E; }

/* 已处理行淡化 */
.data-table tbody tr.row-done td:not(:first-child):not(:nth-child(2)) { opacity: 0.6; }

.tb-btn {
  display: inline-flex; align-items: center; gap: 0.3rem;
  padding: 0.4rem 0.85rem; border-radius: var(--r-sm);
  font-size: 0.8rem; font-weight: 500; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface);
  color: var(--t2); transition: all 0.12s; font-family: inherit;
}
.tb-btn:hover:not(:disabled) { background: var(--surface-2); }
.tb-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.tb-btn--primary { background: var(--p); border-color: var(--p); color: #fff; }
.tb-btn--primary:hover:not(:disabled) { background: var(--p-hover); }

.data-table { width: 100%; border-collapse: collapse; background: var(--surface); font-size: 0.82rem; margin-top: 0.7rem; }
.data-table thead { border-bottom: 1px solid var(--border); }
.data-table th {
  padding: 0.45rem 0.7rem; font-size: 0.68rem; font-weight: 600; color: var(--t4);
  text-transform: uppercase; letter-spacing: 0.3px; text-align: left; white-space: nowrap; background: var(--surface-2);
}
.data-table td {
  padding: 0.4rem 0.7rem; border-bottom: 1px solid var(--border-light);
  white-space: normal; overflow: visible; color: var(--t2);
  vertical-align: top; word-break: break-word;
}
.data-table td.nowrap { white-space: nowrap; }
.data-table td.subject-cell { color: var(--t1); line-height: 1.45; }
.data-table tbody tr { transition: background 0.08s; cursor: pointer; }
.data-table tbody tr:hover { background: var(--surface-2); }
.data-table tbody tr.row-selected { background: var(--p-light); }
.data-table tbody tr:last-child td { border-bottom: none; }
.data-table input[type="checkbox"] { width: 14px; height: 14px; accent-color: var(--p); cursor: pointer; }
.dim { color: var(--t4); font-size: 0.75rem; }
.center { text-align: center; }

.empty { padding: 2rem; text-align: center; color: var(--t4); font-size: 0.85rem; }

.table-footer { padding: 0.65rem 0 0; display: flex; align-items: center; gap: 0.75rem; }
.pager { display: flex; align-items: center; gap: 0.5rem; }
.pager-info { font-size: 0.75rem; color: var(--t4); }

.log-box {
  background: #1a1a1a; color: #c0c0c0; font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 0.7rem; padding: 0.55rem 0.8rem; border-radius: var(--r-sm);
  max-height: 220px; overflow-y: auto; margin-top: 0.75rem; line-height: 1.5;
}
</style>
