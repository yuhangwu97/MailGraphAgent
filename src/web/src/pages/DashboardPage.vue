<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { mailsApi, graphApi, projectsApi, type MailStats, type ProjectItem, type ProjectReport, type ProjectSummary, type AnalysisHistoryItem } from '@/api'
import { useChatStore } from '@/stores/chat'
import SvgIcon from '@/components/SvgIcon.vue'
import KpiCards from '@/components/dashboard/KpiCards.vue'
import ProjectCard from '@/components/dashboard/ProjectCard.vue'
import ProjectReportModal from '@/components/dashboard/ProjectReportModal.vue'

const router = useRouter()
const chatStore = useChatStore()

const PAGE_SIZE = 20

const kpi = ref<MailStats>({ total: 0, done: 0, pending: 0, failed: 0, skipped: 0, ingested: 0, indexed: 0 })
const graphNodes = ref(0)
const projectCount = ref(0)
const contactCount = ref(0)
const projects = ref<ProjectItem[]>([])
const totalProjects = ref(0)
const currentPage = ref(1)
const search = ref('')
const loading = ref(true)

// Report modal state
const modalVisible = ref(false)
const modalProjectName = ref('')
const modalReport = ref<ProjectReport | null>(null)
const modalSummary = ref<ProjectSummary | null>(null)
const modalLoading = ref(false)
const modalHistory = ref<AnalysisHistoryItem[]>([])
const modalViewingHistoryId = ref<string | null>(null)
const modalProgress = ref<string[]>([])

const totalPages = computed(() => Math.max(1, Math.ceil(totalProjects.value / PAGE_SIZE)))

const PEOPLE_TYPES = ['person', 'contact', 'employee']
const etype = (e: any) => String(e?.type || '').toLowerCase()

onMounted(async () => {
  await loadDashboard()
})

