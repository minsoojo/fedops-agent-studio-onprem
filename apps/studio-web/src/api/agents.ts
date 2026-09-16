import { deleteJson, getJson, postJson, postNdjson, putJson } from './http'
import type { SourceList } from './http'

export type AgentRuntimeStatus = 'not-prepared' | 'preparing' | 'installed' | 'ready' | 'error'

export interface HuggingFaceAgentLlm {
  source: 'huggingface'
  repoId: string
  revision: string
  fileName: string | null
  format: 'transformers' | 'gguf'
  displayName: string
  license: string
  localPath: string | null
  runtimeStatus: AgentRuntimeStatus
}

export interface FederatedTaskAgentLlm {
  source: 'federated-task'
  localProjectId: string
  projectName: string
  taskId: string | null
  taskTitle: string
  modelName: string
  releaseId: string | null
  modelVersionId: string | null
  sourceFingerprint: string
  modelSha256: string
  localPath: string
  modelArtifactPath?: string | null
  runtimeStatus: 'installed' | 'ready' | 'error'
}

export type AgentLlm = HuggingFaceAgentLlm | FederatedTaskAgentLlm

export interface AgentToolModel {
  localProjectId: string
  projectName: string
  taskId: string | null
  runtimeKey: string | null
  registryId: string | null
  taskTitle: string
  releaseId: string | null
  modelVersionId: string | null
  modelName: string
  modelVersion: number | null
  format: string | null
  sourceFingerprint: string
  modelSha256: string
  localPath: string
  modelArtifactPath?: string | null
  toolManifest?: {
    description: string
    features: string[]
    input?: {
      modality?: string
      sources?: string[]
      jsonSchema?: Record<string, unknown>
    }
    output: {
      description: string
      labels?: string[]
      jsonSchema?: Record<string, unknown>
    }
  }
}

export interface AgentModelSource {
  localProjectId: string
  projectName: string
  displayPath: string
  capability: 'base-llm' | 'tool-ai'
  ready: boolean
  reason: string | null
  taskId: string | null
  runtimeKey: string | null
  registryId: string | null
  taskTitle: string
  modelName: string
  releaseId: string | null
  modelVersionId: string | null
  modelVersion: number | null
  format: string | null
  sourceFingerprint: string | null
  modelSha256: string | null
  localPath: string | null
  modelArtifactPath?: string | null
  availableModelVersions: AgentModelVersionSource[]
  toolManifest: AgentToolModel['toolManifest'] | null
}

export interface AgentModelVersionSource {
  modelVersionId: string | null
  version: number | null
  role: string | null
  label: string
  sourceFormat?: string | null
  format: string | null
  size: number | null
  sha256: string | null
  cached: boolean
  modelArtifactPath: string | null
}

export interface AgentRegistryModelSource {
  registryId: string
  taskId: string | null
  title: string
  displayName: string
  modelName: string
  capability: 'base-llm' | 'tool-ai'
  ownerHandle: string | null
  slug: string | null
  summary: string
  participationPolicy: 'closed' | 'approval_required' | 'open'
  registryStatus: string
  membershipRole: 'owner' | 'admin' | 'participant' | null
  participationStatus: string | null
  accessState: 'workspace-required' | 'approval-pending' | 'join-required' | 'unavailable'
  canRequestParticipation: boolean
  canOpenWorkspace: boolean
}

export interface AgentHarness {
  instructions: string
  toolRouting: 'automatic' | 'explicit'
  contextWindow: number
  memory: 'none' | 'session'
  safety: 'standard' | 'strict'
  temperature: number
  maxTokens: number
}

export interface AgentValidationCheck {
  id: string
  label: string
  status: 'passed' | 'failed' | 'pending'
  detail: string
}

export interface AgentValidation {
  ok: boolean
  checkedAt: string
  sourceFingerprint: string
  checks: AgentValidationCheck[]
}

export interface AgentDraft {
  schemaVersion: 1
  agentId: string
  name: string
  description: string
  status: 'draft' | 'modified' | 'validation-required' | 'ready' | 'built'
  llm: AgentLlm | null
  tools: AgentToolModel[]
  harness: AgentHarness
  validation: AgentValidation | null
  buildRevision: number
  createdAt: string
  updatedAt: string
}

export interface AgentServing {
  agentId: string
  enabled: boolean
  endpointBase: string
  endpointUrl: string
  port: number | null
  tokenCreatedAt: string | null
  requestCount: number
  dataSourceCount: number
  directToolIds: string[]
}

export type AgentToolInput =
  | { type: 'inline'; payload: Record<string, unknown>; toolId?: string | null }
  | { type: 'data-source'; sourceId: string; sampleIndex?: number | null }

