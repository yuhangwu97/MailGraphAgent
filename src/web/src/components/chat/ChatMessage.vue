<script setup lang="ts">
import { ref, computed } from 'vue'
import { marked } from 'marked'
import type { ChatMessage as ChatMessageType, QueryResult } from '@/api'

// Configure marked for GFM (tables, strikethrough, etc.)
marked.setOptions({
  breaks: true,
  gfm: true,
})

const props = defineProps<{
  message: ChatMessageType
  streaming?: boolean
  result?: QueryResult | null
  progress?: string[]
}>()

const activeSourceTab = ref<'chunks' | 'graph'>('chunks')
const showTrace = ref(false)
const showSources = ref(false)

const trace = computed(() => props.result?.trace ?? [])
const chunks = computed(() => props.result?.chunks ?? [])
const entities = computed(() => props.result?.entities ?? [])
const relationships = computed(() => props.result?.relationships ?? [])
const plan = computed(() => props.result?.query_plan ?? null)
const hasGraphData = computed(() => entities.value.length > 0 || relationships.value.length > 0)

// Score level helper — available in template via <script setup>
function scoreLevel(score: number): string {
  if (score > 0.5) return 'score-high'
  if (score > 0.3) return 'score-mid'
  return 'score-low'
}

// Render markdown content to HTML
const renderedContent = computed(() => {
  let raw = props.message.content || ''

  // Detect and convert tab-separated tables to markdown table format
  // Looks for consecutive lines with tabs that look like table rows
  raw = raw.replace(/(?:^|\n)(([^\n]+\t[^\n]+\n)+)/g, (match: string) => {
    const lines = match.trim().split('\n').filter(l => l.includes('\t'))
    if (lines.length < 2) return match // need at least header + one row

    const rows = lines.map(line => line.split('\t'))
    const colCount = Math.max(...rows.map(r => r.length))
    const normalized = rows.map(r => {
      while (r.length < colCount) r.push('')
      return r
    })

    // Build markdown table
    const header = '| ' + normalized[0].map(c => c.trim()).join(' | ') + ' |'
    const sep =    '| ' + normalized[0].map(() => '---').join(' | ') + ' |'
    const body = normalized.slice(1).map(r =>
      '| ' + r.map(c => c.trim().replace(/\n/g, '<br>')).join(' | ') + ' |'
    ).join('\n')

    return '\n' + header + '\n' + sep + '\n' + body + '\n'
  })

  return marked.parse(raw) as string
})

// Group chunks by doc_name for document sources
const chunkGroups = computed(() => {
  const groups: Record<string, { doc_name: string; chunks: any[]; maxScore: number }> = {}
  for (const c of chunks.value) {
    const name = c.doc_name || '未知来源'
    if (!groups[name]) groups[name] = { doc_name: name, chunks: [], maxScore: 0 }
    groups[name].chunks.push(c)
    groups[name].maxScore = Math.max(groups[name].maxScore, c.score || 0)
  }
  return Object.values(groups).sort((a, b) => b.maxScore - a.maxScore)
})

// Entity summary for graph tab
const entitySummary = computed(() => {
  const m: Record<string, number> = {}
  for (const e of entities.value) {
    const t = e.type || 'Entity'
    m[t] = (m[t] || 0) + 1
  }
  return Object.entries(m).sort((a, b) => b[1] - a[1])
})

// Entities grouped by type for structured display
const entitiesByType = computed(() => {
  const groups: Record<string, any[]> = {}
  for (const e of entities.value) {
    const t = e.type || 'other'
    if (!groups[t]) groups[t] = []
    groups[t].push(e)
  }
  // Sort groups by entity count desc
  return Object.entries(groups).sort((a, b) => b[1].length - a[1].length)
})

// Type metadata: emoji + label
const TYPE_META: Record<string, { emoji: string; label: string }> = {
  person: { emoji: '👤', label: '人员' },
  organization: { emoji: '🏢', label: '组织' },
  project: { emoji: '📋', label: '项目' },
  task: { emoji: '✅', label: '任务' },
  event: { emoji: '📅', label: '事件' },
  document: { emoji: '📄', label: '文档' },
  system: { emoji: '💻', label: '系统' },
  location: { emoji: '📍', label: '地点' },
}
function typeEmoji(t: string): string { return TYPE_META[t]?.emoji || '🏷️' }
function typeLabel(t: string): string { return TYPE_META[t]?.label || t }

