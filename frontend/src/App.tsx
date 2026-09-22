import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import AppShell from './components/AppShell'
import AuthGate from './components/AuthGate'
import Dashboard from './pages/Dashboard'
import History from './pages/History'
import SessionView from './pages/SessionView'
import './App.css'

function App() {
  return (
    <BrowserRouter>
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: 'var(--bg-elevated-2)',
            border: '1px solid var(--border-strong)',
            color: 'var(--text)',
            fontFamily: 'inherit',
            fontSize: '0.86rem',
          },
        }}
      />
      <AuthGate>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/history" element={<History />} />
            <Route path="/sessions/:sessionId" element={<SessionView />} />
          </Route>
        </Routes>
      </AuthGate>
    </BrowserRouter>
  )
}

export default App
