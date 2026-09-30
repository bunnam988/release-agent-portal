import { ChevronDown, ChevronUp, ListChecks, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { getMainTaggingRepos, startSession, type TrackedRepo, type Workflow, type WorkflowArg } from '../api/client'
import Modal from './Modal'

const INTEGRATION_LABELS: Record<string, string> = {
  'jira-ccp': 'Jira',
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** Searchable checkbox picker for main-tagging's --repos arg. Value is
 * kept as a single comma-joined string (matching how build_message
 * assembles every non-flag arg into `--flag value`), not an array --
 * this way no backend/schema change was needed beyond a new `kind`, the
 * join/split is entirely a frontend concern. */
function RepoMultiSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [repos, setRepos] = useState<TrackedRepo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    getMainTaggingRepos()
      .then(setRepos)
      .catch((err) => setLoadError(errorMessage(err)))
      .finally(() => setLoading(false))
  }, [])

  const selected = new Set(value ? value.split(',') : [])
  const filtered = repos.filter((r) => r.slug.toLowerCase().includes(search.toLowerCase()))

  function toggle(slug: string) {
    const next = new Set(selected)
    if (next.has(slug)) next.delete(slug)
    else next.add(slug)
    onChange(Array.from(next).join(','))
  }

  if (loading) return <div className="repo-picker-loading">Loading repos…</div>
  if (loadError) {
    return (
      <div className="banner error">
        Couldn't load the repo list: {loadError}
      </div>
    )
  }

  return (
    <div className="repo-picker">
      <div className="repo-picker-toolbar">
        <div className="repo-picker-search">
          <Search size={14} />
          <input
            type="text"
            placeholder="Search repos…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <button type="button" className="repo-picker-action" onClick={() => onChange(repos.map((r) => r.slug).join(','))}>
          Select all ({repos.length})
        </button>
        <button type="button" className="repo-picker-action" onClick={() => onChange('')}>
          Clear
        </button>
      </div>
      <div className="repo-picker-count">
        {selected.size} of {repos.length} selected
      </div>
      <div className="repo-picker-list">
        {filtered.length === 0 && <div className="repo-picker-empty">No repos match "{search}"</div>}
        {filtered.map((r) => (
          <label key={r.slug} className="repo-picker-item">
            <input type="checkbox" checked={selected.has(r.slug)} onChange={() => toggle(r.slug)} />
            {r.slug}
          </label>
        ))}
      </div>
    </div>
  )
}

function renderArg(
  arg: WorkflowArg,
  value: string | boolean | undefined,
  autoFocus: boolean,
  setValue: (name: string, value: string | boolean) => void,
) {
  if (arg.kind === 'flag') {
    return (
      <label key={arg.name} className="arg-flag">
        <input type="checkbox" onChange={(e) => setValue(arg.name, e.target.checked)} />
        {arg.label}
      </label>
    )
  }
  if (arg.kind === 'repo-multiselect') {
    return (
      <div key={arg.name} className="arg-repo-multiselect">
        <span className="arg-repo-multiselect-label">
          {arg.label}
          {arg.required && <span className="required-mark">*</span>}
        </span>
        <RepoMultiSelect value={(value as string) ?? ''} onChange={(v) => setValue(arg.name, v)} />
      </div>
    )
  }
  return (
    <label key={arg.name} className="arg-text">
      {arg.label}
      {arg.required && <span className="required-mark">*</span>}
      <input
        type="text"
        autoFocus={autoFocus}
        placeholder={arg.required ? `${arg.label} (required)` : arg.label}
        onChange={(e) => setValue(arg.name, e.target.value)}
      />
    </label>
  )
}

interface RunWorkflowModalProps {
  workflow: Workflow
  unavailableIntegrations: string[]
  onClose: () => void
}

/** The input form for a single workflow, shown as a popup so the dashboard
 * cards themselves never vary in size based on how many args a workflow
 * happens to need. */
export default function RunWorkflowModal({ workflow, unavailableIntegrations, onClose }: RunWorkflowModalProps) {
  const [values, setValues] = useState<Record<string, string | boolean>>({})
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Expanded by default -- a first-time user should immediately see how
  // the workflow works, not have to notice and click a small toggle.
  const [showHelp, setShowHelp] = useState(true)
  const navigate = useNavigate()

  const missing = workflow.args.filter((arg) => arg.required && !String(values[arg.name] ?? '').trim())
  const blocked = unavailableIntegrations.length > 0

  function setValue(name: string, value: string | boolean) {
    setValues((prev) => ({ ...prev, [name]: value }))
  }

  async function run() {
    setStarting(true)
    setError(null)
    try {
      const user = window.localStorage.getItem('portal_user') || 'unknown'
      const { session_id } = await startSession(workflow.id, values, user)
      toast.success(`${workflow.label} started`)
      navigate(`/sessions/${session_id}?workflow=${workflow.id}`)
    } catch (err) {
      const message = errorMessage(err)
      setError(message)
      toast.error(`Couldn't start ${workflow.label}`, { description: message })
      setStarting(false)
    }
  }

  return (
    <Modal
      title={workflow.label}
      onClose={onClose}
      footer={
        <>
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button onClick={run} disabled={starting || blocked || missing.length > 0}>
            {starting && <span className="spinner" />}
            {starting ? 'Starting…' : 'Start'}
          </button>
        </>
      }
    >
      <p className="modal-description">{workflow.description}</p>

      {workflow.help_steps.length > 0 && (
        <div className="help-section">
          <button type="button" className="help-toggle" onClick={() => setShowHelp((v) => !v)}>
            <ListChecks size={14} />
            How this works
            {showHelp ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
          {showHelp && (
            <ol className="help-steps">
              {workflow.help_steps.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          )}
        </div>
      )}

      {blocked && (
        <div className="banner warning">
          {unavailableIntegrations.map((m) => INTEGRATION_LABELS[m] ?? m).join(', ')} unavailable right now — try
          again later.
        </div>
      )}
      {error && <div className="banner error">{error}</div>}

      {workflow.args.length > 0 && (
        <div className="modal-form">
          {workflow.args.map((arg) =>
            renderArg(arg, values[arg.name], workflow.args[0]?.name === arg.name, setValue),
          )}
        </div>
      )}

      {missing.length > 0 && (
        <div className="required-hint">Fill in {missing.map((a) => a.label).join(', ')} to run</div>
      )}
    </Modal>
  )
}
