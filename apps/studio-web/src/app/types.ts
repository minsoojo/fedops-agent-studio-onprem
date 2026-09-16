export type Screen = 'home' | 'workspace' | 'registry' | 'federate' | 'agent' | 'agents' | 'settings'
export type BuiltInTheme = 'dark' | 'light' | 'system'
export type AppTheme = BuiltInTheme | `custom:${string}`
export type AppScale = 'standard' | 'comfortable' | 'large'

/** A navigation selection, never a copied Federated Task record. */
export interface ActiveTask {
  registryId: string
  taskId: string | null
  title: string
  runtimeKey: string | null
  ownerHandle: string | null
  slug: string | null
  role: 'owner' | 'admin' | 'participant' | null
  localProjectId?: string | null
}
