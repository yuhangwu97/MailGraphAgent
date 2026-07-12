<script setup lang="ts">
import { computed } from 'vue'
import SvgIcon from '@/components/SvgIcon.vue'
import type { ProjectSummary, NeighborEntity } from '@/api'

const props = defineProps<{
  name: string
  description: string
  people: NeighborEntity[]
  companies: NeighborEntity[]
  tasks: NeighborEntity[]
  events: NeighborEntity[]
  documents: NeighborEntity[]
  systems: NeighborEntity[]
  locations: NeighborEntity[]
  otherNeighbors: NeighborEntity[]
  aiSummary: ProjectSummary | null
}>()

defineEmits<{
  'view-report': [name: string]
  'chat-analyze': [name: string]
  reanalyze: [name: string]
  delete: [name: string]
}>()

const entityLines = computed(() => {
  const lines: { label: string; text: string }[] = []
  const add = (label: string, items: NeighborEntity[]) => {
    if (!items.length) return
    const names = items.slice(0, 4).map(i => i.name)
    const suffix = items.length > 4 ? ` 等${items.length}个` : ''
    lines.push({ label, text: names.join('、') + suffix })
  }
  add('人员', props.people)
  add('公司', props.companies)
  add('任务', props.tasks)
  add('事件', props.events)
  add('文档', props.documents)
  add('系统', props.systems)
  add('地点', props.locations)
  add('其他', props.otherNeighbors)
  return lines
})
</script>

<template>
  <div class="project-card">
    <div class="pc-header">
      <div class="pc-icon">
        <SvgIcon name="project" :size="16" />
      </div>
      <h3 class="pc-name">{{ name }}</h3>
    </div>

    <!-- AI Summary section -->
    <div v-if="aiSummary" class="pc-ai-summary">
      <div class="ai-summary-row">
        <span class="ai-label">📌</span>
        <span class="ai-text">{{ aiSummary.overview }}</span>
      </div>
      <div class="ai-summary-row" v-if="aiSummary.stage">
        <span class="ai-label">📈</span>
        <span class="ai-stage-badge">{{ aiSummary.stage }}</span>
      </div>
      <div class="ai-summary-row" v-if="aiSummary.key_dates">
        <span class="ai-label">📅</span>
        <span class="ai-text">{{ aiSummary.key_dates }}</span>
      </div>
      <div class="ai-summary-row" v-if="aiSummary.core_people?.length">
        <span class="ai-label">👥</span>
        <span class="ai-text">{{ aiSummary.core_people.join('、') }}</span>
      </div>
    </div>

    <!-- Fallback description when no AI summary -->
    <p v-else class="pc-desc">{{ description || '图谱中暂无该项目的描述信息。' }}</p>

    <!-- Entity lines -->
    <div class="pc-meta">
      <div v-if="entityLines.length" class="pc-entity-lines">
        <div v-for="line in entityLines" :key="line.label" class="pc-entity-line">
          <span class="pc-el-label">{{ line.label }}</span>
          <span class="pc-el-text">{{ line.text }}</span>
        </div>
      </div>
      <div v-else class="pc-meta-item empty">
        <SvgIcon name="user" :size="13" />
        <span>暂无关联实体</span>
      </div>
    </div>

    <!-- Action buttons -->
    <div class="pc-actions">
      <button class="pc-btn pc-btn-primary" @click="$emit('view-report', name)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
        </svg>
        {{ aiSummary ? '查看报告' : 'AI 分析' }}
      </button>
      <button
        v-if="aiSummary"
        class="pc-btn pc-btn-ghost"
        @click="$emit('reanalyze', name)"
        title="重新 AI 分析"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
        </svg>
        重新分析
      </button>
      <button
        v-else
        class="pc-btn pc-btn-ghost"
        @click="$emit('chat-analyze', name)"
        title="在 Chat 中深度分析"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        Chat 分析
      </button>
      <button
        class="pc-btn pc-btn-delete"
        @click.stop="$emit('delete', name)"
        title="删除项目"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6"/>
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.project-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 1.15rem 1.25rem;
  transition: box-shadow 0.15s, border-color 0.15s, transform 0.12s;
  display: flex;
  flex-direction: column;
}

.project-card:hover {
  box-shadow: var(--sh-md);
  border-color: var(--t5);
  transform: translateY(-1px);
}

.pc-header {
  display: flex; align-items: center; gap: 0.55rem;
  margin-bottom: 0.5rem;
}

.pc-icon {
  width: 28px; height: 28px;
  border-radius: 7px;
  background: #FEF2F2;
  color: #9A3B2E;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}

.pc-name {
  font-size: 0.92rem; font-weight: 620; color: var(--t1);
  line-height: 1.3; margin: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* AI Summary */
.pc-ai-summary {
  background: var(--p-bg);
  border: 1px solid color-mix(in srgb, var(--p) 15%, transparent);
  border-radius: 8px;
  padding: 0.55rem 0.65rem;
  margin-bottom: 0.65rem;
  flex: 1;
}

.ai-summary-row {
  display: flex;
  gap: 4px;
  font-size: 0.73rem;
  line-height: 1.45;
  margin-bottom: 0.15rem;
}
.ai-summary-row:last-child { margin-bottom: 0; }

.ai-label {
  flex-shrink: 0;
  font-size: 0.7rem;
}

.ai-text {
  color: var(--t2);
}

.ai-stage-badge {
  display: inline-block;
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--p-text);
  background: color-mix(in srgb, var(--p) 15%, transparent);
  padding: 0.05rem 0.45rem;
  border-radius: 999px;
}

/* Description fallback */
.pc-desc {
  font-size: 0.78rem; color: var(--t3); line-height: 1.55;
  margin-bottom: 0.7rem;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden;
  flex: 1;
}

/* Meta */
.pc-meta {
  padding-top: 0.5rem;
  border-top: 1px solid var(--border-light);
}

.pc-entity-lines {
  display: flex;
  flex-direction: column;
  gap: 0.22rem;
}

.pc-entity-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 0.72rem;
  line-height: 1.4;
}

.pc-el-label {
  flex-shrink: 0;
  font-size: 0.68rem;
  font-weight: 550;
  color: var(--t4);
  min-width: 30px;
}

.pc-el-text {
  color: var(--t2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pc-meta-item {
  display: flex; align-items: center; gap: 5px;
  font-size: 0.73rem; color: var(--t3);
}

.pc-meta-item.empty {
  color: var(--t4);
}

/* Actions */
.pc-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.6rem;
  padding-top: 0.55rem;
  border-top: 1px solid var(--border-light);
}

.pc-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.73rem;
  font-weight: 520;
  padding: 0.3rem 0.7rem;
  border-radius: 7px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
  line-height: 1.3;
}

.pc-btn-primary {
  background: var(--p);
  color: #fff;
  border-color: var(--p);
  flex: 1;
  justify-content: center;
}

.pc-btn-primary:hover {
  background: var(--p-hover);
  border-color: var(--p-hover);
}

.pc-btn-ghost {
  background: var(--surface);
  color: var(--t3);
}

.pc-btn-ghost:hover {
  background: var(--surface-2);
  border-color: var(--p);
  color: var(--p);
}

.pc-btn-delete {
  background: transparent;
  border-color: transparent;
  color: var(--t5);
  padding: 0.3rem 0.45rem;
  margin-left: auto;
}

.pc-btn-delete:hover {
  background: #FEF2F2;
  border-color: #FECACA;
  color: #DC2626;
}
</style>
