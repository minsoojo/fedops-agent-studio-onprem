import { deleteJson, getJson, postJson, queryString } from './http'
import type { SourceList } from './http'
import type { WorkspaceRun } from './workspace'

export interface AccountTask {
  taskId: string
  displayName: string
  runtimeKey: string | null
  ownerHandle: string | null
  slug: string | null
  registryStatus: string
  runtimeContract: {
    name: 'legacy-v1' | 'federated-task-v2' | 'federated-task-v3'
    schemaVersion: number
    baselineVersion?: string | null
  }
  yamlConfig: string | null
  primaryModel: PrimaryModel | null
}

export interface PrimaryModel {
  displayName?: string
  workingName?: string
  framework?: string
  architecture?: string
  task?: string
  parameterSignature?: string | null
}

export type RegistryView = 'public' | 'all' | 'owned' | 'joined'

export interface RegistryTaskPermissions {
  canView: boolean
  canManage: boolean
  canDownloadModels: boolean
  canRequestParticipation: boolean
  canOpenWorkspace: boolean
  isOwner: boolean
  isAdmin: boolean
  isParticipant: boolean
  participationStatus: string | null
  canLeaveParticipation: boolean
  leaveRequiresCompletedRun: boolean
  completedParticipationCount: number
  lastParticipatedAt: string | null
}

export interface RegistryTask {
  registryId: string
  taskId: string | null
  title: string
  displayName: string
  taskCategory: string | null
  dataModality: string | null
  primaryModel: PrimaryModel | null
  runtimeKey: string | null
  ownerHandle: string | null
  slug: string | null
  summary: string
  description: string
  cardMarkdown: string
  tags: string[]
  visibility: 'public' | 'private'
  participationPolicy: 'closed' | 'approval_required' | 'open'
  status: string
  dataType: string | null
  modelType: string | null
  modelCapability: 'base-llm' | 'tool-ai'
  strategy: string | null
  numRounds: string | null
  publishedAt: string | null
  registryStatus: string
  runtimeContract: {
    name: 'legacy-v1' | 'federated-task-v2' | 'federated-task-v3'
    schemaVersion: number
    fedopsVersion?: string
    sourceRevision?: string
    baselineVersion?: string | null
  }
  currentPublishedReleaseId: string | null
  updatedAt: string | null
  membership: { role: 'owner' | 'admin' | 'participant'; status: string } | null
  permissions: RegistryTaskPermissions
}

export interface TaskActivity {
  schemaVersion: number
  run: {
    kind: string
    runId?: string | null
    status: string
    phase: string | null
    currentRound: number
    totalRounds: number | null
    progressPercent: number | null
    startedAt: string | null
    endedAt: string | null
    lastUpdatedAt: string | null
    latestModelVersion: number | null
    baseGlobalModelVersion?: number | null
    targetGlobalModelVersion?: number | null
  }
  participants: {
    approved: number
    contributed: number
    registeredDevices: number | null
    onlineDevices: number | null
    trainingDevices: number | null
    selectedThisRun: number | null
    completedCurrentRound: number
  }
  metrics: { latest: TaskMetric | null; history: TaskMetric[] }
  membership: { role: string; status: string; requestedAt?: string | null; reviewedAt?: string | null } | null
  source: { type: string; estimated: boolean; managerAvailable: boolean; observedAt: string }
}

export interface TaskMetric {
  round: number | null
  modelVersion: number | null
  loss: number | null
  accuracy: number | null
  roundTimeSeconds: number | null
}

export interface TaskHubModel {
  id: string
  registeredModelId: string
  modelName: string
  name: string
  version: number
  size: number | null
  createdAt: string | null
  checksum?: string | null
  format?: string | null
  role?: string | null
  aliases?: { latest?: boolean; best?: boolean; champion?: boolean }
}

export interface TaskHubFile {
  id: string
  path: string
  name: string
  version: number
  versionCount: number
  size: number | null
  checksum: string | null
  contentType: string | null
  kind: string
  source: string
  previewable: boolean
  createdAt: string | null
  updatedAt: string | null
}

