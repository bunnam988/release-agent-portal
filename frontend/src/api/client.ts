export interface WorkflowArg {
  name: string
  flag: string
  label: string
  kind: 'flag' | 'text'
  default?: string | null
  required?: boolean
}

export interface Workflow {
  id: string
  label: string
  description: string
  args: WorkflowArg[]
  mutating: boolean
  next_workflow_id?: string | null
  requires_mcp: string[]
  category: 'primary' | 'advanced'
  help_steps: string[]
}

export interface SessionSummary {
  id: string
  title?: string
  time?: { created?: number; updated?: number }
  model?: { id?: string; providerID?: string }
}

export interface MessagePart {
  type: string
  text?: string
  tool?: string
  state?: { status?: string }
  [key: string]: unknown
}

export interface MessageEntry {
  info: {
    role: 'user' | 'assistant'
    finish?: string
    time?: { created?: number; completed?: number }
    [key: string]: unknown
  }
  parts: MessagePart[]
}

export interface HealthStatus {
  ok: boolean
  opencode_reachable: boolean
}

export interface PermissionRequest {
  id: string
  [key: string]: unknown
}

export interface QuestionOption {
  label: string
  description: string
}

export interface QuestionItem {
  question: string
  header: string
  options: QuestionOption[]
}

export interface QuestionRequest {
  id: string
  sessionID: string
  questions: QuestionItem[]
  [key: string]: unknown
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    // Needed for the session cookie (see routers/auth.py) to be sent/
    // received at all when the frontend dev server (:5173) calls the
    // backend (:8000) directly -- genuinely cross-origin, unlike the
    // single-container deployment where FastAPI serves both the API and
    // the built frontend from the same origin/port already.
    credentials: 'include',
    ...init,
  })
  if (res.status === 401 && path !== '/api/auth/login') {
    // The session cookie expired (or auth just got turned on) mid-use --
    // reload rather than showing a confusing in-app error, so AuthGate's
    // own status check runs again and puts the login screen back up.
    // Excludes /api/auth/login itself: a wrong-password attempt there is
    // a normal 401 the caller should handle (show a toast), not a reason
    // to reload.
    window.location.reload()
  }
  if (!res.ok) {
    // FastAPI error responses are usually {"detail": "human readable
    // message"} -- surface that directly instead of a raw status/body
    // dump, since this is exactly the channel used for "integration
    // unavailable" errors that should read cleanly to a non-technical
    // user. Request-validation failures (malformed body) instead send
    // {"detail": [{"loc": [...], "msg": "...", ...}, ...]} -- handle that
    // shape too rather than falling back to a generic "Request failed".
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') {
        message = body.detail
      } else if (Array.isArray(body?.detail)) {
        const first = body.detail[0]
        if (first && typeof first.msg === 'string') {
          const field = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : undefined
          message = field ? `${field}: ${first.msg}` : first.msg
        }
      }
    } catch {
      // ignore, fall back to generic message
    }
    throw new Error(message)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export function listWorkflows(): Promise<Workflow[]> {
  return request('/api/workflows')
}

// Maps MCP server id (e.g. "jira-ccp") -> connected. Used to grey out /
// warn on workflows before the user even tries to run them.
export function getIntegrationStatus(): Promise<Record<string, boolean>> {
  return request('/api/workflows/integrations')
}

export function startSession(
  workflowId: string,
  args: Record<string, string | boolean>,
  user: string,
): Promise<{ session_id: string }> {
  return request('/api/sessions', {
    method: 'POST',
    body: JSON.stringify({ workflow_id: workflowId, args, user }),
  })
}

export function replySession(sessionId: string, text: string): Promise<void> {
  return request(`/api/sessions/${sessionId}/reply`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
}

export function listPermissions(sessionId: string): Promise<PermissionRequest[]> {
  return request(`/api/sessions/${sessionId}/permissions`)
}

export function replyPermission(sessionId: string, requestId: string, allow: boolean): Promise<void> {
  return request(`/api/sessions/${sessionId}/permissions/${requestId}`, {
    method: 'POST',
    body: JSON.stringify({ allow }),
  })
}

export function listQuestions(sessionId: string): Promise<QuestionRequest[]> {
  return request(`/api/sessions/${sessionId}/questions`)
}

// answers[i] = selected option labels for questions[i], in order.
export function answerQuestion(sessionId: string, requestId: string, answers: string[][]): Promise<void> {
  return request(`/api/sessions/${sessionId}/questions/${requestId}`, {
    method: 'POST',
    body: JSON.stringify({ answers }),
  })
}

export function declineQuestion(sessionId: string, requestId: string): Promise<void> {
  return request(`/api/sessions/${sessionId}/questions/${requestId}`, { method: 'DELETE' })
}

export function eventsUrl(sessionId: string): string {
  return `/api/sessions/${sessionId}/events`
}

export interface QueueStatus {
  status: 'running' | 'queued' | 'not_queued'
  ahead_count?: number
  blocked_by?: { label: string; user: string }
}

// Mutating workflows (main-tagging, stable2-release-orchestrator, etc.)
// are serialized to one at a time portal-wide (see backend's
// client.mutating_lock) -- this tells a session's own page whether it's
// actually running yet or still waiting behind someone else's run, so
// the UI isn't just a blank "Connecting..." screen with no explanation.
export function getQueueStatus(sessionId: string): Promise<QueueStatus> {
  return request(`/api/sessions/${sessionId}/queue`)
}

export function listSessions(): Promise<SessionSummary[]> {
  return request('/api/sessions')
}

export function getMessages(sessionId: string): Promise<MessageEntry[]> {
  return request(`/api/sessions/${sessionId}/messages`)
}

export function getHealth(): Promise<HealthStatus> {
  return request('/api/health')
}

export type Role = 'admin' | 'user'

export interface AuthStatus {
  auth_enabled: boolean
  authenticated: boolean
  role: Role | null
}

export function getAuthStatus(): Promise<AuthStatus> {
  return request('/api/auth/status')
}

export function login(password: string): Promise<{ ok: boolean; role: Role }> {
  return request('/api/auth/login', { method: 'POST', body: JSON.stringify({ password }) })
}

export function logout(): Promise<{ ok: boolean }> {
  return request('/api/auth/logout', { method: 'POST' })
}
