import { deleteJson, getJson, postJson, putJson, queryString } from './http'
import type { WorkspaceRun } from './workspace'

export type EnvironmentOwnerType = 'federated-task' | 'agent-build' | 'agent-serve'
export type PythonEnvironmentStatus = 'missing' | 'outdated' | 'syncing' | 'ready' | 'error'

export interface PythonEnvironment {
  environmentId: string
  ownerType: EnvironmentOwnerType
  ownerId: string
  name: string
  pythonVersion: string
  path: string
  selected: boolean
  status: PythonEnvironmentStatus
  lockStatus: 'missing' | 'outdated' | 'synced'
  uvVersion: string | null
  createdAt: string
  lastSyncedAt: string | null
  error: string | null
}

export interface UvRuntimeInformation {
  available: boolean
  version: string | null
  executable: string | null
}

export interface PythonEnvironmentList {
  items: PythonEnvironment[]
  ownerType: EnvironmentOwnerType
  ownerId: string
  uv: UvRuntimeInformation
  source: 'local-runtime-metadata'
}

export interface TaskRequirements {
  path: 'requirements.txt'
  mode: 'requirements' | 'pyproject' | 'missing'
  editable: boolean
  content: string
  dependencies: string[]
  valid: boolean
  error: string | null
  sha256: string | null
}

export async function listPythonEnvironments(ownerType: EnvironmentOwnerType, ownerId: string): Promise<PythonEnvironmentList> {
  return getJson(`/api/v1/environments${queryString({ owner_type: ownerType, owner_id: ownerId })}`)
}

export async function createPythonEnvironment(input: {
  ownerType: EnvironmentOwnerType
  ownerId: string
  name: string
  pythonVersion: string
  select?: boolean
}): Promise<PythonEnvironment> {
  return postJson('/api/v1/environments', input)
}

export async function selectPythonEnvironment(ownerType: EnvironmentOwnerType, ownerId: string, environmentId: string): Promise<PythonEnvironment> {
  return postJson(`/api/v1/environments/${encodeURIComponent(environmentId)}/select`, { ownerType, ownerId })
}

export async function syncPythonEnvironment(ownerType: EnvironmentOwnerType, ownerId: string, environmentId: string): Promise<WorkspaceRun> {
  return postJson(`/api/v1/environments/${encodeURIComponent(environmentId)}/sync`, { ownerType, ownerId })
}

export async function deletePythonEnvironment(ownerType: EnvironmentOwnerType, ownerId: string, environmentId: string): Promise<PythonEnvironmentList> {
  return deleteJson(`/api/v1/environments/${encodeURIComponent(environmentId)}`, { ownerType, ownerId })
}

export async function getTaskRequirements(ownerType: EnvironmentOwnerType, ownerId: string): Promise<TaskRequirements> {
  return getJson(`/api/v1/environments/requirements${queryString({ owner_type: ownerType, owner_id: ownerId })}`)
}

export async function updateTaskRequirements(ownerType: EnvironmentOwnerType, ownerId: string, content: string): Promise<TaskRequirements> {
  return putJson('/api/v1/environments/requirements', { ownerType, ownerId, content })
}
