import { LogOut, Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { getAuthStatus, logout } from '../api/client'
import { useTheme } from '../hooks/useTheme'

function titleForPath(pathname: string): string {
  if (pathname === '/') return 'Dashboard'
  if (pathname.startsWith('/history')) return 'History'
  if (pathname.startsWith('/sessions/')) return 'Workflow Run'
  return 'Release Agent'
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

export default function Topbar() {
  const location = useLocation()
  const [theme, toggleTheme] = useTheme()
  const [user, setUser] = useState(() => window.localStorage.getItem('portal_user') || '')
  const [authEnabled, setAuthEnabled] = useState(false)
  const [role, setRole] = useState(() => window.localStorage.getItem('portal_role') || '')

  useEffect(() => {
    if (user) window.localStorage.setItem('portal_user', user)
  }, [user])

  useEffect(() => {
    // Only show "Log out"/role badge at all if the shared-password gate
    // (see AuthGate.tsx / backend/app/routers/auth.py) is actually
    // configured -- otherwise there's nothing to log out of and every
    // session is implicitly "admin".
    getAuthStatus()
      .then((s) => {
        setAuthEnabled(s.auth_enabled)
        if (s.role) setRole(s.role)
      })
      .catch(() => {})
  }, [])

  function editName() {
    const next = window.prompt('Your name (used to attribute runs you start):', user || '')
    if (next !== null && next.trim()) setUser(next.trim())
  }

  async function doLogout() {
    await logout().catch(() => {})
    // A re-login could pick a different role (admin vs user) -- don't
    // leave the old one cached.
    window.localStorage.removeItem('portal_role')
    window.location.reload()
  }

  return (
    <header className="topbar">
      <h1 className="topbar-title">{titleForPath(location.pathname)}</h1>
      <div className="topbar-right">
        <span className="shortcuts-hint" title="Press ? to see keyboard shortcuts">
          <kbd>?</kbd> shortcuts
        </span>
        {authEnabled && role && <span className={`role-badge role-badge-${role}`}>{role}</span>}
        <button
          className="theme-toggle"
          onClick={toggleTheme}
          title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
        >
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>
        <button className="user-badge" onClick={editName} title="Click to change your name">
          <span className="user-avatar">{initials(user || 'You')}</span>
          <span className="user-name">{user || 'Set your name'}</span>
        </button>
        {authEnabled && (
          <button className="logout-button" onClick={doLogout} title="Log out">
            <LogOut size={15} />
          </button>
        )}
      </div>
    </header>
  )
}