// Top relationships (source → type → target) for display
const topRelationships = computed(() => {
  return relationships.value.slice(0, 20).map(r => ({
    source: r.source_id || '',
    target: r.target_id || '',
    type: (r.type || 'related').split(',')[0],
    desc: (r.description || '').split('<SEP>')[0].slice(0, 60),
  }))
})
</script>

<template>
  <div class="msg-wrapper" :class="message.role">
    <!-- Avatar -->
    <div class="msg-avatar" :class="message.role">
      <template v-if="message.role === 'user'">
        <span class="avatar-inner user-avatar-inner">我</span>
      </template>
      <template v-else>
        <span class="avatar-inner assistant-avatar-inner">AI</span>
      </template>
    </div>

    <div class="msg-body">
      <!-- Role label (assistant only) -->
      <div v-if="message.role === 'assistant'" class="msg-role-label assistant">
        AI 助手
      </div>

      <!-- Progress steps during streaming with animation -->
      <div v-if="progress?.length" class="progress-steps">
        <TransitionGroup name="step" tag="div" class="progress-list">
          <div v-for="(p, i) in progress" :key="i" class="progress-step" :style="{ animationDelay: i * 0.08 + 's' }">
            <span class="step-dot" :class="{ done: i < progress.length - 1 || !streaming }"></span>
            <span class="step-text">{{ p }}</span>
            <span v-if="i === progress.length - 1 && streaming" class="step-pulse"></span>
          </div>
        </TransitionGroup>
      </div>

      <!-- Content -->
      <div class="msg-content" :class="message.role">
        <div class="msg-text" v-html="renderedContent"></div>
        <span v-if="streaming" class="cursor-bar"></span>
      </div>

      <!-- Result summary bar (after completion) -->
      <div v-if="!streaming && result" class="result-bar">
        <div class="result-stats">
          <span v-if="chunks.length" class="result-stat">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            {{ chunks.length }} 文档
          </span>
          <span v-if="entities.length" class="result-stat">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
            {{ entities.length }} 实体
          </span>
          <span v-if="relationships.length" class="result-stat">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 9-14 9V3z"/></svg>
            {{ relationships.length }} 关系
          </span>
          <span v-if="result.total_duration_ms" class="result-stat">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            {{ result.total_duration_ms }}ms
          </span>
        </div>
        <div class="result-actions">
          <button v-if="trace.length" class="action-btn" :class="{ active: showTrace }" @click="showTrace = !showTrace">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline :points="showTrace ? '18 15 12 9 6 15' : '6 9 12 15 18 9'"/></svg>
            {{ showTrace ? '收起过程' : '查询过程' }}
          </button>
          <button v-if="chunks.length || hasGraphData" class="action-btn" :class="{ active: showSources }" @click="showSources = !showSources">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
            {{ showSources ? '收起来源' : '查看来源' }}
          </button>
        </div>
      </div>

      <!-- Trace panel -->
      <div v-if="showTrace && trace.length" class="trace-panel">
        <div class="panel-header">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          查询过程
          <span class="panel-badge">{{ trace.length }} 步</span>
        </div>
        <div class="trace-timeline">
          <div v-for="(t, i) in trace" :key="i" class="trace-node" :style="{ '--trace-color': t.color || '#8B5CF6' }">
            <div class="trace-dot-wrap">
              <span class="trace-dot">
                <span class="trace-icon">{{ t.icon || '●' }}</span>
              </span>
              <div v-if="i < trace.length - 1" class="trace-line"></div>
            </div>
            <div class="trace-info">
              <div class="trace-name">{{ t.name }}</div>
              <div v-if="t.detail" class="trace-detail">{{ t.detail }}</div>
              <div v-if="t.content" class="trace-content">{{ t.content }}</div>
              <div v-if="t.duration_ms" class="trace-time">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                {{ t.duration_ms }}ms
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Sources modal overlay -->
      <Teleport to="body">
        <Transition name="modal">
          <div v-if="showSources && (chunks.length || hasGraphData)" class="sources-overlay" @click.self="showSources = false">
            <div class="sources-modal">
              <!-- Modal header -->
              <div class="modal-header">
                <div class="modal-title-row">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                  <span>引用来源</span>
                </div>
                <button class="modal-close" @click="showSources = false" title="关闭">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>

              <!-- Source tabs -->
              <div class="source-tabs">
                <button
                  v-if="chunks.length"
                  class="source-tab"
                  :class="{ active: activeSourceTab === 'chunks' }"
                  @click="activeSourceTab = 'chunks'"
                >
                  <span class="tab-icon">📄</span>
                  <span class="tab-label">文档来源</span>
                  <span class="tab-count">{{ chunkGroups.length }}</span>
                </button>
                <button
                  v-if="hasGraphData"
                  class="source-tab"
                  :class="{ active: activeSourceTab === 'graph' }"
                  @click="activeSourceTab = 'graph'"
                >
                  <span class="tab-icon">📊</span>
                  <span class="tab-label">知识图谱</span>
                  <span class="tab-count">{{ entities.length }}</span>
                </button>
              </div>

              <!-- Modal body -->
              <div class="modal-body">
                <Transition name="fade" mode="out-in">
                  <!-- Document sources -->
                  <div v-if="activeSourceTab === 'chunks' && chunkGroups.length" key="chunks" class="source-list">
                    <div v-for="(g, i) in chunkGroups" :key="i" class="source-card">
                      <div class="source-card-header">
                        <span class="source-rank">#{{ i + 1 }}</span>
                        <span class="source-doc">{{ g.doc_name }}</span>
                        <span v-if="g.maxScore > 0" class="source-score" :class="scoreLevel(g.maxScore)">
                          {{ (g.maxScore * 100).toFixed(0) }}%
                        </span>
                      </div>
                      <div v-for="(c, j) in g.chunks.slice(0, 5)" :key="j" class="source-chunk">
                        <span class="chunk-preview">{{ (c.content || '').slice(0, 1500) }}{{ (c.content || '').length > 1500 ? '…' : '' }}</span>
                      </div>
                    </div>
                  </div>

                  <!-- Graph sources -->
                  <div v-else-if="activeSourceTab === 'graph' && hasGraphData" key="graph" class="graph-source">
                    <!-- Entity type distribution -->
                    <div v-if="entitySummary.length" class="graph-section">
                      <div class="section-label">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                        实体类型分布
                      </div>
                      <div class="stat-chips">
                        <span v-for="[type, count] in entitySummary" :key="type" class="stat-chip">
                          <span class="chip-emoji-sm">{{ typeEmoji(type) }}</span>
                          {{ typeLabel(type) }}
                          <span class="chip-count">{{ count }}</span>
                        </span>
                      </div>
                    </div>

                    <!-- Entities grouped by type -->
                    <div v-if="entitiesByType.length" class="graph-section">
                      <div class="section-label">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                        实体列表
                        <span class="panel-badge">{{ entities.length }}</span>
                      </div>
                      <div v-for="[type, ents] in entitiesByType" :key="type" class="entity-type-group">
                        <div class="type-group-header">
                          <span class="type-group-emoji">{{ typeEmoji(type) }}</span>
                          <span class="type-group-label">{{ typeLabel(type) }}</span>
                          <span class="type-group-count">{{ ents.length }}</span>
                        </div>
                        <div class="type-group-entities">
                          <span v-for="e in ents.slice(0, 15)" :key="e.id" class="entity-tag" :title="(e.description || '').split('<SEP>')[0].slice(0, 200)">
                            <span class="entity-name">{{ e.name || e.id }}</span>
                          </span>
                          <span v-if="ents.length > 15" class="entity-more">+{{ ents.length - 15 }} 更多</span>
                        </div>
                      </div>
                    </div>

                    <!-- Relationships -->
                    <div v-if="topRelationships.length" class="graph-section">
                      <div class="section-label">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 9-14 9V3z"/></svg>
                        关系列表
                        <span class="panel-badge">{{ relationships.length }} 条</span>
                      </div>
                      <div class="rel-list">
                        <div v-for="(r, i) in topRelationships" :key="i" class="rel-item">
                          <span class="rel-source">{{ r.source }}</span>
                          <span class="rel-arrow">→</span>
                          <span class="rel-type-badge">{{ r.type }}</span>
                          <span class="rel-arrow">→</span>
                          <span class="rel-target">{{ r.target }}</span>
                          <span v-if="r.desc" class="rel-desc">{{ r.desc }}</span>
                        </div>
                        <div v-if="relationships.length > 20" class="entity-more">+{{ relationships.length - 20 }} 条更多关系</div>
                      </div>
                    </div>
                  </div>
                </Transition>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>
    </div>
  </div>
