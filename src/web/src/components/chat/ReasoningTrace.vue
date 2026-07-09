<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ trace: { name?: string; duration_ms?: number; status?: string; content?: string; detail?: string; icon?: string }[] }>()

const expanded = ref(false)
</script>

<template>
  <div v-if="trace.length" class="reasoning">
    <button class="reasoning-toggle" @click="expanded = !expanded">
      <span>🧭 查询过程 · {{ trace.map(t => t.icon || '•').join(' → ') }} · {{ trace.reduce((s, t) => s + (t.duration_ms || 0), 0) }}ms</span>
      <span class="toggle-arrow" :class="{ open: expanded }">▾</span>
    </button>
    <div v-if="expanded" class="reasoning-detail">
      <div v-for="(step, i) in trace" :key="i" class="step">
        <span class="step-dot" :class="step.status || 'ok'"></span>
        <div>
          <strong>{{ step.name }}</strong> · {{ step.duration_ms }}ms
          <div v-if="step.content" class="step-content">{{ step.content }}</div>
          <div v-if="step.detail" class="step-detail">{{ step.detail }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.reasoning { margin-bottom: 0.5rem; }

.reasoning-toggle {
  display: flex; align-items: center; justify-content: space-between;
  width: 100%; padding: 0.4rem 0.6rem; border: 1px solid var(--border);
  border-radius: var(--r-sm); background: var(--surface-2);
  font-size: 0.75rem; color: var(--t3); cursor: pointer;
  font-family: inherit;
}

.toggle-arrow { transition: transform 0.15s; }
.toggle-arrow.open { transform: rotate(180deg); }

.reasoning-detail {
  padding: 0.5rem 0.6rem; border: 1px solid var(--border);
  border-top: none; border-radius: 0 0 var(--r-sm) var(--r-sm);
  background: var(--surface);
}

.step {
  display: flex; gap: 0.5rem; padding: 0.3rem 0;
  font-size: 0.78rem; color: var(--t2);
}

.step-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  margin-top: 0.3rem;
}

.step-dot.ok { background: #10B981; }
.step-dot.fail { background: #EF4444; }
.step-dot.warning { background: #F59E0B; }

.step-content { font-size: 0.7rem; color: var(--t4); margin-top: 0.1rem; }
.step-detail { font-size: 0.68rem; color: var(--t4); }
</style>
