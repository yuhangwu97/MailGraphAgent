<script setup lang="ts">
import { ref, shallowRef, onMounted, onBeforeUnmount, watch } from 'vue'
import { ForceGraph, type RawEntity, type RawRelationship, type HoverPayload } from './forceGraph'
import { nameOf } from './graphTheme'

const props = defineProps<{
  entities: RawEntity[]
  relationships: RawRelationship[]
}>()

const wrap = ref<HTMLDivElement>()
const canvas = ref<HTMLCanvasElement>()
const graph = shallowRef<ForceGraph>()

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
    desc: (n.description || '').slice(0, 140),
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

function reset() { graph.value?.fitView() }
</script>

<template>
  <div class="graph-wrap" ref="wrap">
    <canvas ref="canvas" class="graph-canvas"></canvas>

    <div v-if="!entities.length" class="graph-empty">暂无图谱数据</div>

    <button class="reset-btn" @click="reset" title="重置视图">⤢ 适应视图</button>

    <transition name="fade">
      <div
        v-if="tip.show"
        class="node-tip"
        :style="{ left: tip.x + 16 + 'px', top: tip.y + 16 + 'px', '--tc': tip.color }"
      >
        <div class="tip-head">
          <span class="tip-dot"></span>
          <span class="tip-name">{{ tip.name }}</span>
        </div>
        <div class="tip-type">{{ tip.type }}</div>
        <div v-if="tip.desc" class="tip-desc">{{ tip.desc }}</div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.graph-wrap {
  position: relative;
  height: 620px;
  border-radius: var(--r-lg);
  overflow: hidden;
  margin-bottom: 0.5rem;
  background: #0A0E1A;
  box-shadow: 0 0 0 1px rgba(45, 225, 194, 0.14),
              0 20px 60px rgba(0, 0, 0, 0.45),
              inset 0 0 120px rgba(0, 0, 0, 0.4);
}

.graph-canvas {
  width: 100%;
  height: 100%;
  display: block;
  cursor: grab;
}
.graph-canvas:active { cursor: grabbing; }

.graph-empty {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #64748B; font-size: 0.9rem;
}

.reset-btn {
  position: absolute; top: 12px; right: 12px;
  background: rgba(20, 27, 48, 0.72);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(45, 225, 194, 0.3);
  color: #A7F3E4; font-size: 0.72rem; font-weight: 600;
  padding: 0.35rem 0.7rem; border-radius: 8px; cursor: pointer;
  transition: all 0.16s;
}
.reset-btn:hover {
  background: rgba(45, 225, 194, 0.16);
  border-color: rgba(45, 225, 194, 0.6);
  color: #fff;
}

.node-tip {
  position: absolute; z-index: 5; pointer-events: none;
  max-width: 260px;
  background: rgba(13, 18, 33, 0.92);
  backdrop-filter: blur(10px);
  border: 1px solid var(--tc);
  border-radius: 10px; padding: 0.6rem 0.75rem;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.5), 0 0 20px color-mix(in srgb, var(--tc) 35%, transparent);
}
.tip-head { display: flex; align-items: center; gap: 7px; }
.tip-dot {
  width: 9px; height: 9px; border-radius: 50%;
  background: var(--tc); box-shadow: 0 0 10px var(--tc);
  flex-shrink: 0;
}
.tip-name { color: #F1F5F9; font-weight: 700; font-size: 0.85rem; line-height: 1.3; }
.tip-type {
  color: var(--tc); font-size: 0.68rem; font-weight: 600;
  margin-top: 2px; letter-spacing: 0.02em;
}
.tip-desc {
  color: #94A3B8; font-size: 0.72rem; line-height: 1.45;
  margin-top: 5px;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.14s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
