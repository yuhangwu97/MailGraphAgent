import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  conversationsApi,
  queryApi,
  type ConvSession,
  type ChatMessage,
  type AgentMemory,
  type QueryResult,
} from '@/api'

const DEFAULT_TITLE = '新对话'
const TITLE_MAX = 20

/** Derive a short session title from the user's first question. */
function deriveTitle(text: string): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  if (!clean) return DEFAULT_TITLE
  return clean.length > TITLE_MAX ? clean.slice(0, TITLE_MAX) + '…' : clean
}

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<ConvSession[]>([])
  const activeSessionId = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const memory = ref<AgentMemory | null>(null)
  const streaming = ref(false)
  const streamAnswer = ref('')
  const streamResult = ref<QueryResult | null>(null)
  const streamProgress = ref<string[]>([])

  const activeSession = computed(() =>
    sessions.value.find(s => s.id === activeSessionId.value) ?? null
  )

  async function fetchSessions() {
    try {
      sessions.value = await conversationsApi.list()
    } catch (e) {
      console.error('Failed to fetch sessions:', e)
    }
  }

  async function ensureSession() {
    if (!activeSessionId.value || !sessions.value.find(s => s.id === activeSessionId.value)) {
      if (sessions.value.length > 0) {
        activeSessionId.value = sessions.value[0].id
      }
    }
    if (!activeSessionId.value) {
      const s = await conversationsApi.create()
      sessions.value.unshift(s)
      activeSessionId.value = s.id
    }
  }

  async function createSession() {
    const s = await conversationsApi.create()
    sessions.value.unshift(s)
    activeSessionId.value = s.id
    messages.value = []
    return s
  }

  async function deleteSession(id: string) {
    await conversationsApi.delete(id)
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (activeSessionId.value === id) {
      activeSessionId.value = sessions.value[0]?.id ?? null
      messages.value = []
    }
    if (!activeSessionId.value) {
      await ensureSession()
    }
  }

  async function loadMessages(sessionId: string) {
    try {
      messages.value = await conversationsApi.messages(sessionId)
    } catch {
      messages.value = []
    }
  }

  async function loadMemory() {
    try {
      memory.value = await conversationsApi.memory()
    } catch {
      memory.value = null
    }
  }

  async function switchSession(sessionId: string) {
    activeSessionId.value = sessionId
    await loadMessages(sessionId)
    await loadMemory()
  }

  async function sendMessage(question: string) {
    if (!activeSessionId.value) await ensureSession()
    const sid = activeSessionId.value!

    // Auto-title from the first question — only when this is the opening
    // message and the user hasn't renamed the session away from the default.
    const sess = sessions.value.find(s => s.id === sid)
    const shouldAutoTitle =
      messages.value.length === 0 &&
      !!question.trim() &&
      (!sess?.title || sess.title === DEFAULT_TITLE)

    // Add user message
    const userMsg: ChatMessage = {
      id: 'local_' + Date.now(),
      role: 'user',
      content: question,
      created_at: Date.now() / 1000,
    }
    messages.value.push(userMsg)
    await conversationsApi.addMessage(sid, 'user', question)

    if (shouldAutoTitle && sess) {
      const title = deriveTitle(question)
      sess.title = title // instant local update (reactive)
      conversationsApi.rename(sid, title).catch(e =>
        console.error('Failed to rename session:', e)
      )
    }

    // Stream answer
    streaming.value = true
    streamAnswer.value = ''
    streamResult.value = null
    streamProgress.value = []

    return new Promise<void>((resolve, reject) => {
      let fullAnswer = ''
      let result: QueryResult | null = null

      queryApi.run(question, sid, {
        onProgress(data: any) {
          if (data.token) {
            fullAnswer += data.token
            streamAnswer.value = fullAnswer
          } else if (data.msg) {
            streamProgress.value = [...streamProgress.value, data.msg]
          }
        },
        onComplete(data: any) {
          result = data as QueryResult
          streamResult.value = result
          if (result.answer) {
            fullAnswer = result.answer
            streamAnswer.value = fullAnswer
          }
        },
        onError(msg: string) {
          fullAnswer = `⚠️ ${msg}`
          streamAnswer.value = fullAnswer
        },
        onDone() {
          const finalAnswer = fullAnswer || '未找到相关信息。'
          const assistantMsg: ChatMessage = {
            id: 'local_' + Date.now(),
            role: 'assistant',
            content: finalAnswer,
            result: result ?? undefined,
            created_at: Date.now() / 1000,
          }
          messages.value.push(assistantMsg)

          conversationsApi.addMessage(sid, 'assistant', finalAnswer, result ?? undefined)
          conversationsApi.updateMemory(question, finalAnswer)
          loadMemory()

          streaming.value = false
          streamAnswer.value = ''
          streamResult.value = null
          resolve()
        },
      })
    })
  }

  return {
    sessions, activeSessionId, messages, memory,
    streaming, streamAnswer, streamResult, streamProgress, activeSession,
    fetchSessions, ensureSession, createSession, deleteSession,
    loadMessages, loadMemory, switchSession, sendMessage,
  }
})
