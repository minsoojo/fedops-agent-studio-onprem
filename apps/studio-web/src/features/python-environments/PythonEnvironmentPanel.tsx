import { useEffect, useMemo, useState } from 'react'
import {
  createPythonEnvironment,
  deletePythonEnvironment,
  getTaskRequirements,
  listPythonEnvironments,
  selectPythonEnvironment,
  syncPythonEnvironment,
  updateTaskRequirements,
} from '../../api/environments'
import type { EnvironmentOwnerType, PythonEnvironment, PythonEnvironmentList, TaskRequirements } from '../../api/environments'
import { cancelWorkspaceRun, getWorkspaceRun } from '../../api/workspace'
import type { WorkspaceRun } from '../../api/workspace'
import { Badge, Button, C, MetaRow, SectionLabel } from '../../ui/UIKit'
import { useI18n } from '../../app/i18n'

export interface PythonEnvironmentController {
  data: PythonEnvironmentList | null
  requirements: TaskRequirements | null
  selected: PythonEnvironment | null
  run: WorkspaceRun | null
  cancelling: boolean
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  saveRequirements: (content: string) => Promise<void>
  create: (name: string, pythonVersion: string) => Promise<void>
  select: (environmentId: string) => Promise<void>
  sync: (environmentId: string) => Promise<void>
  cancelSync: () => Promise<void>
  remove: (environmentId: string) => Promise<void>
}

export function usePythonEnvironments(ownerType: EnvironmentOwnerType, ownerId: string): PythonEnvironmentController {
  const [data, setData] = useState<PythonEnvironmentList | null>(null)
  const [run, setRun] = useState<WorkspaceRun | null>(null)
  const [requirements, setRequirements] = useState<TaskRequirements | null>(null)
  const [loading, setLoading] = useState(true)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const selected = useMemo(() => data?.items.find(item => item.selected) ?? null, [data])

  async function refresh() {
    setError(null)
    try {
      const [environmentList, dependencyContract] = await Promise.all([
        listPythonEnvironments(ownerType, ownerId),
        getTaskRequirements(ownerType, ownerId),
      ])
      setData(environmentList)
      setRequirements(dependencyContract)
    } catch (cause) {
      setError(errorText(cause))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setData(null)
    setRun(null)
    setLoading(true)
    void refresh()
  }, [ownerType, ownerId])

  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return
    const timer = window.setInterval(() => {
      getWorkspaceRun(run.runId)
        .then(next => {
          setRun(next)
          if (!['queued', 'running'].includes(next.status)) void refresh()
        })
        .catch(cause => setError(errorText(cause)))
    }, 700)
    return () => window.clearInterval(timer)
  }, [run?.runId, run?.status, ownerType, ownerId])

  useEffect(() => {
    if (run && !['queued', 'running'].includes(run.status)) setCancelling(false)
  }, [run?.runId, run?.status])

  return {
    data,
    requirements,
    selected,
    run,
    cancelling,
    loading,
    error,
    refresh,
    saveRequirements: async content => {
      setError(null)
      try {
        setRequirements(await updateTaskRequirements(ownerType, ownerId, content))
        await refresh()
      } catch (cause) {
        setError(errorText(cause))
        throw cause
      }
    },
    create: async (name, pythonVersion) => {
      setError(null)
      try {
        await createPythonEnvironment({ ownerType, ownerId, name, pythonVersion, select: true })
        await refresh()
      } catch (cause) {
        setError(errorText(cause))
        throw cause
      }
    },
    select: async environmentId => {
      setError(null)
      try {
        await selectPythonEnvironment(ownerType, ownerId, environmentId)
        await refresh()
      } catch (cause) {
        setError(errorText(cause))
      }
    },
    sync: async environmentId => {
      setError(null)
      setCancelling(false)
      try {
        setRun(await syncPythonEnvironment(ownerType, ownerId, environmentId))
        await refresh()
      } catch (cause) {
        setError(errorText(cause))
      }
    },
    cancelSync: async () => {
      if (!run || !['queued', 'running'].includes(run.status) || cancelling) return
      setError(null)
      setCancelling(true)
      try {
        setRun(await cancelWorkspaceRun(run.runId))
      } catch (cause) {
        setCancelling(false)
        setError(errorText(cause))
      }
    },
    remove: async environmentId => {
      setError(null)
      try {
        setData(await deletePythonEnvironment(ownerType, ownerId, environmentId))
      } catch (cause) {
        setError(errorText(cause))
        throw cause
      }
    },
  }
}

