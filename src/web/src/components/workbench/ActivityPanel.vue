<script setup lang="ts">
import { ref, computed } from 'vue'
import type { MailStats, GraphStatus, ServiceStatus } from '@/api'

export interface ActivityEvent {
  id: string
  type: 'processed' | 'processing' | 'failed' | 'ingested' | 'skipped' | 'started'
  message_id: string
  subject: string
  detail: string
  timestamp: number
}

const props = defineProps<{
  activities: ActivityEvent[]
  kpi: MailStats
  graphStatus: GraphStatus
  services: ServiceStatus
}>()

const graphExpanded = ref(false)

function timeAgo(ts: number): string {
  const sec = Math.floor((Date.now() - ts) / 1000)
  if (sec < 5) return '刚刚'
  if (sec < 60) return `${sec}秒前`
  if (sec < 3600) return `${Math.floor(sec / 60)}分钟前`
  return `${Math.floor(sec / 3600)}小时前`
}

function eventIcon(type: string): string {
  switch (type) {
    case 'processed': return '🟢'
    case 'processing': return '🔵'
    case 'failed': return '🔴'
    case 'skipped': return '⏭️'
    case 'ingested': return '⚪'
    case 'started': return '▶️'
    default: return '⚪'
  }
}

function eventTitle(type: string): string {
  switch (type) {
    case 'processed': return '建图完成'
    case 'processing': return '正在处理'
    case 'failed': return '处理失败'
    case 'skipped': return '已跳过'
    case 'ingested': return '已入库'
    case 'started': return '开始处理'
    default: return type
  }
}

const kpiItems = computed(() => [
  { key: 'total', label: '总数', value: props.kpi.total, color: '#57534E' },
  { key: 'pending', label: '待处理', value: (props.kpi.indexed || 0) + (props.kpi.pending || 0) + (props.kpi.processing || 0), color: '#3B6EA5' },
  { key: 'done', label: '已入库', value: props.kpi.done, color: '#1F6F5C' },
  { key: 'failed', label: '失败', value: props.kpi.failed, color: '#9A3B2E' },
])

const docChips = computed(() => {
  const d = props.graphStatus.docs
  return [
    { key: 'processed', label: '已建图', value: d.processed, cls: 'st-done' },
    { key: 'processing', label: '建图中', value: d.processing, cls: 'st-processing' },
    { key: 'pending', label: '排队', value: d.pending, cls: 'st-pending' },
    { key: 'failed', label: '失败', value: d.failed, cls: 'st-failed' },
  ]
})
</script>

<template>
  <aside class="act-panel">
    <!-- Activity stream -->
    <div class="ap-section ap-stream">
      <div class="ap-section-head">
        <h4 class="ap-section-title">实时动态</h4>
        <span v-if="activities.length" class="ap-badge">{{ activities.length }}</span>
      </div>
      <div class="ap-stream-list">
        <div
          v-for="ev in activities"
          :key="ev.id"
          :class="['ap-event', `ev-${ev.type}`]"
        >
          <span class="ap-ev-icon">{{ eventIcon(ev.type) }}</span>
          <div class="ap-ev-body">
            <div class="ap-ev-head">
              <span class="ap-ev-title">{{ eventTitle(ev.type) }}</span>
              <span class="ap-ev-time">{{ timeAgo(ev.timestamp) }}</span>
            </div>
            <div class="ap-ev-subject">{{ ev.subject || '(无主题)' }}</div>
            <div v-if="ev.detail" class="ap-ev-detail">{{ ev.detail }}</div>
          </div>
        </div>
        <div v-if="!activities.length" class="ap-stream-empty">
          暂无动态 — 处理邮件后将在此显示
        </div>
      </div>
    </div>

    <!-- KPI cards -->
    <div class="ap-section ap-kpi">
      <div class="ap-kpi-grid">
        <div v-for="item in kpiItems" :key="item.key" class="ap-kpi-item">
          <span class="ap-kpi-num" :style="{ color: item.color }">
            {{ item.value.toLocaleString() }}
          </span>
          <span class="ap-kpi-label">{{ item.label }}</span>
        </div>
      </div>
    </div>

    <!-- Graph status (collapsible) -->
    <div class="ap-section ap-graph">
      <button class="ap-graph-toggle" @click="graphExpanded = !graphExpanded">
        <span class="ap-graph-toggle-icon">{{ graphExpanded ? '▾' : '▸' }}</span>
        <span class="ap-graph-label">图谱状态</span>
        <span class="ap-graph-summary">
          {{ graphStatus.graph.entities.toLocaleString() }} 实体 · {{ graphStatus.graph.relationships.toLocaleString() }} 关系
        </span>
      </button>

      <div v-if="graphExpanded" class="ap-graph-detail">
        <!-- Doc progress -->
        <div class="ap-doc-chips">
          <span
            v-for="d in docChips"
            :key="d.key"
            class="ap-chip"
            :class="d.cls"
          >
            {{ d.label }} <b>{{ d.value }}</b>
          </span>
        </div>

        <!-- Pipeline -->
        <div class="ap-pipeline">
          <span class="ap-pl-pill" :class="graphStatus.pipeline.busy ? 'pl-busy' : 'pl-idle'">
            <span class="ap-pl-dot" />
            {{ graphStatus.pipeline.busy ? '建图中' : '空闲' }}
          </span>
          <span
            v-if="graphStatus.pipeline.busy && graphStatus.pipeline.latest_message"
            class="ap-pl-msg"
          >
            {{ graphStatus.pipeline.latest_message }}
          </span>
        </div>
      </div>
    </div>

    <!-- Service health -->
    <div class="ap-section ap-services">
      <span
        v-for="s in [
          { key: 'redis', label: 'Redis', ok: services.redis },
          { key: 'neo4j', label: 'Neo4j', ok: services.neo4j },
          { key: 'milvus', label: 'Milvus', ok: services.milvus },
        ]"
        :key="s.key"
        :class="['ap-svc', { down: !s.ok }]"
      >
        <span class="ap-svc-dot" />
        {{ s.label }}
      </span>
    </div>
  </aside>
