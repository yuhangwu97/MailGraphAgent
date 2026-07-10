<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { mailsApi, type MailStats, type MailItem } from '@/api'
import AccountManager from '@/components/workbench/AccountManager.vue'
import FileImportPanel from '@/components/workbench/FileImportPanel.vue'

const kpi = ref<MailStats>({ total: 0, done: 0, pending: 0, failed: 0, skipped: 0, ingested: 0, indexed: 0 })
const pendingMails = ref<MailItem[]>([])
const doneMails = ref<MailItem[]>([])
const selectedIds = ref<Set<string>>(new Set())
const fetchLogs = ref<string[]>([])
const ingestLogs = ref<string[]>([])
const fetching = ref(false)
const ingesting = ref(false)
const activeTab = ref<'pending' | 'done'>('pending')
const sourceMode = ref<'imap' | 'file'>('imap')

const folder = ref('INBOX')
const limit = ref(20)

const allSelected = computed(() =>
  doneMails.value.length > 0 && selectedIds.value.size === doneMails.value.length
)

function toggleAll() {
  if (allSelected.value) selectedIds.value = new Set()
  else selectedIds.value = new Set(doneMails.value.map(m => m.message_id))
}

function toggleSelect(id: string) {
  if (selectedIds.value.has(id)) selectedIds.value.delete(id)
  else selectedIds.value.add(id)
}

const kpiCards = [
  { key: 'total' as const, value: '邮件总数', color: '#57534E', get: (k: MailStats) => k.total },
  { key: 'indexed' as const, value: '待解析', color: '#3B6EA5', get: (k: MailStats) => k.indexed },
  { key: 'done' as const, value: '已处理', color: '#1F6F5C', get: (k: MailStats) => k.done },
  { key: 'pending' as const, value: '待导入', color: '#B4791F', get: (k: MailStats) => k.pending },
  { key: 'failed' as const, value: '失败', color: '#9A3B2E', get: (k: MailStats) => k.failed },
  { key: 'skipped' as const, value: '已跳过', color: '#A8A29E', get: (k: MailStats) => k.skipped },
]

async function refreshStats() { try { kpi.value = await mailsApi.stats() } catch (e) { console.error(e) } }
async function refreshPending() { try { pendingMails.value = await mailsApi.pending() } catch (e) { console.error(e) } }
async function refreshDone() { try { const r = await mailsApi.done({ page_size: 100 }); doneMails.value = r.items } catch (e) { console.error(e) } }

