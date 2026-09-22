import { Clock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listSessions, type SessionSummary } from '../api/client'

function formatTime(ms?: number): string {
  if (!ms) return '—'
  return new Date(ms).toLocaleString()
}

export default function History() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listSessions()
      .then((all) => {
        const sorted = [...all].sort((a, b) => (b.time?.created ?? 0) - (a.time?.created ?? 0))
        setSessions(sorted)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="history-page">
      <p className="subtitle">Past and in-progress runs on this opencode instance.</p>
      {error && <div className="banner error">{error}</div>}
      {loading && (
        <div className="history-list">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton skeleton-row" />
          ))}
        </div>
      )}
      {!loading && sessions.length === 0 && (
        <div className="empty-state">
          <Clock size={28} />
          <p>No runs yet. Start one from the Dashboard.</p>
        </div>
      )}
      {sessions.length > 0 && (
        <div className="history-list">
          {sessions.map((s) => (
            <Link className="history-row" to={`/sessions/${s.id}`} key={s.id}>
              <div className="history-row-main">
                <div className="history-row-title">{s.title || s.id}</div>
                <div className="history-row-meta">
                  {formatTime(s.time?.created)}
                  {s.model?.providerID && ` · ${s.model.providerID}/${s.model.id}`}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
