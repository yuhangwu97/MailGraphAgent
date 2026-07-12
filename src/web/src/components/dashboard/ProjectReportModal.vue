<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ProjectReport, ProjectSummary, AnalysisHistoryItem } from '@/api'

const props = defineProps<{
  visible: boolean
  projectName: string
  report: ProjectReport | null
  summary: ProjectSummary | null
  loading: boolean
  progress: string[]
  history: AnalysisHistoryItem[]
  viewingHistoryId: string | null
}>()

const emit = defineEmits<{
  close: []
  'chat-analyze': [name: string]
  'view-history': [item: AnalysisHistoryItem]
  reanalyze: [name: string]
}>()

const showHistoryDropdown = ref(false)

// Reset dropdown when modal opens/closes
watch(() => props.visible, (v) => {
  if (!v) showHistoryDropdown.value = false
})

function formatContent(val: string): string {
  if (!val) return ''
  val = val.trim()
  // Try parse as JSON — if array/object, format to readable text
  if (val.startsWith('[') || val.startsWith('{')) {
    try {
      const parsed = JSON.parse(val)
      if (Array.isArray(parsed)) {
        return parsed.map((item: any) => {
          if (typeof item === 'object' && item.name) {
            return item.role ? item.name + '（' + item.role + '）' : item.name
          }
          return String(item)
        }).join('\n')
      }
    } catch { /* not valid JSON, display as-is */ }
  }
  return val
}

function formatTime(ts: number): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const sections = computed(() => {
  if (!props.report) return []
  return [
    { icon: '📌', label: '一句话概述', content: formatContent(props.report.overview) },
    { icon: '📈', label: '项目阶段/状态', content: formatContent(props.report.stage) },
    { icon: '💰', label: '合同与金额', content: formatContent(props.report.contract) },
    { icon: '📅', label: '关键时间节点', content: formatContent(props.report.key_dates) },
    { icon: '👥', label: '核心人员', content: formatContent(props.report.core_people) },
    { icon: '🏢', label: '相关公司/组织', content: formatContent(props.report.companies) },
    { icon: '📝', label: '近期关键动态', content: formatContent(props.report.recent_activity) },
  ].filter(s => s.content)
})

const hasHistory = computed(() => props.history.length > 1)

function selectHistory(item: AnalysisHistoryItem) {
  showHistoryDropdown.value = false
  emit('view-history', item)
}

function isViewingHistoryItem(item: AnalysisHistoryItem): boolean {
  if (item.is_latest) return !props.viewingHistoryId
  return props.viewingHistoryId === item.id
}
</script>

<template>
  <Teleport to="body">
    <Transition name="report-modal">
      <div v-if="visible" class="report-overlay" @click.self="$emit('close')">
        <div class="report-modal">
          <!-- Header -->
          <div class="report-header">
            <div class="report-title">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
              </svg>
              <span>{{ projectName }} — AI 分析报告</span>
            </div>
            <div class="report-header-right">
              <!-- History selector -->
              <div v-if="hasHistory && !loading" class="history-select-wrap">
                <button class="history-trigger" @click="showHistoryDropdown = !showHistoryDropdown">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
                  </svg>
                  <span>{{ viewingHistoryId ? '历史报告' : '最新报告' }}</span>
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
                </button>
                <Transition name="drop">
                  <div v-if="showHistoryDropdown" class="history-dropdown" @click.self="showHistoryDropdown = false">
                    <button
                      v-for="item in history"
                      :key="item.id"
                      class="history-item"
                      :class="{ active: isViewingHistoryItem(item) }"
                      @click="selectHistory(item)"
                    >
                      <span class="history-item-label">
                        {{ item.is_latest ? '🆕 最新' : '📋 历史' }}
                      </span>
                      <span class="history-item-time">{{ formatTime(item.generated_at) }}</span>
                    </button>
                  </div>
                </Transition>
              </div>

              <button class="report-close" @click="$emit('close')">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            </div>
          </div>

          <!-- Body -->
          <div class="report-body">
            <!-- Loading state -->
            <div v-if="loading" class="report-loading">
              <div class="loading-spinner"></div>
              <p>AI 正在分析项目「{{ projectName }}」…</p>
              <!-- Progress steps -->
              <div v-if="progress.length" class="analysis-progress">
                <div v-for="(p, i) in progress" :key="i" class="analysis-step">
                  <span class="step-dot" :class="{ done: i < progress.length - 1 }"></span>
                  <span class="step-text">{{ p }}</span>
                  <span v-if="i === progress.length - 1" class="step-pulse"></span>
                </div>
              </div>
              <p v-else class="loading-hint">正在从知识图谱中提取邮件、人员、合同等关键信息</p>
            </div>

            <!-- Report content -->
            <div v-else-if="report">
              <div v-for="s in sections" :key="s.label" class="report-section">
                <div class="report-section-title">
                  <span class="rs-icon">{{ s.icon }}</span>
                  <span>{{ s.label }}</span>
                </div>
                <p class="report-section-content">{{ s.content }}</p>
              </div>

              <!-- Empty state if no sections -->
              <div v-if="!sections.length" class="report-empty">
                报告内容为空，请重新生成。
              </div>
            </div>
          </div>

          <!-- Footer -->
          <div class="report-footer" v-if="!loading && report">
            <p class="footer-hint">
              <template v-if="viewingHistoryId">正在查看历史报告（{{ formatTime(Number(viewingHistoryId)) }}）</template>
              <template v-else-if="hasHistory">已生成 {{ history.length }} 次报告</template>
              <template v-else>不满意这份报告？可以重新生成。</template>
            </p>
            <div class="footer-btns">
              <button class="footer-reanalyze-btn" @click="$emit('reanalyze', projectName)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                </svg>
                重新分析
              </button>
              <button class="footer-chat-btn" @click="$emit('chat-analyze', projectName)">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
                Chat 分析 →
              </button>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Overlay */
