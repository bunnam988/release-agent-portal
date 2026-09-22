import { History, LayoutDashboard, Rocket } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { useHealth } from '../hooks/useHealth'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/history', label: 'History', icon: History, end: false },
]

export default function Sidebar() {
  const health = useHealth()

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-icon">
          <Rocket size={20} />
        </div>
        <div>
          <div className="sidebar-brand-title">Release Agent</div>
          <div className="sidebar-brand-subtitle">RDK Broadband Portal</div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) => `sidebar-link${isActive ? ' sidebar-link-active' : ''}`}
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className={`connection-pill ${health === true ? 'ok' : health === false ? 'down' : 'pending'}`}>
          <span className="connection-dot" />
          {health === true ? 'Agent connected' : health === false ? 'Agent unreachable' : 'Connecting…'}
        </div>
      </div>
    </aside>
  )
}
