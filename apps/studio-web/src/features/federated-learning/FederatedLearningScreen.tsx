import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { useStudio } from '../../app/StudioProvider'
import { useI18n } from '../../app/i18n'
import type { ActiveTask } from '../../app/types'
import {
  getFederatedParticipation,
  getFederatedParticipationHistory,
  listFederatedParticipations,
  listFederatedParticipationHistory,
  preflightFederatedParticipation,
  startFederatedParticipation,
  stopFederatedParticipation,
} from '../../api/federatedLearning'
import type {
  FederatedParticipation,
  FederatedParticipationHistory,
  FederatedParticipationSession,
  FederatedPreflight,
  FederatedRuntimeEvent,
} from '../../api/federatedLearning'
import {
  cancelWorkspaceRun,
  getWorkspaceRun,
  listWorkspaceRuns,
  openWorkspaceDataFolder,
  prepareWorkspaceDataBinding,
  startWorkspaceAction,
} from '../../api/workspace'
import type { LocalDataBinding, WorkspaceRun } from '../../api/workspace'
import { Badge, Button, C } from '../../ui/UIKit'
import MetricChartPanel from '../../ui/MetricChartPanel'
import type { MetricChartSeries } from '../../ui/MetricChartPanel'
import { groupMetricNames } from '../../ui/metricSemantics'

interface Props {
  activeTask: ActiveTask | null
  onSelectTask: (task: ActiveTask | null) => void
  onOpenWorkspace: () => void
}

type Filter = 'all' | 'ready' | 'active' | 'training' | 'waiting' | 'completed' | 'stopped' | 'needs_attention'
type ConsoleTab = 'Logs' | 'Metrics' | 'Communication' | 'Models & Artifacts'

const ACTIVE_STATES = new Set(['starting', 'stopping', 'connecting', 'downloading_global', 'training', 'evaluating', 'preparing_update', 'uploading', 'waiting_aggregation'])
const WAITING_STATES = new Set(['waiting_round', 'waiting_aggregation'])
const PHASES = [
  ['sync', 'Global Model Sync', ['waiting_round', 'connecting', 'downloading_global']],
  ['local', 'Local Train & Evaluate', ['training', 'evaluating']],
  ['upload', 'Send Model Update', ['preparing_update', 'uploading']],
  ['aggregate', 'Server Aggregation', ['waiting_aggregation']],
  ['complete', 'Round Complete', ['global_model_updated', 'completed']],
] as const
const TERMINAL_WORKSPACE_RUNS = new Set<WorkspaceRun['status']>([
  'succeeded',
  'failed',
  'cancelled',
])

const participationRoleLabel = (role?: 'owner' | 'admin' | 'participant') => (
  role === 'owner'
    ? 'Owner · Participant'
    : role === 'admin'
      ? 'Admin access'
      : 'Participant'
)

