<script setup lang="ts">
import { onMounted, ref, computed, nextTick, watch } from 'vue'
import { useChatStore } from '@/stores/chat'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import BrainFab from '@/components/layout/BrainFab.vue'

const chatStore = useChatStore()
const messagesEl = ref<HTMLElement | null>(null)
const chatInputRef = ref<InstanceType<typeof ChatInput> | null>(null)
const editingTitle = ref<string | null>(null)
const titleInput = ref<HTMLInputElement | null>(null)
const sessionPillsEl = ref<HTMLElement | null>(null)

const hasMessages = computed(() => chatStore.messages.length > 0)
const sessionCount = computed(() => chatStore.sessions.length)

onMounted(async () => {
  await chatStore.fetchSessions()
  await chatStore.ensureSession()
  if (chatStore.activeSessionId) {
    await chatStore.loadMessages(chatStore.activeSessionId)
  }
  await chatStore.loadMemory()
})

// Auto-scroll
watch(() => chatStore.messages.length, scrollDown)
watch(() => chatStore.streamAnswer, scrollDown)

async function scrollDown() {
  await nextTick()
  messagesEl.value?.scrollTo({ top: messagesEl.value.scrollHeight, behavior: 'smooth' })
}

// Scroll active session pill into view
watch(() => chatStore.activeSessionId, async () => {
  await nextTick()
  const active = sessionPillsEl.value?.querySelector('.session-pill.active') as HTMLElement | null
  active?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
})

// Session title editing
function startRename() {
  editingTitle.value = chatStore.activeSession?.title || ''
  nextTick(() => titleInput.value?.focus())
}

async function commitRename() {
  if (editingTitle.value && chatStore.activeSessionId) {
    const { conversationsApi } = await import('@/api')
    await conversationsApi.rename(chatStore.activeSessionId, editingTitle.value)
    await chatStore.fetchSessions()
  }
  editingTitle.value = null
}

function handleSuggest(text: string) {
  chatInputRef.value?.setText(text)
}

async function handleSend(question: string) {
  await chatStore.sendMessage(question)
}
</script>

<template>
  <div class="chat-page">
    <!-- ── Header ── -->
    <header class="chat-header">
      <div class="header-top">
        <div class="header-left">
          <!-- Inline title editor -->
          <div v-if="editingTitle !== null" class="title-edit-wrap">
            <input
              ref="titleInput"
              v-model="editingTitle"
              class="title-input"
              placeholder="会话名称"
              @keydown.enter="commitRename()"
              @blur="commitRename()"
            />
          </div>
          <button v-else class="chat-title-btn" @click="startRename()" :title="chatStore.activeSession?.title || '新对话'">
            <span class="chat-title-text">{{ chatStore.activeSession?.title || '新对话' }}</span>
            <svg class="title-edit-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
            </svg>
          </button>
          <span v-if="chatStore.memory?.summary" class="memory-badge" :title="chatStore.memory.summary">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 18h6"/><path d="M10 22h4"/><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"/>
            </svg>
            有记忆
          </span>
        </div>
        <div class="header-actions">
          <button class="header-btn" @click="chatStore.createSession()" title="新对话">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
          </button>
          <button
            v-if="sessionCount > 1"
            class="header-btn header-btn-danger"
            @click="chatStore.deleteSession(chatStore.activeSessionId!)"
            title="删除对话"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
        </div>
      </div>

      <!-- Session pills -->
      <div class="session-pills" ref="sessionPillsEl">
        <button
          v-for="s in chatStore.sessions"
          :key="s.id"
          class="session-pill"
          :class="{ active: s.id === chatStore.activeSessionId }"
          @click="chatStore.switchSession(s.id)"
        >
          <span class="pill-title">{{ s.title || '新对话' }}</span>
          <span class="pill-count" :class="{ 'pill-count-active': s.id === chatStore.activeSessionId }">
            {{ s.message_count }}
          </span>
        </button>
      </div>
    </header>

    <!-- ── Messages area ── -->
    <div class="messages-area" ref="messagesEl">
      <!-- Empty state (no messages + not streaming) -->
      <div v-if="!hasMessages && !chatStore.streaming" class="empty-state">
        <div class="empty-content">
          <div class="empty-icon">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
              <polyline points="22,6 12,13 2,6"/>
            </svg>
          </div>
          <h3 class="empty-title">AI 邮件助手</h3>
          <p class="empty-desc">基于知识图谱的智能邮件分析。可以查询项目进展、合同金额、人员关系、风险预警等。</p>
          <div class="empty-suggestions">
            <button class="suggest-chip" @click="handleSuggest('最近邮件中提到了哪些项目和合同？')">
              <span class="chip-emoji">📋</span>
              项目和合同一览
            </button>
            <button class="suggest-chip" @click="handleSuggest('各项目的进展如何？有哪些风险？')">
              <span class="chip-emoji">⚡</span>
              项目进展与风险
            </button>
            <button class="suggest-chip" @click="handleSuggest('邮件中涉及哪些公司和联系人？')">
              <span class="chip-emoji">👥</span>
              公司联系人
            </button>
            <button class="suggest-chip" @click="handleSuggest('最近有哪些待办事项和截止日期？')">
              <span class="chip-emoji">📅</span>
              待办与截止日
            </button>
          </div>
        </div>
      </div>

      <!-- Message list -->
      <div v-if="hasMessages" class="messages-list">
        <ChatMessage
          v-for="msg in chatStore.messages"
          :key="msg.id"
          :message="msg"
          :result="msg.result ?? null"
        />

        <!-- Streaming message -->
        <ChatMessage
          v-if="chatStore.streaming"
          :message="{
            id: 'stream',
            role: 'assistant',
            content: chatStore.streamAnswer,
            created_at: Date.now() / 1000,
          }"
          :streaming="true"
          :result="chatStore.streamResult"
          :progress="chatStore.streamProgress"
        />
      </div>
    </div>

    <!-- ── Input area (sticky bottom) ── -->
    <div class="input-area">
      <div class="input-inner">
        <ChatInput ref="chatInputRef" :disabled="chatStore.streaming" @send="handleSend" />
        <div v-if="hasMessages || chatStore.streaming" class="input-hint">
          <span>基于 {{ chatStore.messages.length }} 条消息的上下文</span>
        </div>
      </div>
    </div>

    <!-- Floating brain -->
    <BrainFab />
  </div>