</template>

<style scoped>
/* ── Layout ── */
.msg-wrapper {
  display: flex;
  gap: 0.85rem;
  margin: 1.2rem 0;
  align-items: flex-start;
  animation: msgFadeIn 0.3s ease-out;
}
@keyframes msgFadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
.msg-wrapper.user { flex-direction: row-reverse; }

/* ── Avatar ── */
.msg-avatar {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all 0.2s;
}
.msg-avatar.user {
  --avatar-bg: var(--surface-2);
  --avatar-border: var(--border);
}
.msg-avatar.assistant {}
.avatar-inner {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  transition: all 0.2s;
}
.user-avatar-inner {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 12px;
  color: var(--t3);
  font-size: 0.72rem;
  width: 36px;
  height: 36px;
}
.assistant-avatar-inner {
  background: linear-gradient(135deg, #1A6B59 0%, #2DE1C2 50%, #58F0D6 100%);
  color: #fff;
  box-shadow: 0 2px 12px rgba(45, 225, 194, 0.3);
  width: 40px;
  height: 40px;
  position: relative;
}
.assistant-avatar-inner::after {
  content: '';
  position: absolute;
  inset: -2px;
  border-radius: 50%;
  background: linear-gradient(135deg, #2DE1C2, #1A6B59);
  z-index: -1;
  opacity: 0.4;
}

/* ── Body ── */
.msg-body { flex: 1; min-width: 0; }

/* ── Role label ── */
.msg-role-label {
  font-size: 0.68rem;
  font-weight: 650;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  margin-bottom: 0.5rem;
  color: #1A6B59;
  opacity: 0.7;
  display: flex;
  align-items: center;
  gap: 0.45rem;
  line-height: 1;
}
.msg-role-label::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 0.95em;
  border-radius: 2px;
  background: linear-gradient(180deg, #2DE1C2 0%, #1A6B59 100%);
  box-shadow: 0 0 6px rgba(45, 225, 194, 0.5);
}

/* ── Content ── */
.msg-content {
  font-size: 0.9rem;
  line-height: 1.6;
  color: var(--t2);
  padding: 0.1rem 0;
}
.msg-content.assistant {
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid #2DE1C2;
  border-radius: 5px 14px 14px 14px;
  padding: 0.8rem 1rem;
  box-shadow: 0 1px 4px rgba(0,0,0,0.03);
}
.msg-content.user {
  background: linear-gradient(135deg, #175A4A 0%, #1F6F5C 50%, #278A73 100%);
  border: 1px solid rgba(23, 90, 74, 0.4);
  border-radius: 14px 14px 5px 14px;
  padding: 0.8rem 1.2rem;
  color: #fff;
  max-width: 85%;
  box-shadow: 0 2px 12px rgba(23, 90, 74, 0.28);
  display: inline-block;
}
.msg-text {
  white-space: normal;
  word-break: break-word;
}
.msg-text :deep(p) {
  margin: 0.35rem 0;
}
.msg-text :deep(p:first-child) {
  margin-top: 0;
}
.msg-text :deep(p:last-child) {
  margin-bottom: 0;
}
.msg-text :deep(h1), .msg-text :deep(h2), .msg-text :deep(h3), .msg-text :deep(h4) {
  margin: 1.2rem 0 0.5rem;
  line-height: 1.4;
  color: var(--t1);
  font-weight: 700;
}
.msg-text :deep(h1) { font-size: 1.3rem; border-bottom: 1.5px solid var(--border); padding-bottom: 0.4rem; letter-spacing: -0.01em; }
.msg-text :deep(h2) { font-size: 1.15rem; }
.msg-text :deep(h3) { font-size: 1.05rem; }
.msg-text :deep(h4) { font-size: 0.95rem; }
.msg-text :deep(strong) {
  font-weight: 650;
  color: var(--t1);
}
.msg-text :deep(em) {
  font-style: italic;
  color: var(--t2);
}
.msg-text :deep(ul), .msg-text :deep(ol) {
  margin: 0.5rem 0;
  padding-left: 1.6rem;
}
.msg-text :deep(li) {
  margin: 0.25rem 0;
  line-height: 1.7;
}
.msg-text :deep(li > ul), .msg-text :deep(li > ol) {
  margin: 0.15rem 0;
}
.msg-text :deep(code) {
  background: var(--surface-2);
  padding: 0.15em 0.45em;
  border-radius: 4px;
  font-size: 0.85em;
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  color: var(--t1);
  border: 1px solid var(--border);
}
.msg-text :deep(pre) {
  background: #1a1b26;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem 1.2rem;
  margin: 0.6rem 0;
  overflow-x: auto;
  position: relative;
}
.msg-text :deep(pre code) {
  background: none;
  padding: 0;
  border: none;
  font-size: 0.82rem;
  line-height: 1.6;
  color: #c9d1d9;
}
.msg-text :deep(pre)::before {
  content: 'code';
  position: absolute;
  top: 0.4rem;
  right: 0.8rem;
  font-size: 0.62rem;
  font-weight: 600;
  color: var(--t4);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.msg-text :deep(blockquote) {
  border-left: 3px solid var(--p);
  margin: 0.5rem 0;
  padding: 0.3rem 0.8rem;
  color: var(--t3);
  background: var(--surface-2);
  border-radius: 0 6px 6px 0;
}
.msg-text {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
.msg-text :deep(table) {
  border-collapse: collapse;
  width: auto;
  min-width: 100%;
  margin: 0.4rem 0;
  font-size: 0.75rem;
  border-radius: 6px;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  border: 1px solid var(--border);
}
.msg-text :deep(thead) {
  border-bottom: 1px solid var(--border);
}
.msg-text :deep(th), .msg-text :deep(td) {
  padding: 0.25rem 0.5rem;
  text-align: left;
  border-right: 1px solid var(--border);
  vertical-align: top;
  line-height: 1.4;
}
.msg-text :deep(th) {
  white-space: nowrap;
}
.msg-text :deep(td) {
  white-space: normal;
  word-break: break-word;
  min-width: 80px;
}
.msg-text :deep(th:last-child), .msg-text :deep(td:last-child) {
  border-right: none;
}
.msg-text :deep(th) {
  background: #F4F3F0;
  font-weight: 650;
  color: var(--t1);
  font-size: 0.7rem;
  letter-spacing: 0.01em;
}
.msg-text :deep(td) {
  color: var(--t2);
  border-bottom: 1px solid var(--border);
}
.msg-text :deep(tr:last-child td) {
  border-bottom: none;
}
.msg-text :deep(tbody tr:hover) {
  background: rgba(0,0,0,0.03);
}
.msg-text :deep(tr:nth-child(even)) {
  background: rgba(0,0,0,0.01);
}
.msg-text :deep(tbody tr:nth-child(even):hover) {
  background: rgba(0,0,0,0.03);
}
.msg-text :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 0.75rem 0;
}
.msg-text :deep(a) {
  color: #2DE1C2;
  text-decoration: underline;
  text-underline-offset: 2px;
  transition: opacity 0.15s;
}
.msg-text :deep(a:hover) {
  opacity: 0.8;
}

/* ── User bubble: keep markdown readable on the green background ── */
.msg-content.user .msg-text :deep(strong),
.msg-content.user .msg-text :deep(em),
.msg-content.user .msg-text :deep(p),
.msg-content.user .msg-text :deep(li) {
  color: #fff;
}
.msg-content.user .msg-text :deep(a) {
  color: #fff;
  text-decoration-color: rgba(255, 255, 255, 0.6);
}
.msg-content.user .msg-text :deep(code) {
  background: rgba(255, 255, 255, 0.18);
  border-color: rgba(255, 255, 255, 0.25);
  color: #fff;
}

/* ── Cursor bar ── */
.cursor-bar {
  display: inline-block;
  width: 3px;
  height: 1.15em;
  background: linear-gradient(180deg, #2DE1C2 0%, #1F6F5C 100%);
  margin-left: 2px;
  vertical-align: text-bottom;
  border-radius: 2px;
  box-shadow: 0 0 6px rgba(45, 225, 194, 0.5);
  animation: cursorPulse 0.7s ease-in-out infinite;
}
@keyframes cursorPulse {
  0%, 100% { opacity: 1; transform: scaleY(1); }
  50% { opacity: 0.2; transform: scaleY(0.85); }
}

/* ── Progress steps ── */
.progress-steps {
  margin-bottom: 0.75rem;
}
.progress-list {
  display: flex;
  flex-direction: column;
}
.progress-step {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  font-size: 0.8rem;
  color: var(--t3);
  padding: 0.2rem 0;
  animation: stepSlideIn 0.4s cubic-bezier(0.16, 1, 0.3, 1) both;
}
@keyframes stepSlideIn {
  from { opacity: 0; transform: translateY(-8px) scale(0.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.step-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--border);
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  position: relative;
}
.step-dot.done {
  background: linear-gradient(135deg, var(--p), #2DE1C2);
  box-shadow: 0 0 8px rgba(45, 225, 194, 0.4);
}
.step-dot.done::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 1.5px solid rgba(45, 225, 194, 0.25);
  animation: ringPulse 2s ease-in-out infinite;
}
@keyframes ringPulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.4); opacity: 0; }
}
.step-text { flex: 1; }
.step-pulse {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #2DE1C2;
  box-shadow: 0 0 0 rgba(45, 225, 194, 0.5);
  animation: pulseDot 1.2s ease-in-out infinite;
  flex-shrink: 0;
}
@keyframes pulseDot {
  0% { box-shadow: 0 0 0 0 rgba(45, 225, 194, 0.6); }
  50% { box-shadow: 0 0 0 6px rgba(45, 225, 194, 0); }
  100% { box-shadow: 0 0 0 0 rgba(45, 225, 194, 0); }
}

/* ── Result bar ── */
.result-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 0.5rem;
  padding: 0.5rem 0.75rem;
  gap: 0.5rem;
  flex-wrap: wrap;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid #2DE1C2;
  border-radius: 0 10px 10px 10px;
  animation: fadeSlideUp 0.3s ease-out;
}
@keyframes fadeSlideUp {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
.result-stats { display: flex; gap: 0.85rem; flex-wrap: wrap; }
.result-stat {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.72rem;
  color: var(--t4);
  font-weight: 500;
}
.result-stat svg {
  opacity: 0.5;
  width: 13px;
  height: 13px;
}
.result-actions { display: flex; gap: 0.35rem; }
.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.72rem;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 0.3rem 0.65rem;
  cursor: pointer;
  color: var(--t4);
  transition: all 0.15s;
  font-weight: 500;
}
.action-btn:hover {
  background: var(--surface);
  border-color: var(--p);
  color: var(--p);
}
.action-btn.active {
  background: color-mix(in srgb, var(--p) 10%, transparent);
  border-color: var(--p);
  color: var(--p);
}
.action-btn svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
}

/* ── Trace panel (stays inline) ── */
.trace-panel {
  margin-top: 0.5rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid #2DE1C2;
  border-radius: 0 12px 12px 12px;
  padding: 0.85rem 1rem;
  animation: fadeSlideUp 0.3s ease-out;
}
.panel-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.74rem;
  font-weight: 600;
  color: var(--t3);
  letter-spacing: 0.03em;
  margin-bottom: 0.6rem;
}
.panel-header svg {
  opacity: 0.6;
}
.panel-badge {
  font-size: 0.65rem;
  font-weight: 600;
  color: var(--t4);
  background: var(--surface-2);
  padding: 1px 7px;
  border-radius: 20px;
  margin-left: auto;
  border: 1px solid var(--border);
}

