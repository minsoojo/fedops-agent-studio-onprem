import { useLayoutEffect, useState } from 'react'
import type { RegistryTask } from '../api/registry'
import type { AgentRegistryModelSource } from '../api/agents'
import type { WorkspaceProject } from '../api/workspace'
import AgentBuilder from '../features/agent-builder/AgentBuilderScreen'
import Agents from '../features/agents/AgentsScreen'
import FederatedLearning from '../features/federated-learning/FederatedLearningScreen'
import Home from '../features/home/HomeScreen'
import Registry from '../features/registry/RegistryScreen'
import Settings from '../features/settings/SettingsScreen'
import Workspace from '../features/workspace/WorkspaceScreen'
import { C } from '../ui/UIKit'
import ActivityBar from './ActivityBar'
import { I18nProvider, useI18n } from './i18n'
import SessionGate from './SessionGate'
import StatusBar from './StatusBar'
import { useStudio } from './StudioProvider'
import {
  applyThemeSelection,
  customThemeId,
  customThemeSelection,
  loadStoredCustomThemes,
  type CustomThemeV1,
} from './theme'
import TitleBar from './TitleBar'
import { taskDisplayName } from './taskPresentation'
import type { ActiveTask, AppScale, AppTheme, Screen } from './types'

const THEME_STORAGE_KEY = 'fedops-agent-studio-theme'
const THEME_EXPLICIT_KEY = 'fedops-agent-studio-theme-explicit'
const CUSTOM_THEMES_STORAGE_KEY = 'fedops-agent-studio-custom-themes'
const SCALE_STORAGE_KEY = 'fedops-agent-studio-interface-size'
const LOCALE_STORAGE_KEY = 'fedops-agent-studio-language'
const SCALE_FACTORS: Record<AppScale, number> = {
  standard: 1,
  comfortable: 1.1,
  large: 1.2,
}

function selectionFromTask(task: RegistryTask): ActiveTask {
  return {
    registryId: task.registryId,
    taskId: task.taskId,
    title: taskDisplayName(task),
    runtimeKey: task.runtimeKey,
    ownerHandle: task.ownerHandle,
    slug: task.slug,
    role: task.membership?.role ?? null,
  }
}

function selectionFromAgentSource(source: AgentRegistryModelSource): ActiveTask {
  return {
    registryId: source.registryId,
    taskId: source.taskId,
    title: source.title,
    runtimeKey: null,
    ownerHandle: source.ownerHandle,
    slug: source.slug,
    role: source.membershipRole,
  }
}

function selectionFromProject(project: WorkspaceProject): ActiveTask {
  const binding = project.taskBinding
  return {
    registryId: binding?.taskId ?? project.localProjectId,
    taskId: binding?.taskId ?? null,
    title: binding?.displayName || project.name,
    runtimeKey: binding?.runtimeKey ?? null,
    ownerHandle: binding?.ownerHandle ?? null,
    slug: binding?.slug ?? null,
    role: binding?.workspaceRole ?? (binding?.releaseId ? 'participant' : binding ? 'owner' : null),
    localProjectId: project.localProjectId,
  }
}

export default function App() {
  const studio = useStudio()
  const accountKey = studio.bootstrap?.session.isFedOps
    ? studio.bootstrap.session.accountKey
    : null

  return (
    <I18nProvider
      key={accountKey ?? 'signed-out'}
      storageKey={accountKey ? accountStorageKey(accountKey, LOCALE_STORAGE_KEY) : null}
    >
      <StudioContent />
    </I18nProvider>
  )
}

function StudioContent() {
  const studio = useStudio()
  const { t } = useI18n()

  if (studio.status === 'loading' && !studio.bootstrap) {
    return <FullPageMessage>{t('Connecting to Studio API…')}</FullPageMessage>
  }
  if (studio.status === 'error' && !studio.bootstrap) {
    return (
      <FullPageMessage>
        <div>{studio.error ?? t('Studio API is unavailable.')}</div>
        <button onClick={() => void studio.refresh()} style={{ marginTop: 12 }}>{t('Retry')}</button>
      </FullPageMessage>
    )
  }
  const accountKey = studio.bootstrap?.session.accountKey
  if (!studio.bootstrap?.session.isFedOps || !accountKey) return <LoggedOutStudio />
  return <AuthenticatedStudio key={accountKey} accountKey={accountKey} />
}

function LoggedOutStudio() {
  useLayoutEffect(() => {
    applyThemeSelection('light', [])
    document.documentElement.style.setProperty('--studio-ui-scale', String(SCALE_FACTORS.comfortable))
  }, [])
  return <SessionGate />
}