async function loadDashboard() {
  loading.value = true
  try {
    const [stats, gStatus] = await Promise.all([
      mailsApi.stats(),
      graphApi.status(),
    ])
    kpi.value = stats
    graphNodes.value = gStatus.graph.entities || 0
    // 项目和联系人数量由 loadProjects 和实体统计补全
    projectCount.value = 0
    contactCount.value = 0

    await loadProjects(currentPage.value)
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function loadProjects(page: number) {
  try {
    const res = await projectsApi.list(page, PAGE_SIZE)
    projects.value = res.projects
    totalProjects.value = res.total
    projectCount.value = res.total  // 用 projects API 的 total，比从实体过滤准
    currentPage.value = res.page
  } catch (e) {
    console.error('Failed to load projects:', e)
  }
}

const pageLoading = ref(false)

async function goToPage(page: number) {
  if (page < 1 || page > totalPages.value || page === currentPage.value) return
  pageLoading.value = true
  try {
    await loadProjects(page)
  } finally {
    pageLoading.value = false
  }
}

const filtered = computed(() => {
  if (!search.value) return projects.value
  const q = search.value.toLowerCase()
  return projects.value.filter((p: ProjectItem) => p.name.toLowerCase().includes(q))
})

// ── Report modal ──

async function loadHistory(name: string) {
  try {
    const result = await projectsApi.getHistory(name)
    modalHistory.value = result.items
  } catch {
    modalHistory.value = []
  }
}

async function viewHistoryItem(name: string, item: AnalysisHistoryItem) {
  if (item.is_latest) {
    // Already viewing latest — just fetch from cache
    modalViewingHistoryId.value = null
    try {
      const cached = await projectsApi.getAnalysis(name)
      if (cached.report) {
        modalReport.value = cached.report
        modalSummary.value = cached.summary
      }
    } catch { /* ignore */ }
  } else {
    modalViewingHistoryId.value = item.id
    // Use the full report from history if available
    if (item.report) {
      modalReport.value = item.report
    } else if (item.summary) {
      // Fallback: summary-only data from older history entries
      modalReport.value = {
        overview: item.summary.overview || '',
        stage: item.summary.stage || '',
        contract: '',
        key_dates: item.summary.key_dates || '',
        core_people: (item.summary.core_people || []).join('；'),
        companies: '',
        recent_activity: '',
      }
    }
    modalSummary.value = item.summary
  }
  modalLoading.value = false
}

function formatHistoryTime(ts: number): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function handleViewReport(name: string) {
  modalProjectName.value = name
  modalVisible.value = true
  modalHistory.value = []
  modalViewingHistoryId.value = null
  modalProgress.value = []

  // Load history in background
  loadHistory(name)

  // Try cached first
  try {
    const cached = await projectsApi.getAnalysis(name)
    if (cached.cached && cached.report) {
      modalReport.value = cached.report
      modalSummary.value = cached.summary
      modalLoading.value = false
      return
    }
  } catch {
    // Not cached — proceed to generate
  }

  // Generate new analysis via SSE
  modalLoading.value = true
  modalReport.value = null
  modalSummary.value = null

  projectsApi.analyze(name, {
    onProgress(data: any) {
      if (data.msg) {
        modalProgress.value = [...modalProgress.value, data.msg]
      }
    },
    onComplete(data: any) {
      if (data.report) {
        modalReport.value = data.report as ProjectReport
      }
      if (data.summary) {
        modalSummary.value = data.summary as ProjectSummary
      }
      modalLoading.value = false
      // Refresh current page to get updated ai_summary on cards
      loadProjects(currentPage.value)
      // Refresh history
      loadHistory(name)
    },
    onError(msg: string) {
      console.error('Analysis failed:', msg)
      modalLoading.value = false
    },
  })
}

function handleCloseModal() {
  modalVisible.value = false
  modalReport.value = null
  modalSummary.value = null
  modalLoading.value = false
  modalHistory.value = []
  modalViewingHistoryId.value = null
  modalProgress.value = []
}

async function handleReanalyze(name: string) {
  // Force re-analysis: open modal + start generation
  modalProjectName.value = name
  modalVisible.value = true
  modalLoading.value = true
  modalReport.value = null
  modalSummary.value = null
  modalViewingHistoryId.value = null
  modalProgress.value = []

  projectsApi.analyze(name, {
    onProgress(data: any) {
      if (data.msg) {
        modalProgress.value = [...modalProgress.value, data.msg]
      }
    },
    onComplete(data: any) {
      if (data.report) {
        modalReport.value = data.report as ProjectReport
      }
      if (data.summary) {
        modalSummary.value = data.summary as ProjectSummary
      }
      modalLoading.value = false
      loadProjects(currentPage.value)
      loadHistory(name)
    },
    onError(msg: string) {
      console.error('Analysis failed:', msg)
      modalLoading.value = false
    },
  })
}

// ── Chat deep analysis ──

async function handleChatAnalyze(name: string) {
  const project = projects.value.find(p => p.name === name)
  const overview = project?.ai_summary?.overview || project?.description || ''

  const prompt = `请帮我深入分析项目「${name}」。\n\n项目概述：${overview}\n\n请从知识图谱中提取更多细节，包括邮件往来、合同金额、时间线、风险点等。`

  // Create a new conversation and navigate
  try {
    const { conversationsApi } = await import('@/api')
    const session = await conversationsApi.create(`分析: ${name}`)
    // Navigate with prefill prompt
    router.push({ path: '/chat', query: { prompt, session: session.id } })
  } catch {
    // Fallback: just navigate with prompt
    router.push({ path: '/chat', query: { prompt } })
  }
}

async function handleDelete(name: string) {
  if (!confirm(`确定要删除项目「${name}」吗？此操作将从知识图谱中移除该项目及其关联缓存，不可撤销。`)) return
  try {
    await projectsApi.delete(name)
    // Reload current page; if last item on last page, go back one page
    const newTotal = totalProjects.value - 1
    const maxPage = Math.max(1, Math.ceil(newTotal / PAGE_SIZE))
    const page = Math.min(currentPage.value, maxPage)
    await loadProjects(page)
  } catch (e: any) {
    console.error('Failed to delete project:', e)
    alert(`删除失败：${e.message || '未知错误'}`)
  }
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2>项目看板</h2>
        <p class="page-desc">基于邮件内容自动识别的项目、人员与组织关系</p>
      </div>
      <div class="head-actions">
        <div class="search-wrap" v-if="projects.length > 0">
          <SvgIcon name="search" :size="15" class="search-icon" />
          <input
            v-model="search"
            type="text"
            placeholder="搜索项目..."
            class="search-input-inline"
          />
        </div>
        <button class="btn btn-secondary btn-sm refresh-btn" :disabled="loading" @click="loadDashboard">
          🔄 {{ loading ? '刷新中…' : '界面刷新' }}
        </button>
      </div>
    </div>

    <KpiCards
      :total-mails="kpi.total"
      :done-mails="kpi.done"
      :graph-nodes="graphNodes"
      :projects="projectCount"
      :contacts="contactCount"
    />

    <!-- Loading skeleton -->
    <div v-if="loading" class="skeleton-grid">
      <div v-for="i in 6" :key="i" class="skeleton-card">
        <div class="sk-line sk-title"></div>
        <div class="sk-line sk-body"></div>
        <div class="sk-line sk-body short"></div>
        <div class="sk-line sk-meta"></div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else-if="!projects.length" class="empty-state">
      <div class="empty-icon">
        <SvgIcon name="inbox" :size="32" />
      </div>
      <h3>暂无项目数据</h3>
      <p>请先在「邮件工作台」拉取邮件并导入到知识图谱，系统将自动识别项目信息。</p>
    </div>

    <!-- Project grid + Pagination -->
    <template v-else>
      <div class="project-grid" :class="{ 'grid-loading': pageLoading }">
        <div v-if="pageLoading" class="grid-overlay">
          <span class="ml-spinner"></span>
        </div>
        <ProjectCard
          v-for="p in filtered"
          :key="p.name"
          :name="p.name"
          :description="p.description"
          :people="p.people"
          :companies="p.companies"
          :tasks="p.tasks"
          :events="p.events"
          :documents="p.documents"
          :systems="p.systems"
          :locations="p.locations"
          :other-neighbors="p.other_neighbors"
          :ai-summary="p.ai_summary"
          @view-report="handleViewReport"
          @chat-analyze="handleChatAnalyze"
          @reanalyze="handleReanalyze"
          @delete="handleDelete"
        />
      </div>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="pagination">
        <button
          class="page-btn"
          :disabled="currentPage <= 1"
          @click="goToPage(currentPage - 1)"
        >
          ← 上一页
        </button>
        <span class="page-info">
          {{ currentPage }} / {{ totalPages }}
          <span class="page-total">（共 {{ totalProjects }} 个项目）</span>
        </span>
        <button
          class="page-btn"
          :disabled="currentPage >= totalPages"
          @click="goToPage(currentPage + 1)"
        >
          下一页 →
        </button>
      </div>
    </template>

    <p v-if="!loading && projects.length > 0 && !filtered.length" class="text-muted" style="text-align:center;margin-top:2rem;">
      没有匹配「{{ search }}」的项目
    </p>

    <!-- Report Modal -->
    <ProjectReportModal
      :visible="modalVisible"
      :project-name="modalProjectName"
      :report="modalReport"
      :summary="modalSummary"
      :loading="modalLoading"
      :progress="modalProgress"
      :history="modalHistory"
      :viewing-history-id="modalViewingHistoryId"
      @close="handleCloseModal"
      @chat-analyze="(name: string) => { handleCloseModal(); handleChatAnalyze(name) }"
      @view-history="(item: AnalysisHistoryItem) => viewHistoryItem(modalProjectName, item)"
      @reanalyze="(name: string) => handleReanalyze(name)"
    />
  </div>
</template>

<style scoped>
/* Reuse existing styles, add pagination */
.page-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 1.25rem; gap: 1rem; flex-wrap: wrap;
}

.page-desc {
  color: var(--t3); font-size: 0.82rem; margin-top: 0.15rem;
}

.search-wrap {
  position: relative;
  flex-shrink: 0;
}

.head-actions {
  display: flex; align-items: center; gap: 0.6rem;
  flex-shrink: 0;
}
.refresh-btn { flex-shrink: 0; white-space: nowrap; }

.search-icon {
  position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  color: var(--t4); pointer-events: none;
}

.search-input-inline {
  padding-left: 2rem !important;
  max-width: 240px;
  font-size: 0.8rem !important;
  height: 34px;
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 0.85rem;
  position: relative;
}

.grid-loading {
  opacity: 0.5;
  pointer-events: none;
}

.grid-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1;
}

.ml-spinner {
  width: 28px;
  height: 28px;
  border: 3px solid var(--border);
  border-top-color: var(--p);
  border-radius: 50%;
  animation: ml-spin 0.7s linear infinite;
}

@keyframes ml-spin {
  to { transform: rotate(360deg); }
}

/* Pagination */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  margin-top: 1.5rem;
  padding-top: 1rem;
}

