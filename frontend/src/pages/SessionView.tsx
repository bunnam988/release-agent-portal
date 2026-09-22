import {
  AlertTriangle,
  ArrowDown,
  ArrowLeft,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  Download,
  Loader2,
  Send,
  ShieldQuestion,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import {
  answerQuestion,
  declineQuestion,
  eventsUrl,
  getMessages,
  getQueueStatus,
  listPermissions,
  listQuestions,
  listWorkflows,
  replyPermission,
  replySession,
  startSession,
  type MessageEntry,
  type PermissionRequest,
  type QuestionRequest,
  type QueueStatus,
  type Workflow,
} from '../api/client'

interface Part {
  id: string
  kind: 'text' | 'reasoning' | 'tool' | 'other'
  text: string
  tool?: string
  toolStatus?: string
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

// Both multi-phase orchestrators (see their AGENT.md files) print a
// "PHASE N COMPLETE" report block after every phase -- unlike the
// "Proceed with/to Phase N?" confirmation question (paraphrased or
// skipped entirely in --dry-run in practice, confirmed against a real
// transcript), this report text is reliably reproduced verbatim, since
// it's a data block the agent is copying values into rather than
// conversational text it's free to phrase however it likes.
const PHASE_LABELS: Record<string, string[]> = {
  'stable2-release-orchestrator': [
    'Track for stable2',
    'Filter candidates',
    'Evaluate readiness',
    'Collect PRs',
    'Meta sync',
  ],
  'stable2-meta-sync-orchestrator': [
    'GitHub cherry-pick',
    'Tracking ticket',
    'Considered labeling',
    'GitHub tagging',
    'SRCREV prep',
    'Gerrit cherry-pick',
  ],
}

// Returns the number of phases confirmed complete (not the active phase
// index -- see call site, which adds 1 and caps at the label count). The
// full stable2 release runs stable2-meta-sync-orchestrator as its own
// Phase 5, which prints its own "PHASE 1 COMPLETE".."PHASE 6 COMPLETE" --
// since those numbers are all <= the outer's own already-recorded higher
// phase count by the time nesting starts, plain max() here plus the
// call site's cap naturally keeps the outer stepper pinned at its own
// last phase instead of being corrupted by the nested numbers.
function countCompletedPhases(partOrder: string[], parts: Record<string, Part>): number {
  let completed = 0
  for (const id of partOrder) {
    const text = parts[id]?.text
    if (!text) continue
    for (const m of text.matchAll(/PHASE (\d+) COMPLETE/gi)) {
      const n = Number(m[1])
      if (n > completed) completed = n
    }
  }
  return completed
}

// Confirmation gates ("Proceed to Phase N? [Y/n]", "Delete these files...?
// [Y/n]") AND ad-hoc clarification questions ("Can you confirm the
// correct recipe name...?") are plain assistant text the agent prints
// before going idle to wait for the user's next free-text reply --
// unlike a real question/permission tool call, neither populates the
// structured questions/permissions arrays, so the session looks
// indistinguishable from "actually finished" without this check. A
// message ending in "?" right before going idle is, in practice, always
// the agent asking something rather than delivering a final report --
// every final report in this project's skills is declarative/tabular,
// never phrased as a question.
function endsWithConfirmationPrompt(text: string): boolean {
  return /\?\s*(?:[[(]\s*y\s*\/\s*n\s*[\])])?\s*$/i.test(text.trimEnd())
}

function PhaseStepper({ labels, current, complete }: { labels: string[]; current: number; complete: boolean }) {
  return (
    <div className="phase-stepper">
      {labels.map((label, i) => {
        const n = i + 1
        const state = complete || n < current ? 'done' : n === current ? 'active' : 'pending'
        return (
          <div className={`phase-step phase-step-${state}`} key={label}>
            <div className="phase-step-dot">{state === 'done' ? <Check size={12} /> : n}</div>
            <div className="phase-step-label">{label}</div>
            {i < labels.length - 1 && <div className="phase-step-line" />}
          </div>
        )
      })}
    </div>
  )
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error("Couldn't copy to clipboard")
    }
  }

  return (
    <button className="btn-secondary btn-icon" onClick={copy} title={`Copy ${label}`}>
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {copied ? 'Copied' : label}
    </button>
  )
}