export interface AgentServingDataSource {
  sourceId: string
  toolId: string
  name: string
  dataPath: string
  sampleIndex: number
  selectionMode: 'fixed' | 'request'
  enabledForServing: boolean
  available: boolean
  hasEntries: boolean
  fileCount: number
  updatedAt: string
}

export interface AgentToolPredictionResponse {
  tool: Record<string, unknown>
  result: Record<string, unknown>
  durationMs: number
  inputSource: Record<string, unknown>
}

export interface BuiltAgent {
  schemaVersion: 1
  agentId: string
  name: string
  description: string
  status: 'built'
  buildRevision: number
  builtAt: string
  sourceFingerprint: string
  llm: AgentLlm
  tools: AgentToolModel[]
  harness: AgentHarness
  serving: AgentServing
}

export interface AgentChatResponse {
  requestId: string
  agentId: string
  status: 'succeeded' | 'failed'
  response: string
  toolResult: Record<string, unknown> | null
  toolResults: Array<Record<string, unknown>>
  trace: Array<Record<string, unknown>>
  createdAt: string
}

export interface AgentChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export type AgentChatStreamEvent =
  | { type: 'stage'; stage: string }
  | { type: 'delta'; content: string }
  | { type: 'completed'; result: AgentChatResponse }
  | { type: 'error'; detail: string }

export interface AgentLlmPreparation {
  preparationId: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  stage: 'queued' | 'checking' | 'downloading' | 'verifying' | 'ready' | 'failed'
  percent: number
  downloadedBytes: number
  totalBytes: number | null
  provider: string
  model: string
  localPath: string
  detail: string
  startedAt: string
  updatedAt: string
}

export interface AgentRequestRecord {
  requestId: string
  createdAt: string
  method: string
  path: string
  status: number
  latencyMs: number
  source: string
}

export function listAgentDrafts(signal?: AbortSignal) {
  return getJson<SourceList<AgentDraft>>('/api/v1/agent-builder/drafts', signal)
}

export function listAgentModelSources(signal?: AbortSignal) {
  return getJson<SourceList<AgentModelSource>>('/api/v1/agent-builder/model-sources', signal)
}

export function prepareAgentModelVersion(localProjectId: string, modelVersionId: string) {
  return postJson<AgentModelSource>(
    `/api/v1/agent-builder/model-sources/${encodeURIComponent(localProjectId)}/versions/${encodeURIComponent(modelVersionId)}/prepare`,
  )
}

export function listAgentRegistryModelSources(signal?: AbortSignal) {
  return getJson<SourceList<AgentRegistryModelSource>>('/api/v1/agent-builder/registry-model-sources', signal)
}

export function requestAgentRegistryParticipation(source: AgentRegistryModelSource) {
  if (!source.ownerHandle || !source.slug) {
    throw new Error('A public Registry handle and slug are required to join this Task.')
  }
  return postJson<{ status: string; [key: string]: unknown }>(
    `/api/v1/registry/tasks/public/${encodeURIComponent(source.ownerHandle)}/${encodeURIComponent(source.slug)}/join`,
  )
}

export function createAgentDraft(name: string, description = '') {
  return postJson<AgentDraft>('/api/v1/agent-builder/drafts', { name, description })
}

export function updateAgentDraft(draft: AgentDraft) {
  return putJson<AgentDraft>(`/api/v1/agent-builder/drafts/${encodeURIComponent(draft.agentId)}`, {
    name: draft.name,
    description: draft.description,
    llm: draft.llm,
    tools: draft.tools,
    harness: draft.harness,
  })
}

export function deleteAgentDraft(agentId: string) {
  return deleteJson<{ deleted: boolean; agentId: string }>(`/api/v1/agent-builder/drafts/${encodeURIComponent(agentId)}`)
}

export function validateAgentDraft(agentId: string) {
  return postJson<AgentValidation>(`/api/v1/agent-builder/drafts/${encodeURIComponent(agentId)}/validate`)
}

export function buildAgent(agentId: string) {
  return postJson<BuiltAgent>(`/api/v1/agent-builder/drafts/${encodeURIComponent(agentId)}/build`)
}

export function testAgentDraft(agentId: string, message: string, toolInput: Record<string, unknown> | null, toolId: string | null = null) {
  return postJson<AgentChatResponse>(`/api/v1/agent-builder/drafts/${encodeURIComponent(agentId)}/test`, { message, toolInput, toolId })
}

export function chatAgentDraft(
  agentId: string,
  message: string,
  toolInput: Record<string, unknown> | null,
  history: AgentChatMessage[] = [],
  toolId: string | null = null,
) {
  return postJson<AgentChatResponse>(`/api/v1/agent-builder/drafts/${encodeURIComponent(agentId)}/chat`, { message, toolInput, history, toolId })
}

