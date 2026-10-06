import {
  AlertTriangle,
  ArrowDown,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  Download,
  Loader2,
  Send,
  ShieldQuestion,
  Terminal,
  XCircle,
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
  toolCommand?: string
  toolOutput?: string
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
//
// Some agents (e.g. on-demand-cherry-pick) wrap their whole plan,
// including this trailing question, in one fenced code block -- so the
// message's literal last characters are a closing ``` rather than the
// question itself. Strip a trailing fence before checking, so this still
// recognizes the prompt instead of falsely reporting the run as done.
function stripTrailingFence(text: string): string {
  return text.trimEnd().replace(/```\s*$/, '').trimEnd()
}

function endsWithConfirmationPrompt(text: string): boolean {
  return /\?\s*(?:[[(]\s*y\s*\/\s*n\s*[\])])?\s*$/i.test(stripTrailingFence(text))
}

// Pulls out just the trailing "Proceed to Phase 2? [Y/n]" (or similar)
// question so it can be shown as its own actionable prompt, instead of
// echoing the whole message (which usually also contains a PHASE N
// COMPLETE block already rendered as a stage card) a second time.
function extractTrailingPrompt(text: string): string | null {
  if (!endsWithConfirmationPrompt(text)) return null
  const paragraphs = stripTrailingFence(text).split(/\n\s*\n/)
  return paragraphs[paragraphs.length - 1].trim()
}

// Strips the mechanical "PHASE N COMPLETE" report blocks and the
// orchestrator's own closing ascii banner out of a message, leaving only
// whatever plain-language prose (if any) surrounds them. Both of those
// are already rendered as their own clean cards elsewhere (stage-results
// / FinalSummaryCard), so repeating their raw text in the Output panel
// would just look like a pasted-in terminal log.
function stripReportingNoise(text: string): string {
  const lines = text.split('\n')
  const isDivider = (l: string) => /^[-─_=]{5,}$/.test(l.trim())
  const remove = new Array(lines.length).fill(false)

  for (let i = 0; i < lines.length; i++) {
    if (!/^PHASE \d+ COMPLETE$/i.test(lines[i].trim())) continue
    let j = i
    remove[j] = true
    j++
    while (j < lines.length && (isDivider(lines[j]) || lines[j].trim() === '')) {
      remove[j] = true
      j++
    }
    if (/^Results from (.+):$/i.test(lines[j]?.trim() ?? '')) {
      remove[j] = true
      j++
    }
    while (j < lines.length && lines[j].trim() !== '' && /^\s{2,}[^:]+?:\s*.*$/.test(lines[j])) {
      remove[j] = true
      j++
    }
    i = j - 1
  }

  const bannerIdx = lines.findIndex((l) => /COMPLETE/.test(l) && /[═║╔╚]/.test(l))
  if (bannerIdx !== -1) {
    for (let i = bannerIdx; i < lines.length; i++) remove[i] = true
  }

  return lines
    .filter((_, i) => !remove[i])
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

interface ResultTable {
  headers: string[]
  rows: string[][]
}

interface ResultBlock {
  status: 'success' | 'partial' | 'blocked'
  summary: string
  table: ResultTable | null
  details: string[]
  nextSteps: string[]
  before: string
}

// A markdown pipe-table: header row, a `---`-style separator row, then
// data rows. Tolerant of surrounding whitespace and optional leading/
// trailing `|` since models are inconsistent about those.
function parseMarkdownTable(block: string): ResultTable | null {
  const lines = block
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('|'))
  if (lines.length < 2) return null
  const splitRow = (line: string) =>
    line
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((cell) => cell.trim())
  const headers = splitRow(lines[0])
  const rows = lines
    .slice(1)
    .filter((l) => !/^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/.test(l))
    .map(splitRow)
  return headers.length ? { headers, rows } : null
}

// Every skill's final message is free-text prose that varies in shape --
// AGENTS.md instructs the agent to append one standard ===RESULT=== block
// at the very end of a truly-finished run (see that file), so the UI can
// render the same fixed Status/Summary/Table/Details/Next-steps layout no
// matter which workflow produced it. Returns null if the block isn't
// present (e.g. an older session, or a run still mid-flight), in which
// case the caller falls back to rendering the raw markdown as before.
function parseResultBlock(text: string): ResultBlock | null {
  const match = text.match(/===RESULT===([\s\S]*?)===END RESULT===/i)
  if (!match) return null
  const body = match[1]
  const sectionLabels = ['TABLE', 'DETAILS', 'NEXT_STEPS']
  const after = (label: string) => {
    const stop = `(?:\\n(?:${sectionLabels.filter((l) => l !== label).join('|')}):|$)`
    const re = new RegExp(`${label}:\\s*([\\s\\S]*?)${stop}`, 'i')
    return body.match(re)?.[1]
  }
  const bullets = (s?: string) =>
    (s ?? '')
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.startsWith('-'))
      .map((l) => l.replace(/^-\s*/, '').trim())
      .filter(Boolean)
  const statusMatch = body.match(/STATUS:\s*(success|partial|blocked)/i)
  const summaryMatch = body.match(/SUMMARY:\s*([\s\S]*?)(?:\nTABLE:|\nDETAILS:|\nNEXT_STEPS:|$)/i)
  return {
    status: (statusMatch?.[1]?.toLowerCase() as ResultBlock['status']) ?? 'success',
    summary: summaryMatch?.[1]?.trim() ?? '',
    table: parseMarkdownTable(after('TABLE') ?? ''),
    details: bullets(after('DETAILS')),
    nextSteps: bullets(after('NEXT_STEPS')),
    before: text.slice(0, match.index).trim(),
  }
}

const RESULT_STATUS_META = {
  success: { label: 'Success', icon: CheckCircle2 },
  partial: { label: 'Partial', icon: AlertTriangle },
  blocked: { label: 'Blocked', icon: XCircle },
} as const

function ResultTableView({ table }: { table: ResultTable }) {
  return (
    <div className="result-table-wrap">
      <table className="result-table">
        <thead>
          <tr>
            {table.headers.map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// A single tool call's command + real output, collapsed by default (long
// command output would otherwise dominate the log) -- click to expand.
// This is what makes "the full log" a genuine claim: the actual stdout
// opencode captured, not just a tool name and a status word.
function ToolLogEntry({ part }: { part: Part }) {
  const [open, setOpen] = useState(false)
  const hasOutput = !!part.toolOutput?.trim()
  return (
    <div className="log-line log-tool-entry">
      <button
        className="log-tool-header"
        onClick={() => hasOutput && setOpen((o) => !o)}
        disabled={!hasOutput}
      >
        {hasOutput ? open ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : <Terminal size={13} />}
        <Terminal size={13} className="log-tool-icon" />
        <code className="log-tool-command">{part.toolCommand || part.tool}</code>
        <span className={`tool-status tool-status-${part.toolStatus ?? 'pending'}`}>{part.toolStatus}</span>
      </button>
      {open && hasOutput && <pre className="log-tool-output">{part.toolOutput}</pre>}
    </div>
  )
}

// A vibrant, cycling accent per stage number -- purely cosmetic, keeps a
// long multi-phase run from looking like a wall of identical gray cards.
const STAGE_COLORS = ['violet', 'teal', 'amber', 'pink', 'blue', 'green'] as const

interface PhaseReport {
  phase: number
  source?: string
  fields: { key: string; value: string }[]
}

// Both orchestrators (see their .opencode/agent/*.md files) print this
// exact shape after every phase -- not the ===RESULT=== block (that's
// reserved for the true end of a run), but a reliable, pre-existing
// "PHASE N COMPLETE" / "Results from <skill>:" / indented "Key: Value"
// report. Parsed from the raw text instead of asking the orchestrators to
// change their own (already carefully-tuned) prompt format.
function parsePhaseReports(text: string): PhaseReport[] {
  const lines = text.split('\n')
  const isDivider = (l: string) => /^[-─_=]{5,}$/.test(l.trim())
  const reports: PhaseReport[] = []
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].trim().match(/^PHASE (\d+) COMPLETE$/i)
    if (!m) continue
    const phase = Number(m[1])
    let j = i + 1
    while (j < lines.length && (isDivider(lines[j]) || lines[j].trim() === '')) j++
    let source: string | undefined
    const sourceMatch = lines[j]?.trim().match(/^Results from (.+):$/i)
    if (sourceMatch) {
      source = sourceMatch[1]
      j++
    }
    const fields: { key: string; value: string }[] = []
    while (j < lines.length) {
      const line = lines[j]
      if (line.trim() === '' || isDivider(line)) break
      const kv = line.match(/^\s{2,}([^:]+?):\s*(.*)$/)
      if (!kv) break
      fields.push({ key: kv[1].trim(), value: kv[2].trim() })
      j++
    }
    reports.push({ phase, source, fields })
  }
  // Keep only the latest report per phase number (a retried/re-run phase
  // would otherwise show twice).
  const byPhase = new Map<number, PhaseReport>()
  for (const r of reports) byPhase.set(r.phase, r)
  return [...byPhase.values()].sort((a, b) => a.phase - b.phase)
}

interface FinalSummary {
  title: string
  phaseResults: { label: string; detail: string }[]
  nextSteps: string[]
  closingLine?: string
}

// The orchestrators' own closing banner (e.g. "STABLE2 RELEASE
// ORCHESTRATION COMPLETE" / "STABLE2 META SYNC COMPLETE") -- same idea as
// parsePhaseReports, but for the one banner at the very end of a full
// multi-phase run, so it renders as a clean card instead of raw box-
// drawing ASCII art.
function parseFinalSummary(text: string): FinalSummary | null {
  const lines = text.split('\n')
  const titleIdx = lines.findIndex((l) => /COMPLETE/.test(l) && /[═║╔╚]/.test(l))
  if (titleIdx === -1) return null
  const title = lines[titleIdx].replace(/[═║╔╗╚╝]/g, '').trim()
  const sectionLines = (label: string) => {
    const start = lines.findIndex((l, i) => i > titleIdx && l.trim().toLowerCase() === `${label.toLowerCase()}:`)
    if (start === -1) return [] as string[]
    const out: string[] = []
    for (let i = start + 1; i < lines.length; i++) {
      if (lines[i].trim() === '') break
      out.push(lines[i].trim())
    }
    return out
  }
  const phaseResults = sectionLines('Phase Results').map((l) => {
    const m = l.match(/^(\d+\.\s*.+?)\s{2,}(.+)$/)
    return m ? { label: m[1].trim(), detail: m[2].trim() } : { label: l, detail: '' }
  })
  const nextSteps = sectionLines('Next Steps')
    .map((l) => l.replace(/^\d+\.\s*/, '').trim())
    .filter(Boolean)
  const closingLine = lines
    .slice(titleIdx)
    .map((l) => l.trim())
    .find((l) => /finished successfully/i.test(l))
  return { title, phaseResults, nextSteps, closingLine }
}

// Collects every box-drawn banner (╔═…╗ / ║ … ║ / ╚═…╝) in a message into
// its plain-text inner lines, border characters stripped -- used both to
// render the orchestrator's startup banner as a card and, generically, to
// scrub leftover ASCII art out of any text we fall back to showing raw.
function extractBoxBanners(text: string): { banners: string[][]; stripped: string } {
  const lines = text.split('\n')
  const banners: string[][] = []
  const remove = new Array(lines.length).fill(false)
  let i = 0
  while (i < lines.length) {
    if (!/^[╔╚]/.test(lines[i].trim())) {
      i++
      continue
    }
    const start = i
    const content: string[] = []
    let j = i + 1
    while (j < lines.length && !/^[╔╚]/.test(lines[j].trim())) {
      const inner = lines[j].trim().replace(/^║/, '').replace(/║$/, '').trim()
      if (inner) content.push(inner)
      j++
    }
    for (let k = start; k <= j; k++) remove[k] = true
    if (content.length) banners.push(content)
    i = j + 1
  }
  const stripped = lines
    .filter((_, idx) => !remove[idx])
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  return { banners, stripped }
}

interface IntroBanner {
  title: string
  subtitle: string | null
  mode: string | null
  phases: string[]
  noticeTitle: string | null
  noticeBody: string[]
  body: string
}

// Every multi-phase orchestrator prints this once, before Phase 1 --
// a title banner, a "Mode: DRY-RUN"/"Mode: LIVE" line, the numbered list
// of phases it's about to run, and (in dry-run) a second notice banner.
// Parsed so the UI can show it as a clean card instead of raw ASCII art.
//
// Some single-shot agents (e.g. on-demand-cherry-pick) print a similar
// banner but with no phase list -- just their own plan content (which
// repos/branches/commits are in scope) below it. `body` captures that
// leftover content (minus the Mode line, the phase list, and any stray
// fenced-code-block delimiter the agent wrapped the whole message in) so
// it can still be shown instead of silently dropped.
function parseIntroBanner(text: string): IntroBanner | null {
  const { banners, stripped } = extractBoxBanners(text)
  if (banners.length === 0) return null
  const [title, ...subtitleLines] = banners[0]
  if (!title) return null
  const modeMatch = text.match(/^Mode:\s*(.+)$/im)
  const phasesMatch = text.match(/execute \d+ phases?:?\s*\n([\s\S]*?)(?:\n\s*\n|$)/i)
  const phases = phasesMatch
    ? phasesMatch[1]
        .split('\n')
        .map((l) => l.trim().replace(/^\d+\.\s*/, ''))
        .filter(Boolean)
    : []
  const notice = banners[1] ?? []
  // The notice banner's first ALL-CAPS line is its heading (e.g. "DRY-RUN
  // MODE ACTIVE"); everything after is body text/an example command.
  const [noticeTitle, ...noticeBody] = notice
  let body = stripped
  if (modeMatch) body = body.replace(modeMatch[0], '')
  if (phasesMatch) body = body.replace(phasesMatch[0], '')
  body = body
    .split('\n')
    .filter((l) => l.trim() !== '```')
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  return {
    title,
    subtitle: subtitleLines.join(' ').trim() || null,
    mode: modeMatch?.[1]?.trim() ?? null,
    phases,
    noticeTitle: noticeTitle ?? null,
    noticeBody,
    body,
  }
}

interface UpcomingPhase {
  phase: number
  title: string
  checklist: string[]
  prompt: string | null
}

// The "PHASE N: TITLE" divider + "This phase will: ✓ …" checklist that
// every orchestrator prints right before pausing for its Y/n gate --
// parsed into a title/checklist/prompt triple so the UI can show "what's
// about to happen" as a card instead of a raw divider-and-bullets dump.
function parseUpcomingPhase(text: string): UpcomingPhase | null {
  const headerMatch = text.match(/^PHASE (\d+):\s*(.+)$/im)
  if (!headerMatch) return null
  const phase = Number(headerMatch[1])
  const title = headerMatch[2].trim()
  const afterHeader = text.slice((headerMatch.index ?? 0) + headerMatch[0].length)
  const checklist: string[] = []
  const checklistMatch = afterHeader.match(/this phase will:?\s*\n([\s\S]*?)(?:\n\s*\n|$)/i)
  if (checklistMatch) {
    for (const line of checklistMatch[1].split('\n')) {
      const item = line.trim().replace(/^[✓✔x\-*]\s*/i, '')
      if (item) checklist.push(item)
    }
  }
  return { phase, title, checklist, prompt: extractTrailingPrompt(text) }
}

function IntroBannerCard({ banner }: { banner: IntroBanner }) {
  return (
    <div className="orchestrator-banner">
      <div className="orchestrator-banner-title">{banner.title}</div>
      {banner.subtitle && <p className="orchestrator-banner-subtitle">{banner.subtitle}</p>}
      <div className="orchestrator-banner-meta">
        {banner.mode && (
          <span className={`mode-pill mode-pill-${banner.mode.toLowerCase().includes('dry') ? 'dry' : 'live'}`}>
            {banner.mode}
          </span>
        )}
        {banner.phases.length > 0 && <span className="orchestrator-banner-count">{banner.phases.length} phases</span>}
      </div>
      {banner.phases.length > 0 && (
        <ol className="orchestrator-banner-phases">
          {banner.phases.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ol>
      )}
      {banner.noticeTitle && (
        <div className="orchestrator-notice">
          <div className="orchestrator-notice-title">
            <AlertTriangle size={14} />
            {banner.noticeTitle}
          </div>
          {banner.noticeBody.map((line, i) =>
            /^--\S/.test(line) ? (
              <code className="orchestrator-notice-code" key={i}>
                {line}
              </code>
            ) : (
              <p key={i}>{line}</p>
            ),
          )}
        </div>
      )}
    </div>
  )
}

function UpcomingPhaseCard({ phase }: { phase: UpcomingPhase }) {
  return (
    <div className="upcoming-phase-card">
      <div className="upcoming-phase-header">
        <span className="stage-card-badge">{phase.phase}</span>
        <span className="stage-card-title">Next: {phase.title}</span>
      </div>
      {phase.checklist.length > 0 && (
        <ul className="upcoming-phase-checklist">
          {phase.checklist.map((item, i) => (
            <li key={i}>
              <Check size={13} />
              {item}
            </li>
          ))}
        </ul>
      )}
      {phase.prompt && (
        <div className="result-prompt">
          <ShieldQuestion size={15} />
          <span>{phase.prompt}</span>
        </div>
      )}
    </div>
  )
}

function PhaseReportCard({ report, label, color }: { report: PhaseReport; label?: string; color: string }) {
  return (
    <div className={`stage-card stage-card-${color}`}>
      <div className="stage-card-header">
        <span className="stage-card-badge">{report.phase}</span>
        <span className="stage-card-title">{label ?? report.source ?? `Phase ${report.phase}`}</span>
        <span className="stage-card-status">
          <Check size={11} /> Done
        </span>
      </div>
      {report.fields.length > 0 && (
        <div className="stage-card-fields">
          {report.fields.map((f, i) => (
            <div className="stage-card-field" key={i}>
              <span className="stage-card-field-key">{f.key}</span>
              <span className="stage-card-field-value">{f.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function FinalSummaryCard({ summary, compact = false }: { summary: FinalSummary; compact?: boolean }) {
  return (
    <div className={compact ? 'result-section orchestrator-recap' : 'result-structured'}>
      {compact ? (
        <h3>{summary.title}</h3>
      ) : (
        <div className="result-status result-status-success">
          <CheckCircle2 size={15} />
          {summary.title}
        </div>
      )}
      {summary.phaseResults.length > 0 &&
        (compact ? (
          <div className="stage-card-fields">
            {summary.phaseResults.map((p, i) => (
              <div className="stage-card-field" key={i}>
                <span className="stage-card-field-key">{p.label}</span>
                <span className="stage-card-field-value">{p.detail}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="result-section">
            <h3>Phase results</h3>
            <div className="stage-card-fields">
              {summary.phaseResults.map((p, i) => (
                <div className="stage-card-field" key={i}>
                  <span className="stage-card-field-key">{p.label}</span>
                  <span className="stage-card-field-value">{p.detail}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      {!compact && summary.nextSteps.length > 0 && (
        <div className="result-section">
          <h3>Next steps</h3>
          <ul>
            {summary.nextSteps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      {!compact && summary.closingLine && <p className="result-summary">{summary.closingLine}</p>}
    </div>
  )
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
        byId[id] = {
          id,
          kind: 'tool',
          tool: part.tool,
          toolStatus: part.state?.status,
          toolCommand: part.state?.input?.command,
          toolOutput: part.state?.output,
          text: '',
        }
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
            const input = (state.input as Record<string, unknown>) ?? {}
            upsertPart(part.id as string, {
              kind: 'tool',
              tool: part.tool as string,
              toolStatus: state.status as string,
              toolCommand: input.command as string | undefined,
              toolOutput: state.output as string | undefined,
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
  const resultBlock = finalText ? parseResultBlock(finalText) : null

  // Phase/final-summary reports are spread across multiple separate
  // assistant messages (the orchestrator pauses for a Y/n between each
  // one) -- scan every text part, not just the last, to build the
  // stage-by-stage breakdown as the run progresses.
  const allAssistantText = partOrder
    .map((id) => parts[id])
    .filter((p): p is Part => !!p && p.kind === 'text' && !p.text.startsWith('> '))
    .map((p) => p.text)
    .join('\n\n')
  const phaseReports = allAssistantText ? parsePhaseReports(allAssistantText) : []
  // The orchestrator's own closing banner can appear either as the whole
  // finalText (no ===RESULT=== block yet) or tucked inside resultBlock's
  // "before" text (once AGENTS.md's block has been appended after it) --
  // check both so the stage-by-stage recap still renders either way.
  const finalSummary = parseFinalSummary(resultBlock ? resultBlock.before : finalText)
  // Anything left in resultBlock.before once the mechanical phase/banner
  // text is stripped out is genuine plain-language prose the agent wrote
  // (e.g. a one-line intro) -- safe to show as-is, unlike the raw blocks.
  const cleanedBefore = resultBlock ? stripReportingNoise(resultBlock.before) : ''
  // The orchestrator's one-time startup banner (title/mode/phase list) and
  // its per-phase "PHASE N: TITLE" + checklist + Y/n gate are both plain
  // ASCII art in the raw transcript -- parse them into cards instead of
  // ever showing that raw text in the Output panel.
  const introBanner = !resultBlock && !finalSummary ? parseIntroBanner(finalText) : null
  const upcomingPhase = !resultBlock && !finalSummary ? parseUpcomingPhase(finalText) : null
  // Mid-run, with no result/summary yet, the last message is usually just
  // a "PHASE N COMPLETE" report (already shown as a stage card above)
  // optionally followed by a Y/n confirmation -- surface only that
  // trailing question, not the whole message, so nothing looks duplicated.
  // Skipped when upcomingPhase already captured the same prompt.
  const pendingPrompt = !resultBlock && !finalSummary && !upcomingPhase ? extractTrailingPrompt(finalText) : null
  // introBanner.body already holds the plan's real content (e.g.
  // on-demand-cherry-pick's repo/branch/commit table) -- strip the
  // trailing Y/n question back off it when present, since pendingPrompt
  // already renders that separately as its own actionable prompt.
  const introBannerBody =
    introBanner && pendingPrompt && introBanner.body.endsWith(pendingPrompt)
      ? introBanner.body.slice(0, introBanner.body.length - pendingPrompt.length).trim()
      : (introBanner?.body ?? '')

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

      {phaseReports.length > 0 && (
        <div className="stage-results">
          {phaseReports.map((report) => (
            <PhaseReportCard
              key={report.phase}
              report={report}
              label={phaseLabels?.[report.phase - 1]}
              color={STAGE_COLORS[(report.phase - 1) % STAGE_COLORS.length]}
            />
          ))}
        </div>
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
        {resultBlock ? (
          <div className="result-structured">
            {cleanedBefore && (
              <div className="result-text result-text-before">
                <ReactMarkdown>{cleanedBefore}</ReactMarkdown>
              </div>
            )}
            {(() => {
              const meta = RESULT_STATUS_META[resultBlock.status]
              const StatusIcon = meta.icon
              return (
                <div className={`result-status result-status-${resultBlock.status}`}>
                  <StatusIcon size={15} />
                  {meta.label}
                </div>
              )
            })()}
            {finalSummary && <FinalSummaryCard summary={finalSummary} compact />}
            {resultBlock.summary && (
              <div className="result-section">
                <h3>Summary</h3>
                <p className="result-summary">{resultBlock.summary}</p>
              </div>
            )}
            {resultBlock.table && (
              <div className="result-section">
                <h3>Breakdown</h3>
                <ResultTableView table={resultBlock.table} />
              </div>
            )}
            {resultBlock.details.length > 0 && (
              <div className="result-section">
                <h3>Details</h3>
                <ul>
                  {resultBlock.details.map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              </div>
            )}
            {resultBlock.nextSteps.length > 0 && (
              <div className="result-section">
                <h3>Next steps</h3>
                <ul>
                  {resultBlock.nextSteps.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : finalSummary ? (
          <FinalSummaryCard summary={finalSummary} />
        ) : introBanner || upcomingPhase || phaseReports.length > 0 || pendingPrompt ? (
          <div className="result-pending">
            {introBanner && <IntroBannerCard banner={introBanner} />}
            {introBannerBody && (
              <div className="result-text">
                <ReactMarkdown>{introBannerBody}</ReactMarkdown>
              </div>
            )}
            {upcomingPhase ? (
              <UpcomingPhaseCard phase={upcomingPhase} />
            ) : pendingPrompt ? (
              <div className="result-prompt">
                <ShieldQuestion size={15} />
                <span>{pendingPrompt}</span>
              </div>
            ) : phaseReports.length > 0 ? (
              <p className="result-muted">Phase complete — see the summary above. Waiting for the next phase to start…</p>
            ) : null}
          </div>
        ) : (
          <div className="result-text">
            {finalText ? (
              <ReactMarkdown>{extractBoxBanners(finalText).stripped}</ReactMarkdown>
            ) : busy ? (
              <span className="typing-indicator">
                Working <span /> <span /> <span />
              </span>
            ) : (
              'Waiting for output…'
            )}
          </div>
        )}
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
              return <ToolLogEntry key={id} part={part} />
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

      {!isComplete && (
        <div className="quick-replies">
          <button className="btn-secondary" onClick={() => sendReply('Y')}>
            Yes
          </button>
          <button className="btn-secondary" onClick={() => sendReply('n')}>
            No
          </button>
        </div>
      )}
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
