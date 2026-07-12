<script setup lang="ts">
import { ref, watch } from 'vue'
import { mailsApi } from '@/api'

const props = defineProps<{
  open: boolean
  mode: 'fetch' | 'import' | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'refresh'): void
}>()

// ── IMAP fetch ──
const limit = ref(20)
const fetching = ref(false)
const fetchLogs = ref<string[]>([])

async function handleFetch() {
  fetching.value = true
  fetchLogs.value = []
  mailsApi.fetch('INBOX', limit.value, {
    onProgress(d: any) { fetchLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete() {
      fetchLogs.value.push('✅ 拉取完成')
      fetching.value = false
      emit('refresh')
    },
    onError(m: string) {
      fetchLogs.value.push('❌ 错误: ' + m)
      fetching.value = false
    },
  })
}

// ── File import ──
const picking = ref(false)
const pickErr = ref('')
const pickPaths = ref<string[]>([])

async function pickFiles() {
  picking.value = true
  pickErr.value = ''
  try {
    const r = await mailsApi.pick('files')
    if (!r.canceled && r.paths.length) pickPaths.value = r.paths
  } catch (e: any) {
    pickErr.value = e.message || String(e)
  } finally {
    picking.value = false
  }
}

const indexing = ref(false)
const indexLogs = ref<string[]>([])

function doIndex() {
  if (!pickPaths.value.length) return
  indexing.value = true
  indexLogs.value = []
  mailsApi.indexFiles(pickPaths.value, {
    onProgress(d: any) { indexLogs.value.push(d.msg || JSON.stringify(d)) },
    onComplete(d: any) {
      indexLogs.value.push('✅ 扫描完成: ' + JSON.stringify(d))
      indexing.value = false
      pickPaths.value = []
      emit('refresh')
    },
    onError(m: string) {
      indexLogs.value.push('❌ 错误: ' + m)
      indexing.value = false
    },
  })
}

// Reset state when drawer opens
watch(() => props.open, (now) => {
  if (now) {
    fetchLogs.value = []
    indexLogs.value = []
    pickPaths.value = []
    pickErr.value = ''
  }
})
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="open" class="drawer-overlay" @click.self="emit('close')">
        <div class="drawer-panel">
          <!-- Header -->
          <div class="drawer-head">
            <h3>{{ mode === 'fetch' ? '📨 拉取邮件' : '📂 导入文件' }}</h3>
            <button class="drawer-close" @click="emit('close')">✕</button>
          </div>

          <div class="drawer-body">
            <!-- IMAP Fetch -->
            <template v-if="mode === 'fetch'">
              <div class="dr-section">
                <label class="dr-label">拉取数量</label>
                <div class="dr-row">
                  <input v-model.number="limit" type="number" min="1" max="200" class="dr-num" />
                  <button
                    class="btn btn-primary dr-action"
                    :disabled="fetching"
                    @click="handleFetch"
                  >
                    {{ fetching ? '拉取中…' : '开始拉取' }}
                  </button>
                </div>
              </div>

              <div v-if="fetchLogs.length" class="dr-logs">
                <div v-for="(l, i) in fetchLogs" :key="'f'+i" class="dr-log-line">{{ l }}</div>
              </div>
            </template>

            <!-- File Import -->
            <template v-if="mode === 'import'">
              <div class="dr-section">
                <label class="dr-label">选择文件</label>
                <p class="dr-hint">支持 .eml / .msg / .pst / .ost 格式</p>
                <button class="btn btn-secondary dr-pick-btn" :disabled="picking" @click="pickFiles">
                  {{ picking ? '选择中…' : '📄 选择邮件文件…' }}
                </button>
                <div v-if="pickErr" class="dr-err">{{ pickErr }}</div>
              </div>

              <div v-if="pickPaths.length" class="dr-section">
                <label class="dr-label">已选文件 ({{ pickPaths.length }})</label>
                <div class="dr-file-list">
                  <div v-for="(p, i) in pickPaths" :key="i" class="dr-file-item">
                    {{ p.split('/').pop() || p }}
                  </div>
                </div>
                <button
                  class="btn btn-primary dr-action"
                  :disabled="indexing"
                  @click="doIndex"
                >
                  {{ indexing ? '导入中…' : `导入 ${pickPaths.length} 个文件` }}
                </button>
              </div>

              <div v-if="indexLogs.length" class="dr-logs">
                <div v-for="(l, i) in indexLogs" :key="'ix'+i" class="dr-log-line">{{ l }}</div>
              </div>
            </template>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* ── Overlay ── */
.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 17, 15, 0.3);
  z-index: 200;
  display: flex;
  justify-content: flex-end;
}

/* ── Panel ── */
.drawer-panel {
  width: 420px;
  max-width: 90vw;
  height: 100%;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  box-shadow: var(--sh-lg);
}

/* ── Transitions ── */
.drawer-enter-active,
.drawer-leave-active {
  transition: opacity var(--dur) var(--ease);
}

.drawer-enter-active .drawer-panel,
.drawer-leave-active .drawer-panel {
  transition: transform var(--dur) var(--ease-out);
}

.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}

.drawer-enter-from .drawer-panel {
  transform: translateX(100%);
}

.drawer-leave-to .drawer-panel {
  transform: translateX(100%);
}

/* ── Head ── */
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.drawer-head h3 {
  margin: 0;
  font-size: 1rem;
  font-weight: 650;
  color: var(--t1);
}

.drawer-close {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--r-sm);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--t3);
  cursor: pointer;
  font-size: 0.85rem;
  transition: all var(--dur-fast) var(--ease);
}

.drawer-close:hover {
  background: var(--surface-2);
  color: var(--t1);
}

/* ── Body ── */
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 1rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.dr-section {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.dr-label {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--t3);
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.dr-select {
  width: 100%;
  padding: 0.45rem 0.6rem;
  font-size: 0.82rem;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  font-family: inherit;
}

.dr-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.dr-num {
  width: 80px;
  padding: 0.45rem 0.6rem;
  font-size: 0.82rem;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  font-family: inherit;
}

.dr-action {
  width: 100%;
  margin-top: 0.25rem;
}

.dr-hint {
  font-size: 0.7rem;
  color: var(--t4);
  margin: 0;
}

.dr-pick-btn {
  margin-top: 0.25rem;
}

.dr-err {
  font-size: 0.72rem;
  color: var(--red);
  padding: 0.3rem 0.5rem;
  background: var(--red-bg);
  border-radius: var(--r-sm);
}

.dr-file-list {
  max-height: 160px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 0.4rem;
  background: var(--surface-2);
}

.dr-file-item {
  font-size: 0.73rem;
  color: var(--t2);
  padding: 0.2rem 0.35rem;
  border-radius: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dr-file-item:hover {
  background: var(--surface);
}

/* ── Logs ── */
.dr-logs {
  background: #1a1a1a;
  color: #c0c0c0;
  font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 0.66rem;
  padding: 0.5rem 0.7rem;
  border-radius: var(--r-sm);
  max-height: 300px;
  overflow-y: auto;
  line-height: 1.6;
}

.dr-log-line {
  word-break: break-all;
}
</style>
