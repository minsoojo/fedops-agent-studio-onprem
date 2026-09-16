import { useRef, useState } from 'react'

import { useStudio } from '../../app/StudioProvider'
import {
  createCustomThemeBaseline,
  customThemeId,
  customThemeSelection,
  parseCustomThemeJson,
  type CustomThemeV1,
} from '../../app/theme'
import type { AppScale, AppTheme, BuiltInTheme } from '../../app/types'
import { useI18n } from '../../app/i18n'
import { Button, C } from '../../ui/UIKit'

interface SettingsProps {
  theme: AppTheme
  setTheme: (theme: AppTheme) => void
  customThemes: CustomThemeV1[]
  importCustomTheme: (theme: CustomThemeV1) => void
  deleteCustomTheme: (id: string) => void
  scale: AppScale
  setScale: (scale: AppScale) => void
}

export default function Settings({
  theme,
  setTheme,
  customThemes,
  importCustomTheme,
  deleteCustomTheme,
  scale,
  setScale,
}: SettingsProps) {
  const studio = useStudio()
  const { locale, setLocale, t } = useI18n()
  const bootstrap = studio.bootstrap
  const themeFileInput = useRef<HTMLInputElement>(null)
  const [themeMessage, setThemeMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  async function readThemeFile(file: File | undefined) {
    if (!file) return
    setThemeMessage(null)
    try {
      const next = parseCustomThemeJson(await file.text())
      const existing = customThemes.find(item => item.id === next.id)
      if (existing && !window.confirm(t('Replace the imported theme “{{name}}”?', { name: existing.name }))) return
      importCustomTheme(next)
      setThemeMessage({ kind: 'success', text: t('{{name}} was imported and applied.', { name: next.name }) })
    } catch (error) {
      setThemeMessage({
        kind: 'error',
        text: themeErrorText(error, t, 'The theme file could not be imported.'),
      })
    } finally {
      if (themeFileInput.current) themeFileInput.current.value = ''
    }
  }

  function removeTheme(item: CustomThemeV1) {
    if (!window.confirm(t('Remove the imported theme “{{name}}” from this account?', { name: item.name }))) return
    try {
      deleteCustomTheme(item.id)
      setThemeMessage({ kind: 'success', text: t('{{name}} was removed.', { name: item.name }) })
    } catch (error) {
      setThemeMessage({
        kind: 'error',
        text: themeErrorText(error, t, 'The theme could not be removed.'),
      })
    }
  }

  function downloadThemeBaseline() {
    setThemeMessage(null)
    try {
      const baseline = createCustomThemeBaseline()
      const blob = new Blob([`${JSON.stringify(baseline, null, 2)}\n`], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'fedops-theme-baseline.json'
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
      setThemeMessage({
        kind: 'success',
        text: t('The current theme was downloaded as a JSON v1 baseline.'),
      })
    } catch (error) {
      setThemeMessage({
        kind: 'error',
        text: themeErrorText(error, t, 'The baseline could not be downloaded.'),
      })
    }
  }

  return (
    <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', background: C.bg, padding: 22 }}>
      <div style={{ maxWidth: 820, margin: '0 auto' }}>
        <h1 style={{ color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 19, margin: '0 0 4px' }}>{t('Settings & runtime information')}</h1>
        <p style={{ color: C.muted, fontSize: 12, margin: '0 0 22px' }}>{t('Only settings confirmed by the actual bootstrap and local session are shown.')}</p>

        <Section title={t('Appearance')}>
          <ControlLabel>{t('Language')}</ControlLabel>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <ThemeButton active={locale === 'en'} onClick={() => setLocale('en')}>{t('English')}</ThemeButton>
            <ThemeButton active={locale === 'ko'} onClick={() => setLocale('ko')}>{t('Korean')}</ThemeButton>
          </div>
          <div style={{ height: 1, background: C.border, margin: '14px 0' }} />
          <ControlLabel>{t('Color theme')}</ControlLabel>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {([['system', 'System default'], ['light', 'Light'], ['dark', 'Dark']] as [BuiltInTheme, string][]).map(([value, label]) => (
              <ThemeButton key={value} active={theme === value} onClick={() => setTheme(value)}>
                {t(label)}
              </ThemeButton>
            ))}
          </div>
          <div style={{ height: 1, background: C.border, margin: '14px 0' }} />
          <ControlLabel>{t('Custom themes')}</ControlLabel>
          {customThemes.length > 0 && (
            <div style={{ display: 'grid', gap: 7, marginBottom: 10 }}>
              {customThemes.map(item => {
                const selected = customThemeId(theme) === item.id
                return (
                  <div key={item.id} style={{ display: 'flex', gap: 7, alignItems: 'stretch' }}>
                    <ThemeButton
                      active={selected}
                      onClick={() => setTheme(customThemeSelection(item.id))}
                      style={{ flex: 1, justifyContent: 'space-between', textTransform: 'none' }}
                    >
                      <span>{item.name}</span>
                      <span style={{ color: selected ? C.primaryText : C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
                        {item.appearance} · {item.id}
                      </span>
                    </ThemeButton>
                    <Button variant="danger" onClick={() => removeTheme(item)} style={{ paddingInline: 12 }}>
                      {t('Remove')}
                    </Button>
                  </div>
                )
              })}
            </div>
          )}
          <input
            ref={themeFileInput}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={event => void readThemeFile(event.currentTarget.files?.[0])}
          />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <Button variant="ghost" onClick={downloadThemeBaseline}>
              {t('Download baseline JSON')}
            </Button>
            <Button variant="ghost" onClick={() => themeFileInput.current?.click()}>
              {t('Import theme JSON…')}
            </Button>
          </div>
          <p style={{ color: C.dim, fontSize: 11, lineHeight: 1.55, margin: '8px 0 0' }}>
            {t('Download the current theme as a baseline, edit its id and name, and then import it.')}<br />
            {t('JSON schema v1 · up to 64 KB · stored only in this browser for the current FedOps account and never uploaded to the server.')}
          </p>
          {themeMessage && (
            <div style={{ color: themeMessage.kind === 'error' ? C.red : C.green, fontSize: 11, marginTop: 8 }}>
              {themeMessage.text}
            </div>
          )}
          <div style={{ height: 1, background: C.border, margin: '14px 0' }} />
          <ControlLabel>{t('Interface size')}</ControlLabel>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {([
              ['standard', t('Standard · 100%')],
              ['comfortable', t('Comfortable · 110%')],
              ['large', t('Large · 120%')],
            ] as [AppScale, string][]).map(([value, label]) => (
              <button key={value} onClick={() => setScale(value)} style={{ minHeight: C.buttonMinHeight, padding: '7px 16px', borderRadius: C.pillRadius, background: scale === value ? C.primaryBg : C.surface, color: scale === value ? C.primaryText : C.controlText, border: `1px solid ${scale === value ? C.primaryBorder : C.controlBorder}`, cursor: 'pointer', fontWeight: 500 }}>{label}</button>
            ))}
          </div>
        </Section>

        <Section title={t('Local Studio')}>
          <Row label={t('Studio version')} value={bootstrap?.version ?? '—'} />
          <Row label={t('Workspace root')} value={bootstrap?.workspace.displayRoot ?? bootstrap?.workspace.root ?? '—'} />
          <Row label={t('Detected projects')} value={String(bootstrap?.workspace.projects.length ?? 0)} />
          <Row label={t('Legacy projects')} value={String(bootstrap?.workspace.legacyProjects.length ?? 0)} />
          <Row label={t('Project discovery')} value={bootstrap?.workspace.projectDiscovery ?? '—'} />
          <div style={{ marginTop: 12 }}><Button variant="ghost" onClick={() => void studio.refresh()}>{t('Refresh runtime information')}</Button></div>
        </Section>

        <Section title={t('FedOps Web connection')}>
          <Row label="FedOps Web" value={bootstrap?.connections.fedopsWeb ?? '—'} />
          <Row label={t('Session mode')} value={bootstrap?.session.mode ?? 'anonymous'} />
          <Row label={t('Display name')} value={bootstrap?.session.displayName ?? '—'} />
          <Row label="Registry ID" value={bootstrap?.session.handle ? `@${bootstrap.session.handle}` : '—'} />
          <Row label={t('Username')} value={bootstrap?.session.username ?? '—'} />
          <Row label={t('Local account namespace')} value={bootstrap?.session.accountKey ?? '—'} />
          <Row label={t('Registry access')} value={t(bootstrap?.session.isFedOps ? 'Authenticated' : 'FedOps login required')} />
        </Section>

      </div>
    </div>
  )
}

function ThemeButton({
  active,
  onClick,
  children,
  style,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
  style?: React.CSSProperties
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        minHeight: C.buttonMinHeight,
        display: 'flex',
        alignItems: 'center',
        padding: '7px 16px',
        borderRadius: C.pillRadius,
        background: active ? C.primaryBg : C.surface,
        color: active ? C.primaryText : C.controlText,
        border: `1px solid ${active ? C.primaryBorder : C.controlBorder}`,
        cursor: 'pointer',
        fontWeight: 500,
        textTransform: 'capitalize',
        ...style,
      }}
    >
      {children}
    </button>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 15, marginBottom: 12 }}><h2 style={{ color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 14, fontWeight: 500, margin: '0 0 12px' }}>{title}</h2>{children}</section>
}

function ControlLabel({ children }: { children: React.ReactNode }) {
  return <div style={{ color: C.muted, fontSize: 12, fontWeight: 500, marginBottom: 7 }}>{children}</div>
}

function Row({ label, value }: { label: string; value: string }) {
  return <div style={{ display: 'flex', padding: '7px 0', borderBottom: `1px solid ${C.borderSubtle}`, gap: 12 }}><span style={{ minWidth: 170, color: C.dim, fontSize: 12 }}>{label}</span><span style={{ color: C.text, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, overflowWrap: 'anywhere' }}>{value}</span></div>
}

function themeErrorText(
  error: unknown,
  t: (message: string, values?: Record<string, string | number>) => string,
  fallback: string,
): string {
  if (!(error instanceof Error)) return t(fallback)
  const message = error.message
  const fieldRule = message.match(/^(.+) must be (an object|a string)\.$/)
  if (fieldRule) return t('{{field}} must be {{rule}}.', { field: fieldRule[1], rule: t(fieldRule[2]) })
  const colorRule = message.match(/^(.+) must be a #RRGGBB or #RRGGBBAA color\.$/)
  if (colorRule) return t('{{field}} must be a #RRGGBB or #RRGGBBAA color.', { field: colorRule[1] })
  const conversionRule = message.match(/^(.+) could not be converted to a theme color\.$/)
  if (conversionRule) return t('{{field}} could not be converted to a theme color.', { field: conversionRule[1] })
  return t(message)
}
