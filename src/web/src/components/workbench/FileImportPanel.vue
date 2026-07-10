<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { mailsApi, type MailItem, type BrowseFile } from '@/api'

const emit = defineEmits<{ changed: [] }>()

// ── Step 1: 选文件 ──
const pathInput = ref('')
const browseFiles = ref<BrowseFile[]>([])
const selectedPaths = ref<Set<string>>(new Set())
const browsing = ref(false)
const browseErr = ref('')

const allFilesSelected = computed(() =>
  browseFiles.value.length > 0 && selectedPaths.value.size === browseFiles.value.length
)
function toggleAllFiles() {
  if (allFilesSelected.value) selectedPaths.value = new Set()
  else selectedPaths.value = new Set(browseFiles.value.map(f => f.path))
}
function toggleFile(p: string) {
  const s = new Set(selectedPaths.value)
  s.has(p) ? s.delete(p) : s.add(p)
  selectedPaths.value = s
}

async function doBrowse() {
  const dir = pathInput.value.trim()
  if (!dir) return
  browsing.value = true; browseErr.value = ''
  try {
    const r = await mailsApi.browse(dir)
    browseFiles.value = r.files
    selectedPaths.value = new Set(r.files.map(f => f.path)) // 默认全选
    if (!r.files.length) browseErr.value = '该路径下未找到 .pst/.ost/.eml/.msg 文件'
  } catch (e: any) {
    browseErr.value = e.message || String(e)
    browseFiles.value = []
  } finally {
    browsing.value = false
  }
}

function fmtSize(n: number): string {
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n > 1024) return (n / 1024).toFixed(0) + ' KB'
  return n + ' B'
}

// ── Step 2: 导入（扫描表头）──
const indexing = ref(false)
const indexLogs = ref<string[]>([])

