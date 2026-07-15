import { ref, computed } from 'vue'

const BASE = '/api'

async function apiFetch(path: string, options?: RequestInit): Promise<any> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export interface JobRecord {
  job_id: string
  type: string        // scan | parse | reprocess | ingest
  source: string       // imap | file
  stage: string        // acquire | parse-body | parse-attachments | build-graph
  status: string       // queued | running | paused | completed | failed | interrupted | partial
  total: number
  done: number
  failed: number
  skipped: number
  att_failed: number
  cursor: string
  params: string       // JSON
  error: string
  summary: string
  created_at: string
  updated_at: string
  heartbeat_at: string
}

const jobs = ref<JobRecord[]>([])
const loading = ref(false)

export function useJobsStore() {
  const runningJobs = computed(() => jobs.value.filter(j => j.status === 'running'))
  const recentJobs = computed(() => jobs.value.slice(0, 10))

  async function fetchJobs(status?: string) {
    loading.value = true
    try {
      const params = status ? `?status=${status}` : ''
      const data = await apiFetch(`/api/jobs${params}`)
      jobs.value = data.jobs || []
    } catch (e) {
      console.error('Failed to fetch jobs:', e)
    } finally {
      loading.value = false
    }
  }

  async function pauseJob(jobId: string) {
    await apiFetch(`/api/jobs/${jobId}/pause`, { method: 'POST' })
    await fetchJobs()
  }

  async function resumeJob(jobId: string) {
    await apiFetch(`/api/jobs/${jobId}/resume`, { method: 'POST' })
    await fetchJobs()
  }

  async function retryFailed(jobId: string) {
    await apiFetch(`/api/jobs/${jobId}/retry-failed`, { method: 'POST' })
    await fetchJobs()
  }

  async function deleteJob(jobId: string) {
    await apiFetch(`/api/jobs/${jobId}`, { method: 'DELETE' })
    await fetchJobs()
  }

  function getJob(jobId: string): JobRecord | undefined {
    return jobs.value.find(j => j.job_id === jobId)
  }

  function progressPct(job: JobRecord): number {
    const total = Number(job.total) || 1
    const done = Number(job.done) || 0
    return Math.round((done / total) * 100)
  }

  return {
    jobs, loading, runningJobs, recentJobs,
    fetchJobs, pauseJob, resumeJob, retryFailed, deleteJob, getJob, progressPct,
  }
}
