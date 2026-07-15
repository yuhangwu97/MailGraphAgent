<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'

interface JobRecord {
  job_id: string
  type: string
  source: string
  stage: string
  status: string
  total: number
  done: number
  failed: number
  skipped: number
  att_failed: number
  summary: string
  error: string
}

const BASE = '/api'

async function apiFetch(path: string, options?: RequestInit): Promise<any> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

const jobList = ref<JobRecord[]>([])
const loading = ref(false)
const collapsed = ref(true) // 默认折叠

let timer: ReturnType<typeof setInterval> | null = null

async function fetchJobs() {
  try {
    const data = await apiFetch('/jobs')
    jobList.value = data.jobs || []
  } catch (e) {
    // silent
  }
}

onMounted(() => {
  fetchJobs()
  timer = setInterval(fetchJobs, 5000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})

function statusClass(status: string): string {
  return `jc-${status}` || ''
}

function statusLabel(status: string): string {
  const m: Record<string, string> = {
    running: '运行中', completed: '完成', failed: '失败',
    interrupted: '已中断', partial: '部分完成', paused: '已暂停', queued: '排队中',
  }
  return m[status] || status
}

const runningCount = computed(() => jobList.value.filter(j => j.status === 'running').length)
const interruptedCount = computed(() => jobList.value.filter(j => j.status === 'interrupted').length)
const hasJobs = computed(() => jobList.value.length > 0)

async function doPause(jobId: string) {
  await apiFetch(`/jobs/${jobId}/pause`, { method: 'POST' })
  fetchJobs()
}
async function doResume(jobId: string) {
  await apiFetch(`/jobs/${jobId}/resume`, { method: 'POST' })
  fetchJobs()
}
async function doDelete(jobId: string) {
  await apiFetch(`/jobs/${jobId}`, { method: 'DELETE' })
  fetchJobs()
}
</script>

<template>
  <div class="job-center">
    <div class="jc-header" @click="collapsed = !collapsed">
      <div class="jc-header-left">
        <span class="jc-icon">📋</span>
        <span>任务</span>
        <span v-if="runningCount" class="jc-badge jc-badge-run">{{ runningCount }} 运行中</span>
        <span v-if="interruptedCount" class="jc-badge jc-badge-warn">{{ interruptedCount }} 中断</span>
      </div>
      <span class="jc-toggle">{{ collapsed ? '▸' : '▾' }}</span>
    </div>

    <div v-if="!collapsed" class="jc-body">
      <div v-if="!hasJobs" class="jc-empty">暂无任务</div>

      <div v-for="job in jobList.slice(0, 5)" :key="job.job_id" :class="['jc-item', statusClass(job.status)]">
        <div class="jc-item-row">
          <span :class="['jc-dot', statusClass(job.status)]"></span>
          <span class="jc-item-type">{{ job.type === 'scan' ? '扫描' : job.type === 'parse' ? '解析' : job.type }}</span>
          <span class="jc-item-source">{{ job.source === 'imap' ? '📨' : '📂' }}</span>
          <span class="jc-item-status">{{ statusLabel(job.status) }}</span>
          <span v-if="job.status === 'running' && Number(job.total) > 0" class="jc-item-progress">{{ job.done }}/{{ job.total }}</span>
          <span v-if="job.failed > 0" class="jc-item-fail">⚠{{ job.failed }}</span>
        </div>
        <div class="jc-item-actions">
          <button v-if="job.status === 'running'" class="jc-btn-sm" @click.stop="doPause(job.job_id)">暂停</button>
          <button v-if="job.status === 'interrupted' || job.status === 'paused'" class="jc-btn-sm jc-btn-primary" @click.stop="doResume(job.job_id)">续跑</button>
          <button v-if="job.status !== 'running'" class="jc-btn-sm jc-btn-ghost" @click.stop="doDelete(job.job_id)">清除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.job-center {
  background: var(--bg-card, #fff);
  border-radius: 8px;
  overflow: hidden;
}
.jc-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 13px;
  user-select: none;
}
.jc-header:hover { background: var(--bg-hover, #f5f5f5); }
.jc-header-left { display: flex; align-items: center; gap: 6px; }
.jc-icon { font-size: 14px; }
.jc-toggle { color: #999; font-size: 10px; }
.jc-badge {
  font-size: 10px; padding: 1px 6px; border-radius: 8px;
}
.jc-badge-run { background: #e6f0ff; color: #4a90d9; }
.jc-badge-warn { background: #fff7e6; color: #faad14; }
.jc-body { padding: 0 8px 8px; }
.jc-empty { color: #999; font-size: 12px; padding: 8px 0; text-align: center; }
.jc-item {
  border: 1px solid #eee;
  border-radius: 4px;
  padding: 6px 8px;
  margin-bottom: 4px;
  font-size: 12px;
}
.jc-item-row {
  display: flex;
  align-items: center;
  gap: 5px;
}
.jc-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; background: #999;
}
.jc-dot.jc-running { background: #4a90d9; animation: pulse 1.5s infinite; }
.jc-dot.jc-completed { background: #52c41a; }
.jc-dot.jc-failed { background: #f5222d; }
.jc-dot.jc-interrupted { background: #faad14; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
.jc-item-type { font-weight: 500; }
.jc-item-source { font-size: 11px; }
.jc-item-status { color: #666; font-size: 11px; margin-left: auto; }
.jc-item-progress { color: #4a90d9; font-size: 11px; font-weight: 500; }
.jc-item-fail { color: #f5222d; font-size: 11px; }
.jc-item-actions {
  display: flex; gap: 4px; margin-top: 4px;
}
.jc-btn-sm {
  font-size: 10px; padding: 1px 6px; border: 1px solid #ddd;
  border-radius: 3px; background: #fff; cursor: pointer;
}
.jc-btn-primary { color: #4a90d9; border-color: #4a90d9; }
.jc-btn-ghost { color: #999; }
</style>