function doIndex() {
  const paths = selectedPaths.value.size
    ? [...selectedPaths.value]
    : (pathInput.value.trim() ? [pathInput.value.trim()] : [])
  if (!paths.length) return
  indexing.value = true; indexLogs.value = []
  mailsApi.indexFiles(paths, {
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

const totalPages = computed(() => Math.max(1, Math.ceil(indexedTotal.value / pageSize.value)))
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return indexedMails.value
  return indexedMails.value.filter(m => (m.subject || '').toLowerCase().includes(q))
})
const allSelected = computed(() =>
  filtered.value.length > 0 && filtered.value.every(m => selectedIds.value.has(m.message_id))
)
function toggleAll() {
  const s = new Set(selectedIds.value)
  if (allSelected.value) filtered.value.forEach(m => s.delete(m.message_id))
  else filtered.value.forEach(m => s.add(m.message_id))
  selectedIds.value = s
}
function toggleSelect(id: string) {
  const s = new Set(selectedIds.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selectedIds.value = s
}

async function refreshIndexed() {
  try {
    const r = await mailsApi.indexed({ page: page.value, page_size: pageSize.value })
    indexedMails.value = r.items
    indexedTotal.value = r.total
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

onMounted(refreshIndexed)
</script>

<template>
  <div class="file-import">
    <!-- Step 1 + 2: 选文件 & 扫描 -->
    <div class="panel">
      <div class="panel-title">① 选择本地邮件文件（.pst / .ost / .eml / .msg）</div>
      <div class="row">
        <input
          v-model="pathInput"
          class="path-input"
          placeholder="粘贴本地绝对路径（文件或目录），例如 /Users/you/outlook/archive.pst"
          @keyup.enter="doBrowse"
        />
        <button class="tb-btn" :disabled="browsing || !pathInput.trim()" @click="doBrowse">
          {{ browsing ? '扫描目录…' : '浏览目录' }}
        </button>
        <button class="tb-btn tb-btn--primary" :disabled="indexing || (!selectedPaths.size && !pathInput.trim())" @click="doIndex">
          {{ indexing ? '导入中…' : '导入（扫描表头）' }}
        </button>
      </div>
      <div v-if="browseErr" class="hint hint--err">{{ browseErr }}</div>

      <!-- 浏览到的文件列表 -->
      <table v-if="browseFiles.length" class="data-table compact">
        <thead>
          <tr>
            <th style="width:36px"><input type="checkbox" :checked="allFilesSelected" @change="toggleAllFiles" /></th>
            <th>文件</th>
            <th style="width:80px">类型</th>
            <th style="width:90px">大小</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in browseFiles" :key="f.path"
              :class="{ 'row-selected': selectedPaths.has(f.path) }" @click="toggleFile(f.path)">
            <td class="center" @click.stop><input type="checkbox" :checked="selectedPaths.has(f.path)" @change="toggleFile(f.path)" /></td>
            <td>{{ f.name }}</td>
            <td class="dim">{{ f.ext }}</td>
            <td class="dim">{{ fmtSize(f.size) }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="indexLogs.length" class="log-box">
        <div v-for="(l, i) in indexLogs" :key="'ix' + i">{{ l }}</div>
      </div>
    </div>

    <!-- Step 3 + 4: 主题列表 & 解析 -->
    <div class="panel">
      <div class="panel-title">
        ② 主题列表（待解析 {{ indexedTotal }}）
        <input v-model="search" class="search-input" placeholder="搜索主题…" />
      </div>

      <table v-if="filtered.length" class="data-table">
        <thead>
          <tr>
            <th style="width:36px"><input type="checkbox" :checked="allSelected" @change="toggleAll" /></th>
            <th style="width:100px">日期</th>
            <th>主题</th>
            <th style="width:150px">文件夹</th>
            <th style="width:180px">发件人</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in filtered" :key="m.message_id"
              :class="{ 'row-selected': selectedIds.has(m.message_id) }" @click="toggleSelect(m.message_id)">
            <td class="center" @click.stop><input type="checkbox" :checked="selectedIds.has(m.message_id)" @change="toggleSelect(m.message_id)" /></td>
            <td class="dim">{{ (m.date || '').slice(0, 10) }}</td>
            <td>{{ (m.subject || '(无主题)').slice(0, 80) }}</td>
            <td class="dim">{{ m.folder || '-' }}</td>
            <td class="dim">{{ (m.from_addr || m.from_name || '').slice(0, 28) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">暂无待解析邮件，先在上方选文件并「导入」</div>

      <!-- 分页 + 解析按钮 -->
      <div class="table-footer">
        <div class="pager" v-if="totalPages > 1">
          <button class="tb-btn" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
          <span class="pager-info">{{ page }} / {{ totalPages }}</span>
          <button class="tb-btn" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
        </div>
        <div class="spacer" />
        <button class="tb-btn tb-btn--primary" :disabled="parsing || selectedIds.size === 0" @click="doParse">
          {{ parsing ? '解析中…' : `解析所选并向量化 (${selectedIds.size})` }}
        </button>
      </div>

      <div v-if="parseLogs.length" class="log-box">
        <div v-for="(l, i) in parseLogs" :key="'ps' + i">{{ l }}</div>
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
.row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.path-input {
  flex: 1; min-width: 280px; padding: 0.4rem 0.6rem;
  border: 1px solid var(--border); border-radius: var(--r-sm);
  font-size: 0.8rem; font-family: inherit;
}
.search-input {
  margin-left: auto; width: 180px; padding: 0.25rem 0.5rem;
  border: 1px solid var(--border); border-radius: var(--r-sm);
  font-size: 0.75rem; font-family: inherit; font-weight: 400;
}
.hint { font-size: 0.75rem; margin-top: 0.4rem; }
.hint--err { color: #9A3B2E; }

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
.data-table.compact { font-size: 0.78rem; }
.data-table thead { border-bottom: 1px solid var(--border); }
.data-table th {
  padding: 0.45rem 0.7rem; font-size: 0.68rem; font-weight: 600; color: var(--t4);
  text-transform: uppercase; letter-spacing: 0.3px; text-align: left; white-space: nowrap; background: var(--surface-2);
}
.data-table td {
  padding: 0.4rem 0.7rem; border-bottom: 1px solid var(--border-light);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--t2);
}
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
.spacer { flex: 1; }

.log-box {
  background: #1a1a1a; color: #c0c0c0; font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 0.7rem; padding: 0.55rem 0.8rem; border-radius: var(--r-sm);
  max-height: 220px; overflow-y: auto; margin-top: 0.75rem; line-height: 1.5;
}
</style>