/* ── Trace timeline ── */
.trace-timeline {
  display: flex;
  flex-direction: column;
}
.trace-node {
  display: flex;
  gap: 0.7rem;
  position: relative;
}
.trace-dot-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  width: 28px;
}
.trace-dot {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--trace-color) 12%, transparent);
  border: 1.5px solid color-mix(in srgb, var(--trace-color) 30%, transparent);
  transition: all 0.2s;
}
.trace-node:hover .trace-dot {
  background: color-mix(in srgb, var(--trace-color) 20%, transparent);
  border-color: var(--trace-color);
}
.trace-icon {
  font-size: 0.75rem;
  line-height: 1;
}
.trace-line {
  width: 2px;
  flex: 1;
  min-height: 12px;
  background: linear-gradient(180deg, var(--border) 0%, color-mix(in srgb, var(--border) 40%, transparent) 100%);
  margin-top: 4px;
}
.trace-info {
  flex: 1;
  padding-bottom: 0.6rem;
}
.trace-name {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--t2);
}
.trace-detail {
  font-size: 0.72rem;
  color: var(--t4);
  margin-top: 1px;
}
.trace-content {
  font-size: 0.72rem;
  color: var(--t3);
  margin-top: 2px;
  line-height: 1.5;
  background: var(--surface-2);
  padding: 0.3rem 0.6rem;
  border-radius: 6px;
  border: 1px solid var(--border);
}
.trace-time {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 0.68rem;
  color: var(--t4);
  margin-top: 2px;
  background: var(--surface-2);
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--border);
  width: fit-content;
}

