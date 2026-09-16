import type { CSSProperties } from 'react'
import type { WorkspaceProject } from '../../api/workspace'
import { useStudio } from '../../app/StudioProvider'
import { useI18n } from '../../app/i18n'
import type { Screen } from '../../app/types'
import { Badge, Button, C } from '../../ui/UIKit'

interface Props {
  setScreen: (screen: Screen) => void
  onOpenProject: (project: WorkspaceProject) => void
}

type ActionKind = 'workspace' | 'federation' | 'agent'

export default function Home({ setScreen, onOpenProject }: Props) {
  const studio = useStudio()
  const { t } = useI18n()
  const session = studio.bootstrap?.session
  const projects = studio.bootstrap?.workspace.projects ?? []
  const accountName = session?.displayName
    || (session?.handle ? `@${session.handle}` : null)
    || session?.username
    || t('FedOps user')

  const actions: Array<{
    kind: ActionKind
    title: string
    description: string
    action: string
    destination: Screen
  }> = [
    {
      kind: 'workspace',
      title: t('Develop a Federated Task'),
      description: t('Create or open a local Workspace, then edit, train, and validate the Task.'),
      action: t('Open Workspace'),
      destination: 'workspace',
    },
    {
      kind: 'federation',
      title: t('Join Federated Learning'),
      description: t('Find a published Task and prepare this device to participate.'),
      action: t('Browse Registry'),
      destination: 'registry',
    },
    {
      kind: 'agent',
      title: t('Build an AI Agent'),
      description: t('Combine a local model and Tool AI, test it, and serve it.'),
      action: t('Open Agent Builder'),
      destination: 'agent',
    },
  ]

  return (
    <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', background: C.bg }}>
      <main style={{ width: 'min(980px, calc(100% - 36px))', margin: '0 auto', padding: '30px 0 36px' }}>
        <header style={{ marginBottom: 28 }}>
          <div style={eyebrowStyle}>{t('HOME')}</div>
          <h1 style={{ margin: '7px 0 0', color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 23, fontWeight: 700, letterSpacing: '-0.025em' }}>
            {t('Welcome back, {{name}}', { name: accountName })}
          </h1>
          <p style={{ maxWidth: 620, margin: '7px 0 0', color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 13, lineHeight: 1.65 }}>
            {t('Choose what you want to work on. Detailed status remains in each Studio module.')}
          </p>
        </header>

        <section aria-labelledby="home-start-title">
          <SectionHeading id="home-start-title" title={t('Start here')} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 10 }}>
            {actions.map(item => (
              <ActionCard
                key={item.kind}
                {...item}
                onClick={() => setScreen(item.destination)}
              />
            ))}
          </div>
        </section>

        <section aria-labelledby="home-continue-title" style={{ marginTop: 30 }}>
          <SectionHeading
            id="home-continue-title"
            title={t('Continue local work')}
            action={projects.length > 0 ? t('View all') : undefined}
            onAction={() => setScreen('workspace')}
          />
          {projects.length > 0 ? (
            <div style={{ overflow: 'hidden', border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.surface }}>
              {projects.slice(0, 3).map((project, index) => (
                <ProjectRow
                  key={project.localProjectId}
                  project={project}
                  last={index === Math.min(projects.length, 3) - 1}
                  onClick={() => onOpenProject(project)}
                />
              ))}
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: 18, padding: '18px 20px', border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.surface }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 13, fontWeight: 600 }}>
                  {t('No local Workspaces yet')}
                </div>
                <div style={{ marginTop: 4, color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 12, lineHeight: 1.5 }}>
                  {t('Create a local project or open a FedOps Web Draft to begin.')}
                </div>
              </div>
              <Button variant="primary" onClick={() => setScreen('workspace')}>
                {t('Open Workspace')}
              </Button>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}

function SectionHeading({ id, title, action, onAction }: { id: string; title: string; action?: string; onAction?: () => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', minHeight: 29, marginBottom: 9 }}>
      <h2 id={id} style={{ flex: 1, margin: 0, color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 14, fontWeight: 650 }}>
        {title}
      </h2>
      {action && onAction && (
        <button type="button" onClick={onAction} style={textButtonStyle}>
          {action} →
        </button>
      )}
    </div>
  )
}

