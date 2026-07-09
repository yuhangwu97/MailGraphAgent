<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ChatMessage as ChatMessageType, QueryResult } from '@/api'

const props = defineProps<{
  message: ChatMessageType
  streaming?: boolean
  result?: QueryResult | null
  progress?: string[]
}>()

const AVATAR_AI = 'M'
const AVATAR_USER = '我'

const showTrace = ref(false)
const showSources = ref(false)

const trace = computed(() => props.result?.trace ?? [])
const chunks = computed(() => props.result?.chunks ?? [])
const plan = computed(() => props.result?.query_plan ?? null)

// Group chunks by doc_name
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
</script>

<template>
  <div class="msg-wrapper" :class="message.role">
    <div class="msg-avatar" :class="message.role">
      {{ message.role === 'user' ? AVATAR_USER : AVATAR_AI }}
    </div>
    <div class="msg-body">
      <!-- Progress steps (during streaming) -->
      <div v-if="progress?.length" class="progress-steps">
        <div v-for="(p, i) in progress" :key="i" class="progress-step">{{ p }}</div>
      </div>

      <!-- Content -->
      <div class="msg-content" :class="message.role">
        <div class="msg-text" v-text="message.content"></div>
        <span v-if="streaming" class="cursor-blink">|</span>
      </div>

      <!-- Trace (after completion) -->
      <div v-if="!streaming && trace.length" class="trace-section">
        <button class="trace-toggle" @click="showTrace = !showTrace">
          🔍 {{ showTrace ? '收起' : '展开' }}查询过程（{{ trace.length }} 步）
        </button>
        <div v-if="showTrace" class="trace-list">
          <div
            v-for="(t, i) in trace"
            :key="i"
            class="trace-item"
            :style="{ borderLeftColor: t.color || '#8B5CF6' }"
          >
            <span class="trace-icon">{{ t.icon || '📌' }}</span>
            <span class="trace-name">{{ t.name }}</span>
            <span v-if="t.detail" class="trace-detail">{{ t.detail }}</span>
            <span v-if="t.duration_ms" class="trace-time">{{ t.duration_ms }}ms</span>
          </div>
        </div>
      </div>

      <!-- Sources (after completion, for content queries) -->
      <div v-if="!streaming && chunkGroups.length" class="sources-section">
        <button class="sources-toggle" @click="showSources = !showSources">
          📎 {{ showSources ? '收起' : '展开' }}引用来源（{{ chunkGroups.length }} 封邮件）
        </button>
        <div v-if="showSources" class="sources-list">
          <div v-for="(g, i) in chunkGroups" :key="i" class="source-item">
            <div class="source-header">
              <span class="source-index">#{{ i + 1 }}</span>
              <span class="source-name">{{ g.doc_name }}</span>
              <span class="source-score">相关度 {{ (g.maxScore * 100).toFixed(0) }}%</span>
            </div>
            <div
              v-for="(c, j) in g.chunks.slice(0, 2)"
              :key="j"
              class="source-chunk"
            >{{ (c.content || '').slice(0, 300) }}{{ (c.content || '').length > 300 ? '…' : '' }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg-wrapper {
  display: flex; gap: 0.75rem; margin: 0.5rem 0;
}

.msg-wrapper.user { flex-direction: row-reverse; }

.msg-avatar {
  width: 32px; height: 32px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 0.7rem; font-weight: 700; flex-shrink: 0;
}

.msg-avatar.user {
  background: var(--surface-2); color: var(--t3);
}

.msg-avatar.assistant {
  background: var(--p); color: #fff;
}

.msg-body { flex: 1; min-width: 0; }

.msg-content {
  font-size: 0.92rem; line-height: 1.75; color: var(--t2);
}

.msg-content.user {
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 11px; padding: 0.7rem 1rem; color: var(--t1);
}

.msg-text { white-space: pre-wrap; word-break: break-word; }

.cursor-blink {
  animation: blink 0.8s infinite; color: var(--p);
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* ── Progress steps ── */
.progress-steps {
  margin-bottom: 0.4rem;
}

.progress-step {
  font-size: 0.78rem; color: var(--t3);
  padding: 0.2rem 0; display: flex; align-items: center; gap: 0.3rem;
}

/* ── Trace ── */
.trace-section { margin-top: 0.5rem; }

.trace-toggle, .sources-toggle {
  font-size: 0.78rem; color: var(--p); background: none; border: none;
  cursor: pointer; padding: 0.2rem 0;
}
.trace-toggle:hover, .sources-toggle:hover { opacity: 0.8; }

.trace-list {
  margin-top: 0.35rem; display: flex; flex-direction: column; gap: 0.25rem;
}

.trace-item {
  font-size: 0.76rem; color: var(--t3); padding: 0.2rem 0.5rem;
  border-left: 2px solid var(--border); display: flex; align-items: center; gap: 0.35rem;
}

.trace-icon { flex-shrink: 0; }
.trace-name { font-weight: 500; }
.trace-detail { color: var(--t4); }
.trace-time { margin-left: auto; font-size: 0.7rem; color: var(--t4); }

/* ── Sources ── */
.sources-section { margin-top: 0.5rem; }

.sources-list {
  margin-top: 0.35rem; display: flex; flex-direction: column; gap: 0.4rem;
}

.source-item {
  background: var(--surface-2); border-radius: 6px; padding: 0.4rem 0.6rem;
}

.source-header {
  display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.25rem;
}

.source-index {
  font-size: 0.7rem; font-weight: 600; color: var(--p);
}

.source-name {
  font-size: 0.78rem; font-weight: 500; color: var(--t2); flex: 1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.source-score {
  font-size: 0.7rem; color: var(--t4);
}

.source-chunk {
  font-size: 0.74rem; color: var(--t3); line-height: 1.55;
  padding: 0.2rem 0; border-top: 1px solid var(--border);
}
</style>
