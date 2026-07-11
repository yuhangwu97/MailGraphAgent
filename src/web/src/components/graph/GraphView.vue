<script setup lang="ts">
import { ref, shallowRef, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import { ForceGraph, type RawEntity, type RawRelationship, type HoverPayload } from './forceGraph'
import { nameOf, NODE_COLORS } from './graphTheme'

const props = defineProps<{
  entities: RawEntity[]
  relationships: RawRelationship[]
}>()

const wrap = ref<HTMLDivElement>()
const canvas = ref<HTMLCanvasElement>()
const graph = shallowRef<ForceGraph>()
const fullscreen = ref(false)
const showStats = ref(true)

// Stats
const entityStats = computed(() => {
  const m: Record<string, number> = {}
  for (const e of props.entities) {
    const t = nameOf(e.type || 'Entity')
    m[t] = (m[t] || 0) + 1
  }
  return Object.entries(m).sort((a, b) => b[1] - a[1])
})

const tip = ref<{ show: boolean; x: number; y: number; name: string; type: string; desc: string; color: string }>({
  show: false, x: 0, y: 0, name: '', type: '', desc: '', color: '#2DE1C2',
})

let ro: ResizeObserver | null = null

function onHover(p: HoverPayload) {
  if (!p.node) { tip.value.show = false; return }
  const n: any = p.node
  tip.value = {
    show: true,
    x: p.x, y: p.y,
    name: n.name, type: nameOf(n.type),
    desc: (n.description || '').slice(0, 200),
    color: n.color,
  }
}

function sync() {
  graph.value?.setData(props.entities || [], props.relationships || [])
}

onMounted(() => {
  if (!canvas.value) return
  const g = new ForceGraph(canvas.value, { onHover })
  graph.value = g
  g.resize()
  sync()
  g.start()

  ro = new ResizeObserver(() => g.resize())
  if (wrap.value) ro.observe(wrap.value)
})

onBeforeUnmount(() => {
  ro?.disconnect()
  graph.value?.destroy()
})

watch(() => [props.entities, props.relationships], sync, { deep: false })

// Controls
function zoomIn() { graph.value?.zoom(1.3) }
function zoomOut() { graph.value?.zoom(0.7) }
function fitView() { graph.value?.fitView() }
function toggleFullscreen() {
  if (!document.fullscreenElement) {
    wrap.value?.requestFullscreen()
    fullscreen.value = true
  } else {
    document.exitFullscreen()
    fullscreen.value = false
  }
}

// Listen for fullscreen change
onMounted(() => {
  document.addEventListener('fullscreenchange', () => {
    fullscreen.value = !!document.fullscreenElement
    graph.value?.resize()
  })
})
</script>

<template>
  <div class="graph-wrap" ref="wrap" :class="{ 'is-fullscreen': fullscreen }">
    <canvas ref="canvas" class="graph-canvas"></canvas>

    <!-- Empty state -->
    <div v-if="!entities.length" class="graph-empty">
      <div class="empty-icon">🕸️</div>
      <div class="empty-title">暂无图谱数据</div>
      <div class="empty-desc">导入邮件并构建知识图谱后，这里将展示实体关系网络</div>
    </div>

    <!-- Top-left: Stats overlay -->
    <transition name="slide">
      <div v-if="showStats && entities.length" class="stats-panel">
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
          <div class="stat-item" v-for="[type, count] in entityStats.slice(0, 4)" :key="type">
            <span class="stat-val" :style="{ color: NODE_COLORS[Object.keys(NODE_COLORS).find(k => nameOf(k) === type) || 'default'] || '#94A3B8' }">{{ count }}</span>
            <span class="stat-lbl">{{ type }}</span>
          </div>
        </div>
        <button class="stats-toggle" @click="showStats = false">−</button>
      </div>
    </transition>

    <!-- Stats toggle when hidden -->
    <button v-if="!showStats && entities.length" class="stats-show-btn" @click="showStats = true">
      📊
    </button>

    <!-- Top-right: Controls -->
    <div class="controls-bar">
      <button class="ctrl-btn" @click="zoomIn" title="放大">＋</button>
      <button class="ctrl-btn" @click="zoomOut" title="缩小">−</button>
      <button class="ctrl-btn" @click="fitView" title="适应视图">⤢</button>
      <button class="ctrl-btn" @click="toggleFullscreen" title="全屏">
        {{ fullscreen ? '⤓' : '⤢' }}
      </button>
    </div>

    <!-- Hover tooltip -->
    <transition name="fade">
      <div
        v-if="tip.show"
        class="node-tip"
        :style="{ left: tip.x + 14 + 'px', top: tip.y + 14 + 'px', '--tc': tip.color }"
      >
        <div class="tip-head">
          <span class="tip-dot"></span>
          <span class="tip-name">{{ tip.name }}</span>
        </div>
        <div class="tip-type">{{ tip.type }}</div>
        <div v-if="tip.desc" class="tip-desc">{{ tip.desc }}</div>
      </div>
    </transition>

    <!-- Bottom hint -->
    <div class="hint-bar">
      <span>🖱️ 拖拽 · 缩放</span>
      <span>👆 点击节点查看详情</span>
      <span>🔍 滚轮缩放</span>
    </div>
  </div>
</template>

<style scoped>
.graph-wrap {
  position: relative;
  height: 100%;
  min-height: 400px;
  border-radius: 16px;
  overflow: hidden;
  background: #070B17;
  box-shadow:
    0 0 0 1px rgba(45, 225, 194, 0.08),
    0 8px 40px rgba(0, 0, 0, 0.5),
    inset 0 0 160px rgba(0, 0, 0, 0.3);
  transition: height 0.4s ease;
}

.graph-wrap.is-fullscreen {
  height: 100vh;
  border-radius: 0;
}

.graph-canvas {
  width: 100%;
  height: 100%;
  display: block;
  cursor: grab;
}
.graph-canvas:active { cursor: grabbing; }

/* ── Empty state ── */
.graph-empty {
  position: absolute; inset: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 0.6rem;
  background: rgba(7, 11, 23, 0.85);
  backdrop-filter: blur(12px);
}
.empty-icon { font-size: 3rem; opacity: 0.5; }
.empty-title { color: #94A3B8; font-size: 1rem; font-weight: 600; }
.empty-desc { color: #64748B; font-size: 0.82rem; max-width: 280px; text-align: center; line-height: 1.5; }

/* ── Stats panel ── */
.stats-panel {
  position: absolute; top: 12px; left: 12px;
  background: rgba(13, 20, 40, 0.88);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(45, 225, 194, 0.12);
  border-radius: 14px;
  padding: 0.8rem 1rem;
  min-width: 180px;
  z-index: 3;
}
.stats-title {
  color: #A7F3E4; font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; margin-bottom: 0.5rem;
}
.stats-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
}
.stat-item {
  display: flex; flex-direction: column; gap: 1px;
  padding: 4px 6px;
  background: rgba(255,255,255,0.03);
  border-radius: 6px;
}
.stat-val { font-size: 0.95rem; font-weight: 700; color: #E2E8F0; line-height: 1.2; }
.stat-lbl { font-size: 0.62rem; color: #64748B; font-weight: 500; }
.stats-toggle {
  position: absolute; top: 6px; right: 8px;
  background: none; border: none; color: #64748B; cursor: pointer;
  font-size: 0.85rem; padding: 2px 6px; border-radius: 4px;
}
.stats-toggle:hover { color: #A7F3E4; }
.stats-show-btn {
  position: absolute; top: 12px; left: 12px; z-index: 3;
  background: rgba(13, 20, 40, 0.88);
  border: 1px solid rgba(45, 225, 194, 0.12);
  border-radius: 10px; color: #A7F3E4; font-size: 1rem;
  padding: 6px 10px; cursor: pointer;
}

/* ── Controls ── */
.controls-bar {
  position: absolute; top: 12px; right: 12px;
  display: flex; gap: 4px; z-index: 3;
}
.ctrl-btn {
  width: 34px; height: 34px;
  background: rgba(13, 20, 40, 0.88);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(45, 225, 194, 0.15);
  border-radius: 10px;
  color: #A7F3E4; font-size: 0.85rem; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all 0.15s;
}
.ctrl-btn:hover {
  background: rgba(45, 225, 194, 0.15);
  border-color: rgba(45, 225, 194, 0.4);
  color: #fff;
}

/* ── Tooltip ── */
.node-tip {
  position: absolute; z-index: 10; pointer-events: none;
  max-width: 280px;
  background: rgba(10, 16, 30, 0.94);
  backdrop-filter: blur(14px);
  border: 1px solid var(--tc);
  border-radius: 12px; padding: 0.7rem 0.9rem;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.55), 0 0 24px color-mix(in srgb, var(--tc) 30%, transparent);
}
.tip-head { display: flex; align-items: center; gap: 8px; }
.tip-dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--tc); box-shadow: 0 0 14px var(--tc);
  flex-shrink: 0; animation: pulse-dot 1.6s ease-in-out infinite;
}
@keyframes pulse-dot {
  0%, 100% { box-shadow: 0 0 10px var(--tc); }
  50% { box-shadow: 0 0 22px color-mix(in srgb, var(--tc) 80%, white); }
}
.tip-name { color: #F1F5F9; font-weight: 700; font-size: 0.88rem; line-height: 1.3; }
.tip-type {
  color: var(--tc); font-size: 0.68rem; font-weight: 600;
  margin-top: 3px; letter-spacing: 0.03em;
}
.tip-desc {
  color: #94A3B8; font-size: 0.73rem; line-height: 1.5;
  margin-top: 6px; padding-top: 6px; border-top: 1px solid rgba(255,255,255,0.06);
}

/* ── Hint bar ── */
.hint-bar {
  position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%);
  display: flex; gap: 1rem;
  font-size: 0.65rem; color: rgba(148, 163, 184, 0.5);
  z-index: 2; pointer-events: none;
}

/* ── Transitions ── */
.fade-enter-active, .fade-leave-active { transition: opacity 0.15s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

.slide-enter-active { transition: all 0.25s ease; }
.slide-leave-active { transition: all 0.18s ease; }
.slide-enter-from, .slide-leave-to { opacity: 0; transform: translateX(-20px); }
</style>