function ActionCard({ kind, title, description, action, onClick }: {
  kind: ActionKind
  title: string
  description: string
  action: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ minHeight: 174, padding: 16, border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.surface, color: C.text, cursor: 'pointer', textAlign: 'left', boxShadow: C.shadow }}
      onMouseEnter={event => { event.currentTarget.style.borderColor = C.accentBorder }}
      onMouseLeave={event => { event.currentTarget.style.borderColor = C.border }}
    >
      <ActionIcon kind={kind} />
      <div style={{ marginTop: 14, fontFamily: 'Inter, sans-serif', fontSize: 14, fontWeight: 650 }}>{title}</div>
      <div style={{ minHeight: 40, marginTop: 6, color: C.muted, fontFamily: 'Inter, sans-serif', fontSize: 12, lineHeight: 1.55 }}>{description}</div>
      <div style={{ marginTop: 14, color: C.accent, fontFamily: 'Inter, sans-serif', fontSize: 12, fontWeight: 600 }}>{action} →</div>
    </button>
  )
}

function ProjectRow({ project, last, onClick }: { project: WorkspaceProject; last: boolean; onClick: () => void }) {
  const { t } = useI18n()
  const binding = project.taskBinding
  const role = binding?.workspaceRole ?? (binding?.releaseId ? 'participant' : binding ? 'owner' : null)
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ width: '100%', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12, padding: '12px 14px', borderTop: 'none', borderRight: 'none', borderBottom: last ? 'none' : `1px solid ${C.borderSubtle}`, borderLeft: 'none', background: C.surface, cursor: 'pointer', textAlign: 'left' }}
      onMouseEnter={event => { event.currentTarget.style.background = C.surface2 }}
      onMouseLeave={event => { event.currentTarget.style.background = C.surface }}
    >
      <div style={{ width: 32, height: 32, display: 'grid', placeItems: 'center', flexShrink: 0, border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.surface2, color: C.accent }}>
        <MiniWorkspaceIcon />
      </div>
      <div style={{ flex: '1 1 220px', minWidth: 0 }}>
        <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: C.text, fontFamily: 'Inter, sans-serif', fontSize: 13, fontWeight: 600 }}>
          {binding?.displayName || project.name}
        </div>
        <div style={{ marginTop: 3, color: C.dim, fontFamily: 'Inter, sans-serif', fontSize: 11 }}>
          {t(binding ? 'Linked to FedOps Web' : 'Local project')}
        </div>
      </div>
      {role && <Badge variant={role === 'owner' ? 'purple' : 'blue'}>{t(role)}</Badge>}
      <Badge variant={project.projectEnvironmentReady ? 'green' : 'gray'}>
        {t(project.projectEnvironmentReady ? 'Environment ready' : 'Environment setup needed')}
      </Badge>
      <span style={{ color: C.accent, fontFamily: 'Inter, sans-serif', fontSize: 12, whiteSpace: 'nowrap' }}>{t('Continue')} →</span>
    </button>
  )
}

function ActionIcon({ kind }: { kind: ActionKind }) {
  const paths: Record<ActionKind, React.ReactNode> = {
    workspace: <><rect x="4.5" y="5" width="15" height="14" rx="2" /><path d="M8 9h8M8 13h5" /></>,
    federation: <><circle cx="6.5" cy="12" r="2.5" /><circle cx="17.5" cy="7" r="2.5" /><circle cx="17.5" cy="17" r="2.5" /><path d="M8.8 10.9l6.4-2.8M8.8 13.1l6.4 2.8" /></>,
    agent: <><path d="M12 3.8l1.5 4.1 4.2 1.5-4.2 1.5-1.5 4.2-1.5-4.2-4.2-1.5 4.2-1.5L12 3.8z" /><path d="M18.5 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z" /></>,
  }
  return (
    <div style={{ width: 36, height: 36, display: 'grid', placeItems: 'center', border: `1px solid ${C.accentBorder}`, borderRadius: C.radius, background: C.accentDim, color: C.accent }}>
      <svg aria-hidden="true" viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">
        {paths[kind]}
      </svg>
    </div>
  )
}

function MiniWorkspaceIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><rect x="4.5" y="5" width="15" height="14" rx="2" /><path d="M8 9h8M8 13h5" /></svg>
}

const eyebrowStyle: CSSProperties = { color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, fontWeight: 600, letterSpacing: '0.12em' }
const textButtonStyle: CSSProperties = { padding: '3px 0', border: 'none', background: 'transparent', color: C.accent, cursor: 'pointer', fontFamily: 'Inter, sans-serif', fontSize: 12 }
