import { BookOpen } from 'lucide-react'
import { useEffect, useState } from 'react'
import { listWorkflows, type Workflow } from '../api/client'

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

export default function Documentation() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listWorkflows()
      .then(setWorkflows)
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false))
  }, [])

  const primary = workflows.filter((w) => w.category === 'primary')
  const advanced = workflows.filter((w) => w.category !== 'primary')

  return (
    <div className="docs-page">
      <p className="subtitle">How each workflow actually works, step by step.</p>
      {error && <div className="banner error">{error}</div>}

      <section className="docs-intro">
        <h2 className="section-heading">Getting started</h2>
        <p>
          Every workflow here mirrors what an engineer would type at the <code>opencode</code> CLI directly — this UI
          just gives it a form, streams the conversation as a chat transcript, and renders any permission or
          multiple-choice prompts the agent asks along the way as Allow/Deny or radio-button panels instead of raw
          text.
        </p>
        <p>
          The <strong>Common workflows</strong> section on the Dashboard covers the three real use cases most people
          need: the biweekly Main Tagging scan, the full stable2 release pipeline, and On-Demand Cherry-Pick for a
          single hotfix ticket. Everything under <strong>Individual skills</strong> is one step of that same
          pipeline, useful for re-running or debugging a single stage without redoing the whole thing.
        </p>
        <p>
          Workflows marked <span className="badge badge-mutating">mutating</span> actually push to GitHub/Gerrit or
          write to Jira — read-only ones only fetch/compute and write local state files.
        </p>
      </section>

      {loading && (
        <div className="docs-list">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton skeleton-row" />
          ))}
        </div>
      )}

      {!loading && (
        <>
          <section>
            <h2 className="section-heading">Common workflows</h2>
            <div className="docs-list">
              {primary.map((w) => (
                <DocEntry key={w.id} workflow={w} />
              ))}
            </div>
          </section>

          <section>
            <h2 className="section-heading">Individual skills</h2>
            <div className="docs-list">
              {advanced.map((w) => (
                <DocEntry key={w.id} workflow={w} />
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  )
}

function DocEntry({ workflow }: { workflow: Workflow }) {
  return (
    <div className="docs-entry">
      <div className="docs-entry-header">
        <BookOpen size={16} />
        <h3>{workflow.label}</h3>
        {workflow.mutating && <span className="badge badge-mutating">mutating</span>}
      </div>
      <p className="docs-entry-description">{workflow.description}</p>
      {workflow.help_steps.length > 0 && (
        <ol className="docs-entry-steps">
          {workflow.help_steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
    </div>
  )
}
