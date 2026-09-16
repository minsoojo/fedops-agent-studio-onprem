import { postJson } from './http'

export interface StudioSession {
  authenticated: boolean
  mode: 'anonymous' | 'fedops'
  username: string | null
  accountKey: string | null
  handle: string | null
  organization: string | null
  displayName: string | null
  isGuest: boolean
  isFedOps: boolean
}

/** POST /api/v1/auth/login -> features/auth/router.py:login */
export async function loginToFedOps(username: string, password: string): Promise<StudioSession> {
  const result = await postJson<{ status: 'success'; session: StudioSession }>(
    '/api/v1/auth/login',
    { username, password },
  )
  return result.session
}

/** POST /api/v1/auth/logout -> features/auth/router.py:logout */
export async function logoutStudio(): Promise<void> {
  await postJson<{ status: 'success' }>('/api/v1/auth/logout')
}