.report-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

/* Modal */
.report-modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  box-shadow: var(--sh-lg);
  width: 100%;
  max-width: 680px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Header */
.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.report-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.95rem;
  font-weight: 650;
  color: var(--t1);
}
.report-title svg { opacity: 0.7; color: var(--p); }

.report-header-right {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

/* History selector */
.history-select-wrap {
  position: relative;
}

.history-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.73rem;
  font-weight: 520;
  padding: 0.3rem 0.6rem;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--t3);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
  white-space: nowrap;
}
.history-trigger:hover {
  border-color: var(--p);
  color: var(--p);
}

.history-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  min-width: 220px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--sh-lg);
  overflow: hidden;
  z-index: 10;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
  padding: 0.5rem 0.75rem;
  border: none;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
  font-size: 0.78rem;
  color: var(--t2);
  transition: background 0.1s;
}
.history-item:hover {
  background: var(--surface-2);
}
.history-item.active {
  background: var(--p-bg);
  color: var(--p);
}

.history-item-label {
  font-weight: 520;
}

.history-item-time {
  font-size: 0.7rem;
  color: var(--t4);
}
.history-item.active .history-item-time {
  color: var(--p);
}

.report-close {
  width: 32px; height: 32px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--t3);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.report-close:hover { background: var(--border); color: var(--t1); }

/* Body */
.report-body {
  flex: 1;
  overflow-y: auto;
  padding: 1.25rem;
}

/* Loading */
.report-loading {
  text-align: center;
  padding: 2.5rem 0;
}

.loading-spinner {
  width: 36px; height: 36px;
  border: 3px solid var(--border-light);
  border-top-color: var(--p);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 1rem;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.report-loading p {
  color: var(--t2);
  font-size: 0.88rem;
  margin: 0;
}

.loading-hint {
  color: var(--t4) !important;
  font-size: 0.78rem !important;
  margin-top: 0.35rem !important;
}

/* Progress steps during analysis */
.analysis-progress {
  margin-top: 0.75rem;
  text-align: left;
  display: inline-block;
}

.analysis-step {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.78rem;
  color: var(--t3);
  padding: 0.15rem 0;
}

.analysis-step .step-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--border);
  transition: all 0.3s;
}
.analysis-step .step-dot.done {
  background: var(--p);
  box-shadow: 0 0 6px color-mix(in srgb, var(--p) 40%, transparent);
}

.analysis-step .step-pulse {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--p);
  box-shadow: 0 0 0 rgba(0,0,0,0);
  animation: pulse 1.2s ease-in-out infinite;
  flex-shrink: 0;
}

@keyframes pulse {
  0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--p) 60%, transparent); }
  50% { box-shadow: 0 0 0 6px color-mix(in srgb, var(--p) 0%, transparent); }
  100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--p) 0%, transparent); }
}

/* Sections */
.report-section {
  margin-bottom: 1.1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border-light);
}
.report-section:last-child { border-bottom: none; margin-bottom: 0; }

.report-section-title {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.84rem;
  font-weight: 650;
  color: var(--t1);
  margin-bottom: 0.4rem;
}

.rs-icon { font-size: 0.85rem; }

.report-section-content {
  font-size: 0.84rem;
  color: var(--t2);
  line-height: 1.65;
  margin: 0;
  white-space: pre-wrap;
}

.report-empty {
  text-align: center;
  color: var(--t4);
  padding: 2rem 0;
}

/* Footer */
.report-footer {
  padding: 0.9rem 1.25rem;
  border-top: 1px solid var(--border);
  background: var(--surface-2);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.footer-hint {
  font-size: 0.76rem;
  color: var(--t4);
  margin: 0;
}

.footer-chat-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 0.8rem;
  font-weight: 550;
  padding: 0.4rem 0.9rem;
  border-radius: 8px;
  border: 1px solid var(--p);
  background: var(--p-bg);
  color: var(--p);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
}

.footer-chat-btn:hover {
  background: var(--p);
  color: #fff;
}

.footer-btns {
  display: flex;
  gap: 0.4rem;
  flex-shrink: 0;
}

.footer-reanalyze-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.8rem;
  font-weight: 520;
  padding: 0.4rem 0.9rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--t3);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
}
.footer-reanalyze-btn:hover {
  border-color: var(--p);
  color: var(--p);
  background: var(--p-bg);
}

/* Transitions */
.report-modal-enter-active { transition: opacity 0.2s ease; }
.report-modal-leave-active { transition: opacity 0.15s ease; }
.report-modal-enter-from,
.report-modal-leave-to { opacity: 0; }
.report-modal-enter-from .report-modal { transform: scale(0.96); transition: transform 0.2s ease; }
.report-modal-leave-to .report-modal { transform: scale(0.96); transition: transform 0.15s ease; }

/* Dropdown transition */
.drop-enter-active { transition: all 0.15s ease; }
.drop-leave-active { transition: all 0.1s ease; }
.drop-enter-from, .drop-leave-to { opacity: 0; transform: translateY(-4px); }
</style>
