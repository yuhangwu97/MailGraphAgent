<script setup lang="ts">
import { computed } from 'vue'
import type { MailItem, MailStats } from '@/api'

const props = defineProps<{
  mails: MailItem[]
  total: number
  filter: 'all' | 'todo' | 'done'
  selectedIds: Set<string>
  kpi: MailStats
  processing: boolean
}>()

const emit = defineEmits<{
  (e: 'update:filter', f: 'all' | 'todo' | 'done'): void
  (e: 'toggle-select', id: string): void
  (e: 'toggle-all'): void
  (e: 'process'): void
}>()

// Filter chips
const filterChips = computed(() => {
  const k = props.kpi
  const done = (k.done || 0) + (k.skipped || 0)
  const todo = (k.pending || 0) + (k.indexed || 0) + (k.failed || 0) + (k.processing || 0)
  return [
    { key: 'all' as const, label: '全部', count: done + todo },
    { key: 'todo' as const, label: '未完成', count: todo },
    { key: 'done' as const, label: '已完成', count: done },
  ]
})

// Status metadata — 设计文档 §3.5 状态词表
const STATUS_META: Record<string, { label: string; dot: string; cls: string }> = {
  indexed:   { label: '待入库', dot: '⚪', cls: 'st-pending' },
  pending:   { label: '待重拉', dot: '🟡', cls: 'st-pending' },
  processing:{ label: '处理中', dot: '🔵', cls: 'st-processing' },
  done:      { label: '已建图', dot: '🟢', cls: 'st-done' },
  failed:    { label: '失败·可重试', dot: '🔴', cls: 'st-failed' },
  skipped:   { label: '已跳过·噪音', dot: '⏭️', cls: 'st-skipped' },
  degraded:  { label: '部分降级', dot: '🟠', cls: 'st-degraded' },
}

function statusMeta(s: string) {
  return STATUS_META[s] || { label: s || '未知', dot: '⚪', cls: 'st-pending' }
}

// Source label
function sourceLabel(t?: string): { icon: string; label: string; cls: string } {
  if (t === 'pst') return { icon: '📦', label: 'PST', cls: 'src-pst' }
  if (t === 'eml') return { icon: '📧', label: 'EML', cls: 'src-eml' }
  if (t === 'msg') return { icon: '📧', label: 'MSG', cls: 'src-msg' }
  if (t) return { icon: '📂', label: t.toUpperCase(), cls: 'src-file' }
  return { icon: '📨', label: 'IMAP', cls: 'src-imap' }
}

const allSelected = computed(() =>
  props.mails.length > 0 && props.mails.every(m => props.selectedIds.has(m.message_id))
)

// Format date for display
function fmtDate(d: string): { date: string; time: string } {
  if (!d) return { date: '', time: '' }
  try {
    const dt = new Date(d)
    const month = String(dt.getMonth() + 1).padStart(2, '0')
    const day = String(dt.getDate()).padStart(2, '0')
    const hours = String(dt.getHours()).padStart(2, '0')
    const mins = String(dt.getMinutes()).padStart(2, '0')
    return { date: `${month}-${day}`, time: `${hours}:${mins}` }
  } catch {
    return { date: (d || '').slice(0, 10), time: '' }
  }
}

// Attachments summary
function attSummary(m: MailItem): string {
  const count = m.attachment_count ?? (m.attachments?.length ?? 0)
  if (count === 0) return ''
  return `📎 ${count}`
}

function failReason(m: MailItem): string {
  if (m.status !== 'failed') return ''
  return (m as any).error_msg || ''
}
</script>

