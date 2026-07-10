<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { graphApi } from '@/api'
import GraphView from '@/components/graph/GraphView.vue'
import type { RawEntity, RawRelationship } from '@/components/graph/GraphView.vue'
import { NODE_COLORS, LABEL_NAMES, LABEL_ICONS, colorOf } from '@/components/graph/graphTheme'

const entities = ref<RawEntity[]>([])
const relationships = ref<RawRelationship[]>([])
const buildLogs = ref<string[]>([])
const building = ref(false)
const entityTypes = ref<string[]>([])
const selectedTypes = ref<string[]>([])
const typeCounts = ref<Record<string, number>>({})

async function loadGraph() {
  try {
    const [entRes, relRes] = await Promise.all([
      graphApi.entities(1, 500),
      graphApi.relationships(1, 1000),
    ])
    entities.value = entRes.entities || []
    relationships.value = relRes.relationships || []

    const counts: Record<string, number> = {}
    for (const e of entities.value) {
      const t = e.type || 'Entity'
      counts[t] = (counts[t] || 0) + 1
    }
    typeCounts.value = counts
    entityTypes.value = Object.keys(counts).sort()
    selectedTypes.value = [...entityTypes.value]
  } catch (e) { console.error(e) }
}

// Client-side filtering — no server round-trip, instant + smooth
const shownEntities = computed(() => {
  if (selectedTypes.value.length >= entityTypes.value.length) return entities.value
  const set = new Set(selectedTypes.value)
  return entities.value.filter((e) => set.has(e.type || 'Entity'))
})

const shownRelationships = computed(() => {
  const ids = new Set(shownEntities.value.map((e) => e.id))
  return relationships.value.filter((r) => ids.has(r.source_id) && ids.has(r.target_id))
})

async function handleBuild() {
  building.value = true
  buildLogs.value = []
  graphApi.build(300, {
    onProgress(data: any) {
      buildLogs.value.push(data.msg || JSON.stringify(data))
    },
    onComplete(data: any) {
      buildLogs.value.push('✅ ' + (data.message || JSON.stringify(data)))
      building.value = false
      loadGraph()
    },
    onError(msg: string) {
      buildLogs.value.push('❌ ' + msg)
      building.value = false
    },
  })
}

function toggleType(t: string) {
  const idx = selectedTypes.value.indexOf(t)
  if (idx >= 0) selectedTypes.value.splice(idx, 1)
  else selectedTypes.value.push(t)
}

onMounted(loadGraph)
</script>

<template>
  <div class="graph-dashboard">
    <!-- Top toolbar -->
    <div class="graph-toolbar">
      <div class="toolbar-left">
        <h2 class="toolbar-title">🕸️ 关系图谱</h2>
        <span class="toolbar-sub">全局关系可视化 · 大屏模式</span>
      </div>
      <div class="toolbar-center">
        <div class="stat-pills-inline">
          <span class="stat-pill-mini">节点 <b>{{ shownEntities.length }}</b></span>
          <span class="stat-pill-mini">关系 <b>{{ shownRelationships.length }}</b></span>
        </div>
      </div>
      <div class="toolbar-right">
        <div class="filter-row" v-if="entityTypes.length">
          <label
            v-for="t in entityTypes" :key="t"
            class="filter-chip" :class="{ active: selectedTypes.includes(t) }"
            :style="{ '--chip': colorOf(t) }"
          >
            <input type="checkbox" :checked="selectedTypes.includes(t)" @change="toggleType(t)" hidden />
            <span class="chip-dot"></span>
            {{ LABEL_ICONS[t] || '' }} {{ LABEL_NAMES[t] || t }}
            <span class="chip-count">{{ typeCounts[t] }}</span>
          </label>
        </div>
        <button class="btn btn-primary build-btn" :disabled="building" @click="handleBuild">
          🔄 {{ building ? '建图中…' : '重建' }}
        </button>
      </div>
    </div>

    <!-- Build log -->
    <div v-if="buildLogs.length" class="log-box">
      <div v-for="(l, i) in buildLogs" :key="i">{{ l }}</div>
    </div>

    <!-- Graph (fills remaining space) -->
    <div class="graph-container">
      <GraphView :entities="shownEntities" :relationships="shownRelationships" />
    </div>
  </div>
</template>

<style scoped>
.graph-dashboard {
  display: flex; flex-direction: column;
  height: calc(100vh - var(--header-h));
  overflow: hidden;
}

.graph-toolbar {
  display: flex; align-items: center; gap: 1rem;
  padding: 0.5rem 1rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0; flex-wrap: wrap;
}
.toolbar-left { display: flex; align-items: baseline; gap: 0.6rem; }
.toolbar-title { font-size: 0.95rem; font-weight: 700; color: var(--t1); }
.toolbar-sub { font-size: 0.7rem; color: var(--t4); }
.toolbar-center { flex: 1; display: flex; justify-content: center; }
.stat-pills-inline { display: flex; gap: 0.5rem; }
.stat-pill-mini {
  font-size: 0.72rem; color: var(--t3);
  background: var(--surface-2); padding: 3px 10px; border-radius: 20px;
}
.toolbar-right { display: flex; align-items: center; gap: 0.6rem; }
.build-btn { flex-shrink: 0; font-size: 0.75rem; padding: 0.35rem 0.8rem; }

.graph-container {
  flex: 1; min-height: 0;
  padding: 8px;
  background: #070B17;
}

.filter-row { display: flex; gap: 4px; flex-wrap: wrap; }

.log-box {
  background: #0E1424; color: #A7F3E4; font-family: 'SF Mono', monospace;
  font-size: 0.75rem; padding: 0.75rem; border-radius: 8px;
  max-height: 200px; overflow-y: auto; margin-bottom: 1rem;
  line-height: 1.6; border: 1px solid rgba(45, 225, 194, 0.2);
}

.stat-row { display: flex; gap: 0.6rem; margin-bottom: 0.85rem; }
.stat-pill {
  font-size: 0.76rem; color: var(--t3);
  background: var(--surface-2); border: 1px solid var(--border);
  padding: 0.28rem 0.7rem; border-radius: 9999px;
}
.stat-pill b { color: var(--p); font-weight: 700; }

.filter-row {
  display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.85rem;
}
.filter-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 0.28rem 0.7rem; border-radius: 9999px;
  font-size: 0.73rem; font-weight: 500;
  background: var(--surface-2); border: 1px solid var(--border);
  cursor: pointer; transition: all 0.15s; user-select: none;
  opacity: 0.5;
}
.filter-chip .chip-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--chip); flex-shrink: 0; transition: box-shadow 0.15s;
}
.filter-chip .chip-count {
  font-size: 0.66rem; color: var(--t4); font-weight: 600;
}
.filter-chip.active {
  opacity: 1;
  border-color: color-mix(in srgb, var(--chip) 55%, transparent);
  background: color-mix(in srgb, var(--chip) 12%, var(--surface));
  color: var(--t1);
}
.filter-chip.active .chip-dot { box-shadow: 0 0 8px var(--chip); }

.hint { margin: 0.6rem 0; }

.legend {
  display: flex; flex-wrap: wrap; gap: 1.25rem; padding: 0.5rem 0;
}
.legend-item {
  display: flex; align-items: center; gap: 6px;
  font-size: 0.78rem; color: var(--t3);
}
.legend-dot {
  width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
}
</style>