.page-btn {
  font-size: 0.8rem;
  padding: 0.35rem 0.85rem;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--t2);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}

.page-btn:hover:not(:disabled) {
  border-color: var(--p);
  color: var(--p);
  background: var(--p-bg);
}

.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.page-info {
  font-size: 0.82rem;
  color: var(--t2);
  font-weight: 520;
}

.page-total {
  font-weight: 400;
  color: var(--t4);
}

/* Skeleton loading */
.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 0.85rem;
}

.skeleton-card {
  background: var(--surface);
  border: 1px solid var(--border-light);
  border-radius: var(--r);
  padding: 1.15rem 1.25rem;
}

.sk-line {
  height: 12px; border-radius: 4px;
  background: var(--border-light);
  margin-bottom: 0.55rem;
  animation: shimmer 1.6s infinite;
}

.sk-title { width: 55%; height: 15px; }
.sk-body { width: 90%; }
.sk-body.short { width: 65%; }
.sk-meta { width: 40%; height: 10px; }

@keyframes shimmer {
  0% { opacity: 0.5; }
  50% { opacity: 1; }
  100% { opacity: 0.5; }
}

/* Empty state */
.empty-state {
  text-align: center; padding: 3rem 1.5rem;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r);
}

.empty-icon {
  width: 56px; height: 56px; border-radius: 14px;
  background: var(--surface-2); color: var(--t4);
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto 1rem;
}

.empty-state h3 {
  color: var(--t2); margin-bottom: 0.35rem;
}

.empty-state p {
  color: var(--t4); font-size: 0.82rem; max-width: 380px;
  margin: 0 auto; line-height: 1.5;
}
</style>
