import { useEffect, useMemo, useState } from 'react'
import type { AgentToolModel } from '../api/agents'
import {
  buildWorkspaceTaskDataSample,
  openWorkspaceDataFolder,
  prepareWorkspaceDataBinding,
} from '../api/workspace'
import type { LocalDataBinding, TaskDataSample } from '../api/workspace'
import { useI18n } from '../app/i18n'
import { Button, C } from './UIKit'

interface Props {
  tools: AgentToolModel[]
  selectedToolId: string | null
  onSelectTool: (toolId: string | null) => void
  onSample: (sample: TaskDataSample | null) => void
  disabled?: boolean
}

export default function TaskDataPanel({ tools, selectedToolId, onSelectTool, onSample, disabled }: Props) {
  const { t } = useI18n()
  const selected = useMemo(
    () => tools.find(tool => tool.localProjectId === selectedToolId) ?? tools[0] ?? null,
    [tools, selectedToolId],
  )
  const [binding, setBinding] = useState<LocalDataBinding | null>(null)
  const [index, setIndex] = useState(0)
  const [dataPath, setDataPath] = useState('')
  const [loadedSample, setLoadedSample] = useState<TaskDataSample | null>(null)
  const [busy, setBusy] = useState<'refresh' | 'open' | 'sample' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoadedSample(null)
    onSample(null)
    setIndex(0)
    setDataPath('')
    if (!selected) { setBinding(null); return }
    if (selected.localProjectId !== selectedToolId) onSelectTool(selected.localProjectId)
    void refresh(selected.localProjectId)
  }, [selected?.localProjectId])

  function invalidateSample() {
    setLoadedSample(null)
    onSample(null)
  }

  async function refresh(localProjectId = selected?.localProjectId) {
    if (!localProjectId) return
    invalidateSample()
    setBusy('refresh'); setError(null)
    try { setBinding(await prepareWorkspaceDataBinding(localProjectId)) }
    catch (cause) { setError(message(cause)) }
    finally { setBusy(null) }
  }

  async function openFolder() {
    if (!selected) return
    setBusy('open'); setError(null)
    try { setBinding(await openWorkspaceDataFolder(selected.localProjectId)) }
    catch (cause) { setError(message(cause)) }
    finally { setBusy(null) }
  }

  async function loadSample() {
    if (!selected) return
    setBusy('sample'); setError(null)
    try {
      const sample = await buildWorkspaceTaskDataSample(selected.localProjectId, index, dataPath)
      setLoadedSample(sample)
      onSample(sample)
    }
    catch (cause) { setError(message(cause)) }
    finally { setBusy(null) }
  }

  if (!tools.length) return <div style={emptyStyle}>{t('Add a Tool AI to connect Task Data.')}</div>
  return <section style={panelStyle}>
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
      <div>
        <strong style={{ color: C.text, fontSize: 12 }}>{t('Task Data')}</strong>
        <div style={{ color: C.dim, fontSize: 11, marginTop: 3 }}>{t('The same local-only folder is used for training and Tool AI inference.')}</div>
      </div>
      <span style={{ color: binding?.hasEntries ? C.green : C.yellow, fontSize: 11 }}>
        {binding?.hasEntries ? `● ${binding.fileCount} ${t('files')}` : `○ ${t('No data detected')}`}
      </span>
    </div>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
      <label style={{ ...labelStyle, flex: '1 1 220px' }}>{t('Tool AI')}
        <select value={selected?.localProjectId ?? ''} onChange={event => onSelectTool(event.target.value)} disabled={disabled} style={inputStyle}>
          {tools.map(tool => <option key={tool.localProjectId} value={tool.localProjectId}>{tool.modelName}</option>)}
        </select>
      </label>
      <label style={{ ...labelStyle, flex: '0 1 150px' }}>{t('Sample index (starts at 0)')}
        <input type="number" min={0} step={1} value={index} onChange={event => { setIndex(Math.max(0, Math.trunc(Number(event.target.value) || 0))); invalidateSample() }} disabled={disabled} style={inputStyle} />
      </label>
    </div>
    <label style={{ ...labelStyle, display: 'block', marginTop: 8 }}>{t('Data path inside Task Data (optional)')}
      <input
        value={dataPath}
        onChange={event => { setDataPath(event.target.value); invalidateSample() }}
        disabled={disabled}
        placeholder={t('Example: MNIST/raw or client-2/records.csv · blank uses the Task Data root')}
        style={inputStyle}
      />
    </label>
    <div title={effectivePath(binding?.hostPath, dataPath)} style={pathStyle}>{effectivePath(binding?.hostPath, dataPath) || t('Preparing Task Data folder…')}</div>
    <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 9 }}>
      <Button variant="ghost" disabled={disabled || Boolean(busy)} onClick={() => void openFolder()}>{busy === 'open' ? t('Opening…') : t('Open Data Folder')}</Button>
      <Button variant="ghost" disabled={disabled || Boolean(busy)} onClick={() => void refresh()}>{busy === 'refresh' ? t('Refreshing…') : t('Refresh Data')}</Button>
      <Button variant="success" disabled={disabled || Boolean(busy) || !binding?.hasEntries} onClick={() => void loadSample()}>{busy === 'sample' ? t('Loading sample…') : t('Load sample for Agent')}</Button>
    </div>
    {loadedSample
      ? <div style={loadedStyle}>
          <strong>● {t('Tool input ready')}</strong>
          <span>{t('Sample {index} is loaded and will be sent to the selected Tool AI.', { index: loadedSample.index })}</span>
          {Object.keys(loadedSample.metadata).length > 0 && <code style={metadataStyle}>{compactMetadata(loadedSample.metadata)}</code>}
        </div>
      : <div style={waitingStyle}>○ {t('No sample is loaded. Load one before generating a response that should use Tool AI.')}</div>}
    {error && <div style={{ color: C.red, fontSize: 11, marginTop: 8, whiteSpace: 'pre-wrap' }}>{error}</div>}
  </section>
}

function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause) }
function effectivePath(root: string | undefined, relative: string) {
  if (!root) return ''
  const suffix = relative.trim().replace(/^[/\\]+/, '')
  return suffix ? `${root.replace(/[/\\]+$/, '')}/${suffix}` : root
}
function compactMetadata(metadata: Record<string, unknown>) {
  const encoded = JSON.stringify(metadata)
  return encoded.length > 240 ? `${encoded.slice(0, 237)}…` : encoded
}

const panelStyle = { padding: 11, border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.surface2 }
const emptyStyle = { padding: 12, color: C.dim, fontSize: 11, border: `1px dashed ${C.border}`, borderRadius: C.radius }
const labelStyle = { color: C.muted, fontSize: 10 }
const inputStyle = { width: '100%', height: 32, marginTop: 5, boxSizing: 'border-box' as const, border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, background: C.inputBg, color: C.text, padding: '0 8px' }
const pathStyle = { marginTop: 9, padding: '7px 8px', borderRadius: C.radius, background: C.bg, color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }
const loadedStyle = { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' as const, marginTop: 9, padding: '8px 9px', border: `1px solid ${C.greenBorder}`, borderRadius: C.radius, background: C.greenDim, color: C.green, fontSize: 11 }
const waitingStyle = { marginTop: 9, padding: '8px 9px', border: `1px dashed ${C.border}`, borderRadius: C.radius, color: C.dim, fontSize: 11 }
const metadataStyle = { marginLeft: 'auto', color: C.muted, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis' }
