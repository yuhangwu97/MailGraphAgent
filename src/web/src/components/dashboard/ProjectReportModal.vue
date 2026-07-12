<script setup lang="ts">
import { computed } from 'vue'
import type { ProjectReport, ProjectSummary } from '@/api'

const props = defineProps<{
  visible: boolean
  projectName: string
  report: ProjectReport | null
  summary: ProjectSummary | null
  loading: boolean
}>()

defineEmits<{
  close: []
  'chat-analyze': [name: string]
}>()

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
            <button class="report-close" @click="$emit('close')">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>

          <!-- Body -->
          <div class="report-body">
            <!-- Loading state -->
            <div v-if="loading" class="report-loading">
              <div class="loading-spinner"></div>
              <p>AI 正在分析项目「{{ projectName }}」…</p>
              <p class="loading-hint">正在从知识图谱中提取邮件、人员、合同等关键信息</p>
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
            <p class="footer-hint">不满意这份报告？可以在 Chat 中进行更深入的对话分析。</p>
            <button class="footer-chat-btn" @click="$emit('chat-analyze', projectName)">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
              在 Chat 中深度分析 →
            </button>
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

/* Transitions */
.report-modal-enter-active { transition: opacity 0.2s ease; }
.report-modal-leave-active { transition: opacity 0.15s ease; }
.report-modal-enter-from,
.report-modal-leave-to { opacity: 0; }
.report-modal-enter-from .report-modal { transform: scale(0.96); transition: transform 0.2s ease; }
.report-modal-leave-to .report-modal { transform: scale(0.96); transition: transform 0.15s ease; }
</style>
