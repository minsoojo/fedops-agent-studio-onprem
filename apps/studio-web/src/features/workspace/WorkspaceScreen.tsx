import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useStudio } from '../../app/StudioProvider'
import { useI18n } from '../../app/i18n'
import { ServerValidationData } from './ServerValidationData'
import { Badge, BottomTabs, Button, C, EditorTabs, IconBtn, MetaRow, SectionLabel, Sidebar } from '../../ui/UIKit'
import {
  createWorkspace,
  createWorkspaceFile,
  cancelWorkspaceRun,
  deleteWorkspaceFile,
  deleteWorkspace,
  formatWorkspaceFile,
  getReleaseSubmissionReadiness,
  getWorkspaceRun,
  getWorkspaceFileTree,
  importLegacyWorkspaces,
  linkWorkspaceTask,
  listWorkspaceRuns,
  openWorkspaceDataFolder,
  openWorkspaceFolder,
  prepareWorkspaceDataBinding,
  readWorkspaceFile,
  saveWorkspaceFile,
  startWorkspaceAction,
  submitReleaseCandidate,
} from '../../api/workspace'
import type {
  WorkspaceAction,
  WorkspaceFile,
  WorkspaceFileTreeNode,
  LocalDataBinding,
  WorkspaceProject,
  WorkspaceRun,
  TrainingMetricPoint,
  ReleaseSubmissionReadiness,
} from '../../api/workspace'
import {
  PythonEnvironmentPanel,
  usePythonEnvironments,
} from '../python-environments/PythonEnvironmentPanel'
import type { PythonEnvironment, UvRuntimeInformation } from '../../api/environments'
import FileTypeIcon, { fileTypeLabel } from '../../ui/FileTypeIcon'
import MetricChartPanel from '../../ui/MetricChartPanel'
import type { MetricChartSeries } from '../../ui/MetricChartPanel'
import { groupMetricNames, isRatioMetric } from '../../ui/metricSemantics'
import { listAccountTasks, listRegistryTasks, openPublishedRelease } from '../../api/registry'
import type { AccountTask, RegistryTask } from '../../api/registry'
import type { ActiveTask } from '../../app/types'

type WorkspaceView = 'Overview' | 'Code' | 'Project Setup' | 'Python Environments' | 'Task Test'
type BottomView = 'Terminal' | 'Run Output'
type ExplorerView = 'task' | 'all'
type OpenFile = WorkspaceFile & { draft: string; dirty: boolean }
type DeleteStage = 'Checking actions…' | 'Stopping action…' | 'Deleting files…' | 'Refreshing…'
type DeleteProgress = { stage: DeleteStage; percent: number }
const DEFAULT_CODE_FONT_SIZE = 13
const MIN_CODE_FONT_SIZE = 10
const MAX_CODE_FONT_SIZE = 24
const CodeEditor = lazy(() => import('../../ui/CodeEditor').then(module => ({ default: module.CodeEditor })))
const WorkspaceTerminal = lazy(() => import('./WorkspaceTerminal'))

export default function Workspace({ activeTask, onClearTask, onOpenFederation, onTaskDeleted }: {
  activeTask: ActiveTask | null
  onClearTask: () => void
  onOpenFederation: (task: ActiveTask) => void
  onTaskDeleted: (localProjectId: string) => void
}) {
  const studio = useStudio()
  const { t } = useI18n()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [legacyBusy, setLegacyBusy] = useState(false)
  const [deleteProgressByProject, setDeleteProgressByProject] = useState<Record<string, DeleteProgress>>({})
  const [search, setSearch] = useState('')
  const projects = studio.bootstrap?.workspace.projects ?? []
  const legacyProjects = studio.bootstrap?.workspace.legacyProjects ?? []
  const importableLegacyProjects = legacyProjects.filter(project => !project.conflict)
  const accountKey = studio.bootstrap?.session.accountKey ?? ''
  const selected = projects.find(project => project.localProjectId === selectedId) ?? null
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    return query
      ? projects.filter(project => [
          projectDisplayName(project),
          projectRegistryId(project),
          project.name,
          project.path,
        ].some(value => value.toLowerCase().includes(query)))
      : projects
  }, [projects, search])

  useEffect(() => {
    if (!activeTask?.localProjectId) return
    if (projects.some(project => project.localProjectId === activeTask.localProjectId)) {
      setSelectedId(activeTask.localProjectId)
    }
  }, [activeTask?.localProjectId, projects])

  async function openFolder(project: WorkspaceProject) {
    try {
      await openWorkspaceFolder(project.localProjectId)
    } catch (cause) {
      window.alert(errorText(cause))
    }
  }

  async function removeProject(project: WorkspaceProject): Promise<boolean> {
    if (deleteProgressByProject[project.localProjectId]) return false
    const confirmed = window.confirm(
      t('Permanently delete this local Federated Task?\n\n{{path}}\n\nAny active Workspace action will be stopped first. This action cannot be undone.', { path: project.path }),
    )
    if (!confirmed) return false
    updateDeleteProgress(project.localProjectId, { stage: 'Checking actions…', percent: 10 })
    try {
      const runs = await listWorkspaceRuns(project.localProjectId)
      const activeRuns = runs.items.filter(run => run.status === 'queued' || run.status === 'running')
      if (activeRuns.length) {
        updateDeleteProgress(project.localProjectId, { stage: 'Stopping action…', percent: 30 })
        await stopWorkspaceRuns(activeRuns, progress => {
          updateDeleteProgress(project.localProjectId, { stage: 'Stopping action…', percent: progress })
        })
      }
      updateDeleteProgress(project.localProjectId, { stage: 'Deleting files…', percent: 75 })
      await deleteWorkspace(project.localProjectId, project.name)
      updateDeleteProgress(project.localProjectId, { stage: 'Refreshing…', percent: 95 })
      setSelectedId(current => current === project.localProjectId ? null : current)
      studio.forgetWorkspaceProject(project.localProjectId)
      onTaskDeleted(project.localProjectId)
      await studio.refresh()
      return true
    } catch (cause) {
      window.alert(errorText(cause))
      return false
    } finally {
      setDeleteProgressByProject(current => {
        const next = { ...current }
        delete next[project.localProjectId]
        return next
      })
    }
  }

  function updateDeleteProgress(localProjectId: string, progress: DeleteProgress) {
    setDeleteProgressByProject(current => ({ ...current, [localProjectId]: progress }))
  }

  async function importLegacyProjects() {
    const names = importableLegacyProjects.map(project => project.name)
    if (!names.length || legacyBusy) return
    const confirmed = window.confirm(
      t('Import {{count}} projects from the previous shared Workspace into the current FedOps account.\n\n{{names}}\n\nImported projects will not be visible to other accounts.', { count: names.length, names: names.join('\n') }),
    )
    if (!confirmed) return
    setLegacyBusy(true)
    try {
      await importLegacyWorkspaces(names)
      await studio.refresh()
    } catch (cause) {
      window.alert(errorText(cause))
    } finally {
      setLegacyBusy(false)
    }
  }

  if (selected) {
    return <ProjectWorkspace accountKey={accountKey} project={selected} deleteProgress={deleteProgressByProject[selected.localProjectId] ?? null} onBack={() => { setSelectedId(null); onClearTask() }} onRefresh={() => studio.refresh()} onOpenFolder={() => void openFolder(selected)} onDelete={() => void removeProject(selected)} onOpenFederation={() => {
      const binding = selected.taskBinding
      if (!binding) return
      onOpenFederation({
        registryId: binding.taskId,
        taskId: binding.taskId,
        title: binding.displayName,
        runtimeKey: binding.runtimeKey,
        ownerHandle: binding.ownerHandle,
        slug: binding.slug ?? null,
        role: null,
        localProjectId: selected.localProjectId,
      })
    }} />
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: C.bg }}>
      <header style={{ padding: '14px 20px 10px', borderBottom: `1px solid ${C.border}`, background: C.surface, flexShrink: 0 }}>
        <div className="studio-responsive-header" style={{ marginBottom: 10 }}>
          <div style={{ flex: 1 }}>
            <h1 style={{ margin: 0, fontFamily: 'Inter, sans-serif', fontSize: 17, color: C.text }}>{t('Local Federated Task Workspaces')}</h1>
            <div style={{ color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 13, marginTop: 2 }}>
              {t('Develop FedOps Federated Tasks in {{path}}.', { path: studio.bootstrap?.workspace.displayRoot ?? studio.bootstrap?.workspace.root ?? '—' })}
            </div>
          </div>
          <Button variant="ghost" onClick={() => void studio.refresh()}>↻ {t('Refresh')}</Button>
          <Button variant="primary" onClick={() => setCreateOpen(true)}>+ {t('New Federated Task')}</Button>
        </div>
        <div style={{ position: 'relative' }}>
          <input
            value={search}
            onChange={event => setSearch(event.target.value)}
            placeholder={t('Search by project name or path…')}
            style={{ width: '100%', boxSizing: 'border-box', background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 4, padding: '6px 10px 6px 28px', color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 13, outline: 'none' }}
          />
          <span style={{ position: 'absolute', left: 9, top: 6, color: C.dim }}>⌕</span>
        </div>
      </header>

      <main className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 14 }}>
        {legacyProjects.length > 0 && (
          <section style={{ marginBottom: 12, padding: '12px 14px', border: `1px solid ${C.yellowBorder}`, borderRadius: C.radius, background: C.yellowDim }}>
            <div className="studio-responsive-header" style={{ alignItems: 'center' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: C.text, fontSize: 13, fontWeight: 600 }}>{t('Previous Workspace projects found')}</div>
                <div style={{ color: C.muted, fontSize: 12, marginTop: 3 }}>
                  {t('Projects in the previous shared Workspace are not automatically linked to an account. Review the projects to import into the current account.')}
                </div>
                <div style={{ color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, marginTop: 7, overflowWrap: 'anywhere' }}>
                  {legacyProjects.map(project => `${project.name}${project.conflict ? ` (${t('name conflict')})` : ''}`).join(' · ')}
                </div>
              </div>
              <Button variant="primary" disabled={!importableLegacyProjects.length || legacyBusy} onClick={() => void importLegacyProjects()}>
                {legacyBusy ? t('Importing…') : t(importableLegacyProjects.length === 1 ? 'Import {{count}} project' : 'Import {{count}} projects', { count: importableLegacyProjects.length })}
              </Button>
            </div>
          </section>
        )}
        <div style={{ color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, marginBottom: 8 }}>
          {t('{{count}} projects', { count: filtered.length })} · {studio.bootstrap?.workspace.projectDiscovery ?? '—'}
        </div>
        {filtered.length === 0 ? (
          <div style={emptyStyle}>{t(search ? 'No search results.' : 'No local Federated Task Workspaces were found.')}</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {filtered.map(project => <ProjectCard key={project.localProjectId} project={project} deleteProgress={deleteProgressByProject[project.localProjectId] ?? null} onOpen={() => setSelectedId(project.localProjectId)} onOpenFolder={() => void openFolder(project)} onDelete={() => void removeProject(project)} />)}
          </div>
        )}
      </main>
      {createOpen && (
        <NewTaskDialog
          workspaceRoot={studio.bootstrap?.workspace.displayRoot ?? studio.bootstrap?.workspace.root ?? '/workspace'}
          projects={projects}
          onClose={() => setCreateOpen(false)}
          onCreated={async localProjectId => {
            await studio.refresh()
            setCreateOpen(false)
            setSelectedId(localProjectId)
          }}
        />
      )}
    </div>
  )
}

async function stopWorkspaceRuns(activeRuns: WorkspaceRun[], onProgress: (percent: number) => void): Promise<void> {
  for (const run of activeRuns) {
    try {
      await cancelWorkspaceRun(run.runId)
    } catch (cause) {
      const current = await getWorkspaceRun(run.runId)
      if (current.status === 'queued' || current.status === 'running') throw cause
    }
  }

  const pending = new Set(activeRuns.map(run => run.runId))
  for (let attempt = 0; pending.size && attempt < 40; attempt += 1) {
    const states = await Promise.all([...pending].map(runId => getWorkspaceRun(runId)))
    states.forEach(run => {
      if (run.status !== 'queued' && run.status !== 'running') pending.delete(run.runId)
    })
    if (!pending.size) return
    onProgress(Math.min(65, 35 + Math.round((attempt + 1) / 40 * 30)))
    await new Promise(resolve => window.setTimeout(resolve, 250))
  }
  throw new Error('The active Workspace action did not stop in time. Try deleting this Task again in a moment.')
}

