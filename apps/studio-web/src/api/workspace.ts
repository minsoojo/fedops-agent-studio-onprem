import { deleteJson, getJson, postJson, putJson, queryString } from './http'
import type { SourceList } from './http'

export interface WorkspaceProject {
  localProjectId: string
  name: string
  path: string
  origin: 'local'
  hasPyproject: boolean
  hasFedOpsTask: boolean
  projectEnvironmentReady: boolean
  taskBinding: TaskBinding | null
}

export interface TaskBinding {
  schemaVersion: 1
  taskId: string
  runtimeKey: string
  displayName: string
  workspaceRole?: 'owner' | 'admin' | 'participant'
  taskCategory?: string | null
  dataModality?: string | null
  primaryModel?: {
    displayName?: string
    workingName?: string
    framework?: string
    architecture?: string
    task?: string
  } | null
  ownerHandle: string | null
  slug?: string | null
  registryStatus: string
  runtimeContract?: {
    name: 'legacy-v1' | 'federated-task-v2' | 'federated-task-v3'
    schemaVersion: number
    fedopsVersion?: string
    sourceRevision?: string
    baselineVersion?: string | null
  }
  linkedAt: string
  releaseId?: string
  modelVersionId?: string
  bundleSha256?: string
}

export interface LegacyWorkspaceProject {
  name: string
  path: string
  hasPyproject: boolean
  hasFedOpsTask: boolean
  conflict: boolean
}

export type WorkspaceAction = 'validate' | 'local-train' | 'release-readiness' | 'participation-readiness' | 'run-file'

export interface TrainingProgress {
  schemaVersion: 1
  stage: 'preparing' | 'loading-data' | 'training' | 'evaluating' | 'exporting' | 'completed'
  percent: number
  message: string
  timestamp: string
  epoch: number | null
  epochs: number | null
  batch: number | null
  totalBatches: number | null
  step: number | null
  totalSteps: number | null
  metrics: Record<string, number>
}

export interface TrainingMetricPoint {
  timestamp: string
  stage: TrainingProgress['stage']
  percent: number
  epoch: number | null
  batch: number | null
  step: number | null
  totalSteps: number | null
  metrics: Record<string, number>
}

export interface WorkspaceRun {
  runId: string
  kind: 'create' | 'environment-sync' | 'release-candidate' | WorkspaceAction
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  localProjectId: string | null
  environmentId: string | null
  taskId: string | null
  command: string
  createdAt: string
  startedAt: string | null
  endedAt: string | null
  exitCode: number | null
  output: string
  resultLocalProjectId: string | null
  result: Record<string, unknown> | null
  progress: TrainingProgress | null
  metricSeries: TrainingMetricPoint[]
}

export interface WorkspaceFileTreeNode {
  name: string
  path: string
  type: 'directory' | 'file'
  readOnly: boolean
  accessKind: 'editable' | 'generated' | 'managed'
  role?: string | null
  children?: WorkspaceFileTreeNode[]
}

export interface WorkspaceFileTree {
  project: WorkspaceProject
  tree: WorkspaceFileTreeNode
}

export interface WorkspaceFile {
  path: string
  name: string
  content: string
  language: string
  size: number
  modifiedAt: string
  readOnly: boolean
}

export interface LocalDataBinding {
  localProjectId: string
  containerPath: string
  hostPath: string
  hasEntries: boolean
  fileCount: number
  totalBytes: number
  fingerprint: string
  source: 'task-local-data'
}

export interface TaskDataSample {
  localProjectId: string
  index: number
  dataPath: string
  payload: Record<string, unknown>
  metadata: Record<string, unknown>
  source: 'task-data-adapter'
}

/** GET /api/v1/workspaces -> features/workspace/router.py:list_workspaces */
export async function listLocalWorkspaces(signal?: AbortSignal): Promise<SourceList<WorkspaceProject>> {
  return getJson<SourceList<WorkspaceProject>>('/api/v1/workspaces', signal)
}

export type CreateWorkspaceInput =
  | { sourceTaskId: string; name?: never }
  | { name: string; sourceTaskId?: never }

/** POST /api/v1/workspaces -> features/workspace/router.py:create_workspace */
export async function createWorkspace(input: CreateWorkspaceInput): Promise<WorkspaceRun> {
  return postJson('/api/v1/workspaces', input)
}

export async function linkWorkspaceTask(localProjectId: string, taskId: string): Promise<TaskBinding> {
  return putJson(`/api/v1/workspaces/${encodeURIComponent(localProjectId)}/binding`, { taskId })
}

export interface TaskRelease {
  releaseId: string
  revision: number
  status: string
  bundleSha256: string
  sourceFingerprint: string
  files: { path: string; sha256: string; size: number }[]
  model: { modelVersionId: string; version: number; sha256: string }
  readiness: { ok: boolean; mode: string; checkerVersion: string }
  catalog?: Record<string, unknown>
}