function AuthenticatedStudio({ accountKey }: { accountKey: string }) {
  const [screen, setScreen] = useState<Screen>('home')
  const [activeTask, setActiveTask] = useState<ActiveTask | null>(null)
  const [menuNavigationRevision, setMenuNavigationRevision] = useState(0)
  const [customThemes, setCustomThemes] = useState<CustomThemeV1[]>(() => storedCustomThemes(accountKey))
  const [theme, setTheme] = useState<AppTheme>(() => storedTheme(accountKey, customThemes))
  const [scale, setScale] = useState<AppScale>(() => storedScale(accountKey))

  function setAppTheme(next: AppTheme) {
    window.localStorage.setItem(accountStorageKey(accountKey, THEME_EXPLICIT_KEY), 'true')
    setTheme(next)
  }

  function importCustomTheme(next: CustomThemeV1) {
    const updated = [
      ...customThemes.filter(themeItem => themeItem.id !== next.id),
      next,
    ].sort((left, right) => left.name.localeCompare(right.name))
    storeCustomThemes(accountKey, updated)
    setCustomThemes(updated)
    setAppTheme(customThemeSelection(next.id))
  }

  function deleteCustomTheme(id: string) {
    const updated = customThemes.filter(themeItem => themeItem.id !== id)
    storeCustomThemes(accountKey, updated)
    setCustomThemes(updated)
    if (customThemeId(theme) === id) setAppTheme('light')
  }

  useLayoutEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: light)')
    const update = () => applyThemeSelection(
      theme === 'system' ? (media.matches ? 'light' : 'dark') : theme,
      customThemes,
    )
    window.localStorage.setItem(accountStorageKey(accountKey, THEME_STORAGE_KEY), theme)
    update()
    if (theme === 'system') media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [accountKey, customThemes, theme])

  useLayoutEffect(() => {
    const factor = SCALE_FACTORS[scale]
    document.documentElement.style.setProperty('--studio-ui-scale', String(factor))
    window.localStorage.setItem(accountStorageKey(accountKey, SCALE_STORAGE_KEY), scale)
  }, [accountKey, scale])

  function openTask(task: RegistryTask, destination: Screen) {
    setActiveTask(selectionFromTask(task))
    setScreen(destination)
  }

  function navigateFromMenu(destination: Screen) {
    setActiveTask(null)
    setMenuNavigationRevision(current => current + 1)
    setScreen(destination)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%', overflow: 'hidden' }}>
      <TitleBar />
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <ActivityBar screen={screen} setScreen={navigateFromMenu} />
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex' }}>
          {screen === 'home' && (
            <Home
              key={`home:${menuNavigationRevision}`}
              setScreen={setScreen}
              onOpenProject={project => {
                setActiveTask(selectionFromProject(project))
                setScreen('workspace')
              }}
            />
          )}
          {screen === 'workspace' && (
            <Workspace
              key={`workspace:${menuNavigationRevision}`}
              activeTask={activeTask}
              onClearTask={() => setActiveTask(null)}
              onTaskDeleted={localProjectId => {
                setActiveTask(current => (
                  current?.localProjectId === localProjectId ? null : current
                ))
              }}
              onOpenFederation={task => {
                setActiveTask(task)
                setScreen('federate')
              }}
            />
          )}
          {screen === 'registry' && <Registry key={`registry:${menuNavigationRevision}`} activeTask={activeTask} onOpenWorkspace={() => setScreen('workspace')} onOpenFederation={(task) => openTask(task, 'federate')} />}
          {screen === 'federate' && (
            <FederatedLearning
              key={`federate:${menuNavigationRevision}`}
              activeTask={activeTask}
              onSelectTask={setActiveTask}
              onOpenWorkspace={() => setScreen('workspace')}
            />
          )}
          {screen === 'agent' && <AgentBuilder key={`agent:${menuNavigationRevision}`} onNavigate={setScreen} onOpenRegistry={source => { setActiveTask(selectionFromAgentSource(source)); setScreen('registry') }} />}
          {screen === 'agents' && <Agents key={`agents:${menuNavigationRevision}`} onNavigate={setScreen} onOpenFederation={source => {
            setActiveTask({
              registryId: source.taskId ?? source.localProjectId,
              taskId: source.taskId,
              title: source.taskTitle,
              runtimeKey: 'runtimeKey' in source ? source.runtimeKey : null,
              ownerHandle: null,
              slug: null,
              role: 'participant',
              localProjectId: source.localProjectId,
            })
            setScreen('federate')
          }} />}
          {screen === 'settings' && (
            <Settings
              key={`settings:${menuNavigationRevision}`}
              theme={theme}
              setTheme={setAppTheme}
              customThemes={customThemes}
              importCustomTheme={importCustomTheme}
              deleteCustomTheme={deleteCustomTheme}
              scale={scale}
              setScale={setScale}
            />
          )}
        </div>
      </div>
      <StatusBar activeTask={activeTask} />
    </div>
  )
}

function FullPageMessage({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center', background: C.bg, color: C.text, fontFamily: 'Inter, sans-serif', textAlign: 'center' }}>
      <div>{children}</div>
    </div>
  )
}

function storedTheme(accountKey: string, customThemes: CustomThemeV1[]): AppTheme {
  if (window.localStorage.getItem(accountStorageKey(accountKey, THEME_EXPLICIT_KEY)) !== 'true') return 'light'
  const saved = window.localStorage.getItem(accountStorageKey(accountKey, THEME_STORAGE_KEY))
  if (saved === 'light' || saved === 'dark' || saved === 'system') return saved
  if (saved?.startsWith('custom:') && customThemes.some(theme => theme.id === customThemeId(saved as AppTheme))) {
    return saved as AppTheme
  }
  return 'light'
}

function storedCustomThemes(accountKey: string): CustomThemeV1[] {
  return loadStoredCustomThemes(
    window.localStorage.getItem(accountStorageKey(accountKey, CUSTOM_THEMES_STORAGE_KEY)),
  )
}

function storeCustomThemes(accountKey: string, themes: CustomThemeV1[]) {
  window.localStorage.setItem(
    accountStorageKey(accountKey, CUSTOM_THEMES_STORAGE_KEY),
    JSON.stringify(themes),
  )
}

function storedScale(accountKey: string): AppScale {
  const saved = window.localStorage.getItem(accountStorageKey(accountKey, SCALE_STORAGE_KEY))
  return saved === 'standard' || saved === 'comfortable' || saved === 'large' ? saved : 'comfortable'
}

function accountStorageKey(accountKey: string, preference: string): string {
  return `fedops:${accountKey}:${preference}`
}