function DeleteProjectButton({ progress, idleLabel, onClick }: { progress: DeleteProgress | null; idleLabel: 'Delete' | 'Delete Task'; onClick: () => void }) {
  const { t } = useI18n()
  return (
    <Button
      variant="danger"
      disabled={Boolean(progress)}
      onClick={onClick}
      style={{ position: 'relative', minWidth: progress ? 174 : undefined, overflow: 'hidden', cursor: progress ? 'wait' : 'pointer' }}
    >
      {progress ? (
        <>
          <span style={{ position: 'relative', zIndex: 1, display: 'flex', justifyContent: 'space-between', gap: 10 }}>
            <span>{t(progress.stage)}</span>
            <span style={{ fontFamily: 'JetBrains Mono, monospace' }}>{progress.percent}%</span>
          </span>
          <span
            aria-hidden="true"
            style={{ position: 'absolute', left: 0, bottom: 0, height: 3, width: `${progress.percent}%`, background: C.red, opacity: 0.65, transition: 'width 180ms ease' }}
          />
        </>
      ) : t(idleLabel)}
    </Button>
  )
}

function ProjectCard({ project, deleteProgress, onOpen, onOpenFolder, onDelete }: { project: WorkspaceProject; deleteProgress: DeleteProgress | null; onOpen: () => void; onOpenFolder: () => void; onDelete: () => void }) {
  const { t } = useI18n()
  const [hovered, setHovered] = useState(false)
  const displayName = projectDisplayName(project)
  const registryId = projectRegistryId(project)
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{ width: '100%', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, boxSizing: 'border-box', background: hovered ? C.surface2 : C.surface, borderTop: `1px solid ${hovered ? C.accentBorder : C.border}`, borderRight: `1px solid ${hovered ? C.accentBorder : C.border}`, borderBottom: `1px solid ${hovered ? C.accentBorder : C.border}`, borderLeft: `3px solid ${project.hasFedOpsTask ? C.green : C.yellow}`, borderRadius: C.radius, padding: '8px 10px 8px 14px' }}
    >
      <button onClick={onOpen} style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 10, padding: '2px 0', border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span style={{ color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 13, fontWeight: 600 }}>{displayName}</span>
            <Badge variant="purple">Federated Task</Badge>
          </div>
          <div style={{ color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, marginTop: 5 }}>
            {registryId ? `${registryId} · ` : ''}{project.path}
          </div>
        </div>
        <Badge variant={project.hasFedOpsTask ? 'green' : 'yellow'}>{t(project.hasFedOpsTask ? 'FedOps Task' : 'Previous format')}</Badge>
        <Badge variant={project.projectEnvironmentReady ? 'blue' : 'gray'}>{t(project.projectEnvironmentReady ? 'Environment ready' : 'Sync required')}</Badge>
        <span style={{ color: C.accent, fontSize: 12 }}>{t('Open in Workspace →')}</span>
      </button>
      <Button variant="ghost" onClick={onOpenFolder}>{t('Open folder')}</Button>
      <DeleteProjectButton progress={deleteProgress} idleLabel="Delete" onClick={onDelete} />
    </div>
  )
}

function ProjectWorkspace({ accountKey, project, deleteProgress, onBack, onRefresh, onOpenFolder, onDelete, onOpenFederation }: { accountKey: string; project: WorkspaceProject; deleteProgress: DeleteProgress | null; onBack: () => void; onRefresh: () => Promise<void>; onOpenFolder: () => void; onDelete: () => void; onOpenFederation: () => void }) {
  const { t } = useI18n()
  const [view, setView] = useState<WorkspaceView>('Overview')
  const environments = usePythonEnvironments('federated-task', project.localProjectId)
  const [tree, setTree] = useState<WorkspaceFileTreeNode | null>(null)
  const [treeError, setTreeError] = useState<string | null>(null)
  const [treeLoading, setTreeLoading] = useState(false)
  const [openFiles, setOpenFiles] = useState<Record<string, OpenFile>>({})
  const [tabs, setTabs] = useState<string[]>([])
  const [activePath, setActivePath] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [actionError, setActionError] = useState<string | null>(null)
  const [deletingPath, setDeletingPath] = useState<string | null>(null)
  const activeFile = openFiles[activePath]
  const dirtyCount = Object.values(openFiles).filter(file => file.dirty).length

  useEffect(() => {
    if (environments.run?.status === 'succeeded') void onRefresh()
  }, [environments.run?.runId, environments.run?.status])

  async function loadTree() {
    setTreeLoading(true)
    setTreeError(null)
    try {
      const result = await getWorkspaceFileTree(project.localProjectId)
      setTree(result.tree)
      setExpanded(new Set([
        ...(result.tree.children?.filter(node => node.type === 'directory').map(node => node.path) ?? []),
        'federated_task/local_training',
        'federated_task/tool_ai',
      ]))
    } catch (cause) {
      setTreeError(errorText(cause))
    } finally {
      setTreeLoading(false)
    }
  }

  useEffect(() => { void loadTree() }, [project.localProjectId])

  async function openFile(path: string) {
    setView('Code')
    setActionError(null)
    if (openFiles[path]) {
      setActivePath(path)
      return
    }
    try {
      const file = await readWorkspaceFile(project.localProjectId, path)
      setOpenFiles(previous => ({ ...previous, [path]: { ...file, draft: file.content, dirty: false } }))
      setTabs(previous => previous.includes(path) ? previous : [...previous, path])
      setActivePath(path)
    } catch (cause) {
      setActionError(errorText(cause))
    }
  }

  function closeFile(path: string) {
    if (openFiles[path]?.dirty && !window.confirm(t('Close the unsaved changes in {{path}}?', { path }))) return
    const nextTabs = tabs.filter(item => item !== path)
    setTabs(nextTabs)
    setOpenFiles(previous => {
      const next = { ...previous }
      delete next[path]
      return next
    })
    if (activePath === path) setActivePath(nextTabs[nextTabs.length - 1] ?? '')
  }

  function updateDraft(content: string) {
    setSaveState('idle')
    setOpenFiles(previous => {
      // Monaco may emit a final change while a deleted model is being disposed.
      // Read the current state again instead of relying on the render-time file.
      const current = previous[activePath]
      if (!current || current.readOnly) return previous
      return {
        ...previous,
        [activePath]: { ...current, draft: content, dirty: content !== current.content },
      }
    })
  }

  async function saveActive(): Promise<boolean> {
    if (!activeFile) return false
    if (activeFile.readOnly) return !activeFile.dirty
    if (!activeFile.dirty) return true
    setSaveState('saving')
    setActionError(null)
    try {
      const saved = await saveWorkspaceFile(project.localProjectId, activePath, activeFile.draft)
      setOpenFiles(previous => ({ ...previous, [activePath]: { ...saved, draft: saved.content, dirty: false } }))
      setSaveState('saved')
      return true
    } catch (cause) {
      setSaveState('error')
      setActionError(errorText(cause))
      return false
    }
  }

  async function createFile() {
    const activeParent = activePath.includes('/') ? activePath.slice(0, activePath.lastIndexOf('/') + 1) : ''
    const parent = (!activeFile || activeFile.readOnly) && project.hasFedOpsTask
      ? 'federated_task/local_training/'
      : activeParent
    const filePath = window.prompt(
      t('Enter a project-relative path for the new file.\nYou can create it inside an existing directory.'),
      `${parent}untitled.py`,
    )?.trim()
    if (!filePath) return
    setActionError(null)
    try {
      const file = await createWorkspaceFile(project.localProjectId, filePath)
      setOpenFiles(previous => ({ ...previous, [file.path]: { ...file, draft: file.content, dirty: false } }))
      setTabs(previous => previous.includes(file.path) ? previous : [...previous, file.path])
      setActivePath(file.path)
      setView('Code')
      await loadTree()
    } catch (cause) {
      setActionError(errorText(cause))
    }
  }

  async function deleteActiveFile() {
    if (!activeFile || activeFile.readOnly || deletingPath) return
    const warning = activeFile.dirty ? t('\nUnsaved changes will also be lost.') : ''
    if (!window.confirm(t('Permanently delete {{path}}?{{warning}}', { path: activeFile.path, warning }))) return
    const deletedPath = activeFile.path
    setActionError(null)
    setDeletingPath(deletedPath)
    try {
      await deleteWorkspaceFile(project.localProjectId, deletedPath)
      setTabs(previous => previous.filter(path => path !== deletedPath))
      setOpenFiles(previous => {
        const next = { ...previous }
        delete next[deletedPath]
        return next
      })
      setActivePath(previous => previous === deletedPath ? '' : previous)
      await loadTree()
    } catch (cause) {
      setActionError(errorText(cause))
    } finally {
      setDeletingPath(null)
    }
  }

  useEffect(() => {
    if (activePath && openFiles[activePath]) return
    const fallback = [...tabs].reverse().find(path => openFiles[path]) ?? ''
    if (fallback !== activePath) setActivePath(fallback)
  }, [activePath, openFiles, tabs])

  async function formatActiveFile(): Promise<boolean> {
    if (!activeFile || activeFile.readOnly) return false
    if (!await saveActive()) return false
    setActionError(null)
    try {
      const formatted = await formatWorkspaceFile(project.localProjectId, activeFile.path)
      setOpenFiles(previous => ({ ...previous, [formatted.path]: { ...formatted, draft: formatted.content, dirty: false } }))
      setSaveState('saved')
      return true
    } catch (cause) {
      setActionError(errorText(cause))
      return false
    }
  }

  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        void saveActive()
      }
    }
    window.addEventListener('keydown', keydown)
    return () => window.removeEventListener('keydown', keydown)
  }, [activePath, activeFile?.draft, activeFile?.dirty])

  return (
    <div style={{ display: 'flex', flex: 1, flexDirection: 'column', overflow: 'hidden', background: C.bg }}>
      <header className="workspace-project-header">
        <div className="workspace-project-header__identity">
          <button onClick={onBack} style={backButton}>‹</button>
          <div className="workspace-project-header__divider" />
          <div className="workspace-project-header__summary">
            <div className="workspace-project-header__labels">
              <span className="workspace-project-header__title" title={projectDisplayName(project)}>{projectDisplayName(project)}</span>
              <span className="workspace-project-header__registry-id" title={projectRegistryId(project) || project.name}>{projectRegistryId(project) || project.name}</span>
              <Badge variant="purple">Federated Task</Badge>
              <Badge variant={project.hasFedOpsTask ? 'green' : 'yellow'}>{t(project.hasFedOpsTask ? 'FedOps Task' : 'Previous format')}</Badge>
              {dirtyCount > 0 && <Badge variant="yellow">{t('{{count}} modified', { count: dirtyCount })}</Badge>}
            </div>
            <div className="workspace-project-header__path" title={project.path}>{project.path}</div>
          </div>
        </div>
        <div className="workspace-project-header__actions">
          <select
            aria-label={t('Selected Python environment')}
            value={environments.selected?.environmentId ?? ''}
            disabled={environments.loading || !environments.data?.items.length}
            onChange={event => void environments.select(event.target.value)}
            className="workspace-project-header__environment"
          >
            {!environments.selected && <option value="">{t('Python environment')}</option>}
            {environments.data?.items.map(item => <option key={item.environmentId} value={item.environmentId}>{item.name} · Python {item.pythonVersion} · {t(item.status)}</option>)}
          </select>
          <Button variant="ghost" onClick={onOpenFolder}>{t('Open folder')}</Button>
          {project.taskBinding?.registryStatus === 'published' && <Button variant="success" onClick={onOpenFederation}>{t('Open Federated Learning')}</Button>}
          <DeleteProjectButton progress={deleteProgress} idleLabel="Delete Task" onClick={onDelete} />
          <Button variant="ghost" onClick={() => void loadTree()}>↻ {t('Explorer')}</Button>
          <Button variant="ghost" disabled={!activeFile?.dirty || activeFile.readOnly || saveState === 'saving'} onClick={() => void saveActive()}>{t(saveState === 'saving' ? 'Saving…' : 'Save')}</Button>
        </div>
      </header>

      <nav className="workspace-project-tabs">
        {(['Overview', 'Code', 'Project Setup', 'Python Environments', 'Task Test'] as WorkspaceView[]).map(item => (
          <button key={item} onClick={() => setView(item)} style={{ flexShrink: 0, padding: '7px 12px', background: 'transparent', borderTop: 'none', borderLeft: 'none', borderRight: 'none', borderBottom: `2px solid ${view === item ? C.accent : 'transparent'}`, color: view === item ? C.text : C.muted, cursor: 'pointer' }}>{t(item)}</button>
        ))}
      </nav>

      {actionError && <div style={errorStyle}>{actionError}</div>}
      {view === 'Overview' && (
        <WorkspaceOverview project={project} environment={environments.selected} tree={tree} treeLoading={treeLoading} treeError={treeError} onOpenCode={() => setView('Code')} onOpenEnvironment={() => setView('Python Environments')} onOpenValidation={() => setView('Task Test')} onRefresh={onRefresh} />
      )}
      {view === 'Code' && (
        <CodeWorkspace
          accountKey={accountKey}
          project={project}
          environment={environments.selected}
          uv={environments.data?.uv ?? null}
          tree={tree}
          treeLoading={treeLoading}
          treeError={treeError}
          expanded={expanded}
          setExpanded={setExpanded}
          tabs={tabs}
          activePath={activePath}
          activeFile={activeFile}
          readOnlyPaths={new Set(Object.values(openFiles).filter(file => file.readOnly).map(file => file.path))}
          deletingPath={deletingPath}
          setActivePath={setActivePath}
          onOpenFile={path => void openFile(path)}
          onCloseFile={closeFile}
          onChange={updateDraft}
          onSave={saveActive}
          onCreateFile={() => void createFile()}
          onDeleteFile={() => void deleteActiveFile()}
          onFormatFile={formatActiveFile}
          saveState={saveState}
        />
      )}
      {view === 'Project Setup' && <ProjectSetupView project={project} onOpenFile={path => { void openFile(path); setView('Code') }} />}
      {view === 'Python Environments' && <PythonEnvironmentPanel controller={environments} />}
      {view === 'Task Test' && (
        <ValidationView
          project={project}
          environment={environments.selected}
          onWorkspaceChanged={async () => {
            await loadTree()
            await onRefresh()
          }}
        />
      )}
    </div>
  )
}