</template>

<style scoped>
/* ═══════════════════════════════════════════
   ChatPage — Product-grade layout
   ═══════════════════════════════════════════ */

.chat-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-h));
  background: var(--bg);
  position: relative;
}

/* ── Header ── */

.chat-header {
  flex-shrink: 0;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: var(--space-4) var(--space-5) var(--space-3);
  z-index: 10;
}

.header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-3);
}

.header-left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  flex: 1;
}

/* ── Title button (clickable) ── */

.chat-title-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  margin: -0.2rem -0.4rem;
  border-radius: var(--r-sm);
  transition: background var(--dur-fast) var(--ease);
  max-width: 320px;
  font-family: inherit;
}

.chat-title-btn:hover {
  background: var(--surface-2);
}

.chat-title-text {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--t1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  user-select: none;
}

.title-edit-icon {
  flex-shrink: 0;
  color: var(--t4);
  opacity: 0;
  transition: opacity var(--dur) var(--ease);
}

.chat-title-btn:hover .title-edit-icon {
  opacity: 1;
}

/* ── Title input (editing) ── */

.title-edit-wrap {
  max-width: 320px;
}

.title-input {
  font-size: var(--text-md);
  font-weight: 600;
  padding: 0.25rem 0.5rem;
  border: 1.5px solid var(--p);
  border-radius: var(--r-sm);
  outline: none;
  background: var(--surface);
  color: var(--t1);
  width: 100%;
  font-family: inherit;
}

/* ── Memory badge ── */

.memory-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--p-text);
  background: var(--p-bg);
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  cursor: help;
  flex-shrink: 0;
  white-space: nowrap;
}

.memory-badge svg {
  opacity: 0.7;
}

/* ── Header action buttons ── */

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
}

.header-btn {
  width: 32px;
  height: 32px;
  border-radius: var(--r);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--t4);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease);
}

.header-btn:hover {
  background: var(--surface-2);
  border-color: var(--p);
  color: var(--p);
}

.header-btn-danger:hover {
  border-color: var(--red);
  color: var(--red);
  background: var(--red-bg);
}

/* ── Session pills ── */

.session-pills {
  display: flex;
  gap: var(--space-2);
  overflow-x: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
  padding-bottom: 2px;
}

