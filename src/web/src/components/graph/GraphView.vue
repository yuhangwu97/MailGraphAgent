<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import { Network } from 'vis-network'
import { DataSet } from 'vis-data'
import { NODE_COLORS, LABEL_NAMES, LABEL_ICONS } from './graphTheme'

export interface RawEntity {
  id: string; name?: string; type?: string; description?: string
}
export interface RawRelationship {
  source_id: string; target_id: string; type?: string; description?: string
}

const props = defineProps<{
  entities: RawEntity[]
  relationships: RawRelationship[]
}>()

const wrap = ref<HTMLDivElement>()
const network = ref<Network | null>(null)
const fullscreen = ref(false)
const selectedNode = ref<any>(null)

const entityStats = computed(() => {
  const m: Record<string, number> = {}
  for (const e of props.entities) {
    const t = e.type || 'Entity'
    m[t] = (m[t] || 0) + 1
  }
  return Object.entries(m).sort((a, b) => b[1] - a[1])
})

function buildGraph() {
  if (!wrap.value) return

  const nodes = new DataSet(
    props.entities.map(e => {
      const type = e.type || 'Entity'
      return {
        id: e.id,
        label: (e.name || e.id).slice(0, 20),
        title: `<div style="max-width:220px"><b>${e.name}</b><br/><span style="color:#94A3B8;font-size:0.75rem">${type}</span>${e.description ? `<br/><span style="font-size:0.7rem;color:#CBD5E1">${e.description.slice(0, 100)}</span>` : ''}</div>`,
        color: {
          background: NODE_COLORS[type] || '#94A3B8',
          border: (NODE_COLORS[type] || '#94A3B8'),
          highlight: { background: '#fff', border: NODE_COLORS[type] || '#94A3B8' },
        },
        font: { color: '#CBD5E1', size: 12, face: 'Inter, system-ui' },
        borderWidth: 2,
        shape: 'dot',
        size: 12 + Math.min((props.relationships.filter(r => r.source_id === e.id || r.target_id === e.id).length || 1) * 3, 20),
      }
    })
  )

  const edges = new DataSet(
    props.relationships.map((r, i) => ({
      id: i,
      from: r.source_id,
      to: r.target_id,
      label: (r.type || '').slice(0, 12),
      color: {
        color: 'rgba(45,225,194,0.3)',
        highlight: 'rgba(45,225,194,0.8)',
        hover: 'rgba(45,225,194,0.5)',
      },
      font: { color: '#64748B', size: 9, face: 'Inter, system-ui', strokeWidth: 2 },
      width: 1,
    }))
  )

  const options = {
    physics: {
      solver: 'forceAtlas2Based' as const,
      forceAtlas2Based: {
        gravitationalConstant: -60,
        centralGravity: 0.01,
        springLength: 180,
        springConstant: 0.08,
        damping: 0.4,
      },
      stabilization: { iterations: 200, updateInterval: 10 },
    },
    interaction: {
      hover: true,
      tooltipDelay: 200,
      zoomView: true,
      dragView: true,
      navigationButtons: false,
    },
    nodes: {
      scaling: { min: 8, max: 40 },
    },
    edges: {
      smooth: { enabled: true, type: 'continuous', roundness: 0.5 },
      arrows: { to: { enabled: true, scaleFactor: 0.5 } },
    },
  }

  const net = new Network(wrap.value, { nodes, edges }, options)
  network.value = net

  net.on('click', (params: any) => {
    if (params.nodes.length) {
      const nodeId = params.nodes[0]
      const entity = props.entities.find(e => e.id === nodeId)
      selectedNode.value = entity || null
    } else {
      selectedNode.value = null
    }
  })

  net.on('stabilizationIterationsDone', () => {
    net.fit({ animation: { duration: 800, easingFunction: 'easeInOutQuad' } })
  })
}

onMounted(() => buildGraph())
onBeforeUnmount(() => { network.value?.destroy() })
watch(() => [props.entities, props.relationships], () => {
  network.value?.destroy()
  buildGraph()
}, { deep: false })

function zoomIn() { network.value?.moveTo({ scale: (network.value?.getScale() || 1) * 1.3 }) }
function zoomOut() { network.value?.moveTo({ scale: (network.value?.getScale() || 1) * 0.7 }) }
function fitView() { network.value?.fit({ animation: true }) }

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    wrap.value?.requestFullscreen(); fullscreen.value = true
  } else {
    document.exitFullscreen(); fullscreen.value = false
  }
}

onMounted(() => {
  document.addEventListener('fullscreenchange', () => {
    fullscreen.value = !!document.fullscreenElement
    network.value?.fit({ animation: true })
  })
})
</script>

