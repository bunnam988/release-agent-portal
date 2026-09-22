import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { startSession, type Workflow } from '../api/client'
import Modal from './Modal'

const INTEGRATION_LABELS: Record<string, string> = {
  'jira-ccp': 'Jira',
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
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
            arg.kind === 'flag' ? (
              <label key={arg.name} className="arg-flag">
                <input type="checkbox" onChange={(e) => setValue(arg.name, e.target.checked)} />
                {arg.label}
              </label>
            ) : (
              <label key={arg.name} className="arg-text">
                {arg.label}
                {arg.required && <span className="required-mark">*</span>}
                <input
                  type="text"
                  autoFocus={workflow.args[0]?.name === arg.name}
                  placeholder={arg.required ? `${arg.label} (required)` : arg.label}
                  onChange={(e) => setValue(arg.name, e.target.value)}
                />
              </label>
            ),
          )}
        </div>
      )}

      {missing.length > 0 && (
        <div className="required-hint">Fill in {missing.map((a) => a.label).join(', ')} to run</div>
      )}
    </Modal>
  )
}