/* ── Sources modal ── */
.sources-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
  padding: 2rem;
}
.sources-modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  box-shadow: var(--sh-lg);
  width: 100%; max-width: 900px; max-height: 85vh;
  display: flex; flex-direction: column;
  overflow: hidden;
}
.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 1rem 1.25rem; border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.modal-title-row {
  display: flex; align-items: center; gap: 0.5rem;
  font-size: 0.92rem; font-weight: 650; color: var(--t1);
}
.modal-close {
  width: 32px; height: 32px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--surface-2);
  color: var(--t3); cursor: pointer; display: flex;
  align-items: center; justify-content: center;
  transition: all 0.15s;
}
.modal-close:hover { background: var(--border); color: var(--t1); }
.modal-body {
  flex: 1; overflow-y: auto; padding: 1rem 1.25rem;
}

/* ── Source tabs (in modal) ── */
.source-tabs {
  display: flex; gap: 0; padding: 0 1.25rem;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2); flex-shrink: 0;
}
.source-tab {
  display: flex; align-items: center; gap: 0.4rem;
  font-size: 0.85rem; padding: 0.55rem 1rem;
  border: none; border-bottom: 2px solid transparent;
  background: transparent; color: var(--t3); cursor: pointer;
  transition: all 0.15s; font-family: inherit;
}
.source-tab.active {
  color: var(--p); border-bottom-color: var(--p);
  font-weight: 600; background: var(--surface);
}
.source-tab:hover:not(.active) { color: var(--t2); }
.tab-icon { font-size: 0.9rem; }
.tab-count {
  font-size: 0.65rem; background: var(--surface-2); color: var(--t4);
  padding: 1px 6px; border-radius: 10px; font-weight: 600;
}
.source-tab.active .tab-count { background: var(--p-bg); color: var(--p); }