<template>
  <div class="graph-wrap" :class="{ 'is-fullscreen': fullscreen }" ref="wrap">
    <!-- Empty state -->
    <div v-if="!entities.length" class="graph-empty">
      <div class="empty-icon">🕸️</div>
      <div class="empty-title">暂无图谱数据</div>
      <div class="empty-desc">导入邮件并构建知识图谱后，这里将展示实体关系网络</div>
    </div>

    <!-- Stats overlay -->
    <div v-if="entities.length" class="stats-panel">
      <div class="stats-title">图谱概览</div>
      <div class="stats-grid">
        <div class="stat-item">
          <span class="stat-val">{{ entities.length }}</span>
          <span class="stat-lbl">实体</span>
        </div>
        <div class="stat-item">
          <span class="stat-val">{{ relationships.length }}</span>
          <span class="stat-lbl">关系</span>
        </div>
        <div class="stat-item" v-for="[type, count] in entityStats.slice(0, 6)" :key="type">
          <span class="stat-val" :style="{ color: NODE_COLORS[type] || '#94A3B8' }">{{ count }}</span>
          <span class="stat-lbl">{{ LABEL_NAMES[type] || type }}</span>
        </div>
      </div>
    </div>

    <!-- Selected node detail -->
    <transition name="slide">
      <div v-if="selectedNode" class="node-detail">
        <button class="detail-close" @click="selectedNode = null">✕</button>
        <div class="detail-name">{{ selectedNode.name }}</div>
        <div class="detail-type" :style="{ color: NODE_COLORS[selectedNode.type] || '#94A3B8' }">
          {{ selectedNode.type }}
        </div>
        <div v-if="selectedNode.description" class="detail-desc">{{ selectedNode.description }}</div>
      </div>
    </transition>

    <!-- Controls -->
    <div class="controls-bar">
      <button class="ctrl-btn" @click="zoomIn" title="放大">＋</button>
      <button class="ctrl-btn" @click="zoomOut" title="缩小">−</button>
      <button class="ctrl-btn" @click="fitView" title="适应">⤢</button>
      <button class="ctrl-btn" @click="toggleFullscreen" :title="fullscreen ? '退出全屏' : '全屏'">
        {{ fullscreen ? '⤓' : '⤢' }}
      </button>
    </div>

    <!-- Legend -->
    <div v-if="entityStats.length" class="legend-bar">
      <span v-for="[type, count] in entityStats" :key="type" class="legend-item">
        <span class="legend-dot" :style="{ background: NODE_COLORS[type] || '#64748B' }"></span>
        {{ LABEL_ICONS[type] || '' }} {{ LABEL_NAMES[type] || type }} {{ count }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.graph-wrap {
  position: relative; height: 100%; min-height: 400px;
  border-radius: 16px; overflow: hidden;
  background: linear-gradient(135deg, #060B17 0%, #0B1024 40%, #0F182E 100%);
  box-shadow: 0 0 0 1px rgba(45,225,194,0.06), 0 8px 40px rgba(0,0,0,0.5), inset 0 0 200px rgba(0,0,0,0.3);
}
.is-fullscreen { height: 100vh; border-radius: 0; }

.graph-empty {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 0.6rem;
  background: rgba(7,11,23,0.85); backdrop-filter: blur(12px); z-index: 2;
}
.empty-icon { font-size: 3rem; opacity: 0.5; }
.empty-title { color: #94A3B8; font-size: 1rem; font-weight: 600; }
.empty-desc { color: #64748B; font-size: 0.82rem; max-width: 280px; text-align: center; line-height: 1.5; }

/* Stats panel */
.stats-panel {
  position: absolute; top: 12px; left: 12px; z-index: 3;
  background: rgba(10,16,32,0.9); backdrop-filter: blur(16px);
  border: 1px solid rgba(45,225,194,0.12); border-radius: 14px;
  padding: 0.7rem 0.9rem; min-width: 180px;
}
.stats-title { color: #A7F3E4; font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.4rem; }
.stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
.stat-item { display: flex; flex-direction: column; gap: 1px; padding: 3px 5px; background: rgba(255,255,255,0.02); border-radius: 5px; }
.stat-val { font-size: 0.85rem; font-weight: 700; color: #E2E8F0; line-height: 1.2; }
.stat-lbl { font-size: 0.6rem; color: #64748B; font-weight: 500; }

/* Node detail */
.node-detail {
  position: absolute; bottom: 60px; right: 12px; z-index: 3; max-width: 280px;
  background: rgba(10,16,32,0.94); backdrop-filter: blur(16px);
  border: 1px solid rgba(45,225,194,0.2); border-radius: 14px;
  padding: 0.8rem 1rem;
}
.detail-close {
  position: absolute; top: 6px; right: 8px;
  background: none; border: none; color: #64748B; cursor: pointer; font-size: 0.85rem;
}
.detail-name { font-size: 0.9rem; font-weight: 700; color: #F1F5F9; }
.detail-type { font-size: 0.7rem; font-weight: 600; margin-top: 2px; }
.detail-desc { font-size: 0.73rem; color: #94A3B8; line-height: 1.5; margin-top: 6px; }

/* Controls */
.controls-bar {
  position: absolute; top: 12px; right: 12px; z-index: 3;
  display: flex; gap: 4px;
}
.ctrl-btn {
  width: 32px; height: 32px; background: rgba(10,16,32,0.88); backdrop-filter: blur(12px);
  border: 1px solid rgba(45,225,194,0.15); border-radius: 10px;
  color: #A7F3E4; font-size: 0.8rem; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all 0.15s;
}
.ctrl-btn:hover { background: rgba(45,225,194,0.15); border-color: rgba(45,225,194,0.4); color: #fff; }

/* Legend bar */
.legend-bar {
  position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%); z-index: 2;
  display: flex; gap: 0.75rem; flex-wrap: wrap; justify-content: center;
  font-size: 0.62rem; color: rgba(148,163,184,0.6);
  pointer-events: none;
}
.legend-item { display: flex; align-items: center; gap: 3px; }
.legend-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }

/* Transitions */
.slide-enter-active { transition: all 0.25s ease; }
.slide-leave-active { transition: all 0.15s ease; }
.slide-enter-from, .slide-leave-to { opacity: 0; transform: translateX(40px); }
</style>
