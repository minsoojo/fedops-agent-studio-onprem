import { useStudio } from './StudioProvider'
import FedOpsLogo from '../ui/FedOpsLogo'
import { C } from '../ui/UIKit'
import { useI18n } from './i18n'

export default function TitleBar() {
  const studio = useStudio()
  const { t } = useI18n()
  const session = studio.bootstrap?.session
  const accountLabel = session?.handle
    ? `@${session.handle}`
    : session?.organization || session?.username || t('FedOps Account')
  const accountDetail = session?.handle && session.organization
    ? session.organization
    : null

  return (
    <div className="studio-titlebar" style={{
      height: 44, background: C.chrome, borderBottom: `1px solid ${C.border}`,
      display: 'flex', alignItems: 'center', paddingLeft: 13, paddingRight: 12,
      gap: 0, flexShrink: 0, userSelect: 'none',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginRight: 10, color: C.brand, flexShrink: 0 }}>
        <FedOpsLogo size={32} />
        <span style={{ fontFamily: 'Inter, sans-serif', fontSize: 18, fontWeight: 750, letterSpacing: '-0.035em' }}>
          FedOps Agent Studio
        </span>
      </div>

      <div style={{ flex: 1 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginLeft: 10, minWidth: 0, flexShrink: 0 }}>
        <a
          className="studio-titlebar-web-link"
          href={studio.bootstrap?.connections.fedopsWeb}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t('Open FedOps Web')}
          title={t('Open FedOps Web')}
          style={{ display: 'flex', alignItems: 'center', gap: 5, minHeight: 27, boxSizing: 'border-box', background: C.surface, border: `1px solid ${C.controlBorder}`, borderRadius: C.pillRadius, color: C.controlText, padding: '4px 9px', textDecoration: 'none', fontFamily: 'Inter, sans-serif', fontSize: 11, fontWeight: 500, whiteSpace: 'nowrap' }}
        >
          <OpenWebIcon />
          <span className="studio-titlebar-web-label">FedOps Web</span>
        </a>
        <div
          className="studio-titlebar-account-info"
          title={accountDetail ? `${accountLabel}\n${accountDetail}` : accountLabel}
          style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0, maxWidth: 260 }}
        >
          <PersonIcon />
          <div style={{ minWidth: 0 }}>
            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 11, fontWeight: 600, lineHeight: 1.15 }}>
              {accountLabel}
            </div>
            {accountDetail && (
              <div className="studio-titlebar-account-secondary" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 9, lineHeight: 1.2, marginTop: 2 }}>
                {accountDetail}
              </div>
            )}
          </div>
        </div>
        <button
          className="studio-titlebar-account"
          type="button"
          title={t('Sign out')}
          onClick={() => void studio.logout()}
          disabled={studio.sessionBusy}
          style={{ display: 'flex', alignItems: 'center', gap: 5, background: C.surface, border: `1px solid ${C.controlBorder}`, borderRadius: C.pillRadius, color: C.controlText, padding: '4px 10px', cursor: studio.sessionBusy ? 'not-allowed' : 'pointer', fontFamily: 'Inter, sans-serif', fontSize: 11, fontWeight: 500, whiteSpace: 'nowrap' }}
        >
          <SignOutIcon />
          {t('Sign out')}
        </button>
      </div>
    </div>
  )
}

function PersonIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" style={{ color: C.muted, flexShrink: 0 }}>
      <circle cx="12" cy="8" r="3.25" />
      <path d="M5.75 19c.55-3.35 2.65-5 6.25-5s5.7 1.65 6.25 5" strokeLinecap="round" />
    </svg>
  )
}

function SignOutIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H10" />
      <path d="M13 8l4 4-4 4M17 12H9" />
    </svg>
  )
}

function OpenWebIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 5h5v5M19 5l-8 8" />
      <path d="M18 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </svg>
  )
}
