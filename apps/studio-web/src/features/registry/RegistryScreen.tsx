import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useStudio } from '../../app/StudioProvider'
import { useI18n } from '../../app/i18n'
import { Badge, Button, C, MetaRow } from '../../ui/UIKit'
import FileTypeIcon, { fileTypeLabel } from '../../ui/FileTypeIcon'
import {
  getModelDownload,
  getTaskActivity,
  getTaskFileDownload,
  getTaskHub,
  leaveTaskParticipation,
  listRegistryTasks,
  openPublishedRelease,
  previewTaskFile,
  requestTaskParticipation,
} from '../../api/registry'
import type {
  RegistryTask,
  RegistryView,
  TaskActivity,
  TaskFilePreview,
  TaskHub,
  TaskHubFile,
  TaskHubModel,
} from '../../api/registry'
import type { ActiveTask } from '../../app/types'

interface Props {
  activeTask: ActiveTask | null
  onOpenWorkspace: () => void
  onOpenFederation: (task: RegistryTask) => void
}

type DetailTab = 'overview' | 'activity' | 'models' | 'files'
type RegistryScreenView = RegistryView | 'legacy'

const VIEWS: { id: RegistryView; label: string }[] = [
  { id: 'public', label: 'Public Registry' },
  { id: 'all', label: 'My Federated Tasks' },
  { id: 'owned', label: 'Owned' },
  { id: 'joined', label: 'Joined' },
]
const isLegacyTask = (task: RegistryTask) => task.runtimeContract.name === 'legacy-v1'
const tasksForView = (items: RegistryTask[], view: RegistryScreenView) => (
  view === 'legacy' ? items.filter(isLegacyTask) : items.filter(task => !isLegacyTask(task))
)
const DETAIL_TAB_LABELS: Record<DetailTab, string> = {
  overview: 'Overview',
  activity: 'Activity',
  models: 'Models',
  files: 'Files & versions',
}

