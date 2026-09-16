import { useEffect, useState } from 'react'
import { useI18n } from '../../app/i18n'
import { Button, C, MetaRow } from '../../ui/UIKit'
import { getValidationData, openValidationFolder, uploadValidationData } from '../../api/validationData'
import type { ValidationDataStatus } from '../../api/validationData'

export function ServerValidationData({ localProjectId }: { localProjectId: string }) {
  const { t } = useI18n()
  const [status, setStatus] = useState<ValidationDataStatus | null>(null)
  const [relative, setRelative] = useState('')
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [uploaded, setUploaded] = useState('')
  async function refresh() {
    setBusy(true); setError(''); setConsent(false)
    try { setStatus(await getValidationData(localProjectId)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }
  useEffect(() => { void refresh() }, [localProjectId])
  async function open() {
    setBusy(true); setError('')
    try { await openValidationFolder(localProjectId) }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }
  async function upload() {
    if (!consent || busy) return
    setBusy(true); setError(''); setUploaded('')
    try {
      const result = await uploadValidationData(localProjectId, relative)
      setUploaded(result.dataPath)
      setStatus(await getValidationData(localProjectId))
      setConsent(false)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }
  return <details style={{ margin: '12px 0', padding: 12, border: `1px solid ${C.border}`, borderRadius: C.radius, fontSize: 12 }}>
    <summary style={{ cursor: 'pointer' }}>{t('Server Validation Data')}</summary>
    <p style={{ color: C.muted }}>{t('Selected files are uploaded to the FL server for validation. Training data stays on this device.')}</p>
    <MetaRow label={t('Validation folder')} value={status?.local.hostPath ?? '—'} mono />
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '10px 0' }}>
      <Button variant="ghost" disabled={busy} onClick={() => void open()}>{t('Open Validation Folder')}</Button>
      <Button variant="ghost" disabled={busy} onClick={() => void refresh()}>{t('Refresh')}</Button>
      <span>{status ? `${status.local.fileCount} files · ${(status.local.totalBytes / 1024 / 1024).toFixed(1)} MiB` : ''}</span>
    </div>
    <label style={{ display: 'block' }}>{t('Subfolder to upload (blank: entire validation folder)')}
      <input value={relative} disabled={busy} onChange={event => { setRelative(event.target.value); setConsent(false) }} style={{ display: 'block', boxSizing: 'border-box', width: '100%', marginTop: 5 }} />
    </label>
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, margin: '10px 0' }}>
      <input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} />
      {t('I have permission to upload these files and agree to server storage.')}
    </label>
    <Button variant="primary" disabled={busy || !consent || !status?.local.hasEntries || Boolean(status?.serverError)} onClick={() => void upload()}>{t(busy ? 'Working…' : 'Upload Validation Data')}</Button>
    {status?.serverError && <p role="status" style={{ color: C.yellow }}>{status.serverError}</p>}
    {error && <p role="alert" style={{ color: C.red }}>{error}</p>}
    {uploaded && <p role="status" style={{ color: C.green }}>{t('Upload complete.')}</p>}
    <details style={{ marginTop: 10, color: C.muted }}>
      <summary style={{ cursor: 'pointer' }}>{t('Upload details')}</summary>
      <p>{t('Limit: 512 MiB / 10,000 files. No symlinks or hidden files. Upload creates a new dataset; it never overwrites existing data.')}</p>
      {Boolean(status?.items.length) && <ul>{status!.items.map(item => <li key={item.dataPath}><code style={{ overflowWrap: 'anywhere' }}>{item.dataPath}</code> · {item.fileCount} files</li>)}</ul>}
    </details>
  </details>
}