export interface ReleaseSubmissionReadiness {
  ready: boolean
  reason: string | null
}

export async function getReleaseSubmissionReadiness(localProjectId: string): Promise<ReleaseSubmissionReadiness> {
  return getJson(`/api/v1/workspaces/${encodeURIComponent(localProjectId)}/release-submission-readiness`)
}

export async function submitReleaseCandidate(localProjectId: string): Promise<WorkspaceRun> {
  return postJson(`/api/v1/workspaces/${encodeURIComponent(localProjectId)}/release-candidate`)
}

export async function listLegacyWorkspaces(): Promise<{ items: LegacyWorkspaceProject[]; source: string }> {
  return getJson('/api/v1/workspaces/legacy')
}

export async function importLegacyWorkspaces(projectNames: string[]): Promise<{ imported: number; projects: WorkspaceProject[] }> {
  return postJson('/api/v1/workspaces/legacy/import', { projectNames })
}

export async function deleteWorkspace(localProjectId: string, name: string): Promise<{ deleted: true; localProjectId: string; name: string }> {
  return deleteJson(`/api/v1/workspaces/${encodeURIComponent(localProjectId)}`, { name })
}

export async function openWorkspaceFolder(localProjectId: string): Promise<{ opened: true; path: string; mode: 'native' | 'host-bridge' }> {
  return postJson(`/api/v1/workspaces/${encodeURIComponent(localProjectId)}/open-folder`)
}

export async function prepareWorkspaceDataBinding(localProjectId: string): Promise<LocalDataBinding> {
  return putJson(`/api/v1/workspaces/${encodeURIComponent(localProjectId)}/data-binding`, {})
}

export async function openWorkspaceDataFolder(localProjectId: string): Promise<LocalDataBinding & { opened: true; mode: 'native' | 'host-bridge' }> {
  return postJson(`/api/v1/workspaces/${encodeURIComponent(localProjectId)}/open-data-folder`)
}

export async function buildWorkspaceTaskDataSample(localProjectId: string, index = 0, dataPath = ''): Promise<TaskDataSample> {
  return postJson(`/api/v1/workspaces/${encodeURIComponent(localProjectId)}/task-data/sample`, { index, dataPath })
}

/** POST /api/v1/workspaces/{id}/actions -> features/workspace/router.py:start_workspace_action */
export async function startWorkspaceAction(localProjectId: string, action: WorkspaceAction, environmentId?: string, filePath?: string, dataPath?: string): Promise<WorkspaceRun> {
  return postJson(`/api/v1/workspaces/${encodeURIComponent(localProjectId)}/actions`, { action, environmentId, filePath, dataPath })
}

/** GET /api/v1/workspaces/runs/{runId} -> features/workspace/router.py:get_workspace_run */
export async function getWorkspaceRun(runId: string, signal?: AbortSignal): Promise<WorkspaceRun> {
  return getJson(`/api/v1/workspaces/runs/${encodeURIComponent(runId)}`, signal)
}

export async function cancelWorkspaceRun(runId: string): Promise<WorkspaceRun> {
  return postJson(`/api/v1/workspaces/runs/${encodeURIComponent(runId)}/cancel`)
}

export async function listWorkspaceRuns(localProjectId?: string, signal?: AbortSignal): Promise<SourceList<WorkspaceRun>> {
  return getJson(`/api/v1/workspaces/runs${queryString({ local_project_id: localProjectId })}`, signal)
}

export async function getWorkspaceFileTree(localProjectId: string, signal?: AbortSignal): Promise<WorkspaceFileTree> {
  return getJson(`/api/v1/workspaces/files/tree${queryString({ local_project_id: localProjectId })}`, signal)
}

export async function readWorkspaceFile(localProjectId: string, filePath: string, signal?: AbortSignal): Promise<WorkspaceFile> {
  return getJson(`/api/v1/workspaces/files/content${queryString({ local_project_id: localProjectId, file_path: filePath })}`, signal)
}

export async function saveWorkspaceFile(localProjectId: string, filePath: string, content: string): Promise<WorkspaceFile> {
  return putJson('/api/v1/workspaces/files/content', { localProjectId, filePath, content })
}

export async function createWorkspaceFile(localProjectId: string, filePath: string, content = ''): Promise<WorkspaceFile> {
  return postJson('/api/v1/workspaces/files/content', { localProjectId, filePath, content })
}

export async function deleteWorkspaceFile(localProjectId: string, filePath: string): Promise<{ deleted: true; path: string }> {
  return deleteJson('/api/v1/workspaces/files/content', { localProjectId, filePath })
}

export async function formatWorkspaceFile(localProjectId: string, filePath: string): Promise<WorkspaceFile> {
  return postJson('/api/v1/workspaces/files/format', { localProjectId, filePath })
}

export async function prepareWorkspaceTerminal(): Promise<{ status: 'ready' }> {
  return postJson('/api/v1/workspaces/terminal/session')
}
