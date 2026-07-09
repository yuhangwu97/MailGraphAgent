/**
 * API client — fetch wrapper for all MailGraph REST + SSE endpoints.
 */

const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// ── SSE stream helper ──
export function sseStream(
  path: string,
  body: unknown,
  handlers: {
    onProgress?: (data: any) => void
    onComplete?: (data: any) => void
    onError?: (msg: string) => void
    onDone?: () => void
  },
): AbortController {
  const controller = new AbortController()

  fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: controller.signal,
  }).then(async (res) => {
    const reader = res.body?.getReader()
    if (!reader) return
    const decoder = new TextDecoder()
    let buffer = ''
    let streamDone = false

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Parse SSE frames
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let eventType = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          const dataStr = line.slice(6)
          try {
            const data = JSON.parse(dataStr)
            if (eventType === 'progress') handlers.onProgress?.(data)
            else if (eventType === 'complete') handlers.onComplete?.(data)
            else if (eventType === 'error') handlers.onError?.(data.msg || String(data))
            else if (eventType === 'done') { handlers.onDone?.(); streamDone = true }
            else if (eventType === 'result') handlers.onComplete?.(data)
            else handlers.onProgress?.(data)
          } catch {
            // non-JSON data, ignore
          }
          eventType = ''
        }
      }
    }
    // Fallback: only fire if the server didn't send an explicit "done" event
    if (!streamDone) handlers.onDone?.()
  }).catch((err) => {
    if (err.name !== 'AbortError') {
      handlers.onError?.(err.message)
    }
  })

  return controller
}

// ═══════════════════════════════════════════════════════════════
// Account API
// ═══════════════════════════════════════════════════════════════

export interface Account {
  id: string
  label: string
  imap_server: string
  imap_port: number
  email_user: string
  provider: string
}

export const accountsApi = {
  list: () => request<Account[]>('/accounts'),
  get: (id: string) => request<Account>(`/accounts/${id}`),
  create: (data: {
    label: string; imap_server: string; imap_port: number
    email_user: string; email_pass: string; provider: string
  }) => request<Account>('/accounts', { method: 'POST', body: JSON.stringify(data) }),
  delete: (id: string) => request<void>(`/accounts/${id}`, { method: 'DELETE' }),
  migrateFromEnv: () => request<{ migrated: boolean; account_count: number }>('/accounts/migrate-from-env', { method: 'POST' }),
}

// ═══════════════════════════════════════════════════════════════
// Mail API
// ═══════════════════════════════════════════════════════════════

export interface MailStats {
  total: number; done: number; pending: number
  failed: number; skipped: number; ingested: number
}

export interface MailItem {
  message_id: string; subject: string; from_addr: string
  from_name: string; date: string; status: string
  attachment_count: number; attachments: { filename: string }[]
}

export interface MailDetail extends MailItem {
  body: string; to_addrs: string[]; cc_addrs: string[]
}

export const mailsApi = {
  stats: () => request<MailStats>('/mails/stats'),
  pending: () => request<MailItem[]>('/mails/pending'),
  done: (limit = 100) => request<MailItem[]>(`/mails/done?limit=${limit}`),
  recent: (limit = 50) => request<MailItem[]>(`/mails/recent?limit=${limit}`),
  detail: (id: string) => request<MailDetail>(`/mails/${id}`),
  query: (data: unknown) => request<any>('/mails/query', { method: 'POST', body: JSON.stringify(data) }),

  fetch: (folder: string, limit: number, handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/mails/fetch', { folder, limit }, handlers),
  ingest: (limit: number | null, handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/mails/ingest', { limit }, handlers),
  reprocess: (messageIds: string[], handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/mails/reprocess', { message_ids: messageIds }, handlers),
}

// ═══════════════════════════════════════════════════════════════
// Conversation API
// ═══════════════════════════════════════════════════════════════

export interface ConvSession {
  id: string; title: string; created_at: number
  updated_at: number; message_count: number
}

export interface ChatMessage {
  id: string; role: 'user' | 'assistant'; content: string
  result?: any; created_at: number
}

export interface AgentMemory {
  preferences: string[]; pinned_context: string[]
  last_topics: string[]; summary: string; updated_at: number
}

export const conversationsApi = {
  list: () => request<ConvSession[]>('/conversations'),
  create: (title = '新对话') => request<ConvSession>('/conversations', { method: 'POST', body: JSON.stringify({ title }) }),
  get: (id: string) => request<ConvSession>(`/conversations/${id}`),
  rename: (id: string, title: string) => request<ConvSession>(`/conversations/${id}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  delete: (id: string) => request<void>(`/conversations/${id}`, { method: 'DELETE' }),
  messages: (id: string) => request<ChatMessage[]>(`/conversations/${id}/messages`),
  addMessage: (id: string, role: string, content: string, result?: any) =>
    request<ChatMessage>(`/conversations/${id}/messages`, { method: 'POST', body: JSON.stringify({ role, content, result }) }),
  context: (id: string) => request<{ role: string; content: string }[]>(`/conversations/${id}/context`),
  memory: () => request<AgentMemory>('/conversations/memory'),
  updateMemory: (question: string, answer: string) =>
    request<{ ok: boolean }>(`/conversations/memory?question=${encodeURIComponent(question)}&answer=${encodeURIComponent(answer)}`, { method: 'POST' }),
}

// ═══════════════════════════════════════════════════════════════
// Query API
// ═══════════════════════════════════════════════════════════════

export interface QueryResult {
  question: string; answer: string
  entities: any[]; relationships: any[]; chunks: any[]
  rows?: any[]; columns?: string[]; total_rows: number
  trace: any[]; error?: string; query_plan?: any; total_duration_ms: number
}

export const queryApi = {
  run: (question: string, sessionId: string | null, handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/query', { question, session_id: sessionId }, handlers),
}

// ═══════════════════════════════════════════════════════════════
// Graph API
// ═══════════════════════════════════════════════════════════════

export const graphApi = {
  entities: (page = 1, pageSize = 500) =>
    request<{ entities: any[]; page: number }>(`/graph/entities?page=${page}&page_size=${pageSize}`),
  relationships: (page = 1, pageSize = 1000) =>
    request<{ relationships: any[]; page: number }>(`/graph/relationships?page=${page}&page_size=${pageSize}`),
  build: (timeout: number, handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/graph/build', { timeout }, handlers),
  visualize: (entityTypes: string[] | null) =>
    request<{ html: string }>('/graph/visualize', { method: 'POST', body: JSON.stringify({ entity_types: entityTypes }) }),
}

// ═══════════════════════════════════════════════════════════════
// Status API
// ═══════════════════════════════════════════════════════════════

export interface ServiceStatus {
  ragflow: boolean; redis: boolean; mysql: boolean; minio: boolean
}

export interface StatusResponse {
  services: ServiceStatus
  active_account_id: string | null
  accounts: Account[]
}

export const statusApi = {
  health: () => request<StatusResponse>('/status'),
  tokens: () => request<{ prompt_tokens: number; completion_tokens: number }>('/usage/tokens'),
  logs: (service: string, lines = 50) => request<{ log: string }>(`/logs/${service}?lines=${lines}`),
}