function WorkspaceOverview({ project, environment, tree, treeLoading, treeError, onOpenCode, onOpenEnvironment, onOpenValidation, onRefresh }: { project: WorkspaceProject; environment: PythonEnvironment | null; tree: WorkspaceFileTreeNode | null; treeLoading: boolean; treeError: string | null; onOpenCode: () => void; onOpenEnvironment: () => void; onOpenValidation: () => void; onRefresh: () => Promise<void> }) {
  const { t } = useI18n()
  const fileCount = tree ? countFiles(tree) : 0
  const [tasks, setTasks] = useState<AccountTask[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState('')
  const [linking, setLinking] = useState(false)
  const [linkError, setLinkError] = useState('')

  useEffect(() => {
    if (project.taskBinding) return
    listAccountTasks().then(result => setTasks(result.items)).catch(cause => setLinkError(errorText(cause)))
  }, [project.localProjectId, project.taskBinding?.taskId])

  async function linkDraft() {
    if (!selectedTaskId || linking) return
    setLinking(true)
    setLinkError('')
    try {
      await linkWorkspaceTask(project.localProjectId, selectedTaskId)
      await onRefresh()
    } catch (cause) {
      setLinkError(errorText(cause))
    } finally {
      setLinking(false)
    }
  }
  return (
    <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, maxWidth: 1050 }}>
        <OverviewSection title={t('Task Identity')}>
          <MetaRow label={t('Federated Task')} value={projectDisplayName(project)} />
          <MetaRow label={t('Registry ID')} value={projectRegistryId(project) || '—'} mono />
          <MetaRow label={t('Local folder')} value={project.name} mono />
          <MetaRow label={t('Path')} value={project.path} mono />
          <MetaRow label={t('Task contract')} value={project.hasFedOpsTask ? '[tool.fedops.task]' : t('Previous format')} mono />
          <MetaRow label={t('Web Draft')} value={project.taskBinding ? t('Linked') : t('Not linked')} />
          {project.taskBinding ? (
            <MetaRow label={t('Registry state')} value={project.taskBinding.registryStatus} mono />
          ) : (
            <div style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', gap: 8 }}>
                <select value={selectedTaskId} onChange={event => setSelectedTaskId(event.target.value)} style={{ ...fieldInput, flex: 1 }}>
                  <option value="">{t('Select an owned Web Draft')}</option>
                  {tasks.map(task => <option key={task.taskId} value={task.taskId}>{task.displayName} · {task.taskId}</option>)}
                </select>
                <Button variant="primary" disabled={!selectedTaskId || linking} onClick={() => void linkDraft()}>{t(linking ? 'Linking…' : 'Link Web Draft')}</Button>
              </div>
              {linkError && <div style={{ ...errorStyle, marginTop: 8 }}>{linkError}</div>}
            </div>
          )}
        </OverviewSection>
        <OverviewSection title={t('Workspace Readiness')}>
          <MetaRow label="pyproject.toml" value={t(project.hasPyproject ? 'Detected' : 'Missing')} />
          <MetaRow label={t('Selected environment')} value={environment ? `${environment.name} · Python ${environment.pythonVersion}` : t('Loading')} />
          <MetaRow label={t('Environment status')} value={t(environment?.status ?? 'Unknown')} />
          <MetaRow label={t('Explorer')} value={t(treeLoading ? 'Loading' : treeError ? 'Unavailable' : 'Ready')} />
          <MetaRow label={t('Visible files')} value={String(fileCount)} />
        </OverviewSection>
        <div style={{ gridColumn: '1 / -1' }}>
          <OverviewSection title={t('Quick Actions')}>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="primary" onClick={onOpenCode}>{t('Open Code')}</Button>
              <Button variant="ghost" onClick={onOpenEnvironment}>{t('Python Environments')}</Button>
              <Button variant="ghost" onClick={onOpenValidation}>{t('Task Test')}</Button>
            </div>
          </OverviewSection>
        </div>
      </div>
    </div>
  )
}

