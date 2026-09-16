import type { StudioSession } from './auth'
import { getJson } from './http'
import type { LegacyWorkspaceProject, WorkspaceProject } from './workspace'

export interface StudioCapabilities {
  workspaceFiles: boolean
  createFederatedTask: boolean
  projectInstall: boolean
  pythonEnvironments: boolean
  federatedTaskValidation: boolean
  accountTaskList: boolean
  publicTaskRegistry: boolean
  taskHub: boolean
  taskActivity: boolean
  participationRequest: boolean
  baselineImport: boolean
  localValidation: boolean
  fedopsLogin: boolean
  stableTaskBinding: boolean
  globalModelRegistry: boolean
  federatedParticipation: boolean
  agentBuilder: boolean
  agentRuntime: boolean
}

export interface HardwareGpuDevice {
  name: string
  memoryBytes: number | null
  computeUnits: number | null
}

export interface HardwareInformation {
  source: 'host' | 'runtime'
  platform: {
    system: string
    release: string
    architecture: string
  }
  cpu: {
    model: string
    logicalCores: number
  }
  memory: { totalBytes: number }
  gpu: {
    detected: boolean
    devices: HardwareGpuDevice[]
  }
}

export interface StudioBootstrap {
  version: string
  connections: { fedopsWeb: string; fedopsRegister: string }
  session: StudioSession
  workspace: {
    root: string
    displayRoot: string
    projects: WorkspaceProject[]
    legacyProjects: LegacyWorkspaceProject[]
    projectDiscovery: string
  }
  hardware: HardwareInformation
  capabilities: StudioCapabilities
}

/** GET /api/v1/bootstrap -> features/system/router.py:bootstrap */
export async function getStudioBootstrap(signal?: AbortSignal): Promise<StudioBootstrap> {
  return getJson<StudioBootstrap>('/api/v1/bootstrap', signal)
}