.session-pills::-webkit-scrollbar {
  display: none;
}

.session-pill {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0.35rem 0.85rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--t3);
  font-size: var(--text-xs);
  font-weight: 500;
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease);
  white-space: nowrap;
  font-family: inherit;
  user-select: none;
}

.session-pill:hover {
  border-color: var(--p);
  color: var(--p);
  background: var(--p-bg);
}

.session-pill.active {
  background: var(--p);
  border-color: var(--p);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 1px 6px rgba(31, 111, 92, 0.25);
}

.pill-title {
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pill-count {
  font-size: 0.62rem;
  font-weight: 600;
  opacity: 0.55;
  background: var(--surface-2);
  padding: 0 5px;
  border-radius: 20px;
  line-height: 1.4;
  min-width: 16px;
  text-align: center;
  transition: all var(--dur-fast) var(--ease);
}

.session-pill:hover .pill-count {
  opacity: 0.8;
  background: transparent;
}

.pill-count-active {
  opacity: 0.85;
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

/* ── Messages area ── */

.messages-area {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  scroll-behavior: smooth;
}

/* ── Messages list (non-empty) ── */

.messages-list {
  width: 100%;
  max-width: 768px;
  margin: 0 auto;
  padding: var(--space-4) var(--space-6) var(--space-6);
}

/* ── Empty state ── */

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-8) var(--space-6);
}

.empty-content {
  text-align: center;
  max-width: 480px;
  animation: emptyFadeIn 0.5s var(--ease-out);
}

@keyframes emptyFadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

.empty-icon {
  color: var(--t5);
  margin-bottom: var(--space-5);
  display: flex;
  justify-content: center;
}

.empty-title {
  font-size: var(--text-lg);
  font-weight: 700;
  color: var(--t1);
  letter-spacing: -0.02em;
  margin-bottom: var(--space-2);
}

.empty-desc {
  font-size: var(--text-sm);
  color: var(--t4);
  line-height: 1.7;
  margin-bottom: var(--space-6);
}

.empty-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  justify-content: center;
}

.suggest-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--text-xs);
  padding: 0.45rem 1rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--t3);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease);
  font-family: inherit;
  font-weight: 500;
  user-select: none;
}

.suggest-chip:hover {
  background: var(--p-bg);
  border-color: var(--p);
  color: var(--p);
  transform: translateY(-1px);
  box-shadow: var(--sh-sm);
}

.suggest-chip:active {
  transform: translateY(0);
}

.chip-emoji {
  font-size: 0.9rem;
  line-height: 1;
}

/* ── Input area (sticky bottom) ── */

.input-area {
  position: sticky;
  bottom: 0;
  flex-shrink: 0;
  z-index: 10;
  padding: var(--space-4) var(--space-6) var(--space-5);
  margin-top: auto;
  /* Gradient overlay + blur for a clean glass effect */
  background: linear-gradient(
    to top,
    var(--bg) 0%,
    var(--bg) 65%,
    transparent 100%
  );
  backdrop-filter: blur(12px) saturate(180%);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
}

/* Smooth border transition at top of input area */
.input-area::before {
  content: '';
  position: absolute;
  top: 0;
  left: var(--space-6);
  right: var(--space-6);
  height: 1px;
  background: linear-gradient(
    to right,
    transparent,
    var(--border) 20%,
    var(--border) 80%,
    transparent
  );
  opacity: 0.6;
}

.input-inner {
  max-width: 768px;
  margin: 0 auto;
  position: relative;
}

.input-hint {
  text-align: center;
  font-size: var(--text-xs);
  color: var(--t4);
  margin-top: var(--space-2);
  opacity: 0.55;
}

/* ── Responsive adjustments ── */

@media (max-width: 640px) {
  .chat-header {
    padding: var(--space-3) var(--space-3) var(--space-2);
  }

  .chat-title-text {
    font-size: var(--text-base);
  }

  .title-edit-wrap {
    max-width: 200px;
  }

  .messages-list {
    padding: var(--space-3) var(--space-3) var(--space-4);
  }

  .empty-state {
    padding: var(--space-6) var(--space-3);
  }

  .input-area {
    padding: var(--space-3) var(--space-3) var(--space-4);
  }

  .input-area::before {
    left: var(--space-3);
    right: var(--space-3);
  }

  .pill-title {
    max-width: 80px;
  }
}
</style>