function NewTaskDialog({ workspaceRoot, projects, onClose, onCreated }: {
  workspaceRoot: string
  projects: WorkspaceProject[]
  onClose: () => void
  onCreated: (localProjectId: string) => Promise<void>
}) {
  const { t } = useI18n()
  const [sourceMode, setSourceMode] = useState<'web-draft' | 'joined-task' | 'local'>('web-draft')
  const [name, setName] = useState('')
  const [run, setRun] = useState<WorkspaceRun | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tasks, setTasks] = useState<AccountTask[]>([])
  const [joinedTasks, setJoinedTasks] = useState<RegistryTask[]>([])
  const [sourceTaskId, setSourceTaskId] = useState('')
  const [joinedTaskId, setJoinedTaskId] = useState('')
  const completedRef = useRef(false)
  const active = run?.status === 'queued' || run?.status === 'running'
  const compatibleTasks = tasks.filter(task => task.runtimeContract.name !== 'legacy-v1')
  const compatibleJoinedTasks = joinedTasks.filter(task => (
    task.taskId
    && task.registryStatus === 'published'
    && task.runtimeContract.name !== 'legacy-v1'
    && task.permissions.canOpenWorkspace
  ))
  const selectedTask = compatibleTasks.find(task => task.taskId === sourceTaskId) ?? null
  const selectedJoinedTask = compatibleJoinedTasks.find(task => task.taskId === joinedTaskId) ?? null
  const existingJoinedProject = selectedJoinedTask
    ? projects.find(project => project.taskBinding?.taskId === selectedJoinedTask.taskId) ?? null
    : null
  const preview = workspaceNamePreview(
    sourceMode === 'web-draft'
      ? selectedTask?.slug || selectedTask?.displayName || ''
      : sourceMode === 'joined-task'
        ? existingJoinedProject?.name || selectedJoinedTask?.slug || selectedJoinedTask?.displayName || ''
      : name,
  )
  const canSubmit = sourceMode === 'web-draft'
    ? Boolean(sourceTaskId)
    : sourceMode === 'joined-task'
      ? Boolean(joinedTaskId)
      : Boolean(name.trim())

  useEffect(() => {
    Promise.allSettled([listAccountTasks(), listRegistryTasks('joined')]).then(([owned, joined]) => {
      const ownedItems = owned.status === 'fulfilled' ? owned.value.items : []
      const joinedItems = joined.status === 'fulfilled' ? joined.value.items : []
      const hasDraft = ownedItems.some(task => task.runtimeContract.name !== 'legacy-v1')
      const hasJoined = joinedItems.some(task => (
        task.taskId
        && task.registryStatus === 'published'
        && task.runtimeContract.name !== 'legacy-v1'
        && task.permissions.canOpenWorkspace
      ))
      setTasks(ownedItems)
      setJoinedTasks(joinedItems)
      setSourceMode(hasDraft ? 'web-draft' : hasJoined ? 'joined-task' : 'local')
    })
  }, [])

  useEffect(() => {
    if (!run || !active) return
    const timer = window.setInterval(() => {
      getWorkspaceRun(run.runId)
        .then(setRun)
        .catch(cause => setError(errorText(cause)))
    }, 700)
    return () => window.clearInterval(timer)
  }, [run?.runId, run?.status])

  useEffect(() => {
    if (run?.status !== 'succeeded' || !run.resultLocalProjectId || completedRef.current) return
    completedRef.current = true
    void onCreated(run.resultLocalProjectId)
  }, [run?.status, run?.resultLocalProjectId])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!canSubmit || active) return
    setError(null)
    completedRef.current = false
    try {
      setRun(sourceMode === 'joined-task' && selectedJoinedTask
        ? await openPublishedRelease(selectedJoinedTask)
        : await createWorkspace(
          sourceMode === 'web-draft' ? { sourceTaskId } : { name: name.trim() },
        ))
    } catch (cause) {
      setError(errorText(cause))
    }
  }

  return (
    <div style={dialogBackdrop} role="presentation" onMouseDown={event => { if (event.target === event.currentTarget && !active) onClose() }}>
      <form onSubmit={submit} style={dialogPanel} role="dialog" aria-modal="true" aria-labelledby="new-task-title">
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, borderBottom: `1px solid ${C.border}`, padding: '15px 17px 13px' }}>
          <div style={{ flex: 1 }}>
            <h2 id="new-task-title" style={{ margin: 0, color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 16 }}>{t('Start a Federated Task Workspace')}</h2>
            <div style={{ marginTop: 4, color: C.muted, fontSize: 12 }}>{t('Open your Web Draft, an approved Registry Task, or start a local project.')}</div>
          </div>
          <button type="button" disabled={active} onClick={onClose} style={backButton}>×</button>
        </div>
        <div style={{ padding: 17 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginBottom: 14 }}>
            <Button type="button" variant={sourceMode === 'web-draft' ? 'primary' : 'ghost'} disabled={active || run?.status === 'succeeded' || compatibleTasks.length === 0} onClick={() => setSourceMode('web-draft')}>
              {t('My Web Draft')}
            </Button>
            <Button type="button" variant={sourceMode === 'joined-task' ? 'primary' : 'ghost'} disabled={active || run?.status === 'succeeded' || compatibleJoinedTasks.length === 0} onClick={() => setSourceMode('joined-task')}>
              {t('Joined Registry Task')}
            </Button>
            <Button type="button" variant={sourceMode === 'local' ? 'primary' : 'ghost'} disabled={active || run?.status === 'succeeded'} onClick={() => setSourceMode('local')}>
              {t('Start Local Project')}
            </Button>
          </div>

          {sourceMode === 'web-draft' ? (
            <>
              <label style={fieldLabel} htmlFor="workspace-web-draft">{t('FedOps Web Draft')}</label>
              <select id="workspace-web-draft" autoFocus value={sourceTaskId} disabled={active || run?.status === 'succeeded'} onChange={event => setSourceTaskId(event.target.value)} style={fieldInput}>
                <option value="">{t('Select an owned Web Draft')}</option>
                {compatibleTasks.map(task => <option key={task.taskId} value={task.taskId}>{task.displayName} · {accountTaskRegistryId(task)}</option>)}
              </select>
              {selectedTask && (
                <div style={{ marginTop: 10, padding: '10px 11px', background: C.surface2, border: `1px solid ${C.borderSubtle}`, borderRadius: C.radius }}>
                  <MetaRow label={t('Federated Task')} value={selectedTask.displayName} />
                  <MetaRow label={t('Registry ID')} value={accountTaskRegistryId(selectedTask)} mono />
                  <MetaRow label={t('Primary Model')} value={selectedTask.primaryModel?.displayName || selectedTask.primaryModel?.workingName || t('Finalize in config.yaml')} />
                  <MetaRow label={t('Registry state')} value={selectedTask.registryStatus} mono />
                </div>
              )}
              <div style={{ marginTop: 9, color: C.muted, fontSize: 12, lineHeight: 1.5 }}>
                {t('The Federated Task name comes from FedOps Web. Agent Studio only creates its local implementation Workspace.')}
              </div>
            </>
          ) : sourceMode === 'joined-task' ? (
            <>
              <label style={fieldLabel} htmlFor="workspace-joined-task">{t('Approved Registry Task')}</label>
              <select id="workspace-joined-task" autoFocus value={joinedTaskId} disabled={active || run?.status === 'succeeded'} onChange={event => setJoinedTaskId(event.target.value)} style={fieldInput}>
                <option value="">{t('Select a joined Published Task')}</option>
                {compatibleJoinedTasks.map(task => (
                  <option key={task.taskId!} value={task.taskId!}>
                    {task.displayName} · {task.ownerHandle ?? t('unknown')}/{task.slug ?? task.runtimeKey ?? task.taskId}
                  </option>
                ))}
              </select>
              {selectedJoinedTask && (
                <div style={{ marginTop: 10, padding: '10px 11px', background: C.surface2, border: `1px solid ${C.borderSubtle}`, borderRadius: C.radius }}>
                  <MetaRow label={t('Federated Task')} value={selectedJoinedTask.displayName} />
                  <MetaRow label={t('Primary Model')} value={selectedJoinedTask.primaryModel?.displayName || selectedJoinedTask.primaryModel?.workingName || '—'} />
                  <MetaRow label={t('Participation status')} value={selectedJoinedTask.permissions.participationStatus || selectedJoinedTask.membership?.status || '—'} mono />
                  <MetaRow label={t('Local Workspace')} value={existingJoinedProject ? t('Already available · it will be reopened') : t('Not downloaded yet')} />
                </div>
              )}
              <div style={{ marginTop: 9, color: C.muted, fontSize: 12, lineHeight: 1.5 }}>
                {t('Agent Studio verifies the Published Release and model once. Opening the same taskId again reuses the existing Workspace.')}
              </div>
            </>
          ) : (
            <>
              <label style={fieldLabel} htmlFor="workspace-project-name">{t('Local Project Name')}</label>
              <input
                id="workspace-project-name"
                autoFocus
                maxLength={64}
                disabled={active || run?.status === 'succeeded'}
                value={name}
                onChange={event => setName(event.target.value)}
                placeholder={t('For example: ECG Classification')}
                style={fieldInput}
              />
              <div style={{ marginTop: 9, color: C.muted, fontSize: 12, lineHeight: 1.5 }}>
                {t('This name identifies only the local Workspace. Link the project to a FedOps Web Draft when it is ready to become a published Federated Task.')}
              </div>
            </>
          )}
          <div style={{ marginTop: 10, padding: '9px 11px', background: C.bg, border: `1px solid ${C.borderSubtle}`, borderRadius: 4 }}>
            <div style={{ color: C.dim, fontSize: 10, textTransform: 'uppercase', letterSpacing: '.08em' }}>{t('Workspace path')}</div>
            <div style={{ color: C.muted, fontFamily: 'JetBrains Mono, monospace', fontSize: 12, marginTop: 4 }}>{workspaceRoot}/{preview || (sourceMode === 'web-draft' ? t('select-a-draft') : sourceMode === 'joined-task' ? t('select-a-registry-task') : t('local-project'))}</div>
          </div>
          {error && <div style={{ ...errorStyle, marginTop: 12, border: `1px solid ${C.redBorder}` }}>{error}</div>}
          {run && (
            <div style={{ marginTop: 12 }}>
              <RunSummary run={run} />
              <RunOutput run={run} compact />
            </div>
          )}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '11px 17px', borderTop: `1px solid ${C.border}` }}>
          <Button variant="ghost" disabled={active} onClick={onClose}>{t('Cancel')}</Button>
          <Button type="submit" variant="primary" disabled={!canSubmit || active || run?.status === 'succeeded'}>
            {t(active
              ? (sourceMode === 'local' ? 'Creating…' : 'Opening…')
              : run?.status === 'failed'
                ? 'Retry'
                : sourceMode === 'web-draft'
                  ? 'Open Draft in Workspace'
                  : sourceMode === 'joined-task'
                    ? 'Open Registry Task in Workspace'
                    : 'Create Local Project')}
          </Button>
        </div>
      </form>
    </div>
  )
}

function ProjectSetupView({ project, onOpenFile }: { project: WorkspaceProject; onOpenFile: (path: string) => void }) {
  const { t } = useI18n()
  const binding = project.taskBinding
  const primaryModel = binding?.primaryModel
  const steps = [
    { done: Boolean(binding), label: t('Link a FedOps Web Draft') },
    { done: Boolean(primaryModel?.displayName || primaryModel?.workingName), label: t('Define the Primary Model identity') },
    { done: project.hasFedOpsTask, label: t('Implement the local model and data contract') },
    { done: project.projectEnvironmentReady, label: t('Synchronize a Python environment') },
  ]
  return (
    <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
      <div style={{ maxWidth: 980 }}>
        <OverviewSection title={t('Federated Task Project Setup')}>
          <p style={bodyText}>{t('Web Draft metadata, Workspace implementation, and runtime campaign settings are separate. Complete the model and data contract here; choose rounds and clients later in FedOps Web Server Management.')}</p>
          <MetaRow label={t('Federated Task')} value={binding?.displayName ?? t('Local-first project — link later')} />
          <MetaRow label={t('Registry ID')} value={binding ? projectRegistryId(project) : '—'} mono />
          <MetaRow label={t('Primary Model')} value={primaryModel?.displayName ?? primaryModel?.workingName ?? t('Finalize in config.yaml')} />
          <MetaRow label={t('Category / modality')} value={[binding?.taskCategory, binding?.dataModality].filter(Boolean).join(' / ') || '—'} />
        </OverviewSection>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10, marginBottom: 10 }}>
          <SetupCard
            title={t('Model & data contract')}
            description={t('Define what the model consumes, returns, trains, and exchanges during federation.')}
            files={[
              ['federated_task/conf/config.yaml', t('Model, dataset, and local training contract')],
              ['federated_task/local_training/model.py', t('Model definition')],
              ['federated_task/local_training/data_preparation.py', t('Local-only data loading and preprocessing')],
            ]}
            onOpenFile={onOpenFile}
          />
          <SetupCard
            title={t('Training & Tool AI')}
            description={t('Implement local training and expose the released model to Agent Builder as a Tool AI.')}
            files={[
              ['federated_task/local_training/training.py', t('Local training and model export')],
              ['federated_task/tool_ai/manifest.json', t('Tool AI input and output contract')],
              ['README.md', t('Registry Task Card and user guide')],
            ]}
            onOpenFile={onOpenFile}
          />
          <SetupCard
            title={t('FedOps execution contract')}
            description={t('These files connect the project to FedOps. Most users review them but do not need to edit them.')}
            files={[
              ['requirements.txt', t('Editable Task library versions')],
              ['pyproject.toml', t('Fixed Python and FedOps Task contract')],
              ['federated_task/federated_learning/client_main.py', t('Federated client entry point')],
            ]}
            onOpenFile={onOpenFile}
          />
          <section style={{ border: `1px solid ${C.border}`, background: C.surface, borderRadius: C.radius, padding: 14 }}>
            <h3 style={{ margin: 0, color: C.text, fontSize: 13 }}>{t('Release path')}</h3>
            <div style={{ marginTop: 7, color: C.muted, fontSize: 12, lineHeight: 1.55 }}>{t('The Registry receives one immutable Release snapshot only after every check passes.')}</div>
            <div style={{ display: 'grid', gap: 7, marginTop: 12 }}>
              {steps.map(step => <div key={step.label} style={{ display: 'flex', gap: 8, alignItems: 'center', color: step.done ? C.green : C.muted, fontSize: 12 }}><span>{step.done ? '●' : '○'}</span><span>{step.label}</span></div>)}
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', color: C.muted, fontSize: 12 }}><span>○</span><span>{t('Local Train → Release Readiness → Submit Candidate')}</span></div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', color: C.muted, fontSize: 12 }}><span>○</span><span>{t('Owner Publish in FedOps Web')}</span></div>
            </div>
          </section>
        </div>
        <div style={{ padding: '10px 12px', border: `1px solid ${C.accentBorder}`, background: C.accentDim, borderRadius: C.radius, color: C.muted, fontSize: 12, lineHeight: 1.55 }}>
          {t('Federated rounds, minimum clients, and aggregation strategy are Campaign settings. They are not baked into the reusable Release and are selected when the owner creates the aggregation server.')}
        </div>
      </div>
    </div>
  )
}