export function PythonEnvironmentPanel({ controller }: { controller: PythonEnvironmentController }) {
  const { t } = useI18n()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [pythonVersion, setPythonVersion] = useState('3.12')
  const [requirementsDraft, setRequirementsDraft] = useState('')
  const [savingRequirements, setSavingRequirements] = useState(false)
  const [requirementsSaved, setRequirementsSaved] = useState(false)
  const active = controller.run?.status === 'queued' || controller.run?.status === 'running'
  const syncProgress = controller.run ? environmentSyncProgress(controller.run) : null

  useEffect(() => {
    setRequirementsDraft(controller.requirements?.content ?? '')
  }, [controller.requirements?.sha256, controller.requirements?.mode])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!name.trim()) return
    try {
      await controller.create(name.trim(), pythonVersion)
      setName('')
      setAdding(false)
    } catch {
      // The controller exposes the API error in the panel.
    }
  }

  async function saveRequirements() {
    setSavingRequirements(true)
    setRequirementsSaved(false)
    try {
      await controller.saveRequirements(requirementsDraft)
      setRequirementsSaved(true)
    } catch {
      // The controller exposes line-specific validation errors in the panel.
    } finally {
      setSavingRequirements(false)
    }
  }

  return (
    <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
      <div style={{ maxWidth: 1100 }}>
        <section style={sectionStyle}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <SectionLabel>{t('Task dependencies')}</SectionLabel>
              <p style={bodyText}>{t('Use one library==version entry per line. This single Task contract is synchronized into each selected Python environment and uv.lock is generated automatically.')}</p>
            </div>
            <Badge variant={controller.requirements?.editable ? 'blue' : 'gray'}>
              {controller.requirements?.editable ? 'requirements.txt' : t('Previous format')}
            </Badge>
          </div>
          {controller.requirements?.editable ? (
            <>
              {controller.requirements.error && <div style={errorStyle}>{controller.requirements.error}</div>}
              <textarea
                aria-label={t('Task dependencies')}
                value={requirementsDraft}
                onChange={event => {
                  setRequirementsDraft(event.target.value)
                  setRequirementsSaved(false)
                }}
                spellCheck={false}
                style={requirementsInput}
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
                <span style={{ ...bodyText, flex: 1, color: requirementsSaved ? C.green : bodyText.color }}>
                  {requirementsSaved
                    ? t('Dependencies saved. Synchronize each outdated environment to install and verify them.')
                    : t('Saving dependency changes marks every environment as outdated until it is synchronized again.')}
                </span>
                <Button
                  variant="primary"
                  disabled={savingRequirements || active || requirementsDraft === controller.requirements.content}
                  onClick={() => void saveRequirements()}
                >
                  {savingRequirements ? t('Saving…') : t('Save dependencies')}
                </Button>
              </div>
            </>
          ) : (
            <div style={{ ...emptyStyle, marginTop: 12 }}>{t('This previous-format Task keeps dependencies in pyproject.toml. New Baseline Tasks use requirements.txt.')}</div>
          )}
        </section>

        <section style={sectionStyle}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <SectionLabel>{t('Python runtime environments')}</SectionLabel>
              <p style={bodyText}>{t('Pin this Federated Task’s Python version and dependencies with uv.lock. The selected environment is used consistently for Task Test and Code Run.')}</p>
              <p style={{ ...bodyText, marginTop: 4 }}>{t('Environments are stored in this account’s host-mounted Workspace. Container restarts and image updates preserve them; deleting the environment or Task removes them.')}</p>
            </div>
            <Badge variant={controller.data?.uv.available ? 'green' : 'red'}>
              {controller.data?.uv.available ? `uv ${controller.data.uv.version}` : t('uv unavailable')}
            </Badge>
            <Button variant="ghost" onClick={() => void controller.refresh()}>↻ {t('Refresh')}</Button>
            <Button variant="primary" onClick={() => setAdding(value => !value)}>+ {t('Add environment')}</Button>
          </div>
          {controller.error && <div style={errorStyle}>{controller.error}</div>}
          {adding && (
            <form onSubmit={submit} style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1fr) 150px auto', gap: 8, marginTop: 14, padding: 12, background: C.bg, border: `1px solid ${C.borderSubtle}`, borderRadius: C.radius }}>
              <label style={fieldLabel}>{t('Environment name')}<input autoFocus value={name} onChange={event => setName(event.target.value)} placeholder={t('For example: CUDA development')} style={fieldInput} /></label>
              <label style={fieldLabel}>{t('Python version')}<select value={pythonVersion} onChange={event => setPythonVersion(event.target.value)} style={fieldInput}>{['3.10', '3.11', '3.12', '3.13', '3.14'].map(version => <option key={version}>{version}</option>)}</select></label>
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6 }}><Button type="submit" variant="primary" disabled={!name.trim()}>{t('Create')}</Button><Button variant="ghost" onClick={() => setAdding(false)}>{t('Cancel')}</Button></div>
            </form>
          )}
        </section>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
          {controller.loading && <div style={emptyStyle}>{t('Loading Python environments…')}</div>}
          {!controller.loading && controller.data?.items.map(environment => (
            <EnvironmentCard
              key={environment.environmentId}
              environment={environment}
              active={Boolean(active)}
              onSelect={() => void controller.select(environment.environmentId)}
              onSync={() => void controller.sync(environment.environmentId)}
              onDelete={() => {
                const impact = environment.selected
                  ? t('This environment is selected. After deletion, another environment or a new Default environment will be selected.')
                  : t('The environment directory and installed packages will be deleted.')
                if (window.confirm(t('Delete the {{name}} environment?\n\n{{impact}}\nTask source and uv.lock will be preserved.', { name: environment.name, impact }))) {
                  void controller.remove(environment.environmentId)
                }
              }}
            />
          ))}
        </div>

        {controller.run && (
          <section style={{ ...sectionStyle, marginTop: 12 }}>
            <div className="environment-sync-header">
              <div style={{ minWidth: 0, flex: 1 }}>
                <SectionLabel style={{ margin: 0 }}>{t('Environment sync')}</SectionLabel>
                <strong style={{ display: 'block', marginTop: 5, color: C.text, fontSize: 13 }}>{t(syncProgress?.stage ?? 'Preparing environment')}</strong>
              </div>
              <StatusBadge status={controller.run.status} />
              {active && <Button variant="danger" disabled={controller.cancelling} onClick={() => void controller.cancelSync()}>{controller.cancelling ? t('Stopping…') : t('Stop sync')}</Button>}
            </div>
            {syncProgress && <div className="environment-sync-progress" role="progressbar" aria-label={t('Environment sync progress')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={syncProgress.percent}>
              <div className="environment-sync-progress__labels"><span>{t('Progress')}</span><strong>{syncProgress.percent}%</strong></div>
              <div className="environment-sync-progress__track"><div className="environment-sync-progress__fill" data-active={active} data-status={controller.run.status} style={{ width: `${syncProgress.percent}%` }} /></div>
              <p>{t(syncProgress.detail)}</p>
            </div>}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '12px 0 8px' }}><SectionLabel style={{ margin: 0, flex: 1 }}>{t('Environment sync output')}</SectionLabel></div>
            <pre style={outputStyle}>{controller.run.output || t('Waiting for uv output…')}</pre>
          </section>
        )}
      </div>
    </div>
  )
}