/* ── Document sources ── */
.source-list { display: flex; flex-direction: column; gap: 0.5rem; }
.source-card {
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 10px; padding: 0.6rem 0.8rem;
  transition: border-color 0.15s;
}
.source-card:hover { border-color: color-mix(in srgb, var(--p) 40%, transparent); }
.source-card-header {
  display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem;
}
.source-rank {
  font-size: 0.75rem; font-weight: 700; color: var(--p);
  background: color-mix(in srgb, var(--p) 12%, transparent);
  padding: 3px 8px; border-radius: 5px; flex-shrink: 0;
}
.source-doc {
  font-size: 0.88rem; font-weight: 600; color: var(--t2);
  flex: 1; word-break: break-word;
}
.source-score {
  font-size: 0.75rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 5px;
  flex-shrink: 0;
}
.score-high { color: #2DE1C2; background: color-mix(in srgb, #2DE1C2 10%, transparent); }
.score-mid { color: #FBBF24; background: color-mix(in srgb, #FBBF24 10%, transparent); }
.score-low { color: #94A3B8; background: color-mix(in srgb, #94A3B8 10%, transparent); }
.source-chunk {
  padding: 0.35rem 0;
  border-top: 1px solid var(--border);
}
.source-chunk:last-child {
  padding-bottom: 0;
}
.chunk-preview {
  font-size: 0.84rem;
  color: var(--t2);
  line-height: 1.65;
}

/* ── Graph source ── */
.graph-source {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.graph-section {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.section-label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.78rem;
  color: var(--t3);
  font-weight: 600;
}
.section-label svg {
  opacity: 0.5;
  flex-shrink: 0;
}
.stat-chips {
  display: flex;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.stat-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.78rem;
  padding: 3px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 20px;
  color: var(--t3);
}
.chip-count {
  font-weight: 700;
  color: var(--p);
  font-size: 0.68rem;
}
.chip-emoji-sm {
  font-size: 0.75rem;
  line-height: 1;
}

/* Entity type groups */
.entity-type-group {
  border: 1px solid var(--border);
  border-radius: 9px;
  overflow: hidden;
}
.type-group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0.4rem 0.7rem;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
  font-size: 0.75rem;
}
.type-group-emoji { font-size: 0.85rem; line-height: 1; }
.type-group-label { font-weight: 600; color: var(--t2); flex: 1; }
.type-group-count {
  font-size: 0.68rem;
  color: var(--t4);
  background: var(--surface);
  padding: 1px 7px;
  border-radius: 10px;
  font-weight: 600;
}
.type-group-entities {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  padding: 0.5rem 0.7rem;
}

/* Entity tags */
.entity-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 0.76rem;
  padding: 4px 9px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--t2);
  transition: all 0.15s;
  cursor: default;
}
.entity-tag:hover {
  border-color: var(--p);
  background: color-mix(in srgb, var(--p) 6%, transparent);
}
.entity-name { font-weight: 500; }
.entity-more {
  font-size: 0.72rem;
  color: var(--t4);
  padding: 2px 6px;
  font-style: italic;
}

/* Relationship list */
.rel-list {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.rel-item {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  font-size: 0.73rem;
  padding: 0.35rem 0.5rem;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 7px;
  line-height: 1.5;
}
.rel-source, .rel-target {
  font-weight: 550;
  color: var(--t2);
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rel-arrow {
  color: var(--t4);
  font-size: 0.65rem;
  flex-shrink: 0;
}
.rel-type-badge {
  font-size: 0.65rem;
  font-weight: 600;
  color: var(--p-text);
  background: var(--p-bg);
  padding: 1px 6px;
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
}
.rel-desc {
  font-size: 0.68rem;
  color: var(--t4);
  margin-left: auto;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Old flat entity grid — deprecated, replaced by type-group */
.entity-grid {
  display: flex;
  gap: 0.35rem;
  flex-wrap: wrap;
}

/* ── Transitions ── */
.step-enter-active {
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}
.step-leave-active {
  transition: all 0.2s ease-in;
}
.step-enter-from {
  opacity: 0;
  transform: translateX(-12px) scale(0.96);
}
.step-leave-to {
  opacity: 0;
  transform: translateX(8px);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* ── Modal transitions ── */
.modal-enter-active { transition: opacity 0.25s var(--ease-out); }
.modal-leave-active { transition: opacity 0.2s var(--ease-in); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .sources-modal { transform: scale(0.95) translateY(12px); transition: transform 0.25s var(--ease-out); }
.modal-leave-to .sources-modal { transform: scale(0.95) translateY(12px); transition: transform 0.2s var(--ease-in); }
</style>
