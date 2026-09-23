import { Eye, EyeOff, Loader2, User } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { toast } from 'sonner'
import { getAuthStatus, login } from '../api/client'
import RdkMark from './RdkMark'

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

type Step = 'checking' | 'password' | 'name' | 'ready'

// Gates the whole app behind two shared passwords, one per role (see
// backend/app/routers/auth.py) -- still not per-user identity, just two
// shared secrets. Once logged in, also requires a display name be set
// (used for run attribution, e.g. "Started by Alice") before anything
// else is reachable -- previously this was optional and only prompted
// lazily from the topbar. If neither PORTAL_ADMIN_PASSWORD nor
// PORTAL_USER_PASSWORD is configured server-side, /api/auth/status
// reports auth_enabled: false and the password step is skipped entirely,
// but the name step still applies (it's independent of the password
// gate) so every user of a shared instance is still attributable.
export default function AuthGate({ children }: { children: ReactNode }) {
  const [step, setStep] = useState<Step>('checking')
  const [authWasEnabled, setAuthWasEnabled] = useState(false)
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [passwordError, setPasswordError] = useState(false)
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const shakeTimeout = useRef<number | undefined>(undefined)

  function afterAuth() {
    const existingName = window.localStorage.getItem('portal_user')
    setStep(existingName ? 'ready' : 'name')
  }

  useEffect(() => {
    getAuthStatus()
      .then((s) => {
        setAuthWasEnabled(s.auth_enabled)
        if (s.role) window.localStorage.setItem('portal_role', s.role)
        if (!s.auth_enabled || s.authenticated) {
          afterAuth()
        } else {
          setStep('password')
        }
      })
      .catch(() => {
        // Backend unreachable -- let the rest of the app's own health
        // checks/error states explain that, rather than getting stuck on
        // a login screen for the wrong reason.
        setStep('ready')
      })
    return () => window.clearTimeout(shakeTimeout.current)
  }, [])

  async function onSubmitPassword(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setPasswordError(false)
    try {
      const res = await login(password)
      window.localStorage.setItem('portal_role', res.role)
      afterAuth()
    } catch (err) {
      setPasswordError(true)
      window.clearTimeout(shakeTimeout.current)
      shakeTimeout.current = window.setTimeout(() => setPasswordError(false), 500)
      toast.error('Login failed', { description: errorMessage(err) })
    } finally {
      setSubmitting(false)
    }
  }

  function onSubmitName(e: FormEvent) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    window.localStorage.setItem('portal_user', trimmed)
    setStep('ready')
  }

  if (step === 'checking') return null

  if (step === 'password' || step === 'name') {
    // Only show a 2-step indicator when there's actually a password step
    // to count -- if auth is disabled, the name screen is the only step.
    const stepNumber = step === 'password' ? 1 : 2
    return (
      <div className="auth-gate">
        <div className="auth-backdrop" />
        <div className="auth-card-wrap">
          <div className="auth-brand">
            <div className="auth-brand-icon">
              <RdkMark size={30} />
            </div>
            <div className="auth-brand-name">RDKB Middleware Release Agent</div>
          </div>

          {step === 'password' && (
            <form className={`auth-card ${passwordError ? 'auth-card-shake' : ''}`} onSubmit={onSubmitPassword}>
              <h1>Welcome back</h1>
              <p className="auth-subtitle">Enter the shared password to continue.</p>
              <div className="auth-input-group">
                <input
                  className={passwordError ? 'auth-input-error' : ''}
                  type={showPassword ? 'text' : 'password'}
                  autoFocus
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value)
                    setPasswordError(false)
                  }}
                  placeholder="Password"
                />
                <button
                  type="button"
                  className="auth-input-adornment"
                  onClick={() => setShowPassword((v) => !v)}
                  tabIndex={-1}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              <button type="submit" disabled={submitting || !password}>
                {submitting ? (
                  <>
                    <Loader2 size={15} className="spin" /> Checking…
                  </>
                ) : (
                  'Continue'
                )}
              </button>
            </form>
          )}

          {step === 'name' && (
            <form className="auth-card auth-card-in" onSubmit={onSubmitName}>
              <h1>What's your name?</h1>
              <p className="auth-subtitle">
                Used to attribute the runs you start — shown to anyone else using this portal.
              </p>
              <div className="auth-input-group">
                <input
                  type="text"
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Your name"
                />
                <span className="auth-input-adornment auth-input-icon">
                  <User size={16} />
                </span>
              </div>
              <button type="submit" disabled={!name.trim()}>
                Continue
              </button>
            </form>
          )}

          {authWasEnabled && (
            <div className="auth-steps">
              <span className={`auth-step-dot ${stepNumber >= 1 ? 'auth-step-dot-done' : ''}`} />
              <span className={`auth-step-dot ${stepNumber >= 2 ? 'auth-step-dot-done' : ''}`} />
            </div>
          )}

          <p className="auth-footer">Internal tool — release engineering use only.</p>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
