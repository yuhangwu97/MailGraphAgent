<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useJobsStore, type JobRecord } from '@/stores/jobs'

const store = useJobsStore()
const expanded = ref<Set<string>>(new Set())

let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  store.fetchJobs()
  timer = setInterval(() => store.fetchJobs(), 5000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})

function toggleExpand(jobId: string) {
  const s = new Set(expanded.value)
  s.has(jobId) ? s.delete(jobId) : s.add(jobId)
  expanded.value = s
}

function stageLabel(stage: string): string {
  const m: Record<string, string> = {
    'acquire': '拉取表头',
    'parse-body': '解析正文',
    'parse-attachments': '解析附件',
    'build-graph': '建图中',
  }
  return m[stage] || stage
}

function statusClass(status: string): string {
  const m: Record<string, string> = {
    running: 'jc-running',
    completed: 'jc-done',
    failed: 'jc-failed',
    interrupted: 'jc-interrupted',
    partial: 'jc-partial',
    paused: 'jc-paused',
    queued: 'jc-queued',
  }
  return m[status] || ''
}

function statusLabel(status: string): string {
  const m: Record<string, string> = {
    running: '运行中',
    completed: '完成',
    failed: '失败',
    interrupted: '已中断',
    partial: '部分完成',
    paused: '已暂停',
    queued: '排队中',
  }
  return m[status] || status
}

const hasJobs = computed(() => store.jobs.length > 0)
</script>

<template>
  <div class="job-center">
    <div class="jc-header">
      <h3>任务中心</h3>
      <span v-if="store.jobs.length" class="jc-count">{{ store.jobs.length }}</span>
    </div>

    <div v-if="!hasJobs && !store.loading" class="jc-empty">
      暂无任务
    </div>

    <div v-for="job in store.recentJobs" :key="job.job_id" :class="['jc-card', statusClass(job.status)]">
      <div class="jc-card-header" @click="toggleExpand(job.job_id)">
        <div class="jc-card-left">
          <span :class="['jc-dot', statusClass(job.status)]"></span>
          <span class="jc-type">{{ job.type === 'scan' ? '扫描' : job.type === 'parse' ? '解析' : job.type }}</span>
          <span class="jc-source">{{ job.source === 'imap' ? '📨' : '📂' }} {{ job.source === 'imap' ? 'IMAP' : '文件' }}</span>
          <span class="jc-status">{{ statusLabel(job.status) }}</span>
        </div>
        <div class="jc-card-right">
          <span v-if="job.status === 'running'" class="jc-progress-text">
            {{ job.done }}/{{ job.total }}
          </span>
          <span class="jc-expand">{{ expanded.has(job.job_id) ? '▾' : '▸' }}</span>
        </div>
      </div>

      <!-- Progress bar (running jobs) -->
      <div v-if="job.status === 'running' && Number(job.total) > 0" class="jc-progress">
        <div class="jc-progress-bar" :style="{ width: store.progressPct(job) + '%' }"></div>
      </div>

      <!-- Expanded detail -->
      <div v-if="expanded.has(job.job_id)" class="jc-detail">
        <div class="jc-detail-row">
          <span>阶段</span>
          <span>{{ stageLabel(job.stage) || '-' }}</span>
        </div>
        <div class="jc-detail-row">
          <span>进度</span>
          <span>{{ job.done }}/{{ job.total }} 完成{{ job.failed > 0 ? `（${job.failed} 失败）` : '' }}</span>
        </div>
        <div v-if="job.att_failed > 0" class="jc-detail-row jc-warn">
          <span>附件</span>
          <span>⚠ {{ job.att_failed }} 个附件失败/降级</span>
        </div>
        <div v-if="job.summary" class="jc-detail-row">
          <span>摘要</span>
          <span>{{ job.summary }}</span>
        </div>
        <div v-if="job.error" class="jc-detail-row jc-error">
          <span>错误</span>
          <span>{{ job.error }}</span>
        </div>

        <!-- Actions -->
        <div class="jc-actions">
          <button v-if="job.status === 'running'" class="jc-btn" @click.stop="store.pauseJob(job.job_id)">⏸ 暂停</button>
          <button v-if="job.status === 'interrupted' || job.status === 'paused'" class="jc-btn jc-btn-primary" @click.stop="store.resumeJob(job.job_id)">▶ 续跑</button>
          <button v-if="Number(job.failed) > 0" class="jc-btn jc-btn-warn" @click.stop="store.retryFailed(job.job_id)">↻ 重跑失败</button>
          <button v-if="job.status !== 'running'" class="jc-btn jc-btn-ghost" @click.stop="store.deleteJob(job.job_id)">✕ 清除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.job-center {
  background: var(--bg-card, #fff);
  border-radius: 8px;
  padding: 12px;
  max-height: 400px;
  overflow-y: auto;
}
.jc-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.jc-header h3 { margin: 0; font-size: 14px; }
.jc-count {
  background: var(--brand, #4a90d9);
  color: #fff;
  border-radius: 10px;
  padding: 1px 8px;
  font-size: 11px;
}
.jc-empty { color: var(--text-muted, #999); font-size: 13px; padding: 12px 0; text-align: center; }
.jc-card {
  border: 1px solid var(--border, #eee);
  border-radius: 6px;
  margin-bottom: 6px;
  overflow: hidden;
}
.jc-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  cursor: pointer;
  font-size: 13px;
}
.jc-card-left { display: flex; align-items: center; gap: 6px; }
.jc-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  background: var(--text-muted, #999);
}
.jc-dot.jc-running { background: #4a90d9; animation: pulse 1.5s infinite; }
.jc-dot.jc-done { background: #52c41a; }
.jc-dot.jc-failed { background: #f5222d; }
.jc-dot.jc-interrupted { background: #faad14; }
.jc-dot.jc-partial { background: #faad14; }
.jc-dot.jc-paused { background: #999; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
.jc-source { color: var(--text-muted, #999); }
.jc-progress { height: 3px; background: var(--border, #eee); }
.jc-progress-bar { height: 100%; background: #4a90d9; transition: width 0.3s; }
.jc-detail { padding: 0 10px 10px; font-size: 12px; }
.jc-detail-row { display: flex; justify-content: space-between; padding: 3px 0; }
.jc-warn { color: #faad14; }
.jc-error { color: #f5222d; }
.jc-actions { display: flex; gap: 6px; margin-top: 8px; }
.jc-btn {
  font-size: 11px; padding: 3px 8px; border: 1px solid var(--border, #ddd);
  border-radius: 4px; background: var(--bg, #fff); cursor: pointer;
}
.jc-btn-primary { color: #4a90d9; border-color: #4a90d9; }
.jc-btn-warn { color: #faad14; border-color: #faad14; }
.jc-btn-ghost { color: var(--text-muted, #999); }
.jc-expand { color: var(--text-muted, #999); font-size: 11px; }
</style>