function EnvironmentCard({ environment, active, onSelect, onSync, onDelete }: { environment: PythonEnvironment; active: boolean; onSelect: () => void; onSync: () => void; onDelete: () => void }) {
  const { t, formatDateTime } = useI18n()
  const variant = environment.status === 'ready' ? 'green' : environment.status === 'error' ? 'red' : environment.status === 'syncing' ? 'blue' : 'yellow'
  return (
    <section style={{ ...sectionStyle, borderColor: environment.selected ? C.accentBorder : C.border }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <strong style={{ color: C.text, fontSize: 14 }}>{environment.name}</strong>
        {environment.selected && <Badge variant="blue">{t('Selected')}</Badge>}
        <Badge variant={variant}>{t(environment.status)}</Badge>
        <div style={{ flex: 1 }} />
        {!environment.selected && <Button variant="ghost" disabled={active} onClick={onSelect}>{t('Select')}</Button>}
        <Button variant={environment.status === 'ready' ? 'ghost' : 'primary'} disabled={active} onClick={onSync}>{t(environment.status === 'ready' ? 'Sync again' : 'Sync environment')}</Button>
        <Button variant="danger" disabled={active} onClick={onDelete}>{t('Delete')}</Button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8 }}>
        <MetaRow label="Python" value={environment.pythonVersion} mono />
        <MetaRow label={t('Path')} value={environment.path} mono />
        <MetaRow label={t('Lock')} value={t(environment.lockStatus)} />
        <MetaRow label={t('Last synced')} value={environment.lastSyncedAt ? formatDateTime(environment.lastSyncedAt) : t('Never')} />
      </div>
      {environment.error && <div style={{ ...errorStyle, marginTop: 8 }}>{environment.error}</div>}
    </section>
  )
}