export interface TaskHub {
  task: { handle: string | null; slug: string | null; title: string }
  release?: {
    releaseId: string
    revision: number
    status: string
    bundleSha256: string
    sourceFingerprint: string
    publishedAt: string | null
  }
  permissions: RegistryTaskPermissions
  models: TaskHubModel[]
  files: TaskHubFile[]
  usage: { downloads?: { total?: number; uniqueUsers?: number; recent?: number }; [key: string]: unknown }
}

export interface TaskFilePreview {
  file: { id: string; path: string; name: string; version: number; size: number; checksum: string; contentType: string }
  language: string
  content: string
  truncated: boolean
  previewBytes: number
}

export async function listAccountTasks(signal?: AbortSignal): Promise<SourceList<AccountTask>> {
  return getJson<SourceList<AccountTask>>('/api/v1/registry/account-tasks', signal)
}

/** GET /api/v1/registry/tasks -> features/registry/router.py:list_tasks */
export async function listRegistryTasks(
  view: RegistryView,
  options: { q?: string; modelType?: string; tag?: string; page?: number } = {},
  signal?: AbortSignal,
): Promise<SourceList<RegistryTask> & { view: RegistryView; page: number }> {
  return getJson(`/api/v1/registry/tasks${queryString({
    view,
    q: options.q,
    model_type: options.modelType,
    tag: options.tag,
    page: options.page ?? 1,
  })}`, signal)
}

export async function getTaskActivity(task: RegistryTask, signal?: AbortSignal): Promise<TaskActivity> {
  const locator = task.visibility === 'public' ? publicTaskPath(task) : null
  return getJson(`/api/v1/registry/tasks/${locator || runtimeTaskPath(task)}/activity`, signal)
}

export async function getTaskHub(task: RegistryTask, signal?: AbortSignal): Promise<TaskHub> {
  const locator = task.visibility === 'public' ? publicTaskPath(task) : null
  return getJson(`/api/v1/registry/tasks/${locator || runtimeTaskPath(task)}/hub`, signal)
}

export async function requestTaskParticipation(task: RegistryTask): Promise<{ status: string; [key: string]: unknown }> {
  const locator = publicTaskPath(task)
  if (!locator) throw new Error('A public Registry handle and slug are required to join this Task.')
  return postJson(`/api/v1/registry/tasks/${locator}/join`)
}

export async function leaveTaskParticipation(task: RegistryTask): Promise<{ status: string; [key: string]: unknown }> {
  const locator = publicTaskPath(task)
  if (!locator) throw new Error('A public Registry handle and slug are required to leave this Task.')
  return deleteJson(`/api/v1/registry/tasks/${locator}/participation`)
}

export async function previewTaskFile(task: RegistryTask, fileId: string): Promise<TaskFilePreview> {
  return getJson(`/api/v1/registry/tasks/${taskResourcePath(task)}/files/${encodeURIComponent(fileId)}/preview`)
}

export async function getTaskFileDownload(task: RegistryTask, fileId: string): Promise<{ url: string; expiresIn: number }> {
  return getJson(`/api/v1/registry/tasks/${taskResourcePath(task)}/files/${encodeURIComponent(fileId)}/download`)
}

export async function getModelDownload(task: RegistryTask, versionId: string): Promise<{ url: string; expiresIn: number }> {
  return getJson(`/api/v1/registry/tasks/${taskResourcePath(task)}/models/${encodeURIComponent(versionId)}/download`)
}

export async function openPublishedRelease(task: RegistryTask): Promise<WorkspaceRun> {
  if (!task.taskId) throw new Error('FedOps did not expose a stable taskId for this Published Task.')
  return postJson(`/api/v1/registry/tasks/${encodeURIComponent(task.taskId)}/published-release`, { name: task.displayName })
}

function publicTaskPath(task: RegistryTask): string | null {
  if (!task.ownerHandle || !task.slug) return null
  return `public/${encodeURIComponent(task.ownerHandle)}/${encodeURIComponent(task.slug)}`
}

function runtimeTaskPath(task: RegistryTask): string {
  if (!task.runtimeKey) throw new Error('FedOps did not expose a runtimeKey for this Task.')
  return `runtime/${encodeURIComponent(task.runtimeKey)}`
}

function taskResourcePath(task: RegistryTask): string {
  return publicTaskPath(task) || runtimeTaskPath(task)
}