function SetupCard({ title, description, files, onOpenFile }: {
  title: string
  description: string
  files: [string, string][]
  onOpenFile: (path: string) => void
}) {
  return <section style={{ border: `1px solid ${C.border}`, background: C.surface, borderRadius: C.radius, padding: 14 }}>
    <h3 style={{ margin: 0, color: C.text, fontSize: 13 }}>{title}</h3>
    <div style={{ marginTop: 7, color: C.muted, fontSize: 12, lineHeight: 1.55 }}>{description}</div>
    <div style={{ display: 'grid', gap: 6, marginTop: 12 }}>
      {files.map(([path, label]) => <button key={path} type="button" onClick={() => onOpenFile(path)} style={{ display: 'flex', gap: 8, alignItems: 'center', width: '100%', padding: '7px 9px', border: `1px solid ${C.borderSubtle}`, borderRadius: 4, background: C.surface2, color: C.text, cursor: 'pointer', textAlign: 'left' }}><FileTypeIcon name={path} /><span style={{ minWidth: 0, flex: 1 }}><span style={{ display: 'block', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>{path}</span><span style={{ display: 'block', color: C.dim, fontSize: 10, marginTop: 2 }}>{label}</span></span></button>)}
    </div>
  </section>
}

function ValidationView({ project, environment, onWorkspaceChanged }: { project: WorkspaceProject; environment: PythonEnvironment | null; onWorkspaceChanged: () => Promise<void> }) {
  const { t } = useI18n()
  const workspaceRole = project.taskBinding?.workspaceRole
    ?? (project.taskBinding?.releaseId ? 'participant' : 'owner')
  const participantWorkspace = workspaceRole === 'participant'
  return (
    <WorkspaceActionView
      project={project}
      environment={environment}
      title={t('FedOps Task Test')}
      description={t(participantWorkspace
        ? 'Train only with local data, then verify local data loading, parameter update serialization, and compatibility with the exact Published Task.'
        : 'Train with local-only data, then verify source, Initial Model, parameter round-trip, Tool inference, and README before submitting one immutable Release Candidate.')}
      actions={participantWorkspace ? [
        { action: 'local-train', label: t('Local Train'), disabled: environment?.status !== 'ready' || !project.hasFedOpsTask },
        { action: 'participation-readiness', label: t('Check Participation Readiness'), primary: true, disabled: environment?.status !== 'ready' || !project.hasFedOpsTask },
      ] : [
        { action: 'local-train', label: t('Local Train & Export Model'), disabled: environment?.status !== 'ready' || !project.hasFedOpsTask },
        { action: 'release-readiness', label: t('Check Release Readiness'), primary: true, disabled: environment?.status !== 'ready' || !project.hasFedOpsTask },
        { action: 'participation-readiness', label: t('Check Participation Readiness'), disabled: environment?.status !== 'ready' || !project.hasFedOpsTask },
      ]}
      onSucceeded={async run => {
        if (run.kind === 'local-train') await onWorkspaceChanged()
      }}
      onSubmitRelease={!participantWorkspace && project.taskBinding ? () => submitReleaseCandidate(project.localProjectId) : undefined}
      submissionRequired={!participantWorkspace}
    />
  )
}

interface ActionDefinition { action: WorkspaceAction; label: string; primary?: boolean; disabled?: boolean }

function WorkspaceActionView({ project, environment, title, description, actions, onSucceeded, onSubmitRelease, submissionRequired = false }: { project: WorkspaceProject; environment: PythonEnvironment | null; title: string; description: string; actions: ActionDefinition[]; onSucceeded?: (run: WorkspaceRun) => Promise<void>; onSubmitRelease?: () => Promise<WorkspaceRun>; submissionRequired?: boolean }) {
  const { t, formatDateTime } = useI18n()
  const [runs, setRuns] = useState<WorkspaceRun[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [releaseMessage, setReleaseMessage] = useState('')
  const [releaseSubmissionReadiness, setReleaseSubmissionReadiness] = useState<ReleaseSubmissionReadiness | null>(null)
  const [releaseSubmissionLoading, setReleaseSubmissionLoading] = useState(submissionRequired)
  const [dataBinding, setDataBinding] = useState<LocalDataBinding | null>(null)
  const [dataLoading, setDataLoading] = useState(true)
  const completedRef = useRef<Set<string>>(new Set())
  const selectedRun = runs.find(run => run.runId === selectedRunId) ?? runs[0] ?? null
  const activeRun = runs.find(run => run.status === 'queued' || run.status === 'running')
  const releaseReadinessRunKey = runs
    .filter(run => run.kind === 'release-readiness' || run.kind === 'local-train')
    .map(run => `${run.runId}:${run.status}`)
    .join('|')

  async function reload() {
    try {
      const result = await listWorkspaceRuns(project.localProjectId)
      setRuns(result.items)
      if (!selectedRunId && result.items[0]) setSelectedRunId(result.items[0].runId)
    } catch (cause) {
      setError(errorText(cause))
    }
  }

  useEffect(() => { void reload() }, [project.localProjectId])

  async function reloadReleaseSubmissionReadiness() {
    if (!submissionRequired || !project.taskBinding) {
      setReleaseSubmissionReadiness(null)
      setReleaseSubmissionLoading(false)
      return
    }
    setReleaseSubmissionLoading(true)
    try {
      setReleaseSubmissionReadiness(await getReleaseSubmissionReadiness(project.localProjectId))
    } catch (cause) {
      setReleaseSubmissionReadiness({ ready: false, reason: errorText(cause) })
    } finally {
      setReleaseSubmissionLoading(false)
    }
  }

  useEffect(() => {
    void reloadReleaseSubmissionReadiness()
  }, [project.localProjectId, project.taskBinding?.taskId, submissionRequired, releaseReadinessRunKey])

  async function reloadDataBinding() {
    setDataLoading(true)
    try {
      setDataBinding(await prepareWorkspaceDataBinding(project.localProjectId))
    } catch (cause) {
      setError(errorText(cause))
    } finally {
      setDataLoading(false)
    }
  }

  useEffect(() => { void reloadDataBinding() }, [project.localProjectId])

  useEffect(() => {
    if (!activeRun) return
    const timer = window.setInterval(() => void reload(), 700)
    return () => window.clearInterval(timer)
  }, [activeRun?.runId, activeRun?.status, project.localProjectId])

  useEffect(() => {
    const completed = runs.filter(run => run.status === 'succeeded' && !completedRef.current.has(run.runId))
    completed.forEach(run => {
      completedRef.current.add(run.runId)
      if (run.kind === 'release-candidate' && run.result) {
        setReleaseMessage(t('Release Candidate submitted: revision {{revision}} · {{status}} · {{releaseId}}', {
          revision: String(run.result.revision ?? '—'),
          status: String(run.result.status ?? 'ready'),
          releaseId: String(run.result.releaseId ?? '—'),
        }))
      }
      if (onSucceeded) void onSucceeded(run)
    })
  }, [runs])

  async function start(action: WorkspaceAction) {
    setError(null)
    if (action === 'local-train' || action === 'release-readiness') {
      setReleaseSubmissionReadiness({
        ready: false,
        reason: action === 'release-readiness'
          ? t('Release Readiness must finish successfully before submission.')
          : t('Run Release Readiness again after Local Train.'),
      })
    }
    try {
      let dataPath: string | undefined
      if (action === 'local-train' || action === 'participation-readiness') {
        const binding = dataBinding ?? await prepareWorkspaceDataBinding(project.localProjectId)
        setDataBinding(binding)
        dataPath = binding.containerPath
      }
      const next = await startWorkspaceAction(project.localProjectId, action, environment?.environmentId, undefined, dataPath)
      setRuns(previous => [next, ...previous.filter(run => run.runId !== next.runId)])
      setSelectedRunId(next.runId)
    } catch (cause) {
      setError(errorText(cause))
    }
  }

  async function openDataFolder() {
    setError(null)
    try {
      setDataBinding(await openWorkspaceDataFolder(project.localProjectId))
    } catch (cause) {
      setError(errorText(cause))
    }
  }

  async function submit() {
    if (!onSubmitRelease || activeRun) return
    setError(null)
    setReleaseMessage('')
    try {
      const next = await onSubmitRelease()
      setRuns(previous => [next, ...previous.filter(run => run.runId !== next.runId)])
      setSelectedRunId(next.runId)
    } catch (cause) {
      setError(errorText(cause))
    }
  }

  return (
    <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
      <div style={{ maxWidth: 1100 }}>
        <OverviewSection title={title}>
          <p style={bodyText}>{description}</p>
          <div style={{ marginBottom: 10 }}><MetaRow label={t('Python environment')} value={environment ? `${environment.name} · Python ${environment.pythonVersion} · ${t(environment.status)}` : t('No environment selected')} mono /></div>
          <div style={{ margin: '12px 0', padding: 12, border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.surface2 }}>
            <div className="studio-responsive-header" style={{ alignItems: 'center' }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <strong style={{ display: 'block', color: C.text, fontSize: 12 }}>{t('Local data setup')}</strong>
                <span style={{ display: 'block', marginTop: 3, color: C.muted, fontSize: 11, lineHeight: 1.5 }}>{t('Place this Task’s private dataset in the dedicated account-local folder before Local Train. It is never included in a Registry Release.')}</span>
              </div>
              <Badge variant={dataBinding?.hasEntries ? 'green' : 'yellow'}>{t(dataLoading ? 'Preparing…' : dataBinding?.hasEntries ? 'Data detected' : 'Folder ready')}</Badge>
              <Button variant="ghost" disabled={dataLoading} onClick={() => void openDataFolder()}>{t('Open Data Folder')}</Button>
              <Button variant="ghost" disabled={dataLoading} onClick={() => void reloadDataBinding()}>↻ {t('Refresh')}</Button>
            </div>
            <div style={{ display: 'grid', gap: 5, marginTop: 10 }}>
              <MetaRow label={t('Host folder')} value={dataBinding?.hostPath ?? t('Preparing…')} mono />
              <MetaRow label={t('Runtime path')} value={dataBinding?.containerPath ?? t('Preparing…')} mono />
            </div>
          </div>
          {submissionRequired && ['owner', 'admin'].includes(project.taskBinding?.workspaceRole ?? '') && (
            <ServerValidationData key={project.localProjectId} localProjectId={project.localProjectId} />
          )}
          <div className="studio-responsive-actions" style={{ justifyContent: 'flex-start', marginTop: 12 }}>
            {actions.map(item => (
              <Button key={item.action} variant={item.primary ? 'primary' : 'ghost'} disabled={Boolean(activeRun) || item.disabled || ((item.action === 'local-train' || item.action === 'participation-readiness') && dataLoading)} onClick={() => void start(item.action)}>
                {activeRun?.kind === item.action ? `${item.label}…` : item.label}
              </Button>
            ))}
            {submissionRequired && <Button variant="ghost" disabled={!onSubmitRelease || Boolean(activeRun) || releaseSubmissionLoading || releaseSubmissionReadiness?.ready !== true} onClick={() => void submit()}>{t(activeRun?.kind === 'release-candidate' ? 'Submitting…' : 'Submit Release Candidate')}</Button>}
          </div>
          {submissionRequired && !onSubmitRelease && <div style={{ color: C.yellow, fontSize: 12, marginTop: 10 }}>{t('Link this Workspace to an owned Web Draft before submitting a Release Candidate.')}</div>}
          {submissionRequired && onSubmitRelease && releaseSubmissionLoading && <div style={{ color: C.muted, fontSize: 12, marginTop: 10 }}>{t('Checking Release Readiness…')}</div>}
          {submissionRequired && onSubmitRelease && !releaseSubmissionLoading && releaseSubmissionReadiness?.ready !== true && <div style={{ color: C.yellow, fontSize: 12, marginTop: 10 }}>{t(releaseSubmissionReadiness?.reason ?? 'Run and pass Release Readiness before submitting a Candidate.')}</div>}
          {submissionRequired && onSubmitRelease && !releaseSubmissionLoading && releaseSubmissionReadiness?.ready === true && <div style={{ color: C.green, fontSize: 12, marginTop: 10 }}>✓ {t('Release Readiness passed. This Workspace can be submitted.')}</div>}
          {releaseMessage && <div style={{ color: C.green, fontSize: 12, marginTop: 10 }}>{releaseMessage}</div>}
          {error && <div style={{ ...errorStyle, marginTop: 12, border: `1px solid ${C.redBorder}` }}>{error}</div>}
        </OverviewSection>

        <div style={{ display: 'grid', gridTemplateColumns: '230px minmax(0, 1fr)', gap: 12, marginTop: 12 }}>
          <OverviewSection title={t('Runtime session history')}>
            {runs.length === 0 ? <div style={{ color: C.dim, fontSize: 12 }}>{t('There is no run history yet.')}</div> : runs.map(run => (
              <button key={run.runId} onClick={() => setSelectedRunId(run.runId)} style={{ width: '100%', textAlign: 'left', padding: '8px 9px', marginBottom: 5, borderRadius: 3, border: `1px solid ${selectedRun?.runId === run.runId ? C.accent : C.borderSubtle}`, background: selectedRun?.runId === run.runId ? C.accentDim : C.bg, color: C.text, cursor: 'pointer' }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}><RunStatusBadge status={run.status} /><span style={{ fontSize: 11 }}>{t(actionLabel(run.kind))}</span></div>
                <div style={{ color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, marginTop: 5 }}>{formatDateTime(run.createdAt)}</div>
              </button>
            ))}
          </OverviewSection>
          <OverviewSection title={t('Run output')}>
            {selectedRun ? <><RunSummary run={selectedRun} />{selectedRun.progress && <RunProgress run={selectedRun} />}<RunOutput run={selectedRun} /></> : <div style={{ color: C.dim, fontSize: 12 }}>{t('Actual logs appear here after a run starts.')}</div>}
          </OverviewSection>
        </div>
      </div>
    </div>
  )
}

function RunProgress({ run }: { run: WorkspaceRun }) {
  const { t } = useI18n()
  const progress = run.progress
  const percent = Math.max(0, Math.min(100, progress?.percent ?? (run.status === 'succeeded' ? 100 : 0)))
  const latestMetrics = progress?.metrics && Object.keys(progress.metrics).length
    ? progress.metrics
    : run.metricSeries[run.metricSeries.length - 1]?.metrics ?? {}
  const metricGroups = groupMetricNames(metricNames(run.metricSeries))
  return (
    <div style={{ marginBottom: 9, padding: 11, border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.surface2 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, color: C.text, fontSize: 12, fontWeight: 600 }}>
            <span>{t(progress?.message ?? (run.status === 'succeeded' ? 'Local model and manifest are ready' : 'Waiting for training progress…'))}</span>
            <span style={{ fontFamily: 'JetBrains Mono, monospace' }}>{percent.toFixed(percent < 10 ? 1 : 0)}%</span>
          </div>
          <div style={{ height: 8, marginTop: 8, overflow: 'hidden', borderRadius: 999, background: C.bg, border: `1px solid ${C.borderSubtle}` }}>
            <div style={{ width: `${percent}%`, height: '100%', borderRadius: 999, background: run.status === 'succeeded' ? C.green : C.accent, transition: 'width 240ms ease' }} />
          </div>
        </div>
        {progress?.epoch && <RunFact label={t('Epoch')} value={`${progress.epoch}${progress.epochs ? ` / ${progress.epochs}` : ''}`} />}
        {progress?.batch && <RunFact label={t('Batch')} value={`${progress.batch}${progress.totalBatches ? ` / ${progress.totalBatches}` : ''}`} />}
        {!progress?.batch && typeof progress?.step === 'number' && <RunFact label={t('Step')} value={`${progress.step}${progress.totalSteps ? ` / ${progress.totalSteps}` : ''}`} />}
      </div>
      {Object.keys(latestMetrics).length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(125px, 1fr))', gap: 7, marginTop: 9 }}>
          {Object.entries(latestMetrics).map(([name, value]) => <RunFact key={name} label={metricLabel(name, t)} value={formatMetric(name, value)} />)}
        </div>
      )}
      {metricGroups.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(310px, 1fr))', gap: 8, marginTop: 9 }}>
          {metricGroups.map(group => <TrainingMetricChart
            key={group.kind}
            title={t(group.kind === 'objective' ? 'Loss & error' : group.kind === 'ratio' ? 'Scores' : 'Metrics')}
            names={group.names}
            points={run.metricSeries}
            percent={group.kind === 'ratio'}
          />)}
        </div>
      )}
    </div>
  )
}