export default function Registry({ activeTask, onOpenWorkspace, onOpenFederation }: Props) {
  const studio = useStudio()
  const { t } = useI18n()
  const [view, setView] = useState<RegistryScreenView>('public')
  const [query, setQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [tasks, setTasks] = useState<RegistryTask[]>([])
  const [selected, setSelected] = useState<RegistryTask | null>(null)
  const [tab, setTab] = useState<DetailTab>('overview')
  const [activity, setActivity] = useState<TaskActivity | null>(null)
  const [hub, setHub] = useState<TaskHub | null>(null)
  const [preview, setPreview] = useState<TaskFilePreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activityError, setActivityError] = useState<string | null>(null)
  const [hubError, setHubError] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [participationBusy, setParticipationBusy] = useState(false)
  const [opening, setOpening] = useState(false)
  const [legacyCount, setLegacyCount] = useState(0)
  const canOpenWorkspace = Boolean(selected?.permissions.canOpenWorkspace)

  useEffect(() => {
    if (!studio.bootstrap?.session.isFedOps) return
    const controller = new AbortController()
    listRegistryTasks('all', {}, controller.signal)
      .then(result => setLegacyCount(result.items.filter(isLegacyTask).length))
      .catch(() => undefined)
    return () => controller.abort()
  }, [studio.bootstrap?.session.isFedOps])

  useEffect(() => {
    if (!studio.bootstrap?.session.isFedOps) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    const sourceView: RegistryView = view === 'legacy' ? 'all' : view
    listRegistryTasks(sourceView, { q: view === 'public' ? submittedQuery : undefined }, controller.signal)
      .then(result => {
        const visibleItems = tasksForView(result.items, view)
        setTasks(visibleItems)
        setLegacyCount(current => sourceView === 'all'
          ? result.items.filter(isLegacyTask).length
          : current)
        setSelected(previous => {
          const requested = activeTask && visibleItems.find(task => task.registryId === activeTask.registryId)
          const same = previous && visibleItems.find(task => task.registryId === previous.registryId)
          return requested || same || visibleItems[0] || null
        })
      })
      .catch(cause => { if (!controller.signal.aborted) setError(t(message(cause))) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [activeTask?.registryId, studio.bootstrap?.session.isFedOps, view, submittedQuery])

  useEffect(() => {
    if (!selected || !studio.bootstrap?.session.isFedOps) {
      setActivity(null)
      setHub(null)
      return
    }
    const controller = new AbortController()
    setDetailLoading(true)
    setActivity(null)
    setHub(null)
    setActivityError(null)
    setHubError(null)
    setPreview(null)
    Promise.allSettled([
      getTaskActivity(selected, controller.signal),
      selected.permissions.canDownloadModels
        ? getTaskHub(selected, controller.signal)
        : Promise.resolve(null),
    ]).then(results => {
      if (controller.signal.aborted) return
      const [activityResult, hubResult] = results
      if (activityResult.status === 'fulfilled') setActivity(activityResult.value)
      else setActivityError(t(message(activityResult.reason)))
      if (hubResult.status === 'fulfilled') {
        if (hubResult.value) setHub(hubResult.value)
      } else {
        setHubError(t(message(hubResult.reason)))
      }
      setDetailLoading(false)
    })
    return () => controller.abort()
  }, [selected?.registryId, studio.bootstrap?.session.isFedOps])

  async function join() {
    if (!selected || participationBusy) return
    setParticipationBusy(true)
    setActionMessage(null)
    try {
      const result = await requestTaskParticipation(selected)
      setActionMessage(t('Participation status: {{status}}', { status: t(String(result.status || 'requested')) }))
      const sourceView: RegistryView = view === 'legacy' ? 'all' : view
      const refreshed = await listRegistryTasks(sourceView, { q: submittedQuery })
      const visibleItems = tasksForView(refreshed.items, view)
      setTasks(visibleItems)
      const next = visibleItems.find(task => task.registryId === selected.registryId)
      if (next) setSelected(next)
    } catch (cause) {
      setActionMessage(t(message(cause)))
    } finally {
      setParticipationBusy(false)
    }
  }

  async function leave() {
    if (!selected || participationBusy) return
    const pending = selected.permissions.participationStatus === 'requested'
    const confirmed = window.confirm(pending
      ? t('Withdraw this Federated Learning participation request?')
      : t('Leave this Federated Task? Web access will be revoked, but local files cannot be removed remotely.'))
    if (!confirmed) return
    setParticipationBusy(true)
    setActionMessage(null)
    try {
      const result = await leaveTaskParticipation(selected)
      setActionMessage(t('Participation status: {{status}}', { status: t(String(result.status || 'left')) }))
      const sourceView: RegistryView = view === 'legacy' ? 'all' : view
      const refreshed = await listRegistryTasks(sourceView, { q: submittedQuery })
      const visibleItems = tasksForView(refreshed.items, view)
      setTasks(visibleItems)
      const next = visibleItems.find(task => task.registryId === selected.registryId)
      setSelected(next ?? (view === 'joined' ? null : selected))
    } catch (cause) {
      setActionMessage(t(message(cause)))
    } finally {
      setParticipationBusy(false)
    }
  }

  async function showPreview(fileId: string) {
    if (!selected) return
    setActionMessage(null)
    try {
      setPreview(await previewTaskFile(selected, fileId))
    } catch (cause) {
      setActionMessage(t(message(cause)))
    }
  }

  async function openDownload(kind: 'file' | 'model', id: string) {
    if (!selected) return
    setActionMessage(null)
    try {
      const result = kind === 'file'
        ? await getTaskFileDownload(selected, id)
        : await getModelDownload(selected, id)
      window.open(result.url, '_blank', 'noopener,noreferrer')
    } catch (cause) {
      setActionMessage(t(message(cause)))
    }
  }

  async function openInWorkspace() {
    if (!selected || opening) return
    setOpening(true)
    setActionMessage(null)
    try {
      const result = await openPublishedRelease(selected)
      setActionMessage(t('Workspace ready: {{project}}', { project: result.resultLocalProjectId ?? '—' }))
      await studio.refresh()
      onOpenWorkspace()
    } catch (cause) {
      setActionMessage(t(message(cause)))
    } finally {
      setOpening(false)
    }
  }

  if (!studio.bootstrap?.session.isFedOps) {
    return <Unavailable title={t('FedOps account login required')}>{t('Registry uses the authenticated Task and Global Model APIs from FedOps Web. Sign out, then sign in with a FedOps account.')}</Unavailable>
  }

  return (
    <div className="registry-responsive-layout" style={{ flex: 1, display: 'grid', overflow: 'hidden', background: C.bg }}>
      <aside style={{ background: C.surface, borderRight: `1px solid ${C.border}`, padding: '12px 8px' }}>
        <div style={sectionLabel}>{t('FEDOPS REGISTRY')}</div>
        {VIEWS.map(item => (
          <button key={item.id} onClick={() => { setView(item.id); setSubmittedQuery(''); setQuery(''); setActionMessage(null) }} style={{ ...navButton, background: view === item.id ? C.surface2 : 'transparent', color: view === item.id ? C.text : C.muted, borderLeftColor: view === item.id ? C.accent : 'transparent' }}>
            {t(item.label)}
          </button>
        ))}
        {legacyCount > 0 && (
          <button onClick={() => { setView('legacy'); setSubmittedQuery(''); setQuery(''); setActionMessage(null) }} style={{ ...navButton, background: view === 'legacy' ? C.surface2 : 'transparent', color: view === 'legacy' ? C.text : C.muted, borderLeftColor: view === 'legacy' ? C.yellow : 'transparent' }}>
            {t('FedOps 1.2 Legacy')} ({legacyCount})
          </button>
        )}
        <div style={{ marginTop: 18, padding: 8, borderTop: `1px solid ${C.border}` }}>
          <div style={{ color: C.dim, fontFamily: 'Inter, sans-serif', fontSize: 11, lineHeight: 1.6 }}>{t('Source')}</div>
          <div style={{ color: C.muted, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, wordBreak: 'break-all' }}>{studio.bootstrap.connections.fedopsWeb}</div>
        </div>
      </aside>

      <section style={{ borderRight: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <form onSubmit={event => { event.preventDefault(); setSubmittedQuery(query.trim()) }} style={{ padding: 10, borderBottom: `1px solid ${C.border}`, display: 'flex', gap: 6 }}>
          <input value={query} onChange={event => setQuery(event.target.value)} disabled={view !== 'public'} placeholder={t(view === 'public' ? 'Search Public Federated Tasks' : 'The current FedOps API does not support account search')} style={inputStyle} />
          <Button variant="ghost" disabled={view !== 'public'}>{t('Search')}</Button>
        </form>
        <div style={{ padding: '6px 10px', color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, borderBottom: `1px solid ${C.border}` }}>{loading ? t('Loading…') : t('{{count}} loaded', { count: tasks.length })}</div>
        <div className="scroll-area" style={{ flex: 1, overflowY: 'auto' }}>
          {error && <InlineError>{error}</InlineError>}
          {!loading && !error && tasks.length === 0 && <Empty>{t('No Federated Tasks were found.')}</Empty>}
          {tasks.map(task => (
            <button key={task.registryId} onClick={() => { setSelected(task); setTab('overview'); setActionMessage(null) }} style={{ width: '100%', padding: '10px 12px', background: selected?.registryId === task.registryId ? C.surface2 : 'transparent', border: 'none', borderBottom: `1px solid ${C.borderSubtle}`, borderLeft: `2px solid ${selected?.registryId === task.registryId ? C.accent : 'transparent'}`, textAlign: 'left', cursor: 'pointer' }}>
              <div style={{ color: C.text, fontFamily: 'Inter, sans-serif', fontWeight: 600, fontSize: 13 }}>{task.title}</div>
              {task.displayName !== task.title && <div style={{ color: C.muted, fontSize: 11, marginTop: 2 }}>{t('Federated Task')}: {task.displayName}</div>}
              <div style={{ color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, marginTop: 4 }}>{task.ownerHandle ?? t('unknown')} / {task.slug ?? task.runtimeKey ?? '—'}</div>
              <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
                <Badge variant={task.modelCapability === 'base-llm' ? 'purple' : 'blue'}>{task.modelCapability === 'base-llm' ? 'LLM' : 'AI'}</Badge>
                <Badge variant={task.visibility === 'public' ? 'green' : 'gray'}>{t(task.visibility)}</Badge>
                {task.permissions.isAdmin && task.membership?.role !== 'admin' && <Badge variant="yellow">{t('Admin access')}</Badge>}
                {task.membership && <Badge variant={task.membership.role === 'owner' || task.membership.role === 'admin' ? 'yellow' : 'blue'}>{t(task.membership.role === 'admin' ? 'Admin access' : task.membership.status)}</Badge>}
                <Badge variant={task.status === 'training' ? 'green' : 'gray'}>{t(task.status)}</Badge>
              </div>
            </button>
          ))}
        </div>
      </section>

      <section style={{ display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
        {!selected ? <Empty>{t('Select a Federated Task from the left.')}</Empty> : (
          <>
            <header style={{ padding: '12px 16px', borderBottom: `1px solid ${C.border}`, background: C.surface }}>
              <div className="studio-responsive-header">
                <div style={{ flex: '1 1 280px', minWidth: 0 }}>
                  <h1 style={{ color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 18, margin: 0 }}>{selected.title}</h1>
                  <div style={{ color: C.muted, fontSize: 12, marginTop: 3 }}>{t('Federated Task')}: {selected.displayName}</div>
                  <div style={{ color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, marginTop: 4 }}>{selected.registryId}{selected.taskId ? ` · taskId ${selected.taskId}` : ''}</div>
                </div>
                <div className="studio-responsive-actions">
                  <Badge variant={selected.modelCapability === 'base-llm' ? 'purple' : 'blue'}>{selected.modelCapability === 'base-llm' ? 'LLM' : 'AI'}</Badge>
                  {selected.permissions.isAdmin && <Badge variant="yellow">{t('Admin access')}</Badge>}
                  {isLegacyTask(selected) && <Badge variant="yellow">{t('FedOps 1.2 Legacy')}</Badge>}
                  {!isLegacyTask(selected) && selected.permissions.canRequestParticipation && <Button variant="primary" disabled={participationBusy} onClick={() => void join()}>{t('Request to join')}</Button>}
                  {selected.permissions.participationStatus === 'requested' && <Badge variant="yellow">{t('Approval pending')}</Badge>}
                  {!isLegacyTask(selected) && ['requested', 'approved'].includes(selected.permissions.participationStatus ?? '') && !selected.permissions.isOwner && (
                    <Button
                      variant="danger"
                      disabled={participationBusy || (selected.permissions.participationStatus === 'approved' && !selected.permissions.canLeaveParticipation)}
                      onClick={() => void leave()}
                    >
                      {t(selected.permissions.participationStatus === 'requested' ? 'Withdraw request' : 'Leave Task')}
                    </Button>
                  )}
                  {!isLegacyTask(selected) && selected.permissions.isParticipant && <Button variant="ghost" onClick={() => onOpenFederation(selected)}>{t('Federated Learning')}</Button>}
                  {!isLegacyTask(selected) && selected.registryStatus === 'published' && selected.taskId && canOpenWorkspace && <Button variant="primary" disabled={opening} onClick={() => void openInWorkspace()}>{t(opening ? 'Opening…' : 'Open in Workspace')}</Button>}
                  <Button variant="ghost" onClick={onOpenWorkspace}>{t('Workspace')}</Button>
                </div>
              </div>
              {actionMessage && <div style={{ marginTop: 8, color: C.yellow, fontFamily: 'Inter, sans-serif', fontSize: 12 }}>{actionMessage}</div>}
              {selected.permissions.leaveRequiresCompletedRun && (
                <div style={{ marginTop: 8, color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 12 }}>
                  {t('Complete at least one Federated Learning run after approval before leaving this task.')}
                </div>
              )}
              {isLegacyTask(selected) && (
                <div style={{ marginTop: 8, color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 12 }}>
                  {t('FedOps 1.2 Legacy Tasks are shown for reference and cannot be opened or run in Agent Studio.')}
                </div>
              )}
            </header>
            <div className="studio-scroll-tabs" style={{ display: 'flex', borderBottom: `1px solid ${C.border}`, paddingLeft: 10 }}>
              {(['overview', 'activity', 'models', 'files'] as DetailTab[]).map(item => (
                <button key={item} onClick={() => setTab(item)} style={{ padding: '8px 12px', background: 'transparent', color: tab === item ? C.text : C.dim, border: 'none', borderBottom: `2px solid ${tab === item ? C.accent : 'transparent'}`, cursor: 'pointer' }}>{t(DETAIL_TAB_LABELS[item])}</button>
              ))}
            </div>
            <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
              {detailLoading && <div style={{ color: C.dim }}>{t('Loading FedOps Task data…')}</div>}
              {!detailLoading && tab === 'overview' && <Overview task={selected} />}
              {!detailLoading && tab === 'activity' && (activity ? <Activity activity={activity} /> : <InlineError>{activityError ?? t('Activity data is unavailable.')}</InlineError>)}
              {!detailLoading && tab === 'models' && (!selected.permissions.canDownloadModels
                ? <ParticipationRequired task={selected} busy={participationBusy} onJoin={() => void join()} />
                : hub ? <Models hub={hub} onDownload={id => void openDownload('model', id)} /> : <InlineError>{hubError ?? t('Model registry data is unavailable.')}</InlineError>)}
              {!detailLoading && tab === 'files' && (!selected.permissions.canDownloadModels
                ? <ParticipationRequired task={selected} busy={participationBusy} onJoin={() => void join()} />
                : hub ? <Files hub={hub} preview={preview} onPreview={id => void showPreview(id)} onDownload={id => void openDownload('file', id)} /> : <InlineError>{hubError ?? t('Files & versions data is unavailable.')}</InlineError>)}
            </div>
          </>
        )}
      </section>
    </div>
  )
}

function Overview({ task }: { task: RegistryTask }) {
  const { t, formatDateTime } = useI18n()
  return <div style={{ maxWidth: 850 }}>
    <div style={cardStyle}>
      <MetaRow label={t('Owner')} value={task.ownerHandle ?? '—'} mono />
      <MetaRow label={t('Federated Task')} value={task.displayName} />
      <MetaRow label={t('Primary Model')} value={task.primaryModel?.displayName ?? task.title} />
      <MetaRow label={t('Category / modality')} value={[task.taskCategory, task.dataModality].filter(Boolean).join(' / ') || '—'} />
      <MetaRow label={t('Visibility')} value={t(task.visibility)} mono />
      <MetaRow label={t('Participation')} value={t(task.participationPolicy)} mono />
      <MetaRow label={t('Runtime status')} value={t(task.status)} mono />
      <MetaRow label={t('Data / model')} value={[task.dataType, task.modelType].filter(Boolean).join(' / ') || '—'} mono />
      <MetaRow label={t('Agent role')} value={task.modelCapability === 'base-llm' ? t('Base LLM') : t('Tool AI')} />
      <MetaRow label={t('Strategy / rounds')} value={[task.strategy, task.numRounds].filter(Boolean).join(' / ') || '—'} mono />
      {task.permissions.participationStatus && <MetaRow label={t('Participation status')} value={t(task.permissions.participationStatus)} mono />}
      {task.permissions.isParticipant && <MetaRow label={t('Completed FL runs')} value={String(task.permissions.completedParticipationCount)} mono />}
      {task.permissions.lastParticipatedAt && <MetaRow label={t('Last participation')} value={formatDateTime(task.permissions.lastParticipatedAt)} />}
    </div>
    <h3 style={headingStyle}>{t('Summary')}</h3>
    <div style={cardStyle}>{task.summary || task.description || t('No description has been provided.')}</div>
    <h3 style={headingStyle}>{t('Task Card')}</h3>
    <article className="registry-task-card-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {task.cardMarkdown || t('No Task Card has been provided.')}
      </ReactMarkdown>
    </article>
  </div>
}

function Activity({ activity }: { activity: TaskActivity }) {
  const { t, formatDateTime } = useI18n()
  const progress = activity.run.progressPercent
  const latest = activity.metrics.latest
  return <div style={{ maxWidth: 980 }}>
    <div className="registry-section-heading">
      <div>
        <div style={sectionLabel}>{t('FEDERATED ACTIVITY')}</div>
        <h2 style={{ margin: 0, color: C.text, fontSize: 18 }}>{t('Training progress')}</h2>
        <p style={{ margin: '5px 0 0', color: C.muted, fontSize: 12 }}>{t('Global progress and privacy-safe participation statistics.')}</p>
      </div>
      <Badge variant={activity.run.status === 'completed' ? 'green' : activity.run.status === 'training' ? 'blue' : 'gray'}>{t(activity.run.status)}</Badge>
    </div>
    <div className="registry-stat-grid">
      <Stat label={t('Status')} value={t(activity.run.status)} />
      <Stat label={t('Round')} value={`${activity.run.currentRound} / ${activity.run.totalRounds ?? '—'}`} />
      <Stat label={t('Approved')} value={activity.participants.approved} />
      <Stat label={t('Online devices')} value={activity.participants.onlineDevices ?? '—'} />
    </div>
    <div className="registry-progress-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
        <div><strong style={{ color: C.text }}>{t('Federated progress')}</strong><div style={{ marginTop: 3, color: C.muted, fontSize: 11 }}>{t('Communication rounds completed by the aggregation server.')}</div></div>
        <strong style={{ color: C.accent, fontFamily: 'JetBrains Mono, monospace', fontSize: 18 }}>{progress === null ? '—' : `${progress}%`}</strong>
      </div>
      <div style={{ height: 9, marginTop: 14, borderRadius: 999, background: C.surface2, overflow: 'hidden' }}><div style={{ height: '100%', width: `${progress ?? 0}%`, borderRadius: 999, background: `linear-gradient(90deg, ${C.accent}, ${C.green})` }} /></div>
      {progress === null && <div style={{ marginTop: 7, color: C.muted, fontSize: 11 }}>{t('Total-round information is unavailable, so progress cannot be calculated.')}</div>}
    </div>
    <div className="registry-section-heading" style={{ marginTop: 22 }}>
      <div><h3 style={{ margin: 0, color: C.text, fontSize: 14 }}>{t('Global model metrics')}</h3><p style={{ margin: '4px 0 0', color: C.muted, fontSize: 11 }}>{t('Aggregated metrics reported for each communication round.')}</p></div>
      {latest && <div style={{ display: 'flex', gap: 6 }}><Badge variant="green">{t('Accuracy')} {formatMetric(latest.accuracy)}</Badge><Badge variant="purple">{t('Loss')} {formatMetric(latest.loss)}</Badge></div>}
    </div>
    {activity.metrics.history.length === 0 ? <Empty>{t('No aggregated global metrics are available.')}</Empty> : (
      <div className="registry-metric-history">
        {activity.metrics.history.map((metric, index) => (
          <div className="registry-metric-row" key={`${metric.round}-${index}`}>
            <div><span style={{ color: C.dim, fontSize: 10 }}>{t('ROUND')}</span><strong>{metric.round ?? '—'}</strong></div>
            <div><span>{t('Model')}</span><strong>v{metric.modelVersion ?? '—'}</strong></div>
            <div><span>{t('Accuracy')}</span><strong style={{ color: C.green }}>{formatMetric(metric.accuracy)}</strong></div>
            <div><span>{t('Loss')}</span><strong style={{ color: C.purple }}>{formatMetric(metric.loss)}</strong></div>
            <div><span>{t('Duration')}</span><strong>{metric.roundTimeSeconds === null ? '—' : `${metric.roundTimeSeconds}s`}</strong></div>
          </div>
        ))}
      </div>
    )}
    <div style={{ marginTop: 10, color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>{t('source')}: {activity.source.type} · {t('manager')} {t(activity.source.managerAvailable ? 'available' : 'unavailable')} · {t('observed')} {activity.source.observedAt ? formatDateTime(activity.source.observedAt) : '—'}</div>
  </div>
}

function Models({ hub, onDownload }: { hub: TaskHub; onDownload: (id: string) => void }) {
  const { t, formatDateTime } = useI18n()
  if (!hub.models.length) return <Empty>{t('No Global Model versions were found in S3.')}</Empty>
  return <div style={{ maxWidth: 1040 }}>
    <div className="registry-section-heading">
      <div><div style={sectionLabel}>{t('GLOBAL MODELS')}</div><h2 style={{ margin: 0, color: C.text, fontSize: 18 }}>{t('Model versions')}</h2><p style={{ margin: '5px 0 0', color: C.muted, fontSize: 12 }}>{t('Immutable model artifacts published for this Federated Task.')}</p></div>
      <Badge variant="blue">{t('{{count}} versions', { count: hub.models.length })}</Badge>
    </div>
    <div className="registry-model-grid">{hub.models.map(model => <ModelCard key={model.id} model={model} onDownload={onDownload} formatDateTime={formatDateTime} />)}</div>
  </div>
}

type FileTreeNode = { name: string; path: string; children: FileTreeNode[]; file?: TaskHubFile }

function Files({ hub, preview, onPreview, onDownload }: { hub: TaskHub; preview: TaskFilePreview | null; onPreview: (id: string) => void; onDownload: (id: string) => void }) {
  const { t } = useI18n()
  const tree = buildFileTree(hub.files)
  const [openFolders, setOpenFolders] = useState(() => new Set(tree.filter(node => !node.file).map(node => node.path)))
  if (hub.files.length === 0) return <Empty>{t('No Files & versions entries are available.')}</Empty>
  const toggle = (path: string) => setOpenFolders(current => {
    const next = new Set(current)
    if (next.has(path)) next.delete(path)
    else next.add(path)
    return next
  })
  return <div style={{ maxWidth: 1120 }}>
    <div className="registry-section-heading">
      <div><div style={sectionLabel}>{t('RELEASE FILES')}</div><h2 style={{ margin: 0, color: C.text, fontSize: 18 }}>{t('Files & versions')}</h2><p style={{ margin: '5px 0 0', color: C.muted, fontSize: 12 }}>{t('The same Released Task structure used by Agent Studio Workspace.')}</p></div>
      <Badge variant="blue">{t('{{count}} files', { count: hub.files.length })}</Badge>
    </div>
    <div className="registry-files-layout">
      <div className="registry-file-tree">
        <div className="registry-file-tree-header"><span>{t('Federated Task Release')}</span><span>v{hub.release?.revision ?? '—'}</span></div>
        {tree.map(node => <FileNode key={node.path} node={node} depth={0} openFolders={openFolders} selectedId={preview?.file.id ?? null} onToggle={toggle} onPreview={onPreview} onDownload={onDownload} />)}
      </div>
      <div className="registry-file-preview">
        {!preview ? <div className="registry-preview-empty"><FileTypeIcon name="README.md" size={30} /><strong>{t('Select a previewable file')}</strong><span>{t('Browse the Release tree to inspect code and documentation.')}</span></div> : <><div className="registry-file-preview-header"><div><strong>{preview.file.path}</strong><span>{preview.language} · {formatBytes(preview.file.size)}{preview.truncated ? ` · ${t('truncated')}` : ''}</span></div><Button variant="ghost" onClick={() => onDownload(preview.file.id)}>{t('Download')}</Button></div><pre>{preview.content}</pre></>}
      </div>
    </div>
  </div>
}

function ModelCard({ model, onDownload, formatDateTime }: { model: TaskHubModel; onDownload: (id: string) => void; formatDateTime: (value: string | Date) => string }) {
  const { t } = useI18n()
  return <article className="registry-model-card">
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
      <div className="registry-model-icon">AI</div>
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        {model.aliases?.latest && <Badge variant="green">{t('latest')}</Badge>}
        {model.aliases?.best && <Badge variant="purple">{t('best')}</Badge>}
        <Badge variant="gray">v{model.version}</Badge>
      </div>
    </div>
    <h3>{model.modelName}</h3>
    <div className="registry-model-meta">
      <div><span>{t('Artifact')}</span><strong>{model.name}</strong></div>
      <div><span>{t('Format')}</span><strong>{model.format || '—'}</strong></div>
      <div><span>{t('Size')}</span><strong>{formatBytes(model.size)}</strong></div>
      <div><span>{t('Published')}</span><strong>{model.createdAt ? formatDateTime(model.createdAt) : '—'}</strong></div>
    </div>
    {model.checksum && <code title={model.checksum}>{model.checksum.slice(0, 18)}…</code>}
    <Button variant="ghost" onClick={() => onDownload(model.id)}>{t('Download model')}</Button>
  </article>
}

function buildFileTree(files: TaskHubFile[]): FileTreeNode[] {
  const root: FileTreeNode = { name: '', path: '', children: [] }
  for (const file of [...files].sort((left, right) => left.path.localeCompare(right.path))) {
    const parts = file.path.split('/').filter(Boolean)
    let current = root
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join('/')
      let child = current.children.find(item => item.name === part)
      if (!child) {
        child = { name: part, path, children: [] }
        current.children.push(child)
      }
      if (index === parts.length - 1) child.file = file
      current = child
    })
  }
  const sort = (nodes: FileTreeNode[]): FileTreeNode[] => nodes
    .map(node => ({ ...node, children: sort(node.children) }))
    .sort((left, right) => Number(Boolean(left.file)) - Number(Boolean(right.file)) || left.name.localeCompare(right.name))
  return sort(root.children)
}

function FileNode({ node, depth, openFolders, selectedId, onToggle, onPreview, onDownload }: { node: FileTreeNode; depth: number; openFolders: Set<string>; selectedId: string | null; onToggle: (path: string) => void; onPreview: (id: string) => void; onDownload: (id: string) => void }) {
  const { t } = useI18n()
  const directory = !node.file
  const open = directory && openFolders.has(node.path)
  return <>
    <div className={`registry-file-row${node.file?.id === selectedId ? ' selected' : ''}`} style={{ paddingLeft: 10 + depth * 17 }}>
      <button type="button" className="registry-file-main" onClick={() => directory ? onToggle(node.path) : node.file?.previewable && onPreview(node.file.id)} title={fileTypeLabel(node.name, directory)}>
        <span className="registry-tree-chevron">{directory ? open ? '⌄' : '›' : ''}</span>
        <FileTypeIcon name={node.name} directory={directory} open={open} size={17} />
        <span>{node.name}</span>
      </button>
      {node.file && <div className="registry-file-actions"><span>v{node.file.version}</span>{node.file.previewable && <button type="button" onClick={() => onPreview(node.file!.id)}>{t('Preview')}</button>}<button type="button" onClick={() => onDownload(node.file!.id)}>{t('Download')}</button></div>}
    </div>
    {open && node.children.map(child => <FileNode key={child.path} node={child} depth={depth + 1} openFolders={openFolders} selectedId={selectedId} onToggle={onToggle} onPreview={onPreview} onDownload={onDownload} />)}
  </>
}

function ParticipationRequired({ task, busy, onJoin }: { task: RegistryTask; busy: boolean; onJoin: () => void }) {
  const { t } = useI18n()
  const status = task.permissions.participationStatus
  return <div className="registry-access-required">
    <div className="registry-access-icon">◎</div>
    <Badge variant={status === 'requested' ? 'yellow' : 'blue'}>{t(status === 'requested' ? 'Approval pending' : 'Participation required')}</Badge>
    <h2>{t('Join to inspect this Release')}</h2>
    <p>{t('Task code, model artifacts, and Files & versions become available after you join Federated Learning and the owner approves the request.')}</p>
    {task.permissions.canRequestParticipation && <Button variant="primary" disabled={busy} onClick={onJoin}>{t(busy ? 'Requesting…' : 'Request to join')}</Button>}
    {status === 'requested' && <span>{t('Your request is waiting for owner approval.')}</span>}
    {task.participationPolicy === 'closed' && !status && <span>{t('This Task is not accepting new participants.')}</span>}
  </div>
}

function Stat({ label, value }: { label: string; value: string | number }) { return <div style={cardStyle}><div style={{ color: C.dim, fontSize: 10 }}>{label}</div><div style={{ color: C.text, fontFamily: 'JetBrains Mono, monospace', fontSize: 16, marginTop: 6 }}>{value}</div></div> }
function Unavailable({ title, children }: { title: string; children: React.ReactNode }) { return <div style={{ flex: 1, display: 'grid', placeItems: 'center', background: C.bg }}><div style={{ ...cardStyle, width: 480 }}><h2 style={{ margin: '0 0 8px', color: C.text, fontSize: 16 }}>{title}</h2><div style={{ color: C.muted, fontSize: 13, lineHeight: 1.6 }}>{children}</div></div></div> }
function InlineError({ children }: { children: React.ReactNode }) { return <div style={{ margin: 10, padding: 10, background: C.redDim, border: `1px solid ${C.redBorder}`, borderRadius: C.radius, color: C.red, fontSize: 12 }}>{children}</div> }
function Empty({ children }: { children: React.ReactNode }) { return <div style={{ padding: 22, color: C.dim, fontSize: 12, textAlign: 'center' }}>{children}</div> }
function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause) }
function formatBytes(value: number | null) { if (value === null || value === undefined) return '—'; if (value < 1024) return `${value} B`; if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1024 ** 2).toFixed(1)} MB` }
function formatMetric(value: number | null) { if (value === null || value === undefined) return '—'; return value > 0 && value <= 1 ? `${(value * 100).toFixed(2)}%` : value.toFixed(4) }

const sectionLabel: React.CSSProperties = { color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, letterSpacing: '0.08em', padding: '0 8px 8px' }
const navButton: React.CSSProperties = { display: 'block', width: '100%', border: 'none', borderLeft: '2px solid transparent', padding: '7px 9px', textAlign: 'left', cursor: 'pointer', fontSize: 12 }
const inputStyle: React.CSSProperties = { flex: 1, minWidth: 0, background: C.inputBg, color: C.text, border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, padding: '6px 8px', outline: 'none', fontSize: 12 }
const cardStyle: React.CSSProperties = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 12, color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 12, lineHeight: 1.6 }
const rowStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, padding: '10px 12px', background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, marginBottom: 6 }
const headingStyle: React.CSSProperties = { color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 13, margin: '16px 0 7px' }
const tableCell: React.CSSProperties = { padding: '7px 9px', borderBottom: `1px solid ${C.border}`, color: C.muted, textAlign: 'left', fontFamily: 'JetBrains Mono, monospace' }
