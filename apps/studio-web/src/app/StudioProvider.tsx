import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { loginToFedOps, logoutStudio } from '../api/auth'
import { getStudioBootstrap } from '../api/system'
import type { StudioBootstrap } from '../api/system'

type StudioStatus = 'loading' | 'ready' | 'error'

interface StudioContextValue {
  status: StudioStatus
  bootstrap: StudioBootstrap | null
  error: string | null
  sessionBusy: boolean
  refresh: () => Promise<void>
  forgetWorkspaceProject: (localProjectId: string) => void
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const StudioContext = createContext<StudioContextValue | null>(null)

export function StudioProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<StudioStatus>('loading')
  const [bootstrap, setBootstrap] = useState<StudioBootstrap | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sessionBusy, setSessionBusy] = useState(false)

  async function refresh() {
    setStatus('loading')
    try {
      const next = await getStudioBootstrap()
      setBootstrap(next)
      setError(null)
      setStatus('ready')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setStatus('error')
    }
  }

  function forgetWorkspaceProject(localProjectId: string) {
    setBootstrap(current => {
      if (!current) return current
      const projects = current.workspace.projects.filter(
        project => project.localProjectId !== localProjectId,
      )
      if (projects.length === current.workspace.projects.length) return current
      return {
        ...current,
        workspace: {
          ...current.workspace,
          projects,
        },
      }
    })
  }

  useEffect(() => { void refresh() }, [])

  async function runSessionAction(action: () => Promise<unknown>) {
    setSessionBusy(true)
    setError(null)
    try {
      await action()
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setStatus('ready')
      throw cause
    } finally {
      setSessionBusy(false)
    }
  }

  async function login(username: string, password: string) {
    await runSessionAction(() => loginToFedOps(username, password))
  }

  async function logout() {
    await runSessionAction(logoutStudio)
  }

  const value = useMemo(
    () => ({ status, bootstrap, error, sessionBusy, refresh, forgetWorkspaceProject, login, logout }),
    [status, bootstrap, error, sessionBusy],
  )

  return <StudioContext.Provider value={value}>{children}</StudioContext.Provider>
}

export function useStudio(): StudioContextValue {
  const value = useContext(StudioContext)
  if (!value) throw new Error('useStudio must be used inside StudioProvider')
  return value
}
