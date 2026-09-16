import type { CSSProperties, ReactNode } from 'react'
import FileTypeIcon, { fileTypeLabel } from './FileTypeIcon'
import { useI18n } from '../app/i18n'

// CSS-variable references keep the shared UI primitives theme-reactive.
// The actual dark/light values live in index.css as the shared design contract.
export const C = {
  bg: 'var(--studio-bg)',
  chrome: 'var(--studio-chrome)',
  surface: 'var(--studio-surface)',
  surface2: 'var(--studio-surface-2)',
  surface3: 'var(--studio-surface-3)',
  border: 'var(--studio-border)',
  borderSubtle: 'var(--studio-border-subtle)',
  controlBorder: 'var(--studio-control-border)',
  controlBg: 'var(--studio-control-bg)',
  controlText: 'var(--studio-control-text)',
  inputBg: 'var(--studio-input-bg)',
  text: 'var(--studio-text)',
  muted: 'var(--studio-muted)',
  dim: 'var(--studio-dim)',
  accent: 'var(--studio-accent)',
  accentDim: 'var(--studio-accent-dim)',
  accentBorder: 'var(--studio-accent-border)',
  green: 'var(--studio-green)',
  greenDim: 'var(--studio-green-dim)',
  greenBorder: 'var(--studio-green-border)',
  yellow: 'var(--studio-yellow)',
  yellowDim: 'var(--studio-yellow-dim)',
  yellowBorder: 'var(--studio-yellow-border)',
  red: 'var(--studio-red)',
  redDim: 'var(--studio-red-dim)',
  redBorder: 'var(--studio-red-border)',
  purple: 'var(--studio-purple)',
  purpleDim: 'var(--studio-purple-dim)',
  purpleBorder: 'var(--studio-purple-border)',
  brand: 'var(--studio-brand)',
  orange: 'var(--studio-orange)',
  orangeDim: 'var(--studio-orange-dim)',
  orangeBorder: 'var(--studio-orange-border)',
  primaryBg: 'var(--studio-primary-bg)',
  primaryText: 'var(--studio-primary-text)',
  primaryBorder: 'var(--studio-primary-border)',
  radius: 'var(--studio-radius)',
  pillRadius: 'var(--studio-pill-radius)',
  buttonMinHeight: 'var(--studio-button-min-height)',
  shadow: 'var(--studio-shadow)',
  overlay: 'var(--studio-overlay)',
}

// ─── Layout ──────────────────────────────────────────────────────────────────

export function Sidebar({ children, width = 200 }: { children: ReactNode; width?: number }) {
  return (
    <div
      className="scroll-area"
      style={{ width, background: C.surface, borderRight: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', flexShrink: 0, overflowY: 'auto' }}
    >
      {children}
    </div>
  )
}

// ─── Data display ─────────────────────────────────────────────────────────────

export function Badge({ variant, children }: { variant: 'blue' | 'green' | 'purple' | 'yellow' | 'red' | 'gray' | 'orange'; children: ReactNode }) {
  const colors: Record<string, { color: string; bg: string; border: string }> = {
    blue:   { color: C.accent,  bg: C.accentDim,  border: C.accentBorder },
    green:  { color: C.green,   bg: C.greenDim,   border: C.greenBorder },
    purple: { color: C.purple,  bg: C.purpleDim,  border: C.purpleBorder },
    yellow: { color: C.yellow,  bg: C.yellowDim,  border: C.yellowBorder },
    orange: { color: C.orange,  bg: C.orangeDim,  border: C.orangeBorder },
    red:    { color: C.red,     bg: C.redDim,      border: C.redBorder },
    gray:   { color: C.muted,   bg: C.surface2,    border: C.border },
  }
  const { color, bg, border } = colors[variant] || colors.gray
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', flexShrink: 0, whiteSpace: 'nowrap', fontFamily: 'JetBrains Mono, monospace', fontSize: 12, fontWeight: 500, color, background: bg, border: `1px solid ${border}`, borderRadius: C.pillRadius, padding: '1px 7px' }}>
      {children}
    </span>
  )
}

export function MetaRow({ label, value, mono, accent }: { label: string; value: string; mono?: boolean; accent?: boolean }) {
  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 5, alignItems: 'flex-start' }}>
      <span style={{ fontFamily: 'Inter, sans-serif', fontSize: 12, color: C.dim, minWidth: 130, flexShrink: 0 }}>{label}</span>
      <span style={{ fontFamily: mono ? 'JetBrains Mono, monospace' : 'Inter, sans-serif', fontSize: 12, color: accent ? C.accent : C.text }}>{value}</span>
    </div>
  )
}

export function SectionLabel({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8, marginTop: 4, ...style }}>
      {children}
    </div>
  )
}

// ─── Buttons ──────────────────────────────────────────────────────────────────

