import { CalendarClock, Cherry, ChevronDown, ChevronUp, Rocket, Wrench, type LucideIcon } from 'lucide-react'
import { useEffect, useMemo, useState, type MouseEvent } from 'react'
import RunWorkflowModal from '../components/RunWorkflowModal'
import { getIntegrationStatus, listWorkflows, type Workflow } from '../api/client'

const PRIMARY_ICONS: Record<string, LucideIcon> = {
  'main-tagging': CalendarClock,
  'stable2-release-orchestrator': Rocket,
  'on-demand-cherry-pick': Cherry,
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

// Cursor-tracking spotlight: purely decorative, tracks pointer position as
// CSS custom properties a ::before radial-gradient reads (see .hero-card
// in App.css). No state/re-render involved -- cheap enough to run on
// every mousemove.
function trackGlow(e: MouseEvent<HTMLDivElement>) {
  const rect = e.currentTarget.getBoundingClientRect()
  e.currentTarget.style.setProperty('--glow-x', `${e.clientX - rect.left}px`)
  e.currentTarget.style.setProperty('--glow-y', `${e.clientY - rect.top}px`)
}

export default function Dashboard() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [integrations, setIntegrations] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeWorkflow, setActiveWorkflow] = useState<Workflow | null>(null)
  // Collapsed by default -- keeps the dashboard focused on the primary
  // workflows first; individual skills are one click away via the
  // toggle below for anyone who needs them.
  const [showAdvanced, setShowAdvanced] = useState(false)

  useEffect(() => {
    listWorkflows()
      .then(setWorkflows)
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false))
    getIntegrationStatus()
      .then(setIntegrations)
      .catch(() => {
        // Non-fatal: if this fails, workflows just won't show a
        // down-integration warning; starting a run still fails safely
        // server-side.
      })
  }, [])

  const primaryWorkflows = useMemo(() => workflows.filter((w) => w.category === 'primary'), [workflows])
  const advancedWorkflows = useMemo(() => workflows.filter((w) => w.category !== 'primary'), [workflows])

  function unavailableIntegrations(workflow: Workflow): string[] {
    return workflow.requires_mcp.filter((m) => integrations[m] === false)
  }

  return (
    <div>
      {error && <div className="banner error">{error}</div>}

      <section className="hero-section">
        <div className="hero-backdrop" aria-hidden="true" />
        <h2 className="section-heading">Common workflows</h2>
        <div className="hero-grid">
          {loading &&
            [0, 1, 2].map((i) => <div key={i} className="skeleton skeleton-hero" />)}
          {!loading &&
            primaryWorkflows.map((w) => {
            const down = unavailableIntegrations(w)
            const Icon = PRIMARY_ICONS[w.id] ?? Wrench
            return (
              <div className="hero-card" key={w.id} onMouseMove={trackGlow}>
                <div className="hero-icon">
                  <Icon size={24} />
                </div>
                <div className="hero-card-body">
                  <h3>{w.label}</h3>
                  <p>{w.description}</p>
                </div>
                {down.length > 0 && <div className="unavailable-tag">Unavailable right now</div>}
                <button className="hero-run" disabled={down.length > 0} onClick={() => setActiveWorkflow(w)}>
                  Run
                </button>
              </div>
            )
          })}
        </div>
      </section>

      <section>
        <button className="advanced-toggle-card" onClick={() => setShowAdvanced((v) => !v)}>
          <div className="advanced-toggle-icon">
            <Wrench size={18} />
          </div>
          <div className="advanced-toggle-text">
            <div className="advanced-toggle-title">Individual skills</div>
            <div className="advanced-toggle-subtitle">
              Run a single step yourself — {advancedWorkflows.length} skills available
            </div>
          </div>
          {showAdvanced ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </button>

        {showAdvanced && (
          <div className="advanced-list">
            {advancedWorkflows.map((w) => {
              const down = unavailableIntegrations(w)
              return (
                <div className="advanced-row" key={w.id}>
                  <div className="advanced-row-icon">
                    <Wrench size={16} />
                  </div>
                  <div className="advanced-row-main">
                    <div className="advanced-row-title">
                      {w.label}
                      {w.mutating && <span className="badge badge-mutating">mutating</span>}
                      {down.length > 0 && <span className="badge badge-warning">unavailable</span>}
                    </div>
                    <div className="advanced-row-description">{w.description}</div>
                  </div>
                  <button disabled={down.length > 0} onClick={() => setActiveWorkflow(w)}>
                    Run
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </section>

      {activeWorkflow && (
        <RunWorkflowModal
          workflow={activeWorkflow}
          unavailableIntegrations={unavailableIntegrations(activeWorkflow)}
          onClose={() => setActiveWorkflow(null)}
        />
      )}
    </div>
  )
}
