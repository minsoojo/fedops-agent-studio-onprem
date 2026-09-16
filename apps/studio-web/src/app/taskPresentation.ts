import type { RegistryTask } from '../api/registry'

const LEGACY_TASK_NAMES: Record<string, string> = {
  sbastepsfl: 'SBA-FL Steps Prediction',
  sbaweightfl: 'SBA-FL Weight Prediction',
}

/** Return the user-facing model or Task name while keeping runtime identifiers out of headings. */
export function taskDisplayName(task: RegistryTask): string {
  const modelName = task.primaryModel?.displayName?.trim()
    || task.primaryModel?.workingName?.trim()
  if (modelName) return modelName

  const runtimeKey = task.runtimeKey?.trim().toLowerCase()
  const internalTitle = task.title.trim().toLowerCase()
  const legacyName = LEGACY_TASK_NAMES[runtimeKey ?? ''] || LEGACY_TASK_NAMES[internalTitle]
  if (legacyName) return legacyName

  return task.displayName?.trim() || task.title
}