async function handleFetch() {
  fetching.value = true; fetchLogs.value = []
  mailsApi.fetch(folder.value, limit.value, {
    onProgress(d: any) { fetchLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete() { fetchLogs.value.push('拉取完成'); fetching.value = false; refreshStats(); refreshPending() },
    onError(m: string) { fetchLogs.value.push('错误: ' + m); fetching.value = false },
  })
}

async function handleIngest() {
  ingesting.value = true; ingestLogs.value = []
  mailsApi.ingest(null, {
    onProgress(d: any) { ingestLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete(d: any) { ingestLogs.value.push('导入完成: ' + JSON.stringify(d)); ingesting.value = false; refreshStats(); refreshPending(); refreshDone() },
    onError(m: string) { ingestLogs.value.push('错误: ' + m); ingesting.value = false },
  })
}

async function handleReprocess() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  ingesting.value = true; ingestLogs.value = []
  mailsApi.reprocess(ids, {
    onProgress(d: any) { ingestLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete(d: any) { ingestLogs.value.push('完成: ' + JSON.stringify(d)); ingesting.value = false; selectedIds.value = new Set(); refreshStats(); refreshPending(); refreshDone() },
    onError(m: string) { ingestLogs.value.push('错误: ' + m); ingesting.value = false },
  })
}

onMounted(async () => { await Promise.all([refreshStats(), refreshPending(), refreshDone()]) })
</script>

<template>
  <div class="workbench">
    <h2>邮件工作台</h2>

    <!-- KPI -->
    <div class="kpi-row">
      <div v-for="card in kpiCards" :key="card.key" class="kpi-item">
        <span class="kpi-num" :style="{ color: card.color }">{{ card.get(kpi).toLocaleString() }}</span>
        <span class="kpi-text">{{ card.value }}</span>
      </div>
    </div>

    <!-- Source mode switch -->
    <div class="seg">
      <button :class="['seg-btn', { active: sourceMode === 'imap' }]" @click="sourceMode = 'imap'">
        从邮箱拉取 (IMAP)
      </button>
      <button :class="['seg-btn', { active: sourceMode === 'file' }]" @click="sourceMode = 'file'">
        从文件导入 (PST / EML / MSG)
      </button>
    </div>

    <!-- ══ IMAP mode ══ -->
    <div v-if="sourceMode === 'imap'">
    <!-- Toolbar -->
    <div class="toolbar">
      <div class="toolbar-account">
        <AccountManager />
      </div>
      <div class="toolbar-actions">
        <input v-model.number="limit" type="number" min="1" max="200" class="tb-num" />
        <select v-model="folder" class="tb-select">
          <option value="INBOX">INBOX</option>
          <option value="[Gmail]/Sent Mail">[Gmail]/Sent Mail</option>
        </select>
        <button class="tb-btn tb-btn--primary" :disabled="fetching" @click="handleFetch">
          {{ fetching ? '拉取中…' : '拉取' }}
        </button>
        <button class="tb-btn tb-btn--primary" :disabled="ingesting || pendingMails.length === 0" @click="handleIngest">
          {{ ingesting ? '导入中…' : '导入图谱' }}
        </button>
      </div>
    </div>

    <!-- Logs -->
    <div v-if="fetchLogs.length" class="log-box">
      <div v-for="(l, i) in fetchLogs" :key="'f' + i">{{ l }}</div>
    </div>
    <div v-if="ingestLogs.length" class="log-box">
      <div v-for="(l, i) in ingestLogs" :key="'i' + i">{{ l }}</div>
    </div>

    <!-- Tabs + Table -->
    <div class="tabs">
      <button
        :class="['tab', { active: activeTab === 'pending' }]"
        @click="activeTab = 'pending'"
      >
        待导入
        <span class="tab-count">{{ pendingMails.length }}</span>
      </button>
      <button
        :class="['tab', { active: activeTab === 'done' }]"
        @click="activeTab = 'done'"
      >
        已入库
        <span class="tab-count">{{ doneMails.length }}</span>
      </button>
    </div>

    <!-- Pending Table -->
    <table v-if="activeTab === 'pending' && pendingMails.length" class="data-table">
      <thead>
        <tr>
          <th style="width:100px">日期</th>
          <th>主题</th>
          <th style="width:200px">发件人</th>
          <th style="width:60px">附件</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="mail in pendingMails" :key="mail.message_id">
          <td class="dim">{{ (mail.date || '').slice(0, 10) }}</td>
          <td>{{ (mail.subject || '(无主题)').slice(0, 80) }}</td>
          <td class="dim">{{ (mail.from_addr || '').slice(0, 30) }}</td>
          <td class="dim center">{{ mail.attachment_count || '-' }}</td>
        </tr>
      </tbody>
    </table>

    <!-- Done Table -->
    <table v-if="activeTab === 'done' && doneMails.length" class="data-table">
      <thead>
        <tr>
          <th style="width:36px">
            <input type="checkbox" :checked="allSelected" @change="toggleAll" />
          </th>
          <th style="width:100px">日期</th>
          <th>主题</th>
          <th style="width:200px">发件人</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="mail in doneMails"
          :key="mail.message_id"
          :class="{ 'row-selected': selectedIds.has(mail.message_id) }"
          @click="toggleSelect(mail.message_id)"
        >
          <td class="center" @click.stop>
            <input type="checkbox" :checked="selectedIds.has(mail.message_id)" @change="toggleSelect(mail.message_id)" />
          </td>
          <td class="dim">{{ (mail.date || '').slice(0, 10) }}</td>
          <td>{{ (mail.subject || '(无主题)').slice(0, 70) }}</td>
          <td class="dim">{{ (mail.from_addr || '').slice(0, 30) }}</td>
        </tr>
      </tbody>
    </table>

    <!-- Empty -->
    <div v-if="activeTab === 'pending' && !pendingMails.length" class="empty">
      队列为空，点击 <b>拉取</b> 获取邮件
    </div>
    <div v-if="activeTab === 'done' && !doneMails.length" class="empty">
      暂无已入库邮件
    </div>

    <!-- Reprocess button -->
    <div v-if="activeTab === 'done' && doneMails.length" class="table-footer">
      <button class="tb-btn tb-btn--primary" :disabled="selectedIds.size === 0" @click="handleReprocess">
        重新处理选中 ({{ selectedIds.size }})
      </button>
    </div>
    </div>

    <!-- ══ File import mode ══ -->
    <div v-else>
      <div class="toolbar">
        <div class="toolbar-account">
          <AccountManager />
        </div>
      </div>
      <FileImportPanel @changed="() => { refreshStats(); refreshDone() }" />
    </div>
  </div>
</template>

<style scoped>
.workbench { max-width: 960px; }

/* ── KPI ── */
.kpi-row {
  display: flex; gap: 0; margin-bottom: 1.25rem;
  border: 1px solid var(--border); border-radius: var(--r);
  overflow: hidden; background: var(--surface);
}
.kpi-item {
  flex: 1; text-align: center; padding: 0.75rem 0.5rem;
  border-right: 1px solid var(--border-light);
}
.kpi-item:last-child { border-right: none; }
.kpi-num { display: block; font-size: 1.35rem; font-weight: 700; line-height: 1.2; }
.kpi-text { display: block; font-size: 0.7rem; color: var(--t4); margin-top: 0.15rem; }

/* ── Source mode segmented control ── */
.seg {
  display: inline-flex; gap: 2px; padding: 3px;
  border: 1px solid var(--border); border-radius: var(--r-sm);
  background: var(--surface-2); margin-bottom: 1rem;
}
.seg-btn {
  padding: 0.4rem 0.9rem; border: none; background: transparent;
  border-radius: calc(var(--r-sm) - 2px); font-size: 0.8rem; font-weight: 500;
  color: var(--t4); cursor: pointer; transition: all 0.12s; font-family: inherit;
}
.seg-btn:hover { color: var(--t2); }
.seg-btn.active { background: var(--surface); color: var(--t1); box-shadow: 0 1px 2px rgba(0,0,0,0.06); }

/* ── Toolbar ── */
.toolbar {
  border: 1px solid var(--border); border-radius: var(--r);
  background: var(--surface); margin-bottom: 1rem;
}
.toolbar-account {
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--border-light);
}
.toolbar-actions {
  display: flex; align-items: center; gap: 0.5rem;
  padding: 0.65rem 1rem;
}
.tb-num { width: 64px; }
.tb-select { width: auto; min-width: 140px; }
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

/* ── Tabs ── */
.tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); margin-bottom: 0; }
.tab {
  padding: 0.5rem 1rem; font-size: 0.82rem; font-weight: 500;
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

/* ── Table ── */
.data-table {
  width: 100%; border-collapse: collapse;
  background: var(--surface); font-size: 0.82rem;
}
.data-table thead { border-bottom: 1px solid var(--border); }
.data-table th {
  padding: 0.5rem 0.75rem; font-size: 0.7rem; font-weight: 600;
  color: var(--t4); text-transform: uppercase; letter-spacing: 0.3px;
  text-align: left; white-space: nowrap; background: var(--surface-2);
}
.data-table td {
  padding: 0.45rem 0.75rem; border-bottom: 1px solid var(--border-light);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  color: var(--t2);
}
.data-table tbody tr { transition: background 0.08s; cursor: pointer; }
.data-table tbody tr:hover { background: var(--surface-2); }
.data-table tbody tr.row-selected { background: var(--p-light); }
.data-table tbody tr:last-child td { border-bottom: none; }
.data-table input[type="checkbox"] { width: 14px; height: 14px; accent-color: var(--p); cursor: pointer; }
.dim { color: var(--t4); font-size: 0.75rem; }
.center { text-align: center; }

/* ── Empty ── */
.empty {
  padding: 2.5rem; text-align: center; color: var(--t4);
  background: var(--surface); border: 1px solid var(--border);
  border-top: none; font-size: 0.85rem;
}

/* ── Footer ── */
.table-footer {
  padding: 0.65rem 0; display: flex; justify-content: flex-end;
}

/* ── Log ── */
.log-box {
  background: #1a1a1a; color: #c0c0c0; font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 0.7rem; padding: 0.55rem 0.8rem; border-radius: var(--r-sm);
  max-height: 220px; overflow-y: auto; margin-bottom: 0.75rem; line-height: 1.5;
}
</style>
