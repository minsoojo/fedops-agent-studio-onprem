import { getJson, postJson } from './http'
import type { LocalDataBinding } from './workspace'

export interface ValidationDataset { dataPath: string; sha256: string; fileCount: number; totalBytes: number }
export interface ValidationDataStatus { local: LocalDataBinding; items: ValidationDataset[]; serverError?: string }
const path = (id: string) => `/api/v1/workspaces/${encodeURIComponent(id)}`
export const getValidationData = (id: string) => getJson<ValidationDataStatus>(`${path(id)}/validation-data`)
export const openValidationFolder = (id: string) => postJson<LocalDataBinding>(`${path(id)}/open-validation-folder`)
export const uploadValidationData = (id: string, relativePath: string) => postJson<ValidationDataset>(`${path(id)}/validation-data`, { relativePath, consent: true })