</template>

<style scoped>
.act-panel {
  display: flex;
  flex-direction: column;
  width: 360px;
  flex-shrink: 0;
  gap: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r);
  overflow: hidden;
  height: 100%;
}

.ap-section {
  padding: 0.65rem 0.85rem;
}

.ap-section + .ap-section {
  border-top: 1px solid var(--border-light);
}

/* ── Section head ── */
.ap-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.4rem;
}

.ap-section-title {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--t4);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin: 0;
}

.ap-badge {
  font-size: 0.6rem;
  font-weight: 700;
  color: var(--p);
  background: var(--p-light);
  padding: 1px 6px;
  border-radius: 9999px;
}

/* ── Activity stream ── */
.ap-stream {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.ap-stream-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.ap-stream-empty {
  padding: 1.5rem 0.5rem;
  text-align: center;
  color: var(--t4);
  font-size: 0.72rem;
  line-height: 1.5;
}

.ap-event {
  display: flex;
  gap: 0.5rem;
  padding: 0.4rem 0.5rem;
  border-radius: var(--r-sm);
  transition: background var(--dur-fast) var(--ease);
}

.ap-event:hover {
  background: var(--surface-2);
}

.ap-ev-icon {
  font-size: 0.75rem;
  flex-shrink: 0;
  margin-top: 1px;
}

.ap-ev-body {
  flex: 1;
  min-width: 0;
}

.ap-ev-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.ap-ev-title {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--t2);
}

.ap-ev-time {
  font-size: 0.62rem;
  color: var(--t4);
  white-space: nowrap;
}

.ap-ev-subject {
  font-size: 0.75rem;
  color: var(--t1);
  font-weight: 500;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ap-ev-detail {
  font-size: 0.66rem;
  color: var(--t4);
  margin-top: 1px;
}

/* Event type borders */
.ev-failed {
  border-left: 2px solid var(--red);
}

.ev-processing {
  border-left: 2px solid #3B82F6;
}

.ev-processed {
  border-left: 2px solid var(--green);
}

/* ── KPI ── */
.ap-kpi {
  flex-shrink: 0;
}

.ap-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0;
}

.ap-kpi-item {
  text-align: center;
  padding: 0.35rem 0.2rem;
  border-right: 1px solid var(--border-light);
}

.ap-kpi-item:last-child {
  border-right: none;
}

.ap-kpi-num {
  display: block;
  font-size: 1.1rem;
  font-weight: 700;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.ap-kpi-label {
  display: block;
  font-size: 0.62rem;
  color: var(--t4);
  margin-top: 1px;
}

/* ── Graph section ── */
.ap-graph {
  flex-shrink: 0;
}

.ap-graph-toggle {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  width: 100%;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  font-family: inherit;
  color: var(--t3);
  font-size: 0.72rem;
}

.ap-graph-toggle:hover {
  color: var(--t1);
}

.ap-graph-toggle-icon {
  font-size: 0.6rem;
  color: var(--t4);
  width: 10px;
}

.ap-graph-label {
  font-weight: 600;
  color: var(--t3);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  font-size: 0.65rem;
}

.ap-graph-summary {
  color: var(--t3);
  font-size: 0.72rem;
  font-weight: 500;
}

.ap-graph-detail {
  margin-top: 0.45rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.ap-doc-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.ap-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 1px 7px;
  border-radius: 9999px;
  font-size: 0.62rem;
  font-weight: 500;
}

.ap-chip b {
  font-weight: 700;
}

.st-pending   { background: #E7EEF7; color: #3B6EA5; }
.st-processing{ background: #DBEAFE; color: #1D4ED8; }
.st-done      { background: #E1F0EA; color: #1F6F5C; }
.st-failed    { background: #F6E3DF; color: #9A3B2E; }

.ap-pipeline {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.ap-pl-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 1px 8px;
  border-radius: 9999px;
  font-size: 0.65rem;
  font-weight: 600;
  white-space: nowrap;
}

.ap-pl-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.pl-idle {
  background: var(--surface-2);
  color: #A8A29E;
}

.pl-busy {
  background: #FBF0DD;
  color: #B4791F;
}

@keyframes pl-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}

.pl-busy .ap-pl-dot {
  animation: pl-pulse 1s ease-in-out infinite;
}

.ap-pl-msg {
  font-size: 0.66rem;
  color: var(--t4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 180px;
}

/* ── Services ── */
.ap-services {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  flex-shrink: 0;
}

.ap-svc {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.66rem;
  font-weight: 500;
  color: var(--t3);
}

.ap-svc-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #1F6F5C;
}

.ap-svc.down {
  color: var(--t5);
}

.ap-svc.down .ap-svc-dot {
  background: #9A3B2E;
}
</style>
