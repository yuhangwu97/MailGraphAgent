/**
 * API client — fetch wrapper for all MailGraph REST + SSE endpoints.
 */

const BASE = '/api'

// ── Active account context ──
// 后端按 X-Account-Id 头区分账户数据；这里保存当前账户 id，由 account store 保持同步，
// 每个 REST / SSE 请求都带上，缺失时后端 fallback 到默认（第一个）账户。
let activeAccountId: string | null = null
export function setActiveAccountId(id: string | null) {
  activeAccountId = id
}
function accountHeaders(): Record<string, string> {
  return activeAccountId ? { 'X-Account-Id': activeAccountId } : {}
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...accountHeaders(), ...options?.headers },
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
    headers: { 'Content-Type': 'application/json', ...accountHeaders() },
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
  is_default?: boolean
}

export const accountsApi = {
  list: () => request<Account[]>('/accounts'),
  get: (id: string) => request<Account>(`/accounts/${id}`),
  create: (data: {
    label: string; imap_server: string; imap_port: number
    email_user: string; email_pass: string; provider: string
  }) => request<Account>('/accounts', { method: 'POST', body: JSON.stringify(data) }),
  delete: (id: string) => request<void>(`/accounts/${id}`, { method: 'DELETE' }),
  setDefault: (id: string) => request<{ default_account_id: string }>(`/accounts/${id}/default`, { method: 'POST' }),
  migrateFromEnv: () => request<{ migrated: boolean; account_count: number }>('/accounts/migrate-from-env', { method: 'POST' }),
}

// ═══════════════════════════════════════════════════════════════
// Mail API
// ═══════════════════════════════════════════════════════════════

export interface MailStats {
  total: number; done: number; pending: number
  failed: number; skipped: number; ingested: number; indexed: number
  processing?: number
}

export interface MailItem {
  message_id: string; subject: string; from_addr: string
  from_name: string; date: string; status: string
  attachment_count: number; attachments: { filename: string }[]
  folder?: string; source_type?: string
}

export interface BrowseFile {
  path: string; name: string; size: number; ext: string
}

export interface BrowseDir {
  path: string; name: string
}

export interface BrowseResponse {
  dir: string; parent: string | null; dirs: BrowseDir[]; files: BrowseFile[]
}

export interface PickResponse {
  paths: string[]; canceled: boolean
}

export interface MailDetail extends MailItem {
  body: string; to_addrs: string[]; cc_addrs: string[]
}

export interface MailQueryRequest {
  start_time?: string
  end_time?: string
  status?: string
  sender?: string
  has_attachment?: boolean
  message_ids?: string[]
  topic?: string
  aggregation?: 'count' | 'list' | 'rate' | 'top_senders'
  limit?: number
}

export interface PaginatedMails {
  items: MailItem[]
  total: number
  page: number
  page_size: number
}