function StatusBadge({ status }: { status: WorkspaceRun['status'] }) {
  const { t } = useI18n()
  const variant = status === 'succeeded' ? 'green' : status === 'failed' || status === 'cancelled' ? 'red' : status === 'queued' ? 'yellow' : 'blue'
  return <Badge variant={variant}>{t(status)}</Badge>
}

function environmentSyncProgress(run: WorkspaceRun): { percent: number; stage: string; detail: string } {
  const output = run.output
  let percent = run.status === 'queued' ? 3 : 8
  let stage = run.status === 'queued' ? 'Waiting to start' : 'Preparing environment'
  let detail = run.status === 'queued' ? 'The sync is queued and will start shortly.' : 'Checking Python and the Task dependency contract.'

  const advance = (needle: string | RegExp, nextPercent: number, nextStage: string, nextDetail: string) => {
    if (typeof needle === 'string' ? output.includes(needle) : needle.test(output)) {
      percent = nextPercent
      stage = nextStage
      detail = nextDetail
    }
  }

  advance(/Using (?:CPython|Python)/, 15, 'Python selected', 'The requested Python interpreter is available.')
  advance('Creating virtual environment', 25, 'Creating virtual environment', 'Creating the account-local uv environment.')
  advance(/Resolved \d+ packages?/, 42, 'Dependencies resolved', 'uv resolved the dependency graph and lock contract.')
  advance(/Downloading |Downloaded /, 58, 'Downloading packages', 'Downloading required packages into the local uv cache.')
  advance(/Prepared \d+ packages?/, 76, 'Packages prepared', 'Packages are ready to be installed in the environment.')
  advance(/Installed \d+ packages?/, 92, 'Installing packages', 'Finishing package installation and environment metadata.')
  advance('Environment and uv.lock are synchronized.', 100, 'Environment ready', 'Python, packages, and uv.lock are synchronized.')

  if (run.status === 'succeeded') return { percent: 100, stage: 'Environment ready', detail: 'Python, packages, and uv.lock are synchronized.' }
  if (run.status === 'cancelled') return { percent, stage: 'Sync stopped', detail: 'The sync was stopped. The environment remains available but must be synchronized again.' }
  if (run.status === 'failed') return { percent, stage: 'Sync failed', detail: 'Review the output below, correct the dependency issue, and run sync again.' }
  return { percent, stage, detail }
}

function errorText(cause: unknown): string { return cause instanceof Error ? cause.message : String(cause) }

const sectionStyle: React.CSSProperties = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: '12px 14px' }
const bodyText: React.CSSProperties = { margin: 0, color: C.muted, fontSize: 12, lineHeight: 1.6 }
const errorStyle: React.CSSProperties = { marginTop: 10, padding: '7px 10px', background: C.redDim, border: `1px solid ${C.redBorder}`, borderRadius: C.radius, color: C.red, fontSize: 12 }
const emptyStyle: React.CSSProperties = { ...sectionStyle, color: C.dim, textAlign: 'center' }
const fieldLabel: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 5, color: C.muted, fontSize: 11 }
const fieldInput: React.CSSProperties = { width: '100%', minHeight: 32, padding: '6px 9px', color: C.text, background: C.inputBg, border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, outline: 'none', fontSize: 13 }
const outputStyle: React.CSSProperties = { margin: 0, height: 210, overflow: 'auto', padding: 10, borderRadius: C.radius, background: '#1c1917', border: '1px solid #292524', color: '#d6d3d1', fontFamily: 'JetBrains Mono, monospace', fontSize: 11, lineHeight: 1.5, whiteSpace: 'pre-wrap' }
const requirementsInput: React.CSSProperties = { width: '100%', minHeight: 210, marginTop: 12, resize: 'vertical', padding: '10px 12px', color: C.text, background: C.inputBg, border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, outline: 'none', fontFamily: 'JetBrains Mono, SFMono-Regular, Consolas, monospace', fontSize: 13, lineHeight: 1.65, boxSizing: 'border-box' }
