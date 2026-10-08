// Operator sign-in. With sign-in required (STI_OPERATOR_TOKEN on the server)
// it asks for the access token as well as a name; without, only for the name
// that goes into the incident log. The token is checked against the server
// before it is kept, so a typo is caught here rather than on the next action.

import { KeyRound, X } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { authHeaders, getName, signIn } from '../lib/auth'

export default function SignInDialog({ required, onClose }: { required: boolean; onClose: () => void }) {
  const [name, setName] = useState(getName)
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    if (!required) {
      signIn('', name)
      onClose()
      return
    }
    setBusy(true)
    try {
      const r = await fetch('/api/auth/check', { method: 'POST', headers: authHeaders(token.trim(), name) })
      if (r.ok) {
        signIn(token.trim(), name)
        onClose()
      } else if (r.status === 429) setError('Too many attempts. Wait a minute and try again.')
      else setError('That access token was not accepted.')
    } catch {
      setError('The server is not reachable.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <form
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="signin-title"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2 id="signin-title" className="modal-t">
            <KeyRound size={18} /> {required ? 'Operator sign-in' : 'Operator name'}
          </h2>
          <button type="button" className="btn btn-icon btn-ghost" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <p className="note">
          {required
            ? 'Viewing needs no sign-in. Changing anything — acknowledging an incident, running live detection, analysing footage, calibrating — does.'
            : 'Sign-in is not required on this server. Your name is recorded against the incidents you acknowledge or resolve.'}
        </p>
        <label className="field mt-2" style={{ maxWidth: 'none' }}>
          <span className="field-l">Your name</span>
          <input type="text" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        {required && (
          <label className="field mt-2" style={{ maxWidth: 'none' }}>
            <span className="field-l">Access token</span>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="current-password"
              required
            />
            <span className="field-h">Set on the server as STI_OPERATOR_TOKEN. Kept for this browser tab only.</span>
          </label>
        )}
        {error && (
          <div className="small mt-1" style={{ color: 'var(--critical)' }} role="alert">
            {error}
          </div>
        )}
        <div className="row mt-2" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || (required && !token.trim())}>
            {required ? 'Sign in' : 'Save'}
          </button>
        </div>
      </form>
    </div>
  )
}