function TrainingMetricChart({ title, names, points, percent = false }: { title: string; names: string[]; points: TrainingMetricPoint[]; percent?: boolean }) {
  const { t } = useI18n()
  const colors = [C.accent, C.green, C.purple, C.orange]
  const series: MetricChartSeries[] = names.map((name, index) => ({
    id: name,
    label: displayMetricName(name),
    color: colors[index % colors.length],
    points: points.flatMap(point => Number.isFinite(point.metrics[name])
      ? [{ x: point.percent, y: point.metrics[name] }]
      : []),
  }))
  return (
    <MetricChartPanel
      title={title}
      description={t('Metrics logged during this local training run.')}
      xLabel={t('Training progress (%)')}
      series={series}
      percent={percent}
    />
  )
}

function metricNames(points: TrainingMetricPoint[]): string[] {
  return [...new Set(points.flatMap(point => Object.keys(point.metrics)))]
}

function displayMetricName(name: string): string {
  return name.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')
}

function metricLabel(name: string, t: (message: string) => string): string {
  const known: Record<string, string> = {
    training_loss: 'Training Loss',
    validation_loss: 'Validation Loss',
    accuracy: 'Accuracy',
    primary_metric: 'Primary Metric',
    f1_score: 'F1 Score',
  }
  return t(known[name] ?? displayMetricName(name))
}

function formatMetric(name: string, value: number): string {
  if (!Number.isFinite(value)) return '—'
  if (isRatioMetric(name) && value >= 0 && value <= 1) return `${(value * 100).toFixed(2)}%`
  return value.toFixed(Math.abs(value) < 0.01 ? 6 : 4)
}

function RunSummary({ run }: { run: WorkspaceRun }) {
  const { t, formatDateTime } = useI18n()
  return (
    <div className="workspace-run-summary" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 7, marginBottom: 8 }}>
      <RunFact label={t('Status')} value={<RunStatusBadge status={run.status} />} />
      <RunFact label={t('Action')} value={t(actionLabel(run.kind))} />
      <RunFact label={t('Started')} value={formatDateTime(run.startedAt ?? run.createdAt)} />
      <RunFact label={t('Exit code')} value={run.exitCode === null ? '—' : String(run.exitCode)} />
    </div>
  )
}

function RunFact({ label, value }: { label: string; value: React.ReactNode }) {
  return <div style={{ padding: '7px 8px', background: C.bg, border: `1px solid ${C.borderSubtle}`, borderRadius: 3 }}><div style={{ color: C.dim, fontSize: 9, textTransform: 'uppercase', letterSpacing: '.07em' }}>{label}</div><div style={{ color: C.muted, fontSize: 11, marginTop: 4, minHeight: 16 }}>{value}</div></div>
}