/** Converts opencode's message history (role + parts) into the same Part
 * shape the live event stream produces, so a session loaded from History
 * shows its real content instead of an empty transcript (the SSE stream
 * is live-only and never replays history on a fresh subscribe). */
function partsFromHistory(messages: MessageEntry[]): { order: string[]; byId: Record<string, Part> } {
  const order: string[] = []
  const byId: Record<string, Part> = {}
  for (const message of messages) {
    for (const part of message.parts) {
      if (part.type === 'step-start') continue
      const id = (part.id as string) ?? `${order.length}`
      if (message.info.role === 'user' && part.type === 'text') {
        byId[id] = { id, kind: 'text', text: `> ${part.text ?? ''}` }
      } else if (part.type === 'text' || part.type === 'reasoning') {
        byId[id] = { id, kind: part.type, text: part.text ?? '' }
      } else if (part.type === 'tool') {
        byId[id] = { id, kind: 'tool', tool: part.tool, toolStatus: part.state?.status, text: '' }
      } else {
        continue
      }
      order.push(id)
    }
  }
  return { order, byId }
}

// opencode's legacy /event stream (see backend/app/opencode_client.py for
// why we use the legacy API). Verified shapes against real runs:
//  - message.part.updated: full part snapshot (text/reasoning/tool/step-start)
//  - message.part.delta: incremental text, {partID, field:"text", delta}
//  - permission.asked / question.asked: a gate opened (we still poll too,
//    this just makes the UI react immediately instead of waiting ~3s)
//  - session.status {type:"busy"} / session.idle: turn started/finished
export default function SessionView() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const [searchParams] = useSearchParams()
  const workflowId = searchParams.get('workflow')
  const navigate = useNavigate()

  const [partOrder, setPartOrder] = useState<string[]>([])
  const [parts, setParts] = useState<Record<string, Part>>({})
  const [replyText, setReplyText] = useState('')
  const [permissions, setPermissions] = useState<PermissionRequest[]>([])
  const [questions, setQuestions] = useState<QuestionRequest[]>([])
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [hasStarted, setHasStarted] = useState(false)
  const [workflow, setWorkflow] = useState<Workflow | null>(null)
  const [nextWorkflow, setNextWorkflow] = useState<Workflow | null>(null)
  const [startingNext, setStartingNext] = useState(false)
  const [sessionLoadError, setSessionLoadError] = useState<string | null>(null)
  const [gatesUnreachable, setGatesUnreachable] = useState(false)
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const [logOpen, setLogOpen] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const gateFailureStreak = useRef(0)
  const transcriptRef = useRef<HTMLDivElement | null>(null)
  const replyInputRef = useRef<HTMLInputElement | null>(null)

  function upsertPart(id: string, patch: Partial<Part>) {
    setParts((prev) => {
      const base: Part = prev[id] ?? { id, kind: 'other', text: '' }
      return { ...prev, [id]: { ...base, ...patch, id } }
    })
    setPartOrder((prev) => (prev.includes(id) ? prev : [...prev, id]))
  }

  function refreshGates() {
    if (!sessionId) return
    Promise.all([listPermissions(sessionId), listQuestions(sessionId)])
      .then(([perms, qs]) => {
        setPermissions(perms)
        setQuestions(qs)
        gateFailureStreak.current = 0
        setGatesUnreachable(false)
      })
      .catch(() => {
        // A single missed poll (every 3s) isn't worth alarming anyone
        // about -- only surface it once it's clearly not transient.
        gateFailureStreak.current += 1
        if (gateFailureStreak.current >= 3) setGatesUnreachable(true)
      })
  }

  useEffect(() => {
    listWorkflows().then((all) => {
      const current = all.find((w) => w.id === workflowId) ?? null
      setWorkflow(current)
      if (current?.next_workflow_id) {
        setNextWorkflow(all.find((w) => w.id === current.next_workflow_id) ?? null)
      }
    })
  }, [workflowId])

  // Load prior content for this session (works for both an in-progress run
  // being revisited on refresh and a fully finished one opened from
  // History) before/alongside subscribing to live updates.
  useEffect(() => {
    if (!sessionId) return
    getMessages(sessionId)
      .then((messages) => {
        if (messages.length === 0) return
        const { order, byId } = partsFromHistory(messages)
        setParts((prev) => ({ ...byId, ...prev }))
        setPartOrder((prev) => [...order.filter((id) => !prev.includes(id)), ...prev])
        const last = messages[messages.length - 1]
        setHasStarted(true)
        setBusy(last.info.role === 'assistant' && !last.info.finish)
      })
      .catch((err) => setSessionLoadError(errorMessage(err)))
  }, [sessionId])

  useEffect(() => {
    if (!sessionId) return
    const es = new EventSource(eventsUrl(sessionId))
    esRef.current = es
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    es.onmessage = (msg) => {
      let evt: { type?: string; properties?: Record<string, unknown> }
      try {
        evt = JSON.parse(msg.data)
      } catch {
        return
      }
      const props = evt.properties ?? {}
      switch (evt.type) {
        case 'message.part.updated': {
          const part = props.part as Record<string, unknown> | undefined
          if (!part) break
          if (part.type === 'text' || part.type === 'reasoning') {
            upsertPart(part.id as string, { kind: part.type as Part['kind'], text: (part.text as string) ?? '' })
          } else if (part.type === 'tool') {
            const state = (part.state as Record<string, unknown>) ?? {}
            upsertPart(part.id as string, {
              kind: 'tool',
              tool: part.tool as string,
              toolStatus: state.status as string,
              text: '',
            })
          }
          break
        }
        case 'message.part.delta': {
          if (props.field === 'text') {
            const partID = props.partID as string
            setParts((prev) => {
              const existing = prev[partID]
              const text = (existing?.text ?? '') + ((props.delta as string) ?? '')
              return { ...prev, [partID]: { id: partID, kind: existing?.kind ?? 'text', text } }
            })
            setPartOrder((prev) => (prev.includes(partID) ? prev : [...prev, partID]))
          }
          break
        }
        case 'session.status':
          setHasStarted(true)
          setBusy((props.status as { type?: string } | undefined)?.type === 'busy')
          break
        case 'session.idle':
          setHasStarted(true)
          setBusy(false)
          // Don't wait for the next 3s poll tick -- a question/permission
          // gate opened right as the turn ends should show up immediately,
          // not leave a window where the run looks finished before it
          // catches up.
          refreshGates()
          break
        case 'permission.asked':
        case 'question.asked':
          refreshGates()
          break
        default:
          break
      }
    }
    refreshGates()
    const poll = setInterval(refreshGates, 3000)
    return () => {
      es.close()
      clearInterval(poll)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  // Auto-scroll the activity log as new content streams in, but only if
  // the user hasn't deliberately scrolled up to read something earlier --
  // otherwise every new tool call would yank them back to the bottom.
  useEffect(() => {
    if (!autoScroll) return
    const el = transcriptRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [partOrder, autoScroll])

  // Mutating workflows are serialized to one at a time portal-wide (see
  // backend's client.mutating_lock) -- poll whether this session is
  // actually running yet or still waiting behind someone else's run, so
  // a queued user sees why nothing is happening instead of a blank
  // "Connecting..." screen. Stops once the run has visibly started
  // (hasStarted), since by then it's definitely not queued anymore.
  useEffect(() => {
    if (!sessionId || hasStarted) {
      setQueueStatus(null)
      return
    }
    let cancelled = false
    function poll() {
      getQueueStatus(sessionId!)
        .then((s) => {
          if (!cancelled) setQueueStatus(s.status === 'queued' ? s : null)
        })
        .catch(() => {})
    }
    poll()
    const interval = setInterval(poll, 2000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [sessionId, hasStarted])

  // Auto-expand the activity log the first time something actually starts
  // happening, but never auto-collapse it -- orchestrators pause for a
  // Y/n confirmation after every phase (busy briefly goes false between
  // phases), and tying `open` directly to `busy` made the whole log
  // (transcript + jump-to-latest button) disappear every single time,
  // which is what made "jump to latest" look broken. From here on it
  // only responds to the user's own manual expand/collapse (onToggle).
  useEffect(() => {
    if (busy) setLogOpen(true)
  }, [busy])

  function onTranscriptScroll() {
    const el = transcriptRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    setAutoScroll(atBottom)
  }

  function jumpToLatest() {
    setAutoScroll(true)
    const el = transcriptRef.current
    if (el) el.scrollTop = el.scrollHeight
  }

  // "/" focuses the reply box, a common convention (Slack, Linear, etc.)
  // -- skipped while already typing somewhere so it doesn't hijack a "/"
  // the user is trying to type into the reply box itself.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null
      const typing = target && ['INPUT', 'TEXTAREA'].includes(target.tagName)
      if (e.key === '/' && !typing) {
        e.preventDefault()
        replyInputRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  function downloadReport() {
    const lines = [
      `# ${workflow?.label ?? 'Workflow run'}`,
      '',
      `- Session: ${sessionId}`,
      `- Generated: ${new Date().toISOString()}`,
      '',
      '## Output',
      '',
      finalText || '(no output captured)',
    ]
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${workflow?.id ?? 'session'}-${sessionId}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  async function sendReply(text: string) {
    if (!sessionId || !text.trim()) return
    setReplyText('')
    setBusy(true)
    try {
      await replySession(sessionId, text)
      upsertPart(`local-${Date.now()}`, { kind: 'text', text: `> ${text}` })
    } catch (err) {
      setBusy(false)
      setReplyText(text)
      toast.error("Couldn't send reply", { description: errorMessage(err) })
    }
  }

  async function decidePermission(requestId: string, allow: boolean) {
    if (!sessionId) return
    try {
      await replyPermission(sessionId, requestId, allow)
      setPermissions((prev) => prev.filter((p) => p.id !== requestId))
    } catch (err) {
      toast.error("Couldn't record your decision", { description: errorMessage(err) })
    }
  }

  async function answer(q: QuestionRequest, selections: string[][]) {
    if (!sessionId) return
    try {
      await answerQuestion(sessionId, q.id, selections)
      setQuestions((prev) => prev.filter((p) => p.id !== q.id))
    } catch (err) {
      toast.error("Couldn't submit your answer", { description: errorMessage(err) })
    }
  }

  async function decline(q: QuestionRequest) {
    if (!sessionId) return
    try {
      await declineQuestion(sessionId, q.id)
      setQuestions((prev) => prev.filter((p) => p.id !== q.id))
    } catch (err) {
      toast.error("Couldn't decline the question", { description: errorMessage(err) })
    }
  }

  async function runNext() {
    if (!nextWorkflow) return
    setStartingNext(true)
    try {
      const user = window.localStorage.getItem('portal_user') || 'unknown'
      const { session_id } = await startSession(nextWorkflow.id, {}, user)
      navigate(`/sessions/${session_id}?workflow=${nextWorkflow.id}`)
    } catch (err) {
      toast.error(`Couldn't start ${nextWorkflow.label}`, { description: errorMessage(err) })
    } finally {
      setStartingNext(false)
    }
  }

  // The most recent assistant "text" part is always shown in the same
  // place, in the same style, regardless of which skill produced it — so
  // the result of every workflow looks the same shape to the user.
  const lastTextPartId = [...partOrder].reverse().find((id) => parts[id]?.kind === 'text')
  const finalText = lastTextPartId ? parts[lastTextPartId].text : ''

  const phaseLabels = workflow ? PHASE_LABELS[workflow.id] : undefined
  const completedPhases = phaseLabels ? countCompletedPhases(partOrder, parts) : 0
  // "Active phase" = one past the last completed one, capped so a
  // nested orchestrator's own higher phase count can't push this past
  // this workflow's own last labeled phase.
  const currentPhase = phaseLabels ? Math.min(completedPhases + 1, phaseLabels.length) : 0

  const waitingForInput = permissions.length > 0 || questions.length > 0
  // A phased orchestrator that's gone idle before finishing its own last
  // phase is, by definition, just paused for a confirmation -- not done.
  const phasesRemain = !!phaseLabels && completedPhases < phaseLabels.length
  const isComplete =
    hasStarted && !busy && !waitingForInput && !phasesRemain && !endsWithConfirmationPrompt(finalText)

  return (
    <div className="session-view">
      <Link className="back-link" to="/">
        <ArrowLeft size={15} /> Back to workflows
      </Link>
      <div className="session-header">
        <div>
          <p className="subtitle">{workflow?.label ?? 'Workflow run'}</p>
          {sessionId && (
            <div className="session-id-row">
              <span className="session-id">{sessionId}</span>
              <CopyButton value={sessionId} label="Copy ID" />
            </div>
          )}
        </div>
        <div className="status-row">
          <span className={`status ${connected ? 'status-ok' : 'status-down'}`}>
            {connected ? 'Connected' : 'Connecting…'}
          </span>
          {hasStarted && (
            <span className={`status ${busy ? 'status-busy' : waitingForInput ? 'status-waiting' : 'status-ok'}`}>
              {busy && <Loader2 size={13} className="spin" />}
              {waitingForInput && !busy && <ShieldQuestion size={13} />}
              {isComplete && <CheckCircle2 size={13} />}
              {busy ? 'Running…' : waitingForInput ? 'Waiting for your input' : 'Complete'}
            </span>
          )}
        </div>
      </div>

      {phaseLabels && hasStarted && (
        <PhaseStepper labels={phaseLabels} current={currentPhase} complete={isComplete} />
      )}

      {sessionLoadError && (
        <div className="banner error">
          <AlertTriangle size={15} />
          Couldn't load this session: {sessionLoadError}. It may not exist, or the opencode server may be
          unreachable.
        </div>
      )}

      {gatesUnreachable && (
        <div className="banner warning">
          <AlertTriangle size={15} />
          Lost touch with the server while checking for pending questions/permissions — retrying automatically.
          Reload the page if this persists.
        </div>
      )}

      {queueStatus && (
        <div className="banner info">
          <Clock size={15} />
          Waiting on <strong>{queueStatus.blocked_by?.label}</strong>
          {queueStatus.blocked_by?.user && <> (started by {queueStatus.blocked_by.user})</>} to finish — only one
          mutating workflow can run at a time across the portal.{' '}
          {queueStatus.ahead_count && queueStatus.ahead_count > 1
            ? `${queueStatus.ahead_count} runs are ahead of you.`
            : 'This run will start automatically as soon as it frees up.'}
        </div>
      )}

      {questions.map((q) => (
        <QuestionPanel key={q.id} question={q} onAnswer={answer} onDecline={decline} />
      ))}

      {permissions.length > 0 && (
        <div className="permission-panel">
          <h2>Pending permission requests</h2>
          {permissions.map((p) => (
            <div className="permission-row" key={p.id}>
              <pre>{JSON.stringify(p, null, 2)}</pre>
              <div className="permission-actions">
                <button onClick={() => decidePermission(p.id, true)}>Allow</button>
                <button className="btn-secondary" onClick={() => decidePermission(p.id, false)}>
                  Deny
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="result-panel">
        <div className="result-panel-header">
          <h2>Output</h2>
          {finalText && <CopyButton value={finalText} label="Copy" />}
        </div>
        <div className="result-text">
          {finalText ? (
            <ReactMarkdown>{finalText}</ReactMarkdown>
          ) : busy ? (
            <span className="typing-indicator">
              Working <span /> <span /> <span />
            </span>
          ) : (
            'Waiting for output…'
          )}
        </div>
      </div>

      {isComplete && (
        <div className="complete-banner complete-banner-in">
          <div className="complete-banner-label">
            <CheckCircle2 size={18} className="complete-check" />
            Run complete
          </div>
          <div className="complete-actions">
            <button className="btn-secondary" onClick={downloadReport}>
              <Download size={13} />
              Download report
            </button>
            {nextWorkflow && (
              <button onClick={runNext} disabled={startingNext}>
                {startingNext ? 'Starting…' : `Run next: ${nextWorkflow.label}`}
              </button>
            )}
            <Link to="/">
              <button className="btn-secondary">Back to workflows</button>
            </Link>
          </div>
        </div>
      )}

      <details className="activity-log" open={logOpen} onToggle={(e) => setLogOpen(e.currentTarget.open)}>
        <summary>Activity log</summary>
        <div className="transcript" ref={transcriptRef} onScroll={onTranscriptScroll}>
          {!autoScroll && (
            <button className="jump-to-latest" onClick={jumpToLatest}>
              <ArrowDown size={13} /> Jump to latest
            </button>
          )}
          {partOrder.map((id) => {
            const part = parts[id]
            if (!part) return null
            if (part.kind === 'tool') {
              return (
                <div className="log-line log-tool" key={id}>
                  (tool: {part.tool} — {part.toolStatus})
                </div>
              )
            }
            if (part.kind === 'reasoning') {
              return (
                <div className="log-line log-reasoning" key={id}>
                  (thinking) {part.text}
                </div>
              )
            }
            return (
              <div className="log-line" key={id}>
                {part.text}
              </div>
            )
          })}
        </div>
      </details>

      <div className="quick-replies">
        <button className="btn-secondary" onClick={() => sendReply('Y')}>
          Yes
        </button>
        <button className="btn-secondary" onClick={() => sendReply('n')}>
          No
        </button>
      </div>
      <form
        className="reply-form"
        onSubmit={(e) => {
          e.preventDefault()
          sendReply(replyText)
        }}
      >
        <input
          ref={replyInputRef}
          type="text"
          value={replyText}
          onChange={(e) => setReplyText(e.target.value)}
          placeholder="Reply to the agent… (press / to focus)"
        />
        <button type="submit">
          <Send size={15} />
        </button>
      </form>
    </div>
  )
}

const OTHER_VALUE = '__other__'

function QuestionPanel({
  question,
  onAnswer,
  onDecline,
}: {
  question: QuestionRequest
  onAnswer: (q: QuestionRequest, selections: string[][]) => void
  onDecline: (q: QuestionRequest) => void
}) {
  const [selected, setSelected] = useState<string[]>(question.questions.map(() => ''))
  const [customText, setCustomText] = useState<string[]>(question.questions.map(() => ''))

  function pick(i: number, label: string) {
    setSelected((prev) => prev.map((v, idx) => (idx === i ? label : v)))
  }

  function setCustom(i: number, text: string) {
    setCustomText((prev) => prev.map((v, idx) => (idx === i ? text : v)))
  }

  // A skill may (or, as seen in practice, may forget to) offer its own
  // "type your own answer" option -- rather than depend on every skill
  // remembering to add one, always offer a free-text fallback here, the
  // same way our own question-asking convention always adds "Other".
  const allAnswered = selected.every((v, i) => v && (v !== OTHER_VALUE || customText[i].trim()))

  function submit() {
    const answers = selected.map((v, i) => [v === OTHER_VALUE ? customText[i].trim() : v])
    onAnswer(question, answers)
  }

  return (
    <div className="permission-panel">
      <h2>Agent needs an answer</h2>
      {question.questions.map((q, i) => (
        <div key={q.header} className="question-block">
          <p className="question-text">{q.question}</p>
          <div className="question-options">
            {q.options.map((opt) => (
              <label key={opt.label} className="question-option">
                <input
                  type="radio"
                  name={`${question.id}-${i}`}
                  checked={selected[i] === opt.label}
                  onChange={() => pick(i, opt.label)}
                />
                <strong>{opt.label}</strong> — {opt.description}
              </label>
            ))}
            <label className="question-option">
              <input
                type="radio"
                name={`${question.id}-${i}`}
                checked={selected[i] === OTHER_VALUE}
                onChange={() => pick(i, OTHER_VALUE)}
              />
              <strong>Other</strong> — type your own answer
            </label>
            {selected[i] === OTHER_VALUE && (
              <input
                type="text"
                className="question-custom-input"
                autoFocus
                value={customText[i]}
                onChange={(e) => setCustom(i, e.target.value)}
                placeholder="Enter your own answer…"
              />
            )}
          </div>
        </div>
      ))}
      <div className="permission-actions">
        <button disabled={!allAnswered} onClick={submit}>
          Submit answer
        </button>
        <button className="btn-secondary" onClick={() => onDecline(question)}>
          Decline
        </button>
      </div>
    </div>
  )
}