export function streamAgentDraftChat(
  agentId: string,
  message: string,
  toolInput: Record<string, unknown> | null,
  history: AgentChatMessage[],
  onEvent: (event: AgentChatStreamEvent) => void,
  signal?: AbortSignal,
  toolId: string | null = null,
) {
  return postNdjson<AgentChatStreamEvent>(
    `/api/v1/agent-builder/drafts/${encodeURIComponent(agentId)}/chat/stream`,
    { message, toolInput, history, toolId },
    onEvent,
    signal,
  )
}

export function prepareAgentDraftLlm(agentId: string) {
  return postJson<AgentLlmPreparation>(`/api/v1/agent-builder/drafts/${encodeURIComponent(agentId)}/llm/prepare`)
}

export function getAgentDraftLlmPreparation(agentId: string) {
  return getJson<AgentLlmPreparation>(`/api/v1/agent-builder/drafts/${encodeURIComponent(agentId)}/llm/prepare`)
}

export function listBuiltAgents(signal?: AbortSignal) {
  return getJson<SourceList<BuiltAgent>>('/api/v1/agents', signal)
}

export function deleteBuiltAgent(agentId: string) {
  return deleteJson<{ deleted: boolean; agentId: string }>(`/api/v1/agents/${encodeURIComponent(agentId)}`)
}

export function testBuiltAgent(agentId: string, message: string, toolInput: Record<string, unknown> | null, toolId: string | null = null) {
  return postJson<AgentChatResponse>(`/api/v1/agents/${encodeURIComponent(agentId)}/test`, { message, toolInput, history: [], toolId })
}

export function chatBuiltAgent(
  agentId: string,
  message: string,
  toolInput: Record<string, unknown> | null,
  history: AgentChatMessage[] = [],
  toolId: string | null = null,
) {
  return postJson<AgentChatResponse>(`/api/v1/agents/${encodeURIComponent(agentId)}/chat`, { message, toolInput, history, toolId })
}

export function streamBuiltAgentChat(
  agentId: string,
  message: string,
  toolInput: Record<string, unknown> | null,
  history: AgentChatMessage[],
  onEvent: (event: AgentChatStreamEvent) => void,
  signal?: AbortSignal,
  toolId: string | null = null,
) {
  return postNdjson<AgentChatStreamEvent>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/chat/stream`,
    { message, toolInput, history, toolId },
    onEvent,
    signal,
  )
}

export function prepareAgentLlm(agentId: string) {
  return postJson<AgentLlmPreparation>(`/api/v1/agents/${encodeURIComponent(agentId)}/llm/prepare`)
}

export function getAgentLlmPreparation(agentId: string) {
  return getJson<AgentLlmPreparation>(`/api/v1/agents/${encodeURIComponent(agentId)}/llm/prepare`)
}

export function enableAgentServing(agentId: string, port: number) {
  return postJson<{ serving: AgentServing; token: string }>(`/api/v1/agents/${encodeURIComponent(agentId)}/serving`, { port })
}

export function disableAgentServing(agentId: string) {
  return deleteJson<AgentServing>(`/api/v1/agents/${encodeURIComponent(agentId)}/serving`)
}

export function updateAgentServingPort(agentId: string, port: number) {
  return putJson<AgentServing>(`/api/v1/agents/${encodeURIComponent(agentId)}/serving`, { port })
}

export function rotateAgentServingToken(agentId: string) {
  return postJson<{ serving: AgentServing; token: string }>(`/api/v1/agents/${encodeURIComponent(agentId)}/serving/token`)
}

export function configureAgentDirectToolServing(agentId: string, toolId: string, enabled: boolean) {
  return putJson<AgentServing>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/serving/tools/${encodeURIComponent(toolId)}`,
    { enabled },
  )
}

export function listAgentServingDataSources(agentId: string, signal?: AbortSignal) {
  return getJson<SourceList<AgentServingDataSource>>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/serving/data-sources`,
    signal,
  )
}

export function saveAgentServingDataSource(
  agentId: string,
  value: {
    toolId: string
    name: string
    dataPath: string
    sampleIndex: number
    selectionMode: 'fixed' | 'request'
    enabledForServing: boolean
  },
) {
  return putJson<AgentServingDataSource>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/serving/data-sources`,
    value,
  )
}

export function deleteAgentServingDataSource(agentId: string, sourceId: string) {
  return deleteJson<{ deleted: boolean; sourceId: string }>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/serving/data-sources/${encodeURIComponent(sourceId)}`,
  )
}

export function testAgentServingDataSource(agentId: string, sourceId: string, sampleIndex?: number) {
  return postJson<AgentToolPredictionResponse>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/serving/data-sources/${encodeURIComponent(sourceId)}/test`,
    sampleIndex === undefined ? {} : { sampleIndex },
  )
}

export function listAgentRequests(agentId: string, signal?: AbortSignal) {
  return getJson<SourceList<AgentRequestRecord>>(`/api/v1/agents/${encodeURIComponent(agentId)}/requests`, signal)
}