export function Button({ variant, onClick, children, disabled, style, title, type = 'button' }: {
  variant: 'primary' | 'ghost' | 'danger' | 'success'
  onClick?: () => void; children: ReactNode; disabled?: boolean; style?: CSSProperties; title?: string; type?: 'button' | 'submit'
}) {
  const styles: Record<string, CSSProperties> = {
    primary: { background: disabled ? C.surface2 : C.primaryBg, border: `1px solid ${disabled ? C.border : C.primaryBorder}`, color: disabled ? C.dim : C.primaryText, cursor: disabled ? 'not-allowed' : 'pointer' },
    ghost:   { background: C.controlBg, border: `1px solid ${C.controlBorder}`, color: C.controlText, cursor: 'pointer' },
    danger:  { background: C.redDim, border: `1px solid ${C.redBorder}`, color: C.red, cursor: 'pointer' },
    success: { background: C.greenDim, border: `1px solid ${C.greenBorder}`, color: C.green, cursor: 'pointer' },
  }
  return (
    <button
      type={type}
      title={title}
      disabled={disabled}
      onClick={disabled ? undefined : onClick}
      style={{ ...styles[variant], minHeight: C.buttonMinHeight, fontFamily: 'Inter, sans-serif', fontSize: 13, fontWeight: 500, borderRadius: C.pillRadius, padding: '5px 14px', whiteSpace: 'nowrap', transition: 'all 0.1s', ...style }}
      onMouseEnter={e => {
        if (!disabled && variant === 'ghost') { const el = e.currentTarget as HTMLButtonElement; el.style.color = C.text; el.style.borderColor = C.muted }
      }}
      onMouseLeave={e => {
        if (!disabled && variant === 'ghost') { const el = e.currentTarget as HTMLButtonElement; el.style.color = C.controlText; el.style.borderColor = C.controlBorder }
      }}
    >
      {children}
    </button>
  )
}

export function IconBtn({ icon, title, onClick, active, disabled = false }: { icon: string; title: string; onClick?: () => void; active?: boolean; disabled?: boolean }) {
  return (
    <button
      title={title}
      disabled={disabled}
      onClick={disabled ? undefined : onClick}
      style={{
        background: active ? C.accentDim : 'transparent',
        border: `1px solid ${active ? C.accent : C.border}`,
        borderRadius: C.radius, padding: '4px 8px', fontSize: 13, color: active ? C.accent : C.muted,
        cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.45 : 1, transition: 'all 0.1s',
      }}
    >
      {icon}
    </button>
  )
}

// ─── Tabs ─────────────────────────────────────────────────────────────────────

export function BottomTabs({ tabs, active, setActive }: { tabs: string[]; active: string; setActive: (t: string) => void }) {
  const { t: translate } = useI18n()
  return (
    <div style={{ display: 'flex', borderBottom: `1px solid ${C.border}`, background: C.surface, flexShrink: 0 }}>
      {tabs.map(t => (
        <button key={t} onClick={() => setActive(t)} style={{
          background: 'none', borderTop: 'none', borderLeft: 'none', borderRight: 'none',
          padding: '6px 14px', fontFamily: 'Inter, sans-serif', fontSize: 13,
          color: active === t ? C.text : C.muted,
          borderBottom: active === t ? `2px solid ${C.accent}` : '2px solid transparent',
          cursor: 'pointer', marginBottom: -1,
        }}>
          {translate(t)}
        </button>
      ))}
    </div>
  )
}

export function EditorTabs({ tabs, active, setActive, onClose, isReadOnly }: {
  tabs: string[]; active: string; setActive: (t: string) => void; onClose?: (t: string) => void; isReadOnly?: (t: string) => boolean
}) {
  const { t: translate } = useI18n()
  return (
    <div style={{ display: 'flex', background: C.bg, borderBottom: `1px solid ${C.border}`, flexShrink: 0, overflowX: 'auto' }}>
      {tabs.map(t => {
        const isActive = active === t
        return (
          <div
            key={t}
            onClick={() => setActive(t)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '6px 14px', cursor: 'pointer', userSelect: 'none',
              background: isActive ? C.surface : 'transparent',
              borderRight: `1px solid ${C.border}`,
              borderTop: isActive ? `1px solid ${C.accent}` : '1px solid transparent',
              fontFamily: 'Inter, sans-serif', fontSize: 13,
              color: isActive ? C.text : C.muted,
              whiteSpace: 'nowrap', flexShrink: 0,
            }}
          >
            <span title={translate(fileTypeLabel(t))} style={{ display: 'grid', placeItems: 'center' }}>
              <FileTypeIcon name={t} size={15} />
            </span>
            <span>{t}</span>
            {isReadOnly?.(t) && <span title={translate('Read only · FedOps managed')} aria-label={translate('Read only · FedOps managed')} style={{ color: C.purple, fontSize: 10 }}>🔒</span>}
            {onClose && (
              <span onClick={e => { e.stopPropagation(); onClose(t) }} style={{ color: C.dim, fontSize: 10, marginLeft: 2, lineHeight: 1 }}>×</span>
            )}
          </div>
        )
      })}
    </div>
  )
}