<template>
  <div class="mail-list">
    <!-- Filter tabs -->
    <div class="ml-filters">
      <button
        v-for="c in filterChips"
        :key="c.key"
        :class="['ml-filter-tab', { active: filter === c.key }]"
        @click="emit('update:filter', c.key)"
      >
        {{ c.label }}
        <span class="ml-filter-count">{{ c.count.toLocaleString() }}</span>
      </button>
    </div>

    <!-- Action bar -->
    <div class="ml-actions">
      <label class="ml-select-all">
        <input
          type="checkbox"
          :checked="allSelected"
          @change="emit('toggle-all')"
        />
        <span v-if="selectedIds.size" class="ml-sel-num">{{ selectedIds.size }} 封已选</span>
        <span v-else class="ml-sel-hint">全选本页</span>
      </label>
      <div class="ml-spacer" />
      <button
        class="btn btn-primary btn-sm"
        :disabled="processing || selectedIds.size === 0"
        @click="emit('process')"
      >
        {{ processing ? '处理中…' : '处理所选' }}
        <span v-if="selectedIds.size" class="ml-btn-badge">{{ selectedIds.size }}</span>
      </button>
    </div>

    <!-- Table -->
    <div class="ml-table-wrap">
      <table v-if="mails.length" class="ml-table">
        <thead>
          <tr>
            <th class="col-cb"></th>
            <th class="col-status">状态</th>
            <th class="col-src">来源</th>
            <th class="col-date">日期</th>
            <th class="col-subject">主题</th>
            <th class="col-from">发件人</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="m in mails"
            :key="m.message_id"
            :class="['ml-row', { selected: selectedIds.has(m.message_id) }]"
            @click="emit('toggle-select', m.message_id)"
          >
            <td class="col-cb" @click.stop>
              <input
                type="checkbox"
                :checked="selectedIds.has(m.message_id)"
                @change="emit('toggle-select', m.message_id)"
              />
            </td>
            <td class="col-status">
              <span class="ml-status" :class="statusMeta(m.status).cls">
                <span class="ml-dot" :class="{ pulse: m.status === 'processing' }" />
                {{ statusMeta(m.status).label }}
              </span>
            </td>
            <td class="col-src">
              <span class="ml-src" :class="sourceLabel(m.source_type).cls">
                {{ sourceLabel(m.source_type).icon }} {{ sourceLabel(m.source_type).label }}
              </span>
            </td>
            <td class="col-date">
              <span class="ml-date">{{ fmtDate(m.date).date }}</span>
              <span class="ml-time">{{ fmtDate(m.date).time }}</span>
            </td>
            <td class="col-subject">
              <div class="ml-subject">{{ m.subject || '(无主题)' }}</div>
              <div class="ml-meta">
                <span v-if="attSummary(m)" class="ml-att">{{ attSummary(m) }}</span>
                <span
                  v-if="m.status === 'failed' && failReason(m)"
                  class="ml-fail-msg"
                >{{ failReason(m) }}</span>
              </div>
            </td>
            <td class="col-from">
              <span class="ml-from-name">{{ m.from_name || m.from_addr || '' }}</span>
              <span v-if="m.from_name && m.from_addr" class="ml-from-addr">{{ m.from_addr }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="ml-empty">
        {{ filter === 'todo' ? '暂无未完成邮件' : filter === 'done' ? '暂无已完成邮件' : '暂无邮件' }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.mail-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r);
  overflow: hidden;
}

/* ── Filters ── */
.ml-filters {
  display: flex;
  gap: 0;
  padding: 0 0.85rem;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.ml-filter-tab {
  padding: 0.55rem 0.9rem;
  font-size: 0.82rem;
  font-weight: 500;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--t4);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease);
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-family: inherit;
}

.ml-filter-tab:hover {
  color: var(--t2);
}

.ml-filter-tab.active {
  color: var(--t1);
  border-bottom-color: var(--p);
}

.ml-filter-count {
  font-size: 0.68rem;
  font-weight: 600;
  padding: 0.05rem 0.35rem;
  border-radius: 9999px;
  background: var(--surface-2);
  color: var(--t4);
}

.ml-filter-tab.active .ml-filter-count {
  background: var(--p-light);
  color: var(--p);
}

