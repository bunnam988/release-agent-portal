import { Outlet } from 'react-router-dom'
import ShortcutsHelp from './ShortcutsHelp'
import Sidebar from './Sidebar'
import Topbar from './Topbar'

export default function AppShell() {
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-main">
        <Topbar />
        <div className="app-content">
          <Outlet />
        </div>
      </div>
      <ShortcutsHelp />
    </div>
  )
}