function RunOutput({ run, compact = false }: { run: WorkspaceRun; compact?: boolean }) {
  const { t } = useI18n()
  return <pre style={{ margin: 0, height: compact ? 105 : 290, overflow: 'auto', padding: 10, borderRadius: 4, background: '#1c1917', border: '1px solid #292524', color: '#d6d3d1', fontFamily: 'JetBrains Mono, monospace', fontSize: 11, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{run.output || t('Waiting for process output…')}</pre>
}

function RunStatusBadge({ status }: { status: WorkspaceRun['status'] }) {
  const { t } = useI18n()
  const variant = status === 'succeeded' ? 'green' : status === 'failed' || status === 'cancelled' ? 'red' : status === 'running' ? 'blue' : 'yellow'
  return <Badge variant={variant}>{t(status)}</Badge>
}

function CodeWorkspace(props: {
  accountKey: string
  project: WorkspaceProject
  environment: PythonEnvironment | null
  uv: UvRuntimeInformation | null
  tree: WorkspaceFileTreeNode | null
  treeLoading: boolean
  treeError: string | null
  expanded: Set<string>
  setExpanded: (value: Set<string>) => void
  tabs: string[]
  activePath: string
  activeFile?: OpenFile
  readOnlyPaths: Set<string>
  deletingPath: string | null
  setActivePath: (path: string) => void
  onOpenFile: (path: string) => void
  onCloseFile: (path: string) => void
  onChange: (content: string) => void
  onSave: () => Promise<boolean>
  onCreateFile: () => void
  onDeleteFile: () => void
  onFormatFile: () => Promise<boolean>
  saveState: 'idle' | 'saving' | 'saved' | 'error'
}) {
  const { t, formatDateTime } = useI18n()
  const [bottomView, setBottomView] = useState<BottomView>('Terminal')
  const [runs, setRuns] = useState<WorkspaceRun[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [formatting, setFormatting] = useState(false)
  const [explorerView, setExplorerView] = useState<ExplorerView>('task')
  const [openTaskGroups, setOpenTaskGroups] = useState<Set<string>>(
    new Set(['configure', 'build-model', 'tool-ai', 'describe']),
  )
  const [explorerWidth, setExplorerWidth] = usePersistentNumber(`fedops:${props.accountKey}:code-explorer-width`, 250, 190, 420)
  const [infoWidth, setInfoWidth] = usePersistentNumber(`fedops:${props.accountKey}:code-info-width`, 230, 190, 420)
  const [bottomHeight, setBottomHeight] = usePersistentNumber(`fedops:${props.accountKey}:code-bottom-height`, 205, 120, 520)
  const [codeFontSize, setCodeFontSize] = usePersistentNumber(
    `fedops:${props.accountKey}:code-font-size`,
    DEFAULT_CODE_FONT_SIZE,
    MIN_CODE_FONT_SIZE,
    MAX_CODE_FONT_SIZE,
  )
  const runFileRuns = runs.filter(item => item.kind === 'run-file')
  const selectedRun = runFileRuns.find(item => item.runId === selectedRunId) ?? runFileRuns[0] ?? null
  const activeRun = runs.find(item => item.status === 'queued' || item.status === 'running') ?? null
  const running = Boolean(activeRun)
  const runnable = props.activeFile?.name.toLowerCase().endsWith('.py') && !props.activeFile.readOnly && props.environment?.status === 'ready'
  const formattable = !props.activeFile?.readOnly && (props.activeFile?.language === 'python' || props.activeFile?.language === 'json')
  const taskFileGroups = useMemo(() => buildTaskFileGroups(props.tree), [props.tree])
  const activeAccessKind = findTreeNode(props.tree, props.activePath)?.accessKind
  const runFileTitle = !props.activeFile?.name.toLowerCase().endsWith('.py')
    ? t('Select a Python (.py) file.')
    : props.environment?.status !== 'ready'
      ? t('Sync the selected Python environment first.')
      : running
        ? t('Another managed run is active in this Workspace.')
        : t('Save the current file and run it without stdin in the selected uv environment.')

  useEffect(() => {
    let cancelled = false
    listWorkspaceRuns(props.project.localProjectId)
      .then(result => {
        if (cancelled) return
        const fileRuns = result.items.filter(item => item.kind === 'run-file')
        setRuns(result.items)
        setSelectedRunId(previous => (
          previous && fileRuns.some(item => item.runId === previous)
            ? previous
            : fileRuns[0]?.runId ?? null
        ))
      })
      .catch(cause => {
        if (!cancelled) setRunError(errorText(cause))
      })
    return () => { cancelled = true }
  }, [props.project.localProjectId])

  useEffect(() => {
    if (!activeRun) return
    const timer = window.setInterval(() => {
      getWorkspaceRun(activeRun.runId)
        .then(next => setRuns(previous => mergeWorkspaceRun(previous, next)))
        .catch(cause => setRunError(errorText(cause)))
    }, 500)
    return () => window.clearInterval(timer)
  }, [activeRun?.runId, activeRun?.status])

  async function reloadRunHistory() {
    setRunError(null)
    try {
      const result = await listWorkspaceRuns(props.project.localProjectId)
      const fileRuns = result.items.filter(item => item.kind === 'run-file')
      setRuns(result.items)
      setSelectedRunId(previous => (
        previous && fileRuns.some(item => item.runId === previous)
          ? previous
          : fileRuns[0]?.runId ?? null
      ))
    } catch (cause) {
      setRunError(errorText(cause))
    }
  }

  async function runActiveFile() {
    if (!props.activeFile || !runnable || running) return
    setRunError(null)
    if (!await props.onSave()) return
    try {
      const started = await startWorkspaceAction(
        props.project.localProjectId,
        'run-file',
        props.environment?.environmentId,
        props.activeFile.path,
      )
      setRuns(previous => mergeWorkspaceRun(previous, started))
      setSelectedRunId(started.runId)
      setBottomView('Run Output')
    } catch (cause) {
      setRunError(errorText(cause))
      setBottomView('Run Output')
    }
  }

  async function stopRun() {
    if (!selectedRun || !['queued', 'running'].includes(selectedRun.status)) return
    try {
      const next = await cancelWorkspaceRun(selectedRun.runId)
      setRuns(previous => mergeWorkspaceRun(previous, next))
    } catch (cause) {
      setRunError(errorText(cause))
    }
  }

  async function formatActiveFile() {
    if (!formattable || formatting) return
    setFormatting(true)
    try {
      await props.onFormatFile()
    } finally {
      setFormatting(false)
    }
  }

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0, flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <Sidebar width={explorerWidth}>
          <div style={{ display: 'flex', alignItems: 'center', padding: '6px 9px 4px' }}>
            <SectionLabel style={{ margin: 0, flex: 1 }}>{t('Explorer')}</SectionLabel>
            <span className="workspace-file-tree__count">
              {props.treeLoading ? t('Loading…') : props.tree ? t('{{count}} files', { count: countFiles(props.tree) }) : '—'}
            </span>
            <IconBtn icon="＋" title={t('New file')} onClick={props.onCreateFile} />
          </div>
          <div className="workspace-explorer-view" role="group" aria-label={t('Explorer view')}>
            <button type="button" data-active={explorerView === 'task'} onClick={() => setExplorerView('task')}>{t('Task view')}</button>
            <button type="button" data-active={explorerView === 'all'} onClick={() => setExplorerView('all')}>{t('All files')}</button>
          </div>
          {props.treeError && <div style={{ padding: 9, color: C.red, fontSize: 11 }}>{props.treeError}</div>}
          <div className="workspace-file-tree" role="tree" aria-label={t('Workspace files')}>
            {explorerView === 'task' && taskFileGroups.map(group => (
              <TaskFileGroup
                key={group.id}
                group={group}
                open={openTaskGroups.has(group.id)}
                onToggle={() => setOpenTaskGroups(previous => {
                  const next = new Set(previous)
                  next.has(group.id) ? next.delete(group.id) : next.add(group.id)
                  return next
                })}
                expanded={props.expanded}
                setExpanded={props.setExpanded}
                activePath={props.activePath}
                onOpen={props.onOpenFile}
              />
            ))}
            {explorerView === 'all' && props.tree?.children?.map(node => (
              <FileTreeNode key={node.path} node={node} depth={0} expanded={props.expanded} setExpanded={props.setExpanded} activePath={props.activePath} onOpen={props.onOpenFile} />
            ))}
            {!props.treeLoading && !props.treeError && props.tree && (props.tree.children?.length ?? 0) === 0 && (
              <div className="workspace-file-tree__empty">{t('There are no files.')}</div>
            )}
          </div>
        </Sidebar>
        <ResizeHandle axis="x" label={t('Resize Explorer')} onDelta={delta => setExplorerWidth(value => clamp(value + delta, 190, 420))} onReset={() => setExplorerWidth(250)} />

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
          <div className="studio-scroll-tabs" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px', borderBottom: `1px solid ${C.border}`, background: C.surface }}>
            {props.activeFile?.readOnly
              ? activeAccessKind === 'generated'
                ? <Badge variant="orange">↻ {t('Generated · View only')}</Badge>
                : <Badge variant="purple">◇ {t('FedOps managed · View only')}</Badge>
              : props.activeFile
                ? <Badge variant="blue">● {t('Owner editable')}</Badge>
                : <span />}
            {props.activeFile && !props.activeFile.readOnly && (
              props.activeFile.dirty
                ? <Badge variant="yellow">{t('Modified')}</Badge>
                : <Badge variant="green">{t('Saved')}</Badge>
            )}
            <div style={{ flex: 1 }} />
            <div className="workspace-code-font-size" role="group" aria-label={t('Code editor font size')}>
              <button
                type="button"
                aria-label={t('Decrease code font size')}
                title={t('Decrease code font size')}
                disabled={codeFontSize <= MIN_CODE_FONT_SIZE}
                onClick={() => setCodeFontSize(value => clamp(value - 1, MIN_CODE_FONT_SIZE, MAX_CODE_FONT_SIZE))}
              >
                −
              </button>
              <button
                className="workspace-code-font-size__value"
                type="button"
                aria-label={t('Code font size {{size}} pixels. Reset to {{defaultSize}} pixels', { size: codeFontSize, defaultSize: DEFAULT_CODE_FONT_SIZE })}
                title={t('Code font size {{size}}px · Click to reset', { size: codeFontSize })}
                onClick={() => setCodeFontSize(DEFAULT_CODE_FONT_SIZE)}
              >
                {codeFontSize}px
              </button>
              <button
                type="button"
                aria-label={t('Increase code font size')}
                title={t('Increase code font size')}
                disabled={codeFontSize >= MAX_CODE_FONT_SIZE}
                onClick={() => setCodeFontSize(value => clamp(value + 1, MIN_CODE_FONT_SIZE, MAX_CODE_FONT_SIZE))}
              >
                +
              </button>
            </div>
            {props.activeFile && (
              <Button variant="ghost" disabled={!formattable || formatting || running} onClick={() => void formatActiveFile()}>
                {t(formatting ? 'Formatting…' : 'Format document')}
              </Button>
            )}
            {props.activeFile?.name.toLowerCase().endsWith('.py') && (
              <Button variant="primary" disabled={!runnable || running} title={runFileTitle} onClick={() => void runActiveFile()}>
                {t(running ? 'Workspace action in progress…' : props.activeFile.dirty ? 'Save & Run Python File' : 'Run Python File')}
              </Button>
            )}
            {props.activeFile && <Button variant="danger" disabled={running || props.activeFile.readOnly || Boolean(props.deletingPath)} onClick={props.onDeleteFile}>{t(props.deletingPath ? 'Deleting…' : 'Delete file')}</Button>}
            <IconBtn icon="⌘S" title={t('Save')} disabled={!props.activeFile || props.activeFile.readOnly || !props.activeFile.dirty} onClick={() => void props.onSave()} active={props.saveState === 'saving'} />
          </div>
          <EditorTabs tabs={props.tabs} active={props.activePath} setActive={props.setActivePath} onClose={props.onCloseFile} isReadOnly={path => props.readOnlyPaths.has(path)} />
          {props.activeFile?.readOnly && (
            <div className="workspace-read-only-notice">
              <span aria-hidden="true">🔒</span>
              <span>{t('This is FedOps-managed implementation. You can inspect and copy it, but only Owner-editable files can be changed.')}</span>
            </div>
          )}
          <TextEditor file={props.activeFile} fontSize={codeFontSize} onChange={props.onChange} />
        </div>

        <ResizeHandle axis="x" label={t('Resize File Info')} onDelta={delta => setInfoWidth(value => clamp(value - delta, 190, 420))} onReset={() => setInfoWidth(230)} />
        <aside className="scroll-area" style={{ width: infoWidth, flexShrink: 0, background: C.surface, borderLeft: `1px solid ${C.border}`, padding: 12, overflowY: 'auto' }}>
          <SectionLabel>{t('File Info')}</SectionLabel>
          {props.activeFile ? (
            <>
              <MetaRow label={t('File')} value={props.activeFile.path} mono />
              <MetaRow label={t('Language')} value={props.activeFile.language} mono />
              <MetaRow label={t('Size')} value={formatBytes(props.activeFile.size)} mono />
              <MetaRow label={t('Modified')} value={formatDateTime(props.activeFile.modifiedAt)} />
              <MetaRow label={t('Access')} value={t(props.activeFile.readOnly ? 'Read only · FedOps managed' : 'Owner editable')} />
              <MetaRow label={t('State')} value={t(props.activeFile.readOnly ? 'Reference only' : props.activeFile.dirty ? 'Modified locally' : 'Saved')} />
            </>
          ) : <div style={{ color: C.dim, fontSize: 12 }}>{t('Select a file in Explorer.')}</div>}
          <div style={{ marginTop: 16 }}>
            <SectionLabel>{t('Project')}</SectionLabel>
            <MetaRow label={t('Name')} value={props.project.name} />
            <MetaRow label={t('Path')} value={props.project.path} mono />
            <MetaRow label={t('Environment')} value={props.environment ? `${props.environment.name} · ${t(props.environment.status)}` : t('Loading')} />
          </div>
        </aside>
      </div>

      <ResizeHandle axis="y" label={t('Resize Terminal height')} onDelta={delta => setBottomHeight(value => clamp(value - delta, 120, 520))} onReset={() => setBottomHeight(205)} />
      <div style={{ height: bottomHeight, flexShrink: 0, borderTop: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column' }}>
        <BottomTabs tabs={['Terminal', 'Run Output']} active={bottomView} setActive={tab => setBottomView(tab as BottomView)} />
        <div style={{ display: bottomView === 'Terminal' ? 'flex' : 'none', flex: 1, minHeight: 0 }}>
          <Suspense fallback={<div style={bottomEmpty}>{t('Loading the interactive terminal…')}</div>}>
            <WorkspaceTerminal accountKey={props.accountKey} project={props.project} environment={props.environment} uv={props.uv} active={bottomView === 'Terminal'} />
          </Suspense>
        </div>
        {bottomView === 'Run Output' && (
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', padding: 7, background: C.bg }}>
            {runError && <div style={{ ...errorStyle, border: `1px solid ${C.redBorder}`, marginBottom: 6 }}>{runError}</div>}
            {selectedRun ? (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <select
                    aria-label={t('Python file run history')}
                    value={selectedRun.runId}
                    onChange={event => setSelectedRunId(event.target.value)}
                    style={{ minWidth: 230, maxWidth: 390, height: 26, padding: '2px 7px', border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, color: C.text, background: C.inputBg, fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}
                  >
                    {runFileRuns.map(item => (
                      <option key={item.runId} value={item.runId}>
                        {formatDateTime(item.createdAt)} · {t(item.status)} · {item.command}
                      </option>
                    ))}
                  </select>
                  <RunStatusBadge status={selectedRun.status} />
                  <div style={{ flex: 1 }} />
                  <Button variant="ghost" onClick={() => void reloadRunHistory()}>{t('Refresh')}</Button>
                  {['queued', 'running'].includes(selectedRun.status) && <Button variant="ghost" onClick={() => void stopRun()}>{t('Stop')}</Button>}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, marginBottom: 6, color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
                  <span title={selectedRun.command} style={{ minWidth: 0, flex: 1, overflow: 'hidden', color: C.muted, textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selectedRun.command}</span>
                  <span style={{ flexShrink: 0 }}>env {selectedRun.environmentId ?? '—'}</span>
                  <span style={{ flexShrink: 0 }}>{t('started')} {formatDateTime(selectedRun.startedAt ?? selectedRun.createdAt)}</span>
                  <span style={{ flexShrink: 0 }}>{t('exit')} {selectedRun.exitCode ?? '—'}</span>
                </div>
                <pre style={{ flex: 1, minHeight: 0, overflow: 'auto', margin: 0, padding: 8, color: '#d6d3d1', background: '#1c1917', fontFamily: 'JetBrains Mono, monospace', fontSize: 11, lineHeight: 1.45, whiteSpace: 'pre-wrap' }}>{selectedRun.output || t('Waiting for process output…')}</pre>
              </>
            ) : <div style={bottomEmpty}>{t('There is no run history. Select a Python file and choose Run Python File to show the managed-run output from the selected uv environment here.')}</div>}
          </div>
        )}
      </div>
    </div>
  )
}

function mergeWorkspaceRun(runs: WorkspaceRun[], run: WorkspaceRun) {
  return [run, ...runs.filter(item => item.runId !== run.runId)]
}

function TextEditor({ file, fontSize, onChange }: { file?: OpenFile; fontSize: number; onChange: (content: string) => void }) {
  const { t } = useI18n()
  if (!file) return <div style={{ flex: 1, display: 'grid', placeItems: 'center', color: C.dim }}>{t('Select a file.')}</div>
  return (
    <div style={{ flex: 1, minHeight: 0, background: C.bg, overflow: 'hidden' }}>
      <Suspense fallback={<div style={{ color: C.dim, padding: 16 }}>{t('Loading the code editor…')}</div>}>
        <CodeEditor path={file.path} language={file.language} value={file.draft} fontSize={fontSize} readOnly={file.readOnly} onChange={onChange} />
      </Suspense>
    </div>
  )
}

function ResizeHandle({ axis, label, onDelta, onReset }: { axis: 'x' | 'y'; label: string; onDelta: (delta: number) => void; onReset: () => void }) {
  const { t } = useI18n()
  function startResize(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault()
    let previous = axis === 'x' ? event.clientX : event.clientY
    const uiScale = currentUiScale()
    const oldCursor = document.body.style.cursor
    const oldSelect = document.body.style.userSelect
    document.body.style.cursor = axis === 'x' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'

    function move(pointer: PointerEvent) {
      const current = axis === 'x' ? pointer.clientX : pointer.clientY
      onDelta((current - previous) / uiScale)
      previous = current
    }
    function stop() {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
      document.body.style.cursor = oldCursor
      document.body.style.userSelect = oldSelect
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
  }

  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation={axis === 'x' ? 'vertical' : 'horizontal'}
      title={t('{{label}} · double-click for the default size', { label })}
      onPointerDown={startResize}
      onDoubleClick={onReset}
      style={{
        zIndex: 2,
        width: axis === 'x' ? 5 : '100%',
        height: axis === 'y' ? 5 : '100%',
        flexShrink: 0,
        marginLeft: axis === 'x' ? -2 : 0,
        marginRight: axis === 'x' ? -3 : 0,
        marginTop: axis === 'y' ? -2 : 0,
        marginBottom: axis === 'y' ? -3 : 0,
        cursor: axis === 'x' ? 'col-resize' : 'row-resize',
        background: C.borderSubtle,
        opacity: 0.45,
        touchAction: 'none',
      }}
    />
  )
}

function currentUiScale() {
  const rawValue = getComputedStyle(document.documentElement)
    .getPropertyValue('--studio-ui-scale')
    .trim()
  const scale = Number(rawValue)
  return Number.isFinite(scale) && scale > 0 ? scale : 1
}

function FileTreeNode({ node, depth, expanded, setExpanded, activePath, onOpen }: { node: WorkspaceFileTreeNode; depth: number; expanded: Set<string>; setExpanded: (value: Set<string>) => void; activePath: string; onOpen: (path: string) => void }) {
  const { t } = useI18n()
  const open = expanded.has(node.path)
  const directory = node.type === 'directory'
  const active = !directory && activePath === node.path
  function toggle() {
    if (!directory) return onOpen(node.path)
    const next = new Set(expanded)
    open ? next.delete(node.path) : next.add(node.path)
    setExpanded(next)
  }
  return (
    <div className="workspace-file-tree__node" role="none">
      <button
        type="button"
        role="treeitem"
        aria-expanded={directory ? open : undefined}
        data-active={active ? 'true' : 'false'}
        data-directory={directory ? 'true' : 'false'}
        data-read-only={node.readOnly ? 'true' : 'false'}
        data-nested={depth > 0 ? 'true' : 'false'}
        title={`${node.name} · ${t(fileTypeLabel(node.name, directory))} · ${t(accessKindLabel(node.accessKind))}`}
        onClick={toggle}
        className="workspace-file-tree__row"
        style={{
          paddingLeft: 7 + depth * 14,
          '--tree-guide-left': `${11 + Math.max(0, depth - 1) * 14}px`,
        } as React.CSSProperties}
      >
        <span className="workspace-file-tree__chevron" data-open={open ? 'true' : 'false'}>
          {directory ? '›' : ''}
        </span>
        <FileTypeIcon name={node.name} directory={directory} open={open} />
        <span className="workspace-file-tree__name">{node.name}</span>
        {node.accessKind === 'generated' && <span className="workspace-file-tree__access" aria-label={t('Generated · View only')}>AUTO</span>}
      </button>
      {directory && open && (
        <div role="group">
          {node.children?.map(child => <FileTreeNode key={child.path} node={child} depth={depth + 1} expanded={expanded} setExpanded={setExpanded} activePath={activePath} onOpen={onOpen} />)}
          {node.children?.length === 0 && (
            <div className="workspace-file-tree__directory-empty" style={{ paddingLeft: 39 + depth * 14 }}>
              {t('Empty')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

interface TaskFileGroupDefinition {
  id: string
  label: string
  description: string
  accessKind: WorkspaceFileTreeNode['accessKind']
  nodes: WorkspaceFileTreeNode[]
}

function TaskFileGroup({ group, open, onToggle, expanded, setExpanded, activePath, onOpen }: {
  group: TaskFileGroupDefinition
  open: boolean
  onToggle: () => void
  expanded: Set<string>
  setExpanded: (value: Set<string>) => void
  activePath: string
  onOpen: (path: string) => void
}) {
  const { t } = useI18n()
  const count = group.nodes.reduce((total, node) => total + countFiles(node), 0)
  return <section className="workspace-task-files">
    <button type="button" className="workspace-task-files__header" onClick={onToggle} aria-expanded={open}>
      <span className="workspace-task-files__chevron" data-open={open ? 'true' : 'false'}>›</span>
      <span className="workspace-task-files__identity">
        <strong>{t(group.label)}</strong>
        <small>{t(group.description)}</small>
      </span>
      <span className="workspace-task-files__count">{count}</span>
      {group.accessKind === 'generated' && <span className="workspace-task-files__kind" data-kind="generated">{t('Generated')}</span>}
      {group.accessKind === 'managed' && <span className="workspace-task-files__kind" data-kind="managed">{t('View only')}</span>}
    </button>
    {open && <div className="workspace-task-files__body" role="group">
      {group.nodes.map(node => <FileTreeNode key={node.path} node={node} depth={0} expanded={expanded} setExpanded={setExpanded} activePath={activePath} onOpen={onOpen} />)}
      {count === 0 && <div className="workspace-file-tree__directory-empty">{t('No files yet')}</div>}
    </div>}
  </section>
}

function buildTaskFileGroups(tree: WorkspaceFileTreeNode | null): TaskFileGroupDefinition[] {
  const definitions: Omit<TaskFileGroupDefinition, 'nodes'>[] = [
    { id: 'configure', label: 'Configure', description: 'Task settings', accessKind: 'editable' },
    { id: 'build-model', label: 'Build model', description: 'Model, data, and local training', accessKind: 'editable' },
    { id: 'tool-ai', label: 'Use as Tool AI', description: 'Agent input, output, and inference', accessKind: 'editable' },
    { id: 'describe', label: 'Describe & dependencies', description: 'Registry Card and Python packages', accessKind: 'editable' },
    { id: 'generated', label: 'Generated outputs', description: 'Local and released model artifacts', accessKind: 'generated' },
    { id: 'managed', label: 'FedOps managed', description: 'Federated runtime and validation contract', accessKind: 'managed' },
  ]
  const editable = (node: WorkspaceFileTreeNode) => node.accessKind === 'editable'
  const direct = (path: string) => findTreeNode(tree, path)
  const selected = (node: WorkspaceFileTreeNode | null, matches: (candidate: WorkspaceFileTreeNode) => boolean) => {
    if (!node) return []
    const result = selectTreeNode(node, matches)
    return result ? [result] : []
  }
  return definitions.map(definition => ({
    ...definition,
    nodes: definition.id === 'configure'
      ? [direct('federated_task/conf/config.yaml')].filter((node): node is WorkspaceFileTreeNode => Boolean(node))
      : definition.id === 'build-model'
        ? selected(direct('federated_task/local_training'), editable)
        : definition.id === 'tool-ai'
          ? selected(direct('federated_task/tool_ai'), editable)
          : definition.id === 'describe'
            ? ['README.md', 'requirements.txt'].map(direct).filter((node): node is WorkspaceFileTreeNode => Boolean(node))
            : definition.id === 'generated'
              ? selectTreeNodes(tree, node => node.accessKind === 'generated')
              : selectTreeNodes(tree, node => node.accessKind === 'managed'),
  }))
}

function selectTreeNodes(tree: WorkspaceFileTreeNode | null, matches: (node: WorkspaceFileTreeNode) => boolean): WorkspaceFileTreeNode[] {
  return (tree?.children ?? []).map(node => selectTreeNode(node, matches)).filter((node): node is WorkspaceFileTreeNode => Boolean(node))
}

function selectTreeNode(node: WorkspaceFileTreeNode, matches: (node: WorkspaceFileTreeNode) => boolean): WorkspaceFileTreeNode | null {
  if (node.type === 'file') return matches(node) ? node : null
  const children = (node.children ?? []).map(child => selectTreeNode(child, matches)).filter((child): child is WorkspaceFileTreeNode => Boolean(child))
  if (children.length === 0 && !(matches(node) && node.accessKind === 'generated')) return null
  return { ...node, children }
}

function findTreeNode(tree: WorkspaceFileTreeNode | null, path: string): WorkspaceFileTreeNode | null {
  if (!tree || !path) return null
  if (tree.path === path) return tree
  for (const child of tree.children ?? []) {
    const match = findTreeNode(child, path)
    if (match) return match
  }
  return null
}

function accessKindLabel(kind: WorkspaceFileTreeNode['accessKind']): string {
  if (kind === 'generated') return 'Generated · View only'
  if (kind === 'managed') return 'FedOps managed · View only'
  return 'Owner editable'
}

function OverviewSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: '12px 14px' }}><SectionLabel>{title}</SectionLabel>{children}</section>
}

function usePersistentNumber(key: string, initial: number, minimum: number, maximum: number) {
  const [value, setValue] = useState(() => {
    const stored = Number(window.localStorage.getItem(key))
    return Number.isFinite(stored) && stored > 0 ? clamp(stored, minimum, maximum) : initial
  })
  useEffect(() => window.localStorage.setItem(key, String(value)), [key, value])
  return [value, setValue] as const
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

function countFiles(node: WorkspaceFileTreeNode): number {
  return node.type === 'file' ? 1 : (node.children ?? []).reduce((total, child) => total + countFiles(child), 0)
}

function projectDisplayName(project: WorkspaceProject): string {
  return project.taskBinding?.displayName || project.name
}

function projectRegistryId(project: WorkspaceProject): string {
  const binding = project.taskBinding
  if (!binding?.slug) return ''
  return binding.ownerHandle ? `@${binding.ownerHandle}/${binding.slug}` : binding.slug
}

function accountTaskRegistryId(task: AccountTask): string {
  if (!task.slug) return task.displayName
  return task.ownerHandle ? `@${task.ownerHandle}/${task.slug}` : task.slug
}

function workspaceNamePreview(value: string): string {
  const normalized = value.trim().toLocaleLowerCase()
  const slug = normalized.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
  return slug.slice(0, 64).replace(/-$/, '')
}
function actionLabel(kind: WorkspaceRun['kind']): string {
  return ({ create: 'Create Task', 'environment-sync': 'Sync environment', validate: 'Task Test', 'local-train': 'Local Train', 'release-readiness': 'Release Readiness', 'participation-readiness': 'Participation Readiness', 'release-candidate': 'Submit Release Candidate', 'run-file': 'Run Python file' } as Record<WorkspaceRun['kind'], string>)[kind]
}
function formatBytes(value: number): string { return value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KiB` }
function errorText(cause: unknown): string { return cause instanceof Error ? cause.message : String(cause) }

const backButton: React.CSSProperties = { background: 'none', border: 'none', color: C.muted, cursor: 'pointer', fontSize: 18, padding: '0 4px', lineHeight: 1 }
const emptyStyle: React.CSSProperties = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 36, color: C.dim, textAlign: 'center', fontSize: 13 }
const errorStyle: React.CSSProperties = { padding: '7px 12px', background: C.redDim, color: C.red, borderBottom: `1px solid ${C.redBorder}`, fontSize: 12 }
const bottomEmpty: React.CSSProperties = { padding: 10, color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }
const dialogBackdrop: React.CSSProperties = { position: 'fixed', inset: 0, zIndex: 100, display: 'grid', placeItems: 'center', padding: 20, background: C.overlay, backdropFilter: 'blur(2px)' }
const dialogPanel: React.CSSProperties = { width: 'min(620px, 100%)', maxHeight: 'calc(100% - 40px)', overflowY: 'auto', background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, boxShadow: C.shadow }
const fieldLabel: React.CSSProperties = { display: 'block', marginBottom: 6, color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 12, fontWeight: 600 }
const fieldInput: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '8px 10px', color: C.text, background: C.inputBg, border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, outline: 'none', fontFamily: 'Inter, sans-serif', fontSize: 13 }
const bodyText: React.CSSProperties = { margin: '0 0 10px', color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 12, lineHeight: 1.6 }
const inlineCode: React.CSSProperties = { color: C.accent, background: C.accentDim, padding: '1px 4px', borderRadius: 2, fontFamily: 'JetBrains Mono, monospace' }