export const mailsApi = {
  stats: () => request<MailStats>('/mails/stats'),
  list: (params?: { filter?: 'all' | 'todo' | 'done'; page?: number; page_size?: number }) => {
    const p = params || {}
    return request<PaginatedMails>(`/mails/list?filter=${p.filter ?? 'all'}&page=${p.page ?? 1}&page_size=${p.page_size ?? 200}`)
  },
  pending: (params?: { page?: number; page_size?: number }) => {
    const p = params || {}
    return request<PaginatedMails>(`/mails/pending?page=${p.page ?? 1}&page_size=${p.page_size ?? 50}`)
  },
  done: (params?: { page?: number; page_size?: number }) => {
    const p = params || {}
    return request<PaginatedMails>(`/mails/done?page=${p.page ?? 1}&page_size=${p.page_size ?? 20}`)
  },
  recent: (params?: { page?: number; page_size?: number }) => {
    const p = params || {}
    return request<PaginatedMails>(`/mails/recent?page=${p.page ?? 1}&page_size=${p.page_size ?? 20}`)
  },
  detail: (id: string) => request<MailDetail>(`/mails/${id}`),
  query: (data: MailQueryRequest) => request<any>('/mails/query', { method: 'POST', body: JSON.stringify(data) }),

  fetch: (folder: string, limit: number, handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/mails/fetch', { folder, limit }, handlers),
  ingest: (limit: number | null, handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/mails/ingest', { limit }, handlers),
  reprocess: (messageIds: string[], handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/mails/reprocess', { message_ids: messageIds }, handlers),

  // ── File import (.eml/.msg/.pst/.ost) ──
  pick: (mode: 'folder' | 'files' = 'folder') => request<PickResponse>(`/mails/pick?mode=${mode}`),
  browse: (dir?: string) => request<BrowseResponse>(`/mails/browse${dir ? `?dir=${encodeURIComponent(dir)}` : ''}`),
  indexed: (params?: { page?: number; page_size?: number; status?: 'pending' | 'done' | 'all' }) => {
    const p = params || {}
    return request<PaginatedMails>(`/mails/indexed?status=${p.status ?? 'pending'}&page=${p.page ?? 1}&page_size=${p.page_size ?? 50}`)
  },
  indexFiles: (paths: string[], handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/mails/index', { paths }, handlers),
  parseSelected: (messageIds: string[], handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/mails/parse-selected', { message_ids: messageIds }, handlers),
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

export interface GraphStatus {
  graph: { entities: number; relationships: number }
  docs: { pending: number; processing: number; processed: number; failed: number; duplicate?: number }
  pipeline: { busy: boolean; latest_message: string; job_name: string }
}

export const graphApi = {
  entities: (page = 1, pageSize = 500) =>
    request<{ entities: any[]; page: number }>(`/graph/entities?page=${page}&page_size=${pageSize}`),
  relationships: (page = 1, pageSize = 1000) =>
    request<{ relationships: any[]; page: number }>(`/graph/relationships?page=${page}&page_size=${pageSize}`),
  status: () => request<GraphStatus>('/graph/status'),
  build: (timeout: number, handlers: Parameters<typeof sseStream>[2]) =>
    sseStream('/graph/build', { timeout }, handlers),
  visualize: (entityTypes: string[] | null) =>
    request<{ html: string }>('/graph/visualize', { method: 'POST', body: JSON.stringify({ entity_types: entityTypes }) }),
  resolveEntities: (dryRun: boolean) =>
    request<{ dry_run: boolean; groups: Array<{ type: string; canonical: string; merged: string[]; error?: string }>; merged_groups: number; merged_entities: number; rejected: number }>(
      `/graph/resolve-entities?dry_run=${dryRun}`, { method: 'POST' }),
}

// ═══════════════════════════════════════════════════════════════
// Status API
// ═══════════════════════════════════════════════════════════════

export interface ServiceStatus {
  redis: boolean; neo4j: boolean; milvus: boolean
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

// ═══════════════════════════════════════════════════════════════
// Project API
// ═══════════════════════════════════════════════════════════════

export interface ProjectSummary {
  overview: string
  stage: string
  key_dates: string
  core_people: string[]
}

export interface ProjectReport {
  overview: string
  stage: string
  contract: string
  key_dates: string
  core_people: string
  companies: string
  recent_activity: string
}

export interface ProjectAnalysis {
  project_name: string
  summary: ProjectSummary | null
  report: ProjectReport | null
  generated_at: number
  cached: boolean
}

export interface NeighborEntity {
  name: string
  type: string
}

export interface ProjectItem {
  name: string
  description: string
  people: NeighborEntity[]
  companies: NeighborEntity[]
  tasks: NeighborEntity[]
  events: NeighborEntity[]
  documents: NeighborEntity[]
  systems: NeighborEntity[]
  locations: NeighborEntity[]
  other_neighbors: NeighborEntity[]
  ai_summary: ProjectSummary | null
}

export interface PaginatedProjects {
  projects: ProjectItem[]
  total: number
  page: number
  page_size: number
}

export const projectsApi = {
  list: (page = 1, pageSize = 20) =>
    request<PaginatedProjects>(`/projects?page=${page}&page_size=${pageSize}`),

  getAnalysis: (name: string) =>
    request<ProjectAnalysis>(`/projects/${encodeURIComponent(name)}/analysis`),

  analyze: (name: string, handlers: Parameters<typeof sseStream>[2]) =>
    sseStream(`/projects/${encodeURIComponent(name)}/analyze`, {}, handlers),
}