/* ── Actions ── */
.ml-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.45rem 0.85rem;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border-light);
  flex-shrink: 0;
}

.ml-select-all {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  cursor: pointer;
  font-size: 0.78rem;
  color: var(--t3);
  user-select: none;
}

.ml-select-all input[type="checkbox"] {
  width: 14px;
  height: 14px;
  accent-color: var(--p);
  cursor: pointer;
}

.ml-sel-num {
  font-weight: 600;
  color: var(--t2);
}

.ml-sel-hint {
  color: var(--t4);
  font-size: 0.72rem;
}

.ml-spacer {
  flex: 1;
}

.ml-btn-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 999px;
  background: rgba(255,255,255,0.25);
  font-size: 0.68rem;
  font-weight: 700;
}

/* ── Table ── */
.ml-table-wrap {
  flex: 1;
  overflow-y: auto;
}

.ml-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}

.ml-table thead {
  position: sticky;
  top: 0;
  z-index: 2;
}

.ml-table th {
  padding: 0.4rem 0.75rem;
  font-size: 0.65rem;
  font-weight: 600;
  color: var(--t4);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  text-align: left;
  white-space: nowrap;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
}

.ml-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border-light);
  vertical-align: top;
}

.ml-table tbody tr {
  transition: background 0.08s;
  cursor: pointer;
}

.ml-table tbody tr:hover {
  background: var(--surface-2);
}

.ml-table tbody tr.selected {
  background: var(--p-light);
}

.ml-table tbody tr:last-child td {
  border-bottom: none;
}

.ml-table input[type="checkbox"] {
  width: 14px;
  height: 14px;
  accent-color: var(--p);
  cursor: pointer;
}

.col-cb {
  width: 36px;
  text-align: center;
}

.col-status { width: 80px; }
.col-src    { width: 85px; }
.col-date   { width: 80px; }

/* ── Status dot ── */
.ml-status {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.72rem;
  font-weight: 600;
  white-space: nowrap;
}

.ml-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.ml-dot.pulse {
  animation: dot-pulse 1s ease-in-out infinite;
}

@keyframes dot-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.2; }
}

.st-pending .ml-dot   { background: #3B6EA5; }
.st-processing .ml-dot { background: #3B82F6; }
.st-done .ml-dot      { background: #1F6F5C; }
.st-failed .ml-dot    { background: #9A3B2E; }
.st-skipped .ml-dot   { background: #A8A29E; }

.st-pending   { color: #3B6EA5; }
.st-processing{ color: #3B82F6; }
.st-done      { color: #1F6F5C; }
.st-failed    { color: #9A3B2E; }
.st-skipped   { color: #A8A29E; }

/* ── Source ── */
.ml-src {
  font-size: 0.7rem;
  font-weight: 500;
}

/* ── Date ── */
.ml-date {
  display: block;
  font-size: 0.75rem;
  color: var(--t3);
  font-weight: 500;
}

.ml-time {
  display: block;
  font-size: 0.65rem;
  color: var(--t4);
}

/* ── Subject ── */
.ml-subject {
  color: var(--t1);
  font-weight: 520;
  line-height: 1.4;
  word-break: break-word;
}

.ml-meta {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-top: 0.2rem;
  flex-wrap: wrap;
}

.ml-att {
  font-size: 0.68rem;
  color: var(--t4);
}

.ml-fail-msg {
  font-size: 0.66rem;
  color: var(--red);
  background: var(--red-bg);
  padding: 1px 6px;
  border-radius: var(--r-sm);
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── From ── */
.ml-from-name {
  display: block;
  font-size: 0.78rem;
  color: var(--t3);
  line-height: 1.3;
}

.ml-from-addr {
  display: block;
  font-size: 0.66rem;
  color: var(--t4);
}

.col-from {
  min-width: 150px;
}

/* ── Empty ── */
.ml-empty {
  padding: 3rem;
  text-align: center;
  color: var(--t4);
  font-size: 0.85rem;
}
</style>
