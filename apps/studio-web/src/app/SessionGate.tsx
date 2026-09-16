import { useState } from 'react'
import FedOpsLogo from '../ui/FedOpsLogo'
import { C } from '../ui/UIKit'
import { useStudio } from './StudioProvider'
import { useI18n } from './i18n'

export default function SessionGate() {
  const studio = useStudio()
  const { t } = useI18n()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)

  async function handleLogin(event: React.FormEvent) {
    event.preventDefault()
    setLocalError(null)
    try {
      await studio.login(username.trim(), password)
      setPassword('')
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  return (
    <main style={{ width: '100%', height: '100%', background: C.bg, color: C.text, display: 'grid', placeItems: 'center' }}>
      <section style={{ width: 420, background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 28, boxShadow: C.shadow }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 8 }}>
          <FedOpsLogo size={34} />
          <div style={{ fontFamily: 'JetBrains Mono, monospace', color: C.text, fontSize: 18, fontWeight: 700 }}>
            FedOps Agent Studio
          </div>
        </div>
        <div style={{ fontFamily: 'Inter, sans-serif', color: C.muted, fontSize: 13, lineHeight: 1.6, marginBottom: 22 }}>
          {t('Sign in to use Registry, Workspace, and Federated Learning with your FedOps account.')}
        </div>

        <form onSubmit={handleLogin}>
          <label style={{ display: 'block', fontFamily: 'Inter, sans-serif', fontSize: 12, color: C.muted, marginBottom: 6 }}>FedOps ID</label>
          <input
            autoFocus
            autoComplete="username"
            value={username}
            onChange={event => setUsername(event.target.value)}
            disabled={studio.sessionBusy}
            style={inputStyle}
          />
          <label style={{ display: 'block', fontFamily: 'Inter, sans-serif', fontSize: 12, color: C.muted, margin: '14px 0 6px' }}>{t('Password')}</label>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={event => setPassword(event.target.value)}
            disabled={studio.sessionBusy}
            style={inputStyle}
          />

          {(localError || studio.error) && (
            <div role="alert" style={{ marginTop: 12, padding: '8px 10px', borderRadius: C.radius, border: `1px solid ${C.redBorder}`, background: C.redDim, color: C.red, fontFamily: 'Inter, sans-serif', fontSize: 12 }}>
              {localError || studio.error}
            </div>
          )}

          <button
            type="submit"
            disabled={studio.sessionBusy || !username.trim() || !password}
            style={{ ...buttonStyle, marginTop: 18, color: C.primaryText, borderColor: C.primaryBorder, background: C.primaryBg }}
          >
            {studio.sessionBusy ? t('Connecting…') : t('Sign in with FedOps')}
          </button>
        </form>

        <div style={{ marginTop: 18, paddingTop: 16, borderTop: `1px solid ${C.border}`, textAlign: 'center', color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 12 }}>
          {t('Don’t have a FedOps account?')}{' '}
          <a href={studio.bootstrap?.connections.fedopsRegister} target="_blank" rel="noopener noreferrer" style={{ color: C.accent, textDecoration: 'none' }}>
            {t('Create a FedOps account ↗')}
          </a>
        </div>
      </section>
    </main>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', background: C.inputBg,
  border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, color: C.text,
  padding: '9px 10px', outline: 'none', fontFamily: 'Inter, sans-serif', fontSize: 13,
}

const buttonStyle: React.CSSProperties = {
  width: '100%', minHeight: C.buttonMinHeight, border: `1px solid ${C.controlBorder}`, borderRadius: C.pillRadius,
  background: C.controlBg, color: C.text, padding: '9px 16px', cursor: 'pointer',
  fontFamily: 'Inter, sans-serif', fontSize: 13, fontWeight: 600,
}
