import { getJson, postJson } from './http'
import type { SourceList } from './http'

export interface FederatedRuntimeEvent {
  schemaVersion: 1
  timestamp: string
  taskId?: string | null
  releaseId?: string | null
  clientInstanceId?: string | null
  stage: string
  round?: number
  progress?: number
  stageProgress?: number
  message?: string
  metrics?: Record<string, unknown>
  epoch?: number
  epochs?: number
  batch?: number
  totalBatches?: number
  step?: number
  totalSteps?: number
  source?: string
  training?: { batchSize?: number; localEpochs?: number }
  globalModelVersion?: string | number
  targetGlobalModelVersion?: string | number
  sourceGlobalModelVersion?: string | number | null
  modelRole?: 'initiative' | 'global' | 'round-aggregate'
  modelLabel?: string
  aggregationScope?: 'round' | 'campaign-final'
  sourceRound?: number
  sampleCount?: number
  [key: string]: unknown
}

export interface FederatedPreflightCheck {
  id: string
  label: string
  status: 'passed' | 'failed'
  detail: string
}

export interface FederatedPreflight {
  ok: boolean
  checkedAt: string
  taskId: string
  releaseId: string
  modelVersionId: string
  campaignRunId: string | null
  sourceFingerprint: string
  dataFingerprint: string
  environmentId: string | null
  dataPath: string
  serverAvailability?: FederatedServerAvailability
  checks: FederatedPreflightCheck[]
}

export interface FederatedServerAvailability {
  ready: boolean
  state: string
  endpoint?: string | null
  observedAt?: string | null
}

export interface FederatedParticipation {
  localProjectId: string
  task: {
    taskId: string
    runtimeKey: string
    title: string
    primaryModel?: { displayName?: string; workingName?: string } | null
    runtimeContract?: {
      name: 'legacy-v1' | 'federated-task-v2' | 'federated-task-v3'
      schemaVersion: number
      fedopsVersion?: string
      sourceRevision?: string
      baselineVersion?: string | null
    }
  }
  release?: {
    releaseId?: string
    revision?: number
    bundleSha256?: string
    sourceFingerprint?: string
  }
  globalModel?: {
    modelVersionId?: string
    version?: string | number
    role?: string
    format?: string
    size?: number
    sha256?: string
  }
  server?: {
    state?: string
    ready?: boolean
    managerUrl?: string
    aggregationServer?: string | null
    host?: string | null
    port?: number | null
    observedAt?: string
  }
  campaign?: {
    schemaVersion?: number
    rounds?: number
    clientsPerRound?: number
    strategy?: { name?: string; parameters?: Record<string, number> } | string
    updatedAt?: string | null
  } | null
  campaignRun?: {
    runId: string
    status: string
    releaseId: string
    baseGlobalModelVersion: number
    targetGlobalModelVersion: number
    campaign?: {
      schemaVersion?: number
      rounds?: number
      clientsPerRound?: number
      strategy?: { name?: string; parameters?: Record<string, number> }
    }
    startedAt?: string | null
    endedAt?: string | null
  } | null
  participation?: { role?: 'owner' | 'admin' | 'participant'; status?: string }
  workspace: {
    ready: boolean
    readyToStart: boolean
    participationReadyToStart?: boolean
    serverAvailability?: FederatedServerAvailability
    releaseUpdateRequired?: boolean
    binding?: Record<string, unknown> | null
    participationReadiness?: Record<string, unknown> | null
    participationPreflight?: FederatedPreflight | null
  }
  runtime: {
    schemaVersion?: number
    status: string
    clientState: string
    clientInstanceId?: string
    environmentId?: string
    managerPort?: number
    clientPort?: number
    startedAt?: string | null
    endedAt?: string | null
    runId?: string | null
    campaignRunId?: string | null
    sessionId?: string | null
    baseGlobalModelVersion?: number | null
    targetGlobalModelVersion?: number | null
  }
  latestEvent?: FederatedRuntimeEvent | null
  events?: FederatedRuntimeEvent[]
  logs?: string
  observedAt?: string
  unavailableReason?: string
}

export interface FederatedParticipationSession {
  sessionId: string
  campaignRunId?: string | null
  runId?: string | null
  taskId?: string | null
  releaseId?: string | null
  status: string
  baseGlobalModelVersion?: number | null
  targetGlobalModelVersion?: number | null
  campaign?: FederatedParticipation['campaign']
  rounds: number[]
  completedRounds: number
  startedAt?: string | null
  endedAt?: string | null
  eventCount: number
}

export interface FederatedParticipationHistory {
  session: FederatedParticipationSession
  events: FederatedRuntimeEvent[]
}

interface ParticipationAction {
  dataPath?: string
  environmentId?: string
}

const root = '/api/v1/federated-learning/participations'
const participationListTimeoutMs = 7_000

async function getWithTimeout<T>(
  path: string,
  signal: AbortSignal | undefined,
  timeoutMessage: string,
): Promise<T> {
  const controller = new AbortController()
  const relayAbort = () => controller.abort(signal?.reason)
  signal?.addEventListener('abort', relayAbort, { once: true })
  let timedOut = false
  const timer = window.setTimeout(
    () => {
      timedOut = true
      controller.abort('Federated Learning state request timed out.')
    },
    participationListTimeoutMs,
  )
  try {
    return await getJson<T>(path, controller.signal)
  } catch (error) {
    if (timedOut) throw new Error(timeoutMessage)
    throw error
  } finally {
    window.clearTimeout(timer)
    signal?.removeEventListener('abort', relayAbort)
  }
}

export function listFederatedParticipations(signal?: AbortSignal): Promise<SourceList<FederatedParticipation>> {
  return getWithTimeout(
    root,
    signal,
    'Federated Learning state could not be loaded in time. Refresh to try again.',
  )
}

export function getFederatedParticipation(localProjectId: string, signal?: AbortSignal): Promise<FederatedParticipation> {
  return getWithTimeout(
    `${root}/${encodeURIComponent(localProjectId)}`,
    signal,
    'This Federated Learning Client state could not be loaded in time. Refresh to try again.',
  )
}

export function listFederatedParticipationHistory(localProjectId: string, signal?: AbortSignal): Promise<SourceList<FederatedParticipationSession>> {
  return getJson(`${root}/${encodeURIComponent(localProjectId)}/history`, signal)
}

export function getFederatedParticipationHistory(localProjectId: string, sessionId: string, signal?: AbortSignal): Promise<FederatedParticipationHistory> {
  return getJson(`${root}/${encodeURIComponent(localProjectId)}/history/${encodeURIComponent(sessionId)}`, signal)
}

export function preflightFederatedParticipation(localProjectId: string, action: ParticipationAction): Promise<FederatedPreflight> {
  return postJson(`${root}/${encodeURIComponent(localProjectId)}/preflight`, action)
}

export function startFederatedParticipation(localProjectId: string, action: ParticipationAction): Promise<FederatedParticipation> {
  return postJson(`${root}/${encodeURIComponent(localProjectId)}/start`, action)
}

export function stopFederatedParticipation(localProjectId: string): Promise<FederatedParticipation> {
  return postJson(`${root}/${encodeURIComponent(localProjectId)}/stop`)
}