export default function FederatedLearning({ activeTask, onSelectTask, onOpenWorkspace }: Props) {
  const studio = useStudio()
  const { t } = useI18n()
  const [items, setItems] = useState<FederatedParticipation[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(activeTask?.localProjectId ?? null)
  const [selected, setSelected] = useState<FederatedParticipation | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function loadList(signal?: AbortSignal) {
    const result = await listFederatedParticipations(signal)
    setItems(result.items)
    setSelectedId(current => current && result.items.some(item => item.localProjectId === current) ? current : null)
  }

  useEffect(() => {
    if (!activeTask || items.length === 0) return
    const fromContext = items.find(item =>
      item.localProjectId === activeTask.localProjectId
      || (activeTask.taskId && item.task.taskId === activeTask.taskId),
    )
    if (fromContext) setSelectedId(fromContext.localProjectId)
  }, [activeTask?.localProjectId, activeTask?.taskId, items])

  useEffect(() => {
    if (!studio.bootstrap?.session.isFedOps) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    loadList(controller.signal)
      .catch(cause => { if (!controller.signal.aborted) setError(message(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [studio.bootstrap?.session.isFedOps])

  useEffect(() => {
    if (!studio.bootstrap?.session.isFedOps) return
    const timer = window.setInterval(() => void loadList().catch(() => undefined), 2500)
    return () => window.clearInterval(timer)
  }, [studio.bootstrap?.session.isFedOps])

  useEffect(() => {
    if (!selectedId) {
      setSelected(null)
      return
    }
    let stopped = false
    let controller: AbortController | null = null
    let timer: number | null = null
    let hasSnapshot = Boolean(selected?.localProjectId === selectedId || items.some(item => item.localProjectId === selectedId))
    let consecutiveFailures = 0
    const poll = async () => {
      let nextDelay = 1200
      const requestController = new AbortController()
      controller = requestController
      try {
        const detail = await getFederatedParticipation(selectedId, requestController.signal)
        if (!stopped) {
          hasSnapshot = true
          consecutiveFailures = 0
          setSelected(detail)
          setItems(current => current.map(item => item.localProjectId === detail.localProjectId ? detail : item))
          setError(null)
          nextDelay = isRunning(detail) ? 400 : 1200
        }
      } catch (cause) {
        if (!stopped && !requestController.signal.aborted) {
          const detail = message(cause)
          if (detail === 'The local Workspace project was not found.') {
            setSelected(null)
            setSelectedId(null)
            onSelectTask(null)
            setError(null)
          } else if (!hasSnapshot) {
            setError(detail)
          } else {
            consecutiveFailures += 1
            if (consecutiveFailures >= 3) {
              setError('Live update is temporarily paused. Showing the last local Client state.')
            }
          }
        }
      } finally {
        if (!stopped) timer = window.setTimeout(() => void poll(), nextDelay)
      }
    }
    void poll()
    return () => {
      stopped = true
      controller?.abort()
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [selectedId])

  useEffect(() => {
    if (!selected) return
    onSelectTask({
      registryId: selected.task.taskId,
      taskId: selected.task.taskId,
      title: selected.task.title,
      runtimeKey: selected.task.runtimeKey,
      ownerHandle: null,
      slug: null,
      role: selected.participation?.role ?? null,
      localProjectId: selected.localProjectId,
    })
  }, [selected?.localProjectId])

  if (!studio.bootstrap?.session.isFedOps) {
    return <Centered title={t('FedOps account login required')}>{t('Sign in to manage your authorized local Federated Learning Clients.')}</Centered>
  }
  const activeClientCount = items.filter(isRunning).length
  if (selectedId) {
    return (
      <ParticipationConsole
        item={selected?.localProjectId === selectedId ? selected : items.find(item => item.localProjectId === selectedId) ?? null}
        activeClientCount={activeClientCount}
        error={error}
        onBack={() => { setSelected(null); setSelectedId(null); onSelectTask(null) }}
        onOpenWorkspace={onOpenWorkspace}
        onChanged={next => { setSelected(next); setItems(current => current.map(item => item.localProjectId === next.localProjectId ? next : item)) }}
      />
    )
  }

  const filters: { id: Filter; label: string; match: (item: FederatedParticipation) => boolean }[] = [
    { id: 'all', label: 'All', match: () => true },
    { id: 'ready', label: 'Ready', match: item => item.workspace.ready && item.server?.ready === true && !isRunning(item) },
    { id: 'active', label: 'Active', match: isRunning },
    { id: 'training', label: 'Training', match: item => item.runtime.clientState === 'training' },
    { id: 'waiting', label: 'Waiting', match: item => WAITING_STATES.has(item.runtime.clientState) },
    { id: 'completed', label: 'Completed', match: isCompleted },
    { id: 'stopped', label: 'Stopped', match: item => item.runtime.status === 'stopped' },
    { id: 'needs_attention', label: 'Needs Attention', match: item => Boolean(item.unavailableReason) || !item.workspace.ready || !item.server?.ready || ['failed', 'disconnected'].includes(item.runtime.status) },
  ]
  const visible = items.filter(filters.find(candidate => candidate.id === filter)?.match ?? (() => true))

  return (
    <div style={pageStyle}>
      <header style={homeHeaderStyle}>
        <div className="studio-responsive-header">
          <div style={{ flex: 1 }}>
            <h1 style={titleStyle}>{t('My Federated Participations')}</h1>
            <p style={subtitleStyle}>{t('Run and monitor this account’s local Client for authorized Federated Tasks.')}</p>
          </div>
          <Badge variant={activeClientCount ? 'green' : 'gray'}>● {activeClientCount} {t('active Clients')}</Badge>
          <Button variant="ghost" onClick={onOpenWorkspace}>→ {t('Open Workspace')}</Button>
          <Button variant="ghost" onClick={() => { setLoading(true); loadList().catch(cause => setError(message(cause))).finally(() => setLoading(false)) }}>↻ {t('Refresh')}</Button>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 12 }}>
          {filters.map(candidate => {
            const count = items.filter(candidate.match).length
            return <FilterButton key={candidate.id} active={filter === candidate.id} onClick={() => setFilter(candidate.id)}>{t(candidate.label)} <span style={{ opacity: .72 }}>{count}</span></FilterButton>
          })}
        </div>
      </header>
      <main className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
        {error && <ErrorBox>{error}</ErrorBox>}
        {activeClientCount > 1 && <ConcurrentResourceNotice count={activeClientCount} />}
        {loading && items.length === 0 ? <Centered title={t('Loading…')}>{t('Loading authorized local Client state from FedOps Web and this device.')}</Centered> : null}
        {!loading && visible.length === 0 ? <Centered title={t('No Federated Participations')}>{t('Open an approved Published Release from Registry, then pass Participation Readiness in Workspace.')}</Centered> : null}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          {visible.map(item => <ParticipationCard key={item.localProjectId} item={item} onClick={() => setSelectedId(item.localProjectId)} />)}
        </div>
      </main>
    </div>
  )
}

function ParticipationCard({ item, onClick }: { item: FederatedParticipation; onClick: () => void }) {
  const { t, formatDateTime } = useI18n()
  const state = item.runtime.clientState || item.runtime.status
  return (
    <button onClick={onClick} style={participationCardStyle}>
      <div style={{ minWidth: 0, flex: 1, textAlign: 'left' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong style={{ color: C.text, fontSize: 14 }}>{item.task.primaryModel?.displayName ?? item.task.primaryModel?.workingName ?? item.task.title}</strong>
          <Badge variant={item.participation?.role === 'admin' ? 'yellow' : 'purple'}>{t(participationRoleLabel(item.participation?.role))}</Badge>
          <StateBadge state={state} />
          <ServerLiveBadge server={item.server} />
        </div>
        <div style={monoSubStyle}>{t('Federated Task')}: {item.task.title} · {item.task.runtimeKey} · {item.release?.releaseId ?? t('Published Release unavailable')}</div>
      </div>
      <CardFact label={t(isInitiativeModel(item) ? 'Initiative Model' : 'Global Model')} value={globalVersion(item)} />
      <CardFact label={t('Round')} value={round(item)} />
      <CardFact label={t('Workspace')} value={item.workspace.ready ? t('Ready') : t('Needs Attention')} tone={item.workspace.ready ? C.green : C.yellow} />
      <CardFact label={t('Client Progress')} value={isCompleted(item) ? t('Completed') : progress(item)} tone={isCompleted(item) ? C.green : C.text} />
      <CardFact label={t('Last activity')} value={item.latestEvent?.timestamp ? formatDateTime(item.latestEvent.timestamp) : '—'} />
      <span style={{ color: C.accent, fontSize: 16 }}>›</span>
    </button>
  )
}

function ParticipationConsole({ item, activeClientCount, error, onBack, onOpenWorkspace, onChanged }: {
  item: FederatedParticipation | null
  activeClientCount: number
  error: string | null
  onBack: () => void
  onOpenWorkspace: () => void
  onChanged: (next: FederatedParticipation) => void
}) {
  const { t } = useI18n()
  const [dataPath, setDataPath] = useState('')
  const [dataBinding, setDataBinding] = useState<LocalDataBinding | null>(null)
  const [preflight, setPreflight] = useState<FederatedPreflight | null>(null)
  const [readinessRun, setReadinessRun] = useState<WorkspaceRun | null>(null)
  const [busy, setBusy] = useState<'start' | 'stop' | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [tab, setTab] = useState<ConsoleTab>('Logs')
  const [historySessions, setHistorySessions] = useState<FederatedParticipationSession[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [historyRefresh, setHistoryRefresh] = useState(0)
  const [selectedSessionId, setSelectedSessionId] = useState('')
  const [historyDetail, setHistoryDetail] = useState<FederatedParticipationHistory | null>(null)
  const [selectedRound, setSelectedRound] = useState('all')
  const [selectedEpoch, setSelectedEpoch] = useState('all')

  useEffect(() => {
    if (!item?.localProjectId) return
    let active = true
    prepareWorkspaceDataBinding(item.localProjectId)
      .then(binding => {
        if (!active) return
        setDataBinding(binding)
        setDataPath(binding.containerPath)
      })
      .catch(cause => { if (active) setActionError(message(cause)) })
    return () => { active = false }
  }, [item?.localProjectId])

  useEffect(() => {
    setPreflight(item?.workspace.participationPreflight ?? null)
  }, [item?.localProjectId, item?.campaignRun?.runId, item?.workspace.participationPreflight?.checkedAt])

  useEffect(() => {
    if (!item?.localProjectId) return
    const controller = new AbortController()
    listWorkspaceRuns(item.localProjectId, controller.signal)
      .then(result => {
        const active = result.items.find(run => run.kind === 'participation-readiness' && !TERMINAL_WORKSPACE_RUNS.has(run.status))
        if (active) setReadinessRun(active)
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [item?.localProjectId])

  useEffect(() => {
    if (!item?.localProjectId || !readinessRun || TERMINAL_WORKSPACE_RUNS.has(readinessRun.status)) return
    let stopped = false
    let timer: number | null = null
    const poll = async () => {
      try {
        const current = await getWorkspaceRun(readinessRun.runId)
        if (stopped) return
        setReadinessRun(current)
        if (TERMINAL_WORKSPACE_RUNS.has(current.status)) {
          if (current.status === 'succeeded') {
            const selectedDataPath = dataPath || (await prepareWorkspaceDataBinding(item.localProjectId)).containerPath
            const result = await preflightFederatedParticipation(item.localProjectId, { dataPath: selectedDataPath })
            if (stopped) return
            setPreflight(result)
            onChanged(await getFederatedParticipation(item.localProjectId))
          } else if (current.status === 'failed') {
            setActionError(current.output.trim() || 'Participation Readiness needs attention.')
          }
          setReadinessRun(null)
          return
        }
        timer = window.setTimeout(() => void poll(), 500)
      } catch (cause) {
        if (!stopped) {
          setActionError(message(cause))
          timer = window.setTimeout(() => void poll(), 1200)
        }
      }
    }
    void poll()
    return () => {
      stopped = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [item?.localProjectId, readinessRun?.runId])

  useEffect(() => {
    if (!item?.localProjectId) return
    const controller = new AbortController()
    let retryTimer: number | null = null
    let attempt = 0
    setHistoryLoading(true)
    setHistoryError(null)
    const load = async () => {
      try {
        const result = await listFederatedParticipationHistory(item.localProjectId, controller.signal)
        if (controller.signal.aborted) return
        setHistorySessions(result.items)
        setHistoryError(null)
        setHistoryLoading(false)
      } catch (cause) {
        if (controller.signal.aborted) return
        if (attempt < 1) {
          attempt += 1
          retryTimer = window.setTimeout(() => void load(), 450)
          return
        }
        setHistoryError(message(cause))
        setHistoryLoading(false)
      }
    }
    void load()
    return () => {
      controller.abort()
      if (retryTimer !== null) window.clearTimeout(retryTimer)
    }
  }, [item?.localProjectId, item?.campaignRun?.runId, item?.runtime.sessionId, item?.runtime.endedAt, historyRefresh])

  useEffect(() => {
    setSelectedSessionId('')
    setHistoryDetail(null)
    setSelectedRound('all')
    setSelectedEpoch('all')
  }, [item?.localProjectId])

  useEffect(() => {
    if (!item?.localProjectId || !selectedSessionId) {
      setHistoryDetail(null)
      return
    }
    const controller = new AbortController()
    let timer: number | null = null
    const poll = async () => {
      try {
        const value = await getFederatedParticipationHistory(item.localProjectId, selectedSessionId, controller.signal)
        setHistoryDetail(value)
        if (['starting', 'running', 'stopping'].includes(value.session.status)) {
          timer = window.setTimeout(() => void poll(), 700)
        }
      } catch (cause) {
        if (!controller.signal.aborted) setActionError(message(cause))
      }
    }
    void poll()
    return () => {
      controller.abort()
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [item?.localProjectId, selectedSessionId])

  if (!item) return <Centered title={t('Loading…')}>{t('Loading local Client state…')}</Centered>
  const participation = item
  const running = isRunning(participation)
  const completed = isCompleted(participation)
  const canStop = canStopClient(participation)
  const readinessActive = Boolean(readinessRun && !TERMINAL_WORKSPACE_RUNS.has(readinessRun.status))
  const participationReady = Boolean(item.workspace.participationReadyToStart)
  const serverAvailable = Boolean(item.workspace.serverAvailability?.ready)
  const currentStage = normalizedStage(item.runtime.clientState, item.latestEvent?.stage)
  const historyEvents = normalizeCampaignRounds(
    historyDetail?.events ?? item.events ?? [],
    Boolean(historyDetail?.session.campaignRunId ?? item.campaignRun?.runId),
  )
  const availableRounds = [...new Set(historyEvents.flatMap(event => typeof event.round === 'number' ? [event.round] : []))].sort((a, b) => a - b)
  const roundEvents = selectedRound === 'all'
    ? historyEvents
    : historyEvents.filter(event => event.round === Number(selectedRound))
  const availableEpochs = selectedRound === 'all' ? [] : [...new Set(roundEvents.flatMap(event => typeof event.epoch === 'number' ? [event.epoch] : []))].sort((a, b) => a - b)
  const metricEvents = selectedEpoch === 'all'
    ? roundEvents
    : roundEvents.filter(event => event.epoch === Number(selectedEpoch))
  const visibleEvents = roundEvents
  const viewedItem: FederatedParticipation = {
    ...item,
    events: visibleEvents,
    latestEvent: visibleEvents.at(-1) ?? null,
    runtime: historyDetail ? {
      ...item.runtime,
      status: historyDetail.session.status,
      clientState: String(visibleEvents.at(-1)?.stage ?? historyDetail.session.status),
      startedAt: historyDetail.session.startedAt,
      endedAt: historyDetail.session.endedAt,
      campaignRunId: historyDetail.session.campaignRunId,
      sessionId: historyDetail.session.sessionId,
    } : item.runtime,
    campaign: historyDetail?.session.campaign ?? item.campaign,
  }
  const viewedStage = normalizedStage(viewedItem.runtime.clientState, viewedItem.latestEvent?.stage)
  const latestMetrics = latestOwnMetrics(viewedItem.events ?? [])

  async function action(kind: 'start' | 'stop') {
    setBusy(kind)
    setActionError(null)
    try {
      const selectedDataPath = kind === 'stop'
        ? dataPath
        : dataPath || (await prepareWorkspaceDataBinding(participation.localProjectId)).containerPath
      if (kind === 'start') onChanged(await startFederatedParticipation(participation.localProjectId, { dataPath: selectedDataPath }))
      if (kind === 'stop') onChanged(await stopFederatedParticipation(participation.localProjectId))
    } catch (cause) {
      setActionError(message(cause))
    } finally {
      setBusy(null)
    }
  }

  async function startReadiness() {
    setActionError(null)
    setPreflight(null)
    try {
      const selectedDataPath = dataPath || (await prepareWorkspaceDataBinding(participation.localProjectId)).containerPath
      const started = await startWorkspaceAction(
        participation.localProjectId,
        'participation-readiness',
        participation.runtime.environmentId,
        undefined,
        selectedDataPath,
      )
      setReadinessRun(started)
    } catch (cause) {
      setActionError(message(cause))
    }
  }

  async function cancelReadiness() {
    if (!readinessRun) return
    setActionError(null)
    try {
      setReadinessRun(await cancelWorkspaceRun(readinessRun.runId))
    } catch (cause) {
      setActionError(message(cause))
    }
  }

  async function openDataFolder() {
    setActionError(null)
    try {
      const binding = await openWorkspaceDataFolder(participation.localProjectId)
      setDataBinding(binding)
      setDataPath(binding.containerPath)
    } catch (cause) {
      setActionError(message(cause))
    }
  }

  return (
    <div style={pageStyle}>
      <header style={consoleHeaderStyle}>
        <div className="studio-responsive-header">
          <button onClick={onBack} style={backButtonStyle}>‹</button>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
              <h1 style={{ ...titleStyle, fontSize: 16 }}>{item.task.primaryModel?.displayName ?? item.task.primaryModel?.workingName ?? item.task.title}</h1>
              <Badge variant={item.participation?.role === 'admin' ? 'yellow' : 'purple'}>{t(participationRoleLabel(item.participation?.role))}</Badge>
              <StateBadge state={currentStage} />
              {running && <Badge variant="green">● {t('Live')}</Badge>}
              {completed && <Badge variant="green">✓ {t('Federated Learning completed')}</Badge>}
              <ServerLiveBadge server={item.server} />
            </div>
            <div style={monoSubStyle}>{t('Federated Task')}: {item.task.title}</div>
          </div>
          <Button variant="ghost" onClick={onOpenWorkspace}>{t('Open Workspace')}</Button>
          {!running && !completed && <Button variant="success" disabled={Boolean(busy) || readinessActive || Boolean(item.unavailableReason) || !item.workspace.readyToStart} onClick={() => void action('start')}>▷ {t(busy === 'start' ? 'Starting…' : 'Start Client')}</Button>}
          {canStop && <Button variant="danger" disabled={Boolean(busy)} onClick={() => void action('stop')}>■ {t(busy === 'stop' ? 'Stopping…' : completed ? 'Finish Client Session' : 'Stop Client')}</Button>}
        </div>
        <div style={headerFactsStyle}>
          <HeaderFact label={t(isInitiativeModel(item) ? 'Initiative Model' : 'Global Model')} value={globalVersion(item)} />
          <HeaderFact label={t('Campaign')} value={`v${item.campaignRun?.targetGlobalModelVersion ?? '—'} · ${t('Round')} ${round(viewedItem)} / ${activeCampaign(item)?.rounds ?? '—'}`} />
          <HeaderFact label={t('Clients per round')} value={String(activeCampaign(item)?.clientsPerRound ?? '—')} />
        </div>
      </header>

      <main className="scroll-area" style={{ flex: 1, overflow: 'auto', padding: 14 }}>
        {(error || actionError || item.unavailableReason) && <ErrorBox>{actionError || error || item.unavailableReason}</ErrorBox>}
        {activeClientCount > 1 && <ConcurrentResourceNotice count={activeClientCount} />}
        {completed && <section style={{ ...panelStyle, borderColor: C.greenBorder, background: C.greenDim, marginBottom: 10 }}><strong style={{ color: C.green }}>{t('Federated Learning completed')}</strong><div style={{ color: C.muted, fontSize: 12, marginTop: 4 }}>{t('The final aggregated Global Model was received. Finish the Client session when you are done reviewing this run.')}</div></section>}
        <ParticipationHistoryControls
            sessions={historySessions}
            loading={historyLoading}
            error={historyError}
            onRefresh={() => setHistoryRefresh(value => value + 1)}
            selectedSessionId={selectedSessionId}
            onSessionChange={value => { setSelectedSessionId(value); setSelectedRound('all'); setSelectedEpoch('all') }}
            rounds={availableRounds}
            selectedRound={selectedRound}
            onRoundChange={value => { setSelectedRound(value); setSelectedEpoch('all') }}
            epochs={availableEpochs}
            selectedEpoch={selectedEpoch}
            onEpochChange={setSelectedEpoch}
          />
        <FederationFlow item={viewedItem} current={viewedStage} failed={viewedItem.runtime.status === 'failed'} />

        <section style={{ ...panelStyle, marginTop: 10 }}>
          <div className="studio-responsive-header" style={{ alignItems: 'center' }}>
            <div style={{ flex: 1, minWidth: 180 }}>
              <strong style={{ color: C.text, fontSize: 12 }}>{t('Task Data')}</strong>
              <div style={{ marginTop: 3, color: dataBinding?.hasEntries ? C.green : C.muted, fontSize: 11 }}>{dataBinding?.hasEntries ? `${dataBinding.fileCount} ${t('files')} · ${bytes(dataBinding.totalBytes)}` : t('Add local training data before starting.')}</div>
            </div>
            <Badge variant={participationReady ? 'green' : 'yellow'}>{participationReady ? `✓ ${t('Local Ready')}` : `○ ${t('Check required')}`}</Badge>
            <Badge variant={serverAvailable ? 'green' : 'gray'}>{serverAvailable ? `● ${t('Server Live')}` : `○ ${t('Server Offline')}`}</Badge>
            <Button variant="ghost" disabled={Boolean(busy)} onClick={() => void openDataFolder()}>{t('Open Data Folder')}</Button>
            <Button variant="ghost" disabled={Boolean(busy) || readinessActive || running} onClick={() => void startReadiness()}>✓ {t(readinessActive ? 'Checking…' : 'Check Participation Readiness')}</Button>
            {readinessActive && <Button variant="danger" onClick={() => void cancelReadiness()}>■ {t('Stop Check')}</Button>}
          </div>
          <details style={{ marginTop: 8, color: C.dim, fontSize: 10 }}>
            <summary style={{ cursor: 'pointer' }}>{t('Data path and privacy')}</summary>
            <div style={{ ...monoSubStyle, marginTop: 7 }}>{(dataBinding?.hostPath ?? dataPath) || '—'}</div>
            <div style={{ ...monoSubStyle, marginTop: 5 }}>{t('The dataset remains in this account’s local .local-data directory and is not uploaded to FedOps Web.')}</div>
          </details>
          {running && <div style={{ ...monoSubStyle, marginTop: 7, color: C.yellow }}>{t('Stop Client ends this device’s participation immediately. The server keeps the saved Clients-per-round policy and waits for another eligible Client.')}</div>}
          {!running && !completed && !serverAvailable && participationReady && <div style={{ ...monoSubStyle, marginTop: 7 }}>{t('Local checks passed. Start the Federated Server in FedOps Web.')}</div>}
          {readinessActive && <PreflightProgress progress={Number(readinessRun?.progress?.percent ?? 5)} phase={String(readinessRun?.progress?.message ?? 'Checking participation requirements')} />}
          {preflight && <PreflightResult result={preflight} />}
        </section>

        <div style={workbenchGridStyle}>
          <section style={panelStyle}>
            <PanelTitle>{t('Local Training')}</PanelTitle>
            <LiveClientProgress
              item={viewedItem}
              stage={viewedStage}
              metricEvents={metricEvents}
              scope={selectedRound === 'all' ? 'rounds' : selectedEpoch === 'all' ? 'epochs' : 'epoch'}
            />
            <MetricGrid values={[
              [t('Current round'), `${round(viewedItem)} / ${activeCampaign(viewedItem)?.rounds ?? '—'}`],
              [t('Samples'), latestSampleCount(viewedItem.events ?? [])],
              [t('Training Loss'), metric(latestMetrics, ['train_loss', 'training_loss', 'loss'])],
              [t('Validation Loss'), metric(latestMetrics, ['val_loss', 'validation_loss', 'test_loss'])],
              [t('Local Accuracy'), metric(latestMetrics, ['val_accuracy', 'test_accuracy', 'accuracy'], true)],
            ]} />
          </section>
          <section style={panelStyle}>
            <PanelTitle>{t('Global Model')}</PanelTitle>
            <MetricGrid values={[
              [t('Model received for this round'), roundStartGlobalVersion(viewedItem)],
              [t(isInitiativeModel(viewedItem) ? 'Initiative Model' : 'Latest Global Model'), globalVersion(viewedItem)],
              [t('Model format'), viewedItem.globalModel?.format ?? '—'],
              [t('Server state'), t(serverLabel(viewedItem.server))],
              [t('Aggregation strategy'), campaignStrategy(viewedItem)],
            ]} />
            <RoundModelHistory item={viewedItem} />
          </section>
        </div>

        <section style={{ ...panelStyle, marginTop: 10, padding: 0, overflow: 'hidden' }}>
          <div style={{ display: 'flex', borderBottom: `1px solid ${C.border}`, background: C.surface2 }}>
            {(['Logs', 'Metrics', 'Communication', 'Models & Artifacts'] as ConsoleTab[]).map(value => <TabButton key={value} active={tab === value} onClick={() => setTab(value)}>{t(value)}</TabButton>)}
          </div>
          <div style={{ minHeight: 190, maxHeight: 320, overflow: 'auto', padding: 12 }}>
            {tab === 'Logs' && <LogView value={selectedSessionId ? t('Historical structured events are shown in the Metrics and Communication tabs.') : item.logs ?? ''} />}
            {tab === 'Metrics' && <EventTable events={(viewedItem.events ?? []).filter(event => event.metrics)} />}
            {tab === 'Communication' && <EventTable events={(viewedItem.events ?? []).filter(event => ['connecting', 'downloading_global', 'uploading', 'waiting_aggregation', 'global_model_updated'].includes(event.stage))} />}
            {tab === 'Models & Artifacts' && <MetricGrid values={[
              [t('Release ID'), item.release?.releaseId ?? '—'],
              [t('Release revision'), String(item.release?.revision ?? '—')],
              [t('Model Version ID'), item.globalModel?.modelVersionId ?? '—'],
              [t('Global Model Version'), globalVersion(viewedItem)],
              [t('Format'), viewedItem.globalModel?.format ?? '—'],
              [t('Size'), bytes(viewedItem.globalModel?.size)],
            ]} />}
          </div>
        </section>
      </main>
    </div>
  )
}

function ParticipationHistoryControls({ sessions, loading, error, onRefresh, selectedSessionId, onSessionChange, rounds, selectedRound, onRoundChange, epochs, selectedEpoch, onEpochChange }: {
  sessions: FederatedParticipationSession[]
  loading: boolean
  error: string | null
  onRefresh: () => void
  selectedSessionId: string
  onSessionChange: (value: string) => void
  rounds: number[]
  selectedRound: string
  onRoundChange: (value: string) => void
  epochs: number[]
  selectedEpoch: string
  onEpochChange: (value: string) => void
}) {
  const { t, formatDateTime } = useI18n()
  return <section style={{ ...panelStyle, marginBottom: 10, padding: 10 }}>
    <div className="studio-responsive-header" style={{ alignItems: 'flex-end' }}>
      <div style={{ minWidth: 150 }}><div style={fieldLabelStyle}>{t('PARTICIPATION HISTORY')}</div></div>
      <label style={{ minWidth: 260 }}>
        <span style={fieldLabelStyle}>{t('Campaign / Global Model')}</span>
        <select value={selectedSessionId} onChange={event => onSessionChange(event.target.value)} style={inputStyle}>
          <option value="">{t('Live / current Client session')}</option>
          {sessions.map(session => <option key={session.sessionId} value={session.sessionId}>
            {session.targetGlobalModelVersion ? `Global Model v${session.targetGlobalModelVersion}` : t('Recorded Campaign')} · {t(session.status)} · {session.startedAt ? formatDateTime(session.startedAt) : session.sessionId}
          </option>)}
        </select>
      </label>
      <label style={{ minWidth: 150 }}>
        <span style={fieldLabelStyle}>{t('Round')}</span>
        <select value={selectedRound} onChange={event => onRoundChange(event.target.value)} style={inputStyle}>
          <option value="all">{t('All rounds')}</option>
          {rounds.map(value => <option key={value} value={value}>{t('Round')} {value}</option>)}
        </select>
      </label>
      {selectedRound !== 'all' && epochs.length > 0 && <label style={{ minWidth: 150 }}>
        <span style={fieldLabelStyle}>{t('Epoch')}</span>
        <select value={selectedEpoch} onChange={event => onEpochChange(event.target.value)} style={inputStyle}>
          <option value="all">{t('All epochs')}</option>
          {epochs.map(value => <option key={value} value={value}>{t('Epoch')} {value}</option>)}
        </select>
      </label>}
      <Badge variant={selectedSessionId ? 'purple' : 'green'}>{t(selectedSessionId ? 'Saved history' : 'Live view')}</Badge>
      <Button variant="ghost" disabled={loading} onClick={onRefresh}>{t(loading ? 'Loading…' : 'Refresh')}</Button>
    </div>
    {error && <div style={{ marginTop: 7, color: C.red, fontSize: 11 }}>{t('Saved Participation History could not be loaded.')} {error}</div>}
    {!loading && !error && sessions.length === 0 && <div style={{ marginTop: 7, color: C.dim, fontSize: 11 }}>{t('No recorded Federated Learning campaigns yet. Live Client activity remains available.')}</div>}
  </section>
}

function FederationFlow({ item, current, failed }: { item: FederatedParticipation; current: string; failed: boolean }) {
  const { t } = useI18n()
  const index = PHASES.findIndex(([, , stages]) => stages.includes(current as never))
  const hasActivity = isRunning(item) || isCompleted(item) || Boolean((item.events ?? []).length)
  const activeIndex = index >= 0 ? index : isCompleted(item) ? PHASES.length - 1 : -1
  const currentPhaseLabel = activeIndex >= 0
    ? PHASES[activeIndex][1]
    : current === 'stopping'
      ? 'Stopping Client'
    : current === 'starting'
      ? 'Starting Client'
    : current === 'not_configured'
      ? 'Federation server unavailable'
    : failed
      ? 'Client needs attention'
    : current === 'stopped'
      ? 'Client stopped'
      : 'Ready to start'
  const localUpdate = activeIndex < 1
    ? 'Pending'
    : activeIndex === 1
      ? 'Training locally'
      : activeIndex === 2
        ? 'Sending to server'
        : 'Sent to server'
  const aggregatedModel = isCompleted(item) || current === 'global_model_updated'
    ? globalVersion(item)
    : 'Waiting for aggregation'
  return <section style={{ ...panelStyle, padding: 12 }}>
    <div style={{ display: 'grid', gridTemplateColumns: hasActivity ? 'minmax(min(100%, 310px), .8fr) minmax(min(100%, 520px), 1.2fr)' : '1fr', gap: 9 }} className="federated-run-focus-grid">
      <div style={{ padding: 12, border: `1px solid ${failed ? C.redBorder : C.accentBorder}`, borderRadius: C.radius, background: failed ? C.redDim : C.accentDim }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
          <span style={{ ...fieldLabelStyle, color: failed ? C.red : C.accent }}>{t(failed ? 'CLIENT NEEDS ATTENTION' : isCompleted(item) ? 'ROUND COMPLETE' : 'CURRENT ACTIVITY')}</span>
          <strong style={{ color: failed ? C.red : isCompleted(item) ? C.green : C.accent, fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>{progress(item)}</strong>
        </div>
        <div style={{ marginTop: 8, color: C.text, fontSize: 17, fontWeight: 700 }}>{t(currentPhaseLabel)}</div>
        <div style={{ marginTop: 5, minHeight: 32, color: C.muted, fontSize: 12, lineHeight: 1.45 }}>{t(current === 'stopping' ? 'Stopping local training and communication processes safely…' : current === 'starting' ? 'Starting the local communication manager and FedOps Client…' : current === 'stopped' ? 'The local Client was stopped by the user. The server can continue with other eligible Clients.' : item.latestEvent?.message ?? 'Check Participation Readiness, then start this local Client.')}</div>
        <div style={{ height: 7, marginTop: 10, overflow: 'hidden', borderRadius: C.pillRadius, background: C.bg }}>
          <div style={{ width: progress(item), height: '100%', borderRadius: C.pillRadius, background: failed ? C.red : isCompleted(item) ? C.green : C.accent, transition: 'width 220ms ease' }} />
        </div>
      </div>

      {hasActivity && <div style={{ padding: 12, border: `1px solid ${C.borderSubtle}`, borderRadius: C.radius, background: C.surface2 }}>
        <div style={fieldLabelStyle}>{t('THIS ROUND’S MODEL FLOW')}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 7, marginTop: 9 }}>
          <ModelFlowFact step="1" label={t('Received Global Model')} value={roundStartGlobalVersion(item)} tone={activeIndex >= 0 ? C.green : C.dim} />
          <ModelFlowFact step="2" label={t('Local Model Update')} value={t(localUpdate)} tone={activeIndex >= 1 ? C.accent : C.dim} />
          <ModelFlowFact step="3" label={t('Aggregated Global Model')} value={t(aggregatedModel)} tone={isCompleted(item) ? C.green : C.muted} />
        </div>
      </div>}
    </div>
  </section>
}

function ModelFlowFact({ step, label, value, tone }: { step: string; label: string; value: string; tone: string }) {
  return <div style={{ minWidth: 0, padding: 9, border: `1px solid ${C.borderSubtle}`, borderRadius: C.radius, background: C.surface }}>
    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}><span style={{ width: 17, height: 17, display: 'grid', placeItems: 'center', borderRadius: '50%', background: `${tone}18`, color: tone, fontFamily: 'JetBrains Mono, monospace', fontSize: 9 }}>{step}</span><span style={fieldLabelStyle}>{label}</span></div>
    <div title={value} style={{ marginTop: 7, color: tone, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</div>
  </div>
}

function RoundModelHistory({ item }: { item: FederatedParticipation }) {
  const { t, formatDateTime } = useI18n()
  const rows = roundModelHistory(item.events ?? [])
  return <details style={{ marginTop: 10, paddingTop: 9, borderTop: `1px solid ${C.borderSubtle}` }}>
    <summary style={{ ...fieldLabelStyle, cursor: 'pointer' }}>{t('Round history')} · {rows.length}</summary>
    {rows.length === 0
      ? <div style={{ marginTop: 7, color: C.dim, fontSize: 11 }}>{t('A completed round will appear here with its received and aggregated model versions.')}</div>
      : <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 7 }}>{rows.slice().reverse().map(row => <div key={row.round} style={{ display: 'grid', gridTemplateColumns: '78px minmax(150px, 1fr) repeat(3, minmax(72px, .5fr)) 138px', gap: 8, alignItems: 'center', padding: '7px 8px', border: `1px solid ${C.borderSubtle}`, borderRadius: C.radius, background: row.status === 'current' ? C.accentDim : C.surface2, fontSize: 10, overflowX: 'auto' }}>
          <strong style={{ color: row.status === 'current' ? C.accent : C.text }}>{t('Round')} {row.round} · {t(row.status)}</strong>
          <span style={{ minWidth: 0, color: C.muted, fontFamily: 'JetBrains Mono, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.received} → {row.aggregated}</span>
          <span style={{ color: C.muted }}>{t('Train loss')} {metric(row.metrics, ['train_loss', 'training_loss', 'loss'])}</span>
          <span style={{ color: C.muted }}>{t('Validation loss')} {metric(row.metrics, ['val_loss', 'validation_loss', 'test_loss'])}</span>
          <span style={{ color: C.muted }}>{t('Accuracy')} {metric(row.metrics, ['val_accuracy', 'test_accuracy', 'accuracy'], true)}</span>
          <span style={{ color: C.dim, textAlign: 'right' }}>{row.timestamp ? formatDateTime(row.timestamp) : '—'}</span>
        </div>)}</div>}
  </details>
}

function PreflightResult({ result }: { result: FederatedPreflight }) {
  const { t } = useI18n()
  const passed = result.checks.filter(check => check.status === 'passed').length
  return <details open={!result.ok} style={{ marginTop: 10, borderTop: `1px solid ${C.border}`, paddingTop: 9 }}>
    <summary style={{ color: result.ok ? C.green : C.red, cursor: 'pointer', fontSize: 11, fontWeight: 600 }}>{t('Readiness details')} · {passed} / {result.checks.length}</summary>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 5, marginTop: 7 }}>
      {result.checks.map(check => <div key={check.id} title={check.detail} style={{ padding: 7, border: `1px solid ${check.status === 'passed' ? C.greenBorder : C.redBorder}`, borderRadius: C.radius, background: check.status === 'passed' ? C.greenDim : C.redDim, color: check.status === 'passed' ? C.green : C.red, fontSize: 11 }}>
        {check.status === 'passed' ? '✓' : '×'} {t(check.label)}
      </div>)}
    </div>
  </details>
}

function ConcurrentResourceNotice({ count }: { count: number }) {
  const { t } = useI18n()
  return <div style={{ padding: 9, marginBottom: 9, border: `1px solid ${C.yellowBorder}`, borderRadius: C.radius, background: C.yellowDim, color: C.muted, fontSize: 11, lineHeight: 1.5 }}>
    <strong style={{ color: C.yellow }}>{count} {t('Federated Learning Clients are running independently.')}</strong>{' '}
    {t('They continue in the background when you change screens and share this device’s CPU, GPU, and memory.')}
  </div>
}

type MetricScope = 'rounds' | 'epochs' | 'epoch'

function LiveClientProgress({ item, stage, metricEvents, scope }: { item: FederatedParticipation; stage: string; metricEvents: FederatedRuntimeEvent[]; scope: MetricScope }) {
  const { t } = useI18n()
  const event = item.latestEvent
  const percent = displayProgress(item)
  const numericMetricEvents = metricEvents.filter(candidate => candidate.metrics && Object.values(candidate.metrics).some(value => typeof value === 'number'))
  const metricGroups = groupMetricNames(runtimeMetricNames(numericMetricEvents))
  return <div style={{ marginBottom: 10 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, color: C.text, fontSize: 11 }}>
          <span>{t(event?.message ?? stage)}</span>
          <strong style={{ fontFamily: 'JetBrains Mono, monospace' }}>{Math.round(percent)}%</strong>
        </div>
        <div style={{ height: 7, marginTop: 7, overflow: 'hidden', borderRadius: C.pillRadius, border: `1px solid ${C.borderSubtle}`, background: C.bg }}>
          <div style={{ width: `${percent}%`, height: '100%', borderRadius: C.pillRadius, background: stage === 'global_model_updated' ? C.green : C.accent, transition: 'width 220ms ease' }} />
        </div>
      </div>
      {event?.epoch && <ProgressFact label={t('Epoch')} value={`${event.epoch}${event.epochs ? ` / ${event.epochs}` : ''}`} />}
      {event?.batch && <ProgressFact label={t('Batch')} value={`${event.batch}${event.totalBatches ? ` / ${event.totalBatches}` : ''}`} />}
      {!event?.batch && typeof event?.step === 'number' && <ProgressFact label={t('Step')} value={`${event.step}${event.totalSteps ? ` / ${event.totalSteps}` : ''}`} />}
    </div>
    {metricGroups.length > 0 && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(310px, 1fr))', gap: 6, marginTop: 8 }}>
      {metricGroups.map(group => <FederatedMetricChart
        key={group.kind}
        title={t(group.kind === 'objective' ? 'Loss & error' : group.kind === 'ratio' ? 'Scores' : 'Metrics')}
        names={group.names}
        events={numericMetricEvents}
        scope={scope}
        percent={group.kind === 'ratio'}
      />)}
    </div>}
  </div>
}

function ProgressFact({ label, value }: { label: string; value: string }) {
  return <div style={{ minWidth: 58, padding: '5px 7px', border: `1px solid ${C.borderSubtle}`, borderRadius: C.radius, background: C.surface2 }}>
    <div style={fieldLabelStyle}>{label}</div>
    <div style={{ marginTop: 3, color: C.text, fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>{value}</div>
  </div>
}

function FederatedMetricChart({ title, names, events, scope, percent = false }: { title: string; names: string[]; events: FederatedRuntimeEvent[]; scope: MetricScope; percent?: boolean }) {
  const { t } = useI18n()
  const colors = [C.accent, C.green, C.purple, C.orange]
  const series: MetricChartSeries[] = names.map((name, seriesIndex) => ({
    id: name,
    label: displayMetricName(name),
    color: colors[seriesIndex % colors.length],
    points: federatedMetricPoints(events, name, scope),
  }))
  return <MetricChartPanel
    title={title}
    description={t('Metrics reported by this local Federated Learning Client.')}
    xLabel={t(scope === 'rounds' ? 'Federated round' : scope === 'epochs' ? 'Local metric step' : 'Batch / step')}
    series={series}
    percent={percent}
  />
}

function federatedMetricPoints(events: FederatedRuntimeEvent[], name: string, scope: MetricScope): Array<{ x: number; y: number }> {
  if (scope === 'rounds') {
    const latestByRound = new Map<number, number>()
    events.forEach(event => {
      const value = event.metrics?.[name]
      if (typeof event.round === 'number' && typeof value === 'number' && Number.isFinite(value)) latestByRound.set(event.round, value)
    })
    return [...latestByRound.entries()].sort(([left], [right]) => left - right).map(([x, y]) => ({ x, y }))
  }
  const matching = events.filter(event => {
    const value = event.metrics?.[name]
    return typeof value === 'number' && Number.isFinite(value)
  })
  let previousX = Number.NEGATIVE_INFINITY
  return matching.map((event, eventIndex) => {
    const value = event.metrics?.[name] as number
    if (scope === 'epoch') {
      const candidate = typeof event.batch === 'number'
        ? event.batch
        : typeof event.step === 'number' ? event.step : eventIndex + 1
      const x = candidate > previousX ? candidate : previousX + 1
      previousX = x
      return { x, y: value }
    }
    const epoch = typeof event.epoch === 'number' ? Math.max(1, event.epoch) : 1
    const totalBatches = typeof event.totalBatches === 'number' ? Math.max(0, event.totalBatches) : 0
    const totalSteps = typeof event.totalSteps === 'number' ? Math.max(0, event.totalSteps) : 0
    const localStep = typeof event.batch === 'number'
      ? event.batch
      : typeof event.step === 'number' ? event.step : eventIndex + 1
    const stepsPerEpoch = totalBatches || totalSteps
    const candidate = typeof event.epoch === 'number' && stepsPerEpoch > 0
      ? ((epoch - 1) * stepsPerEpoch) + localStep
      : localStep
    const x = candidate > previousX ? candidate : previousX + 1
    previousX = x
    return { x, y: value }
  })
}

function PreflightProgress({ progress, phase }: { progress: number; phase: string }) {
  const { t } = useI18n()
  return <div style={{ marginTop: 10, borderTop: `1px solid ${C.border}`, paddingTop: 9 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, color: C.accent, fontSize: 11 }}>
      <span>{t(phase)}</span>
      <strong style={{ fontFamily: 'JetBrains Mono, monospace' }}>{progress}%</strong>
    </div>
    <div style={{ height: 5, marginTop: 7, overflow: 'hidden', borderRadius: C.pillRadius, background: C.surface2 }}>
      <div style={{ width: `${progress}%`, height: '100%', borderRadius: C.pillRadius, background: C.accent, transition: 'width 220ms ease' }} />
    </div>
    <div style={{ ...monoSubStyle, marginTop: 6 }}>{t('Preflight checks readiness only. It does not start Federated Learning.')}</div>
  </div>
}

function EventTable({ events }: { events: FederatedRuntimeEvent[] }) {
  const { t, formatDateTime } = useI18n()
  if (!events.length) return <Empty>{t('No local Client events have been recorded.')}</Empty>
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>{events.slice().reverse().map((event, index) => <div key={`${event.timestamp}-${index}`} style={{ display: 'grid', gridTemplateColumns: '150px 150px 1fr', gap: 8, borderBottom: `1px solid ${C.borderSubtle}`, padding: '5px 2px', fontSize: 11 }}>
    <span style={{ color: C.dim, fontFamily: 'JetBrains Mono, monospace' }}>{formatDateTime(event.timestamp)}</span>
    <span style={{ color: stateColor(event.stage), fontFamily: 'JetBrains Mono, monospace' }}>{t(event.stage)}</span>
    <span style={{ color: C.muted, overflowWrap: 'anywhere' }}>{event.message ?? JSON.stringify(event.metrics ?? {})}</span>
  </div>)}</div>
}

function LogView({ value }: { value: string }) {
  const { t } = useI18n()
  return value ? <pre style={{ margin: 0, color: C.muted, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, lineHeight: 1.55, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{value}</pre> : <Empty>{t('The local Client has not produced logs yet.')}</Empty>
}

function MetricGrid({ values }: { values: [string, string][] }) { return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6 }}>{values.map(([label, value]) => <div key={label} style={{ padding: 8, background: C.surface2, border: `1px solid ${C.borderSubtle}`, borderRadius: C.radius, minWidth: 0 }}><div style={fieldLabelStyle}>{label}</div><div title={value} style={{ color: C.text, fontFamily: 'JetBrains Mono, monospace', fontSize: 12, marginTop: 5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</div></div>)}</div> }
function KeyValue({ label, value }: { label: string; value: string }) { return <div style={{ marginBottom: 9 }}><div style={fieldLabelStyle}>{label}</div><div title={value} style={{ color: C.text, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, marginTop: 3, overflowWrap: 'anywhere' }}>{value}</div></div> }
function PanelTitle({ children }: { children: ReactNode }) { return <h2 style={{ margin: '0 0 10px', color: C.text, fontSize: 13, fontFamily: 'Inter, sans-serif' }}>{children}</h2> }
function HeaderFact({ label, value, tone = C.text }: { label: string; value: string; tone?: string }) { return <div style={{ paddingRight: 14, borderRight: `1px solid ${C.border}` }}><div style={fieldLabelStyle}>{label}</div><div style={{ color: tone, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, marginTop: 3 }}>{value}</div></div> }
function CardFact({ label, value, tone = C.text }: { label: string; value: string; tone?: string }) { return <div style={{ width: 112, textAlign: 'left' }}><div style={fieldLabelStyle}>{label}</div><div title={value} style={{ color: tone, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</div></div> }
function ServerLiveBadge({ server }: { server?: FederatedParticipation['server'] }) { const { t } = useI18n(); const live = Boolean(server?.ready && server.aggregationServer); const starting = ['creating', 'starting', 'pending'].some(value => String(server?.state ?? '').toLowerCase().includes(value)); return <Badge variant={live ? 'green' : starting ? 'yellow' : 'gray'}>{live ? `● ${t('Server Live')}` : starting ? `◐ ${t('Server Starting')}` : `○ ${t('Server Offline')}`}</Badge> }
function StateBadge({ state }: { state: string }) { const { t } = useI18n(); const variant = state === 'failed' || state === 'disconnected' ? 'red' : state === 'training' || state === 'global_model_updated' ? 'green' : state === 'ready' ? 'blue' : WAITING_STATES.has(state) ? 'yellow' : 'gray'; return <Badge variant={variant}>{t(state)}</Badge> }
function FilterButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) { return <button onClick={onClick} style={{ padding: '4px 9px', borderRadius: C.pillRadius, border: `1px solid ${active ? C.accentBorder : C.border}`, background: active ? C.accentDim : C.controlBg, color: active ? C.accent : C.muted, cursor: 'pointer', fontSize: 12 }}>{children}</button> }
function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) { return <button onClick={onClick} style={{ padding: '8px 12px', border: 'none', borderBottom: `2px solid ${active ? C.accent : 'transparent'}`, background: 'transparent', color: active ? C.text : C.muted, cursor: 'pointer', fontSize: 12 }}>{children}</button> }
function ErrorBox({ children }: { children: ReactNode }) { return <div style={errorStyle}>{children}</div> }
function Empty({ children }: { children: ReactNode }) { return <div style={{ color: C.dim, fontSize: 12, padding: 20, textAlign: 'center' }}>{children}</div> }
function Centered({ title, children }: { title: string; children: ReactNode }) { return <div style={{ flex: 1, minHeight: 300, display: 'grid', placeItems: 'center', background: C.bg }}><div style={{ ...panelStyle, maxWidth: 560, textAlign: 'center' }}><h2 style={{ margin: '0 0 7px', color: C.text, fontSize: 16 }}>{title}</h2><div style={{ color: C.muted, fontSize: 13, lineHeight: 1.65 }}>{children}</div></div></div> }

function normalizedStage(runtime: string, event?: string) {
  if (runtime === 'stopped') return 'stopped'
  if (runtime === 'completed') return 'completed'
  const value = event || runtime || 'not_started'
  if (['not_started', 'ready', 'not_configured', 'starting', 'connecting', 'stopped', 'disconnected', 'failed'].includes(value)) return value
  return value
}
function isRunning(item: FederatedParticipation) { return ['starting', 'running', 'stopping'].includes(item.runtime.status) || ACTIVE_STATES.has(item.runtime.clientState) }
function isCompleted(item: FederatedParticipation) { return item.runtime.status === 'completed' || item.runtime.clientState === 'completed' }
function canStopClient(item: FederatedParticipation) { return item.runtime.status !== 'stopping' && (isRunning(item) || isCompleted(item) || ['failed', 'disconnected'].includes(item.runtime.status) || ACTIVE_STATES.has(item.runtime.clientState) || WAITING_STATES.has(item.runtime.clientState)) }
function version(value: string | number | undefined) { return value === undefined ? '—' : `v${value}` }
function activeCampaign(item: FederatedParticipation) { return item.campaignRun?.campaign ?? item.campaign }
function campaignStrategy(item: FederatedParticipation) { const strategy = activeCampaign(item)?.strategy; return typeof strategy === 'string' ? strategy : strategy?.name ?? '—' }
function globalVersion(item: FederatedParticipation) {
  const finalEvent = (item.events ?? []).slice().reverse().find(event => event.aggregationScope === 'campaign-final' || event.stage === 'completed')
  if (finalEvent) return finalEvent.modelLabel ?? version(finalEvent.targetGlobalModelVersion ?? finalEvent.globalModelVersion)
  const base = item.campaignRun?.baseGlobalModelVersion
  return base === 0 ? 'Initiative Model' : base !== undefined ? `Global Model v${base}` : version(item.globalModel?.version)
}
function isInitiativeModel(item: FederatedParticipation) {
  const aggregated = (item.events ?? []).some(event => event.stage === 'completed' || event.aggregationScope === 'campaign-final')
  if (aggregated) return false
  if (item.campaignRun) return item.campaignRun.baseGlobalModelVersion === 0
  const role = String(item.globalModel?.role ?? '').toLowerCase()
  return role === 'registry-bootstrap-model' || role === 'initiative-model' || role === ''
}
function roundStartGlobalVersion(item: FederatedParticipation) {
  const events = item.events ?? []
  const latestRound = Math.max(0, ...events.flatMap(event => typeof event.round === 'number' ? [event.round] : []))
  const event = events.slice().reverse().find(candidate => candidate.stage === 'downloading_global' && (!latestRound || candidate.round === latestRound))
  if (event?.modelLabel) return event.modelLabel
  if (event?.modelRole === 'initiative') return 'Initiative Model'
  const base = item.campaignRun?.baseGlobalModelVersion
  if (base === 0) return 'Initiative Model'
  return version(event?.sourceGlobalModelVersion ?? event?.globalModelVersion ?? base ?? item.globalModel?.version)
}
function serverLabel(server?: FederatedParticipation['server']) { return server?.ready && server.aggregationServer ? 'Server Live' : String(server?.state ?? 'Server Offline') }
function normalizeCampaignRounds(events: FederatedRuntimeEvent[], campaign: boolean) {
  if (!campaign) return events
  return events.map(event => ['connecting', 'waiting_round'].includes(event.stage)
    ? { ...event, round: 1 }
    : event)
}
function round(item: FederatedParticipation) { const values = (item.events ?? []).flatMap(event => typeof event.round === 'number' ? [event.round] : []); const value = values.length ? Math.max(...values) : item.latestEvent?.round; return value === undefined ? '—' : String(value) }
function displayProgress(item: FederatedParticipation) { const value = item.latestEvent?.stageProgress ?? item.latestEvent?.progress; return Math.max(0, Math.min(100, typeof value === 'number' ? value : item.runtime.status === 'completed' ? 100 : 0)) }
function progress(item: FederatedParticipation) { return `${Math.round(displayProgress(item))}%` }
function shortHash(value?: string) { return value ? `${value.slice(0, 12)}…${value.slice(-8)}` : '—' }
function bytes(value?: number) { if (value === undefined) return '—'; const units = ['B', 'KiB', 'MiB', 'GiB']; let amount = value; let unit = 0; while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 } return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}` }
function latestOwnMetrics(events: FederatedRuntimeEvent[]) { const values: Record<string, unknown> = {}; for (const event of events) if (event.metrics) Object.assign(values, event.metrics); return values }
function latestSampleCount(events: FederatedRuntimeEvent[]) { for (const event of events.slice().reverse()) if (typeof event.sampleCount === 'number') return String(event.sampleCount); return '—' }
function roundModelHistory(events: FederatedRuntimeEvent[]) {
  const rows = new Map<number, { round: number; received: string; aggregated: string; timestamp?: string; metrics: Record<string, unknown>; status: 'current' | 'completed' }>()
  const maxRound = Math.max(0, ...events.flatMap(event => typeof event.round === 'number' ? [event.round] : []))
  for (const event of events) {
    if (typeof event.round !== 'number') continue
    const row = rows.get(event.round) ?? { round: event.round, received: '—', aggregated: '—', metrics: {}, status: 'current' }
    if (event.metrics) Object.assign(row.metrics, event.metrics)
    if (event.stage === 'downloading_global') row.received = event.modelLabel ?? (event.modelRole === 'initiative' ? 'Initiative Model' : version(event.sourceGlobalModelVersion ?? event.globalModelVersion))
    if (event.stage === 'global_model_updated') {
      row.aggregated = event.modelLabel ?? version(event.targetGlobalModelVersion ?? event.globalModelVersion)
      row.timestamp = event.timestamp
      row.status = 'completed'
    }
    if (!row.timestamp) row.timestamp = event.timestamp
    rows.set(event.round, row)
  }
  return [...rows.values()].map(row => ({ ...row, status: row.aggregated !== '—' || row.round < maxRound ? 'completed' as const : 'current' as const })).sort((a, b) => a.round - b.round)
}
function runtimeMetricNames(events: FederatedRuntimeEvent[]) {
  return [...new Set(events.flatMap(event => Object.entries(event.metrics ?? {}).flatMap(([name, value]) =>
    typeof value === 'number' && Number.isFinite(value) ? [name] : [],
  )))]
}
function displayMetricName(name: string) { return name.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ') }
function metric(metrics: Record<string, unknown>, keys: string[], percent = false) { for (const key of keys) { const raw = metrics[key]; if (typeof raw === 'number') return percent ? `${(raw <= 1 ? raw * 100 : raw).toFixed(2)}%` : raw.toFixed(4) } return '—' }
function stateColor(state: string) { if (['failed', 'disconnected'].includes(state)) return C.red; if (['training', 'global_model_updated', 'completed'].includes(state)) return C.green; if (WAITING_STATES.has(state)) return C.yellow; return C.accent }
function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause) }

const pageStyle: CSSProperties = { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: C.bg }
const homeHeaderStyle: CSSProperties = { padding: '14px 20px 10px', background: C.surface, borderBottom: `1px solid ${C.border}`, flexShrink: 0 }
const consoleHeaderStyle: CSSProperties = { padding: '9px 14px', background: C.surface, borderBottom: `1px solid ${C.border}`, flexShrink: 0 }
const titleStyle: CSSProperties = { margin: 0, color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 18, fontWeight: 700 }
const subtitleStyle: CSSProperties = { margin: '4px 0 0', color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 13 }
const participationCardStyle: CSSProperties = { width: '100%', display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', padding: '11px 13px', background: C.surface, border: `1px solid ${C.border}`, borderLeft: `3px solid ${C.accent}`, borderRadius: C.radius, cursor: 'pointer' }
const monoSubStyle: CSSProperties = { color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, marginTop: 5, overflowWrap: 'anywhere' }
const headerFactsStyle: CSSProperties = { display: 'flex', gap: 14, alignItems: 'stretch', marginTop: 9, paddingLeft: 34, overflowX: 'auto' }
const panelStyle: CSSProperties = { minWidth: 0, background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 12 }
const workbenchGridStyle: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', gap: 10, marginTop: 10 }
const fieldLabelStyle: CSSProperties = { display: 'block', color: C.dim, fontFamily: 'Inter, sans-serif', fontSize: 10 }
const inputStyle: CSSProperties = { width: '100%', boxSizing: 'border-box', marginTop: 5, minHeight: 31, background: C.inputBg, color: C.text, border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, padding: '6px 9px', fontFamily: 'JetBrains Mono, monospace', fontSize: 11, outline: 'none' }
const backButtonStyle: CSSProperties = { width: 26, height: 26, border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.controlBg, color: C.text, cursor: 'pointer', fontSize: 20, lineHeight: 1 }
const errorStyle: CSSProperties = { padding: 9, marginBottom: 9, border: `1px solid ${C.redBorder}`, borderRadius: C.radius, background: C.redDim, color: C.red, fontSize: 12, overflowWrap: 'anywhere' }
