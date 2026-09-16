import { useEffect, useMemo, useRef, useState } from 'react'
import {
  buildAgent,
  createAgentDraft,
  deleteAgentDraft,
  getAgentDraftLlmPreparation,
  listAgentDrafts,
  listAgentModelSources,
  listAgentRegistryModelSources,
  prepareAgentModelVersion,
  prepareAgentDraftLlm,
  requestAgentRegistryParticipation,
  streamAgentDraftChat,
  testAgentDraft,
  updateAgentDraft,
  validateAgentDraft,
  type AgentDraft,
  type AgentChatMessage,
  type AgentChatResponse,
  type AgentChatStreamEvent,
  type AgentLlm,
  type AgentLlmPreparation,
  type AgentModelSource,
  type AgentRegistryModelSource,
  type AgentToolModel,
} from '../../api/agents'
import type { Screen } from '../../app/types'
import { useI18n } from '../../app/i18n'
import { Badge, Button, C, MetaRow, SectionLabel } from '../../ui/UIKit'
import TaskDataPanel from '../../ui/TaskDataPanel'

type BuilderTab = 'Models' | 'Agent Harness' | 'Test & Build'
type BaseLlmSource = 'federated-task' | 'huggingface'

export default function AgentBuilder({ onNavigate, onOpenRegistry }: { onNavigate: (screen: Screen) => void; onOpenRegistry: (source: AgentRegistryModelSource) => void }) {
  const { t } = useI18n()
  const [drafts, setDrafts] = useState<AgentDraft[]>([])
  const [sources, setSources] = useState<AgentModelSource[]>([])
  const [registrySources, setRegistrySources] = useState<AgentRegistryModelSource[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tab, setTab] = useState<BuilderTab>('Models')
  const [name, setName] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [draftsLoading, setDraftsLoading] = useState(true)
  const [sourcesLoading, setSourcesLoading] = useState(true)
  const [registrySourcesLoading, setRegistrySourcesLoading] = useState(true)
  const [registrySourcesError, setRegistrySourcesError] = useState<string | null>(null)
  const [registrySourcesRevision, setRegistrySourcesRevision] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const selected = drafts.find(draft => draft.agentId === selectedId) ?? null

  useEffect(() => {
    const controller = new AbortController()
    listAgentDrafts(controller.signal)
      .then(result => setDrafts(result.items))
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => { if (!controller.signal.aborted) setDraftsLoading(false) })
    listAgentModelSources(controller.signal)
      .then(result => setSources(result.items))
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => { if (!controller.signal.aborted) setSourcesLoading(false) })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setRegistrySourcesLoading(true)
    setRegistrySourcesError(null)
    listAgentRegistryModelSources(controller.signal)
      .then(result => setRegistrySources(result.items))
      .catch(reason => {
        if (!controller.signal.aborted) setRegistrySourcesError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => { if (!controller.signal.aborted) setRegistrySourcesLoading(false) })
    return () => controller.abort()
  }, [registrySourcesRevision])

  function replaceDraft(next: AgentDraft) {
    setDrafts(current => current.map(item => item.agentId === next.agentId ? next : item))
  }

  async function createDraft() {
    if (!name.trim()) return
    setBusy('create'); setError(null)
    try {
      const created = await createAgentDraft(name.trim())
      setDrafts(current => [created, ...current])
      setSelectedId(created.agentId)
      setName('')
      setShowCreate(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(null) }
  }

  async function saveDraft(draft: AgentDraft) {
    setBusy('save'); setError(null); setMessage(null)
    try {
      const saved = await updateAgentDraft(draft)
      replaceDraft(saved)
      setMessage(t('Agent draft saved.'))
      return saved
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      return null
    } finally { setBusy(null) }
  }

  async function runValidation(draft: AgentDraft) {
    setBusy('validate'); setError(null); setMessage(null)
    try {
      const saved = await updateAgentDraft(draft)
      const validation = await validateAgentDraft(draft.agentId)
      replaceDraft({ ...saved, validation, status: validation.ok ? 'ready' : 'validation-required' })
      setMessage(validation.ok ? t('Agent validation passed.') : t('Agent validation has failed checks.'))
      setTab('Test & Build')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(null) }
  }

  async function runBuild(draft: AgentDraft) {
    setBusy('build'); setError(null); setMessage(null)
    try {
      await updateAgentDraft(draft)
      const built = await buildAgent(draft.agentId)
      replaceDraft({ ...draft, status: 'built', buildRevision: built.buildRevision, updatedAt: built.builtAt })
      setMessage(t('Immutable Agent build completed. Open it in Agents to test or serve it.'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(null) }
  }

  async function saveForPreview(draft: AgentDraft) {
    const saved = await updateAgentDraft(draft)
    replaceDraft(saved)
    return saved
  }

  async function runDraftTest(
    draft: AgentDraft,
    prompt: string,
    toolInput: Record<string, unknown> | null,
    toolId: string | null,
  ): Promise<AgentChatResponse | null> {
    setBusy('draft-test'); setError(null); setMessage(null)
    try {
      const saved = await saveForPreview(draft)
      const result = await testAgentDraft(saved.agentId, prompt, toolInput, toolId)
      setMessage(t('Draft test completed.'))
      return result
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      return null
    } finally { setBusy(null) }
  }

  async function runDraftChat(
    draft: AgentDraft,
    prompt: string,
    toolInput: Record<string, unknown> | null,
    toolId: string | null,
    history: AgentChatMessage[],
    onEvent: (event: AgentChatStreamEvent) => void,
    signal: AbortSignal,
  ): Promise<AgentChatResponse | null> {
    setBusy('draft-chat'); setError(null); setMessage(null)
    try {
      const saved = await saveForPreview(draft)
      const completion: { result: AgentChatResponse | null } = { result: null }
      await streamAgentDraftChat(saved.agentId, prompt, toolInput, history, event => {
        if (event.type === 'error') throw new Error(event.detail)
        if (event.type === 'completed') completion.result = event.result
        onEvent(event)
      }, signal, toolId)
      return completion.result
    } catch (reason) {
      if (signal.aborted) {
        setMessage(t('Response generation stopped.'))
        return null
      }
      setError(reason instanceof Error ? reason.message : String(reason))
      return null
    } finally { setBusy(null) }
  }

  async function prepareDraftLlm(
    draft: AgentDraft,
    onProgress: (progress: AgentLlmPreparation) => void,
  ): Promise<AgentLlmPreparation | null> {
    setBusy('draft-prepare'); setError(null); setMessage(null)
    try {
      const saved = await saveForPreview(draft)
      let runtime = await prepareAgentDraftLlm(saved.agentId)
      onProgress(runtime)
      while (runtime.status === 'queued' || runtime.status === 'running') {
        await new Promise(resolve => window.setTimeout(resolve, 800))
        runtime = await getAgentDraftLlmPreparation(saved.agentId)
        onProgress(runtime)
      }
      if (runtime.status === 'failed') throw new Error(runtime.detail)
      if (saved.llm) {
        replaceDraft({
          ...saved,
          llm: { ...saved.llm, runtimeStatus: 'installed', localPath: runtime.localPath || saved.llm.localPath } as AgentLlm,
        })
      }
      setMessage(t('Base LLM is ready in the account model directory.'))
      return runtime
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      return null
    } finally { setBusy(null) }
  }

  async function removeDraft(draft: AgentDraft) {
    if (!window.confirm(t('Delete this Agent draft? Built Agents must be removed from Agents first.'))) return
    setBusy('delete'); setError(null)
    try {
      await deleteAgentDraft(draft.agentId)
      setDrafts(current => current.filter(item => item.agentId !== draft.agentId))
      setSelectedId(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(null) }
  }

  if (!selected) {
    return (
      <div style={pageStyle}>
        <header style={pageHeaderStyle}>
          <div>
            <div style={eyebrowStyle}>{t('AGENT BUILDER')}</div>
            <h1 style={titleStyle}>{t('Build a Federated AI Agent')}</h1>
            <p style={subtitleStyle}>{t('Combine an exact Published Federated Task model with a Base LLM and a versioned Agent Harness.')}</p>
          </div>
          <div className="studio-responsive-actions"><Button variant="ghost" onClick={() => onNavigate('agents')}>{t('Open Agents')}</Button><Button variant="primary" onClick={() => setShowCreate(true)}>{t('+ New Agent')}</Button></div>
        </header>
        <Notice error={error} message={message} />
        <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
          {draftsLoading && <Empty title={t('Loading Agent drafts…')} detail={t('The Builder is ready while local Agent metadata is being read.')} />}
          {!draftsLoading && drafts.length === 0 && <Empty title={t('No Agent drafts yet')} detail={t('Create an Agent to select a Base LLM, optional local Tool AI projects, and its Harness.')} />}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
            {drafts.map(draft => (
              <button key={draft.agentId} onClick={() => setSelectedId(draft.agentId)} style={draftCardStyle}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <strong style={{ color: C.text, fontSize: 15 }}>{draft.name}</strong>
                  <Badge variant={draft.status === 'built' ? 'green' : draft.status === 'ready' ? 'blue' : draft.status === 'validation-required' ? 'yellow' : 'gray'}>{draft.status}</Badge>
                </div>
                <p style={{ color: C.muted, fontSize: 12, lineHeight: 1.55, minHeight: 38 }}>{draft.description || t('No description')}</p>
                <MetaRow label={t('Base LLM')} value={draft.llm ? llmName(draft.llm) : t('Not selected')} mono />
                <MetaRow label={t('Tool AI')} value={String(draft.tools.length)} />
                <MetaRow label={t('Build revision')} value={draft.buildRevision ? `r${draft.buildRevision}` : '—'} mono />
              </button>
            ))}
          </div>
        </div>
        {showCreate && <div style={modalBackdropStyle} onMouseDown={() => setShowCreate(false)}><section style={modalStyle} onMouseDown={event => event.stopPropagation()}><div style={eyebrowStyle}>{t('NEW AGENT')}</div><h2 style={{ ...titleStyle, fontSize: 20 }}>{t('Create an Agent draft')}</h2><p style={subtitleStyle}>{t('Name the Agent first. Models and Harness are selected in the Builder.')}</p><Field label={t('Agent Name')}><input autoFocus value={name} onChange={event => setName(event.target.value)} onKeyDown={event => event.key === 'Enter' && void createDraft()} placeholder={t('e.g. MNIST Assistant')} style={{ ...inputStyle, width: '100%' }} /></Field><div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 18 }}><Button variant="ghost" onClick={() => setShowCreate(false)}>{t('Cancel')}</Button><Button variant="primary" disabled={!name.trim() || busy === 'create'} onClick={() => void createDraft()}>{busy === 'create' ? t('Creating…') : t('Create Agent')}</Button></div></section></div>}
      </div>
    )
  }

  return (
    <div style={pageStyle}>
      <div style={{ ...pageHeaderStyle, padding: '8px 14px' }}>
        <button onClick={() => setSelectedId(null)} style={backStyle}>‹</button>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <strong style={{ color: C.text, fontSize: 15 }}>{selected.name}</strong>
            <span style={monoDimStyle}>{selected.agentId}</span>
            <Badge variant={selected.status === 'built' ? 'green' : selected.status === 'ready' ? 'blue' : 'gray'}>{selected.status}</Badge>
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <Button variant="ghost" disabled={Boolean(busy)} onClick={() => void removeDraft(selected)}>{t('Delete')}</Button>
        <Button variant="ghost" disabled={Boolean(busy)} onClick={() => void saveDraft(selected)}>{busy === 'save' ? t('Saving…') : t('Save')}</Button>
        <Button variant="ghost" disabled={Boolean(busy)} onClick={() => void runValidation(selected)}>{busy === 'validate' ? t('Validating…') : t('Validate')}</Button>
        <Button variant="primary" disabled={Boolean(busy) || !selected.llm} onClick={() => void runBuild(selected)}>{busy === 'build' ? t('Building…') : t('Build Agent')}</Button>
      </div>
      <div style={tabsStyle}>
        {(['Models', 'Agent Harness', 'Test & Build'] as BuilderTab[]).map(item => (
          <button key={item} onClick={() => setTab(item)} style={tabStyle(tab === item)}>{t(item)}</button>
        ))}
      </div>
      <Notice error={error} message={message} />
      {tab === 'Models' && <ModelsTab
        draft={selected}
        sources={sources}
        sourcesLoading={sourcesLoading}
        registrySources={registrySources}
        registrySourcesLoading={registrySourcesLoading}
        registrySourcesError={registrySourcesError}
        refreshRegistrySources={() => setRegistrySourcesRevision(value => value + 1)}
        onSourcePrepared={prepared => setSources(current => current.map(source => source.localProjectId === prepared.localProjectId ? prepared : source))}
        onOpenRegistry={onOpenRegistry}
        update={replaceDraft}
      />}
      {tab === 'Agent Harness' && <HarnessTab draft={selected} update={replaceDraft} />}
      {tab === 'Test & Build' && <TestBuildTab
        draft={selected}
        busy={busy}
        validate={() => void runValidation(selected)}
        build={() => void runBuild(selected)}
        prepare={onProgress => prepareDraftLlm(selected, onProgress)}
        test={(prompt, toolInput, toolId) => runDraftTest(selected, prompt, toolInput, toolId)}
        chat={(prompt, toolInput, toolId, history, onEvent, signal) => runDraftChat(selected, prompt, toolInput, toolId, history, onEvent, signal)}
        openAgents={() => onNavigate('agents')}
      />}
    </div>
  )
}

function ModelsTab({ draft, sources, sourcesLoading, registrySources, registrySourcesLoading, registrySourcesError, refreshRegistrySources, onSourcePrepared, onOpenRegistry, update }: {
  draft: AgentDraft
  sources: AgentModelSource[]
  sourcesLoading: boolean
  registrySources: AgentRegistryModelSource[]
  registrySourcesLoading: boolean
  registrySourcesError: string | null
  refreshRegistrySources: () => void
  onSourcePrepared: (source: AgentModelSource) => void
  onOpenRegistry: (source: AgentRegistryModelSource) => void
  update: (draft: AgentDraft) => void
}) {
  const { t } = useI18n()
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null)
  const [selectedRegistryId, setSelectedRegistryId] = useState<string | null>(null)
  const [registryActionId, setRegistryActionId] = useState<string | null>(null)
  const [registryActionMessage, setRegistryActionMessage] = useState<string | null>(null)
  const [selectedModelVersionId, setSelectedModelVersionId] = useState<string | null>(null)
  const [preparingModelVersionId, setPreparingModelVersionId] = useState<string | null>(null)
  const [baseLlmSource, setBaseLlmSource] = useState<BaseLlmSource | null>(draft.llm?.source ?? null)
  const [repoId, setRepoId] = useState('bartowski/Qwen_Qwen3.5-4B-GGUF')
  const [revision, setRevision] = useState('main')
  const [modelFormat, setModelFormat] = useState<'gguf' | 'transformers'>('gguf')
  const [fileName, setFileName] = useState('Qwen_Qwen3.5-4B-Q4_K_M.gguf')
  const selectedSource = sources.find(source => source.localProjectId === selectedSourceId) ?? null
  const selectedRegistrySource = registrySources.find(source => source.registryId === selectedRegistryId) ?? null
  const selectedModelVersion = selectedSource?.availableModelVersions.find(version => version.modelVersionId === selectedModelVersionId)
    ?? selectedSource?.availableModelVersions.find(version => version.modelVersionId === selectedSource.modelVersionId)
    ?? selectedSource?.availableModelVersions[0]
    ?? null
  const localLlms = useMemo(() => sources.filter(source => source.capability === 'base-llm'), [sources])
  const localTools = useMemo(() => sources.filter(source => source.capability === 'tool-ai'), [sources])
  const localTaskIds = useMemo(() => new Set(sources.map(source => source.taskId).filter(Boolean)), [sources])
  const localRegistryIds = useMemo(() => new Set(sources.map(source => source.registryId).filter(Boolean)), [sources])
  const remoteSources = useMemo(() => registrySources.filter(source => !localTaskIds.has(source.taskId) && !localRegistryIds.has(source.registryId)), [localRegistryIds, localTaskIds, registrySources])
  const myRemoteSources = useMemo(() => remoteSources.filter(source => source.accessState === 'workspace-required'), [remoteSources])
  const pendingSources = useMemo(() => remoteSources.filter(source => source.accessState === 'approval-pending'), [remoteSources])
  const joinableSources = useMemo(() => remoteSources.filter(source => source.accessState === 'join-required'), [remoteSources])
  const unavailableSources = useMemo(() => remoteSources.filter(source => source.accessState === 'unavailable'), [remoteSources])

  useEffect(() => {
    setBaseLlmSource(draft.llm?.source ?? null)
    setSelectedSourceId(draft.llm?.source === 'federated-task' ? draft.llm.localProjectId : null)
  }, [draft.agentId])

  useEffect(() => {
    setSelectedModelVersionId(selectedSource?.modelVersionId ?? selectedSource?.availableModelVersions[0]?.modelVersionId ?? null)
  }, [selectedSourceId])

  function changed(value: Partial<AgentDraft>): AgentDraft {
    return { ...draft, ...value, validation: null, status: draft.buildRevision ? 'modified' : 'draft' }
  }

  function selectHuggingFace() {
    const cleanRepo = repoId.trim()
    const cleanFile = fileName.trim()
    if (!cleanRepo || !revision.trim() || (modelFormat === 'gguf' && !cleanFile.endsWith('.gguf'))) return
    const llm: AgentLlm = {
      source: 'huggingface', repoId: cleanRepo, revision: revision.trim(),
      fileName: modelFormat === 'gguf' ? cleanFile : null, format: modelFormat,
      displayName: modelFormat === 'gguf'
        ? cleanFile.replace(/\.gguf$/i, '').replace(/^Qwen_/, '')
        : cleanRepo.split('/').pop() || cleanRepo,
      license: cleanRepo.includes('Qwen3.5-4B') ? 'apache-2.0' : 'unknown',
      localPath: null, runtimeStatus: 'not-prepared',
    }
    update(changed({ llm }))
  }

  function selectFederatedLlm(source: AgentModelSource) {
    if (!source.ready || !source.sourceFingerprint || !source.modelSha256 || !source.localPath) return
    update(changed({ llm: {
      source: 'federated-task', localProjectId: source.localProjectId,
      projectName: source.projectName, taskId: source.taskId, taskTitle: source.taskTitle,
      modelName: source.modelName, releaseId: source.releaseId,
      modelVersionId: source.modelVersionId, sourceFingerprint: source.sourceFingerprint,
      modelSha256: source.modelSha256, localPath: source.localPath,
      modelArtifactPath: source.modelArtifactPath, runtimeStatus: 'installed',
    } }))
  }

  function addTool(source: AgentModelSource) {
    if (!source.ready || !source.sourceFingerprint || !source.modelSha256 || !source.localPath || !source.toolManifest) return
    const tool: AgentToolModel = {
      localProjectId: source.localProjectId, projectName: source.projectName,
      taskId: source.taskId, runtimeKey: source.runtimeKey, registryId: source.registryId,
      taskTitle: source.taskTitle, releaseId: source.releaseId,
      modelVersionId: source.modelVersionId, modelName: source.modelName,
      modelVersion: source.modelVersion, format: source.format,
      sourceFingerprint: source.sourceFingerprint, modelSha256: source.modelSha256,
      localPath: source.localPath, toolManifest: source.toolManifest,
      modelArtifactPath: source.modelArtifactPath,
    }
    update(changed({ tools: [...draft.tools.filter(item => item.localProjectId !== tool.localProjectId), tool] }))
  }

  function selectedVersionSource(source: AgentModelSource): AgentModelSource | null {
    if (!selectedModelVersion?.cached || !selectedModelVersion.sha256) return null
    return {
      ...source,
      modelVersionId: selectedModelVersion.modelVersionId,
      modelVersion: selectedModelVersion.version,
      format: selectedModelVersion.format,
      modelSha256: selectedModelVersion.sha256,
      modelArtifactPath: selectedModelVersion.modelArtifactPath,
    }
  }

  async function prepareSelectedVersion(source: AgentModelSource) {
    if (!selectedModelVersion?.modelVersionId) return
    setPreparingModelVersionId(selectedModelVersion.modelVersionId)
    setRegistryActionMessage(null)
    try {
      const prepared = await prepareAgentModelVersion(source.localProjectId, selectedModelVersion.modelVersionId)
      onSourcePrepared(prepared)
      setRegistryActionMessage(t('{{label}} is ready on this device.', { label: selectedModelVersion.label }))
    } catch (reason) {
      setRegistryActionMessage(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setPreparingModelVersionId(null)
    }
  }

  async function requestParticipation(source: AgentRegistryModelSource) {
    setRegistryActionId(source.registryId)
    setRegistryActionMessage(null)
    try {
      const result = await requestAgentRegistryParticipation(source)
      setRegistryActionMessage(t('Participation status: {{status}}', { status: t(String(result.status || 'requested')) }))
      refreshRegistrySources()
    } catch (reason) {
      setRegistryActionMessage(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setRegistryActionId(null)
    }
  }

  return (
    <div className="agent-models-viewport">
      <div className="agent-models-layout">
        <aside className="agent-models-selection scroll-area">
          <div style={railHeaderStyle}>
            <SectionLabel>{t('SELECTED COMPOSITION')}</SectionLabel>
            <p style={railDescriptionStyle}>{t('One Base LLM and any local Tool AI models included in this Agent Draft.')}</p>
          </div>
          <div style={railBodyStyle}>
            <SectionLabel>{t('Base LLM · exactly one')}</SectionLabel>
            {draft.llm ? <div style={bindingCardStyle}>
              <div style={bindingCardHeaderStyle}>
                <div style={{ minWidth: 0 }}><strong style={bindingTitleStyle}>{llmName(draft.llm)}</strong><div title={draft.llm.source === 'huggingface' ? `${draft.llm.repoId}@${draft.llm.revision}` : draft.llm.localProjectId} style={bindingIdentityStyle}>{draft.llm.source === 'huggingface' ? `${draft.llm.repoId}@${draft.llm.revision}` : draft.llm.localProjectId}</div></div>
                <Badge variant="purple">{t('Base LLM')}</Badge>
              </div>
              <div style={bindingDetailsStyle}>
                <CompactMeta label={t('Source')} value={draft.llm.source === 'huggingface' ? 'Hugging Face' : 'Federated Task'} />
                {draft.llm.source === 'huggingface' && <CompactMeta label={t('Runtime')} value={draft.llm.fileName ? `GGUF · ${draft.llm.fileName}` : 'Transformers'} mono />}
                <CompactMeta label={t('Storage')} value={draft.llm.localPath || t('Prepare in Test & Build')} mono />
              </div>
              <div style={bindingFooterStyle}><Button variant="danger" style={compactButtonStyle} onClick={() => update(changed({ llm: null }))}>{t('Remove')}</Button></div>
            </div> : <div style={dashedStyle}>{t('Select one Hugging Face model or local LLM Federated Task.')}</div>}

            <div style={sectionHeadingStyle}><SectionLabel>{t('Tool AI · zero or more')}</SectionLabel><Badge variant="gray">{draft.tools.length}</Badge></div>
            {draft.tools.map(tool => <div key={tool.localProjectId} style={bindingCardStyle}>
              <div style={bindingCardHeaderStyle}>
                <div style={{ minWidth: 0 }}><strong style={bindingTitleStyle}>{tool.modelName}</strong><div title={tool.localProjectId} style={bindingIdentityStyle}>{tool.localProjectId}</div></div>
                <Badge variant="blue">{t('Tool AI')}</Badge>
              </div>
              <div style={bindingDetailsStyle}><CompactMeta label={t('Workspace')} value={tool.localPath} mono /></div>
              <div style={bindingFooterStyle}><Button variant="danger" style={compactButtonStyle} onClick={() => update(changed({ tools: draft.tools.filter(item => item.localProjectId !== tool.localProjectId) }))}>{t('Remove')}</Button></div>
            </div>)}
            {draft.tools.length === 0 && <div style={dashedStyle}>{t('Tool AI is optional. Select a ready local Federated Task when needed.')}</div>}
          </div>
        </aside>

        <aside className="agent-models-sources">
          <div style={railHeaderStyle}><SectionLabel>{t('FEDERATED MODEL SOURCES')}</SectionLabel><p style={railDescriptionStyle}>{t('Workspace-ready models and published Registry options for this account.')}</p></div>
          <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
            {sourcesLoading && <div style={{ padding: '10px 14px', color: C.accent, fontSize: 11 }}>{t('Checking local model sources…')}</div>}
            <SourceGroupLabel label={t('WORKSPACE · LLM')} count={localLlms.length} />
            {localLlms.map(source => <SourceRow key={source.localProjectId} source={source} selected={selectedSourceId === source.localProjectId} select={() => { setBaseLlmSource('federated-task'); setSelectedRegistryId(null); setSelectedSourceId(source.localProjectId) }} />)}
            {!sourcesLoading && localLlms.length === 0 && <div style={emptySourceStyle}>{t('No local LLM Federated Tasks.')}</div>}
            <SourceGroupLabel label={t('WORKSPACE · TOOL AI')} count={localTools.length} />
            {localTools.map(source => <SourceRow key={source.localProjectId} source={source} selected={selectedSourceId === source.localProjectId} select={() => { setSelectedRegistryId(null); setSelectedSourceId(source.localProjectId) }} />)}
            {!sourcesLoading && localTools.length === 0 && <div style={emptySourceStyle}>{t('No local Tool AI projects.')}</div>}
            {registrySourcesLoading && <div style={{ padding: '12px 14px', color: C.purple, fontSize: 11 }}>{t('Loading Registry model options…')}</div>}
            {registrySourcesError && <div style={{ ...emptySourceStyle, color: C.red, borderColor: C.redBorder }}>{registrySourcesError}</div>}
            {!registrySourcesLoading && <>
              <SourceGroupLabel label={t('MY TASKS · OPEN WORKSPACE')} count={myRemoteSources.length} />
              {myRemoteSources.map(source => <RegistrySourceRow key={source.registryId} source={source} selected={selectedRegistryId === source.registryId} select={() => { setRegistryActionMessage(null); setSelectedSourceId(null); setSelectedRegistryId(source.registryId) }} />)}
              {myRemoteSources.length === 0 && <div style={emptySourceStyle}>{t('All available Task models are already local, or none are published.')}</div>}
              <SourceGroupLabel label={t('REGISTRY · JOIN REQUIRED')} count={joinableSources.length} />
              {joinableSources.map(source => <RegistrySourceRow key={source.registryId} source={source} selected={selectedRegistryId === source.registryId} select={() => { setRegistryActionMessage(null); setSelectedSourceId(null); setSelectedRegistryId(source.registryId) }} />)}
              {joinableSources.length === 0 && <div style={emptySourceStyle}>{t('No additional joinable Registry models.')}</div>}
              {pendingSources.length > 0 && <><SourceGroupLabel label={t('APPROVAL PENDING')} count={pendingSources.length} />{pendingSources.map(source => <RegistrySourceRow key={source.registryId} source={source} selected={selectedRegistryId === source.registryId} select={() => { setRegistryActionMessage(null); setSelectedSourceId(null); setSelectedRegistryId(source.registryId) }} />)}</>}
              {unavailableSources.length > 0 && <><SourceGroupLabel label={t('REGISTRY · VIEW ONLY')} count={unavailableSources.length} />{unavailableSources.map(source => <RegistrySourceRow key={source.registryId} source={source} selected={selectedRegistryId === source.registryId} select={() => { setRegistryActionMessage(null); setSelectedSourceId(null); setSelectedRegistryId(source.registryId) }} />)}</>}
            </>}
          </div>
        </aside>

        <main className="agent-models-config scroll-area">
          <div style={configContentStyle}>
            <section style={configurationCardStyle}>
              <div style={configurationHeaderStyle}><div><div style={eyebrowStyle}>{t('BASE LLM SOURCE')}</div><h2 style={{ ...titleStyle, fontSize: 20 }}>{t('Choose where the Base LLM comes from')}</h2></div><Badge variant="purple">{t('Exactly one')}</Badge></div>
              <p style={subtitleStyle}>{t('Select a released LLM Federated Task from this device, or configure an external Hugging Face model.')}</p>
              <div style={baseLlmSourceGridStyle}>
                <button type="button" onClick={() => { setBaseLlmSource('federated-task'); setSelectedRegistryId(null); setSelectedSourceId(null) }} style={baseLlmSourceCardStyle(baseLlmSource === 'federated-task', 'federated-task')}>
                  <span style={baseLlmSourceIconStyle('federated-task')}>LLM</span>
                  <span><strong style={baseLlmSourceTitleStyle}>{t('Federated Task LLM')}</strong><small style={baseLlmSourceDetailStyle}>{t('Use a released LLM already available in the local Workspace.')}</small></span>
                  <span style={baseLlmSourceCheckStyle(baseLlmSource === 'federated-task')}>{baseLlmSource === 'federated-task' ? '✓' : '›'}</span>
                </button>
                <button type="button" onClick={() => { setBaseLlmSource('huggingface'); setSelectedRegistryId(null); setSelectedSourceId(null) }} style={baseLlmSourceCardStyle(baseLlmSource === 'huggingface', 'huggingface')}>
                  <span style={baseLlmSourceIconStyle('huggingface')}>HF</span>
                  <span><strong style={baseLlmSourceTitleStyle}>{t('Hugging Face LLM')}</strong><small style={baseLlmSourceDetailStyle}>{t('Download a repository revision into the account-local model directory.')}</small></span>
                  <span style={baseLlmSourceCheckStyle(baseLlmSource === 'huggingface')}>{baseLlmSource === 'huggingface' ? '✓' : '›'}</span>
                </button>
              </div>
            </section>
            {baseLlmSource === 'huggingface' && <section style={configurationCardStyle}>
              <div style={configurationHeaderStyle}><div><div style={eyebrowStyle}>{t('HUGGING FACE BASE LLM')}</div><h2 style={{ ...titleStyle, fontSize: 20 }}>{t('Select a repository and revision')}</h2></div><Badge variant="purple">{t('External model')}</Badge></div>
              <p style={subtitleStyle}>{t('The default test model is the smaller Qwen3.5-4B Q4_K_M GGUF. Only the selected GGUF file is downloaded.')}</p>
              <div style={modelConfigurationGridStyle}>
                <Field label={t('Repository ID')}><input value={repoId} onChange={event => setRepoId(event.target.value)} style={{ ...inputStyle, width: '100%' }} /></Field>
                <Field label={t('Revision')}><input value={revision} onChange={event => setRevision(event.target.value)} style={{ ...inputStyle, width: '100%' }} /></Field>
                <Field label={t('Runtime format')}><select value={modelFormat} onChange={event => setModelFormat(event.target.value as 'gguf' | 'transformers')} style={{ ...inputStyle, width: '100%' }}><option value="gguf">GGUF · llama.cpp</option><option value="transformers">Transformers</option></select></Field>
                {modelFormat === 'gguf' && <Field label={t('GGUF filename')}><input value={fileName} onChange={event => setFileName(event.target.value)} style={{ ...inputStyle, width: '100%' }} /></Field>}
              </div>
              <div style={configurationFooterStyle}><span style={{ color: C.dim, fontSize: 11 }}>{t('This replaces only the Draft Base LLM selection.')}</span><Button variant="primary" disabled={modelFormat === 'gguf' && !fileName.trim().endsWith('.gguf')} onClick={selectHuggingFace}>{t('Use as Base LLM')}</Button></div>
            </section>}
            {selectedRegistrySource && <section style={configurationCardStyle}>
              <div style={configurationHeaderStyle}><div><div style={eyebrowStyle}>{t('REGISTRY FEDERATED TASK')}</div><h2 style={{ ...titleStyle, fontSize: 20 }}>{selectedRegistrySource.modelName}</h2><div style={bindingIdentityStyle}>@{selectedRegistrySource.ownerHandle || 'unknown'} / {selectedRegistrySource.slug || selectedRegistrySource.registryId}</div></div><Badge variant={selectedRegistrySource.capability === 'base-llm' ? 'purple' : 'blue'}>{selectedRegistrySource.capability === 'base-llm' ? 'LLM' : 'AI'}</Badge></div>
              <p style={subtitleStyle}>{selectedRegistrySource.summary || t('No description has been provided.')}</p>
              <div style={selectedSourceDetailsStyle}>
                <CompactMeta label={t('Agent role')} value={selectedRegistrySource.capability === 'base-llm' ? t('Base LLM') : t('Tool AI')} />
                <CompactMeta label={t('Access')} value={t(registryAccessLabel(selectedRegistrySource.accessState))} />
                <CompactMeta label={t('Participation')} value={t(selectedRegistrySource.participationStatus || selectedRegistrySource.participationPolicy)} />
              </div>
              <p style={{ ...subtitleStyle, marginBottom: 0 }}>{t('A Federated Task must be owned or approved for participation, then opened in Workspace before Agent Builder can use its released model and code.')}</p>
              {registryActionMessage && <div style={{ marginTop: 10, color: C.yellow, fontSize: 11 }}>{registryActionMessage}</div>}
              <div style={configurationFooterStyle}>
                {selectedRegistrySource.accessState !== 'workspace-required' && <Button variant="ghost" onClick={() => onOpenRegistry(selectedRegistrySource)}>{t('Open in Registry')}</Button>}
                {selectedRegistrySource.accessState === 'join-required' && <Button variant="primary" disabled={registryActionId === selectedRegistrySource.registryId} onClick={() => void requestParticipation(selectedRegistrySource)}>{t(registryActionId === selectedRegistrySource.registryId ? 'Requesting…' : 'Request to join')}</Button>}
                {selectedRegistrySource.accessState === 'approval-pending' && <Badge variant="yellow">{t('Approval pending')}</Badge>}
                {selectedRegistrySource.accessState === 'workspace-required' && <Button variant="primary" onClick={() => onOpenRegistry(selectedRegistrySource)}>{t('Continue in Registry')}</Button>}
              </div>
            </section>}
            {baseLlmSource === 'federated-task' && !selectedSource && !selectedRegistrySource && <section style={sourceHintStyle}><div style={{ color: C.text, fontWeight: 600, fontSize: 13 }}>{t('Select an LLM Federated Task')}</div><div style={{ color: C.dim, fontSize: 12, lineHeight: 1.6 }}>{t('Choose an LLM Federated Task from the local model sources panel.')}</div></section>}
            {selectedSource && (selectedSource.capability === 'tool-ai' || baseLlmSource === 'federated-task') ? <section style={configurationCardStyle}>
              <div style={configurationHeaderStyle}><div><div style={eyebrowStyle}>{selectedSource.capability === 'base-llm' ? t('FEDERATED TASK LLM') : t('FEDERATED TASK TOOL AI')}</div><h2 style={{ ...titleStyle, fontSize: 20 }}>{selectedSource.modelName}</h2></div><Badge variant={selectedSource.ready ? 'green' : 'yellow'}>{selectedSource.ready ? t('Ready') : t('Needs setup')}</Badge></div>
              <div style={selectedSourceDetailsStyle}>
                <CompactMeta label={t('Project')} value={selectedSource.projectName} />
                <CompactMeta label={t('Local path')} value={selectedSource.displayPath} mono />
                <CompactMeta label={t('Readiness')} value={selectedSource.ready ? t('Ready') : selectedSource.reason || t('Not ready')} />
              </div>
              {selectedSource.availableModelVersions.length > 0 && <Field label={t('Model version')}><select value={selectedModelVersion?.modelVersionId ?? ''} onChange={event => setSelectedModelVersionId(event.target.value)} style={{ ...inputStyle, width: '100%' }}>{selectedSource.availableModelVersions.map(version => <option key={version.modelVersionId ?? version.label} value={version.modelVersionId ?? ''}>{version.label}{version.cached ? ` · ${t('Ready')}` : ` · ${t('Download required')}`}</option>)}</select>{selectedModelVersion && <div style={{ marginTop: 6, color: C.dim, fontSize: 10 }}>{t('Registry format')}: {selectedModelVersion.sourceFormat ?? selectedModelVersion.format ?? '—'} · {t('Agent runtime format')}: {selectedModelVersion.cached ? selectedModelVersion.format ?? '—' : t('prepared after download')}</div>}</Field>}
              {registryActionMessage && <div style={{ marginTop: 10, color: C.yellow, fontSize: 11 }}>{registryActionMessage}</div>}
              <div style={configurationFooterStyle}><span style={{ color: C.dim, fontSize: 11 }}>{selectedModelVersion?.label ?? t('This model remains in the account-local Workspace.')}</span>{selectedModelVersion && !selectedModelVersion.cached ? <Button variant="primary" disabled={preparingModelVersionId === selectedModelVersion.modelVersionId} onClick={() => void prepareSelectedVersion(selectedSource)}>{t(preparingModelVersionId ? 'Preparing…' : 'Prepare model version')}</Button> : <Button variant="primary" disabled={!selectedSource.ready || !selectedVersionSource(selectedSource)} onClick={() => { const versionSource = selectedVersionSource(selectedSource); if (versionSource) selectedSource.capability === 'base-llm' ? selectFederatedLlm(versionSource) : addTool(versionSource) }}>{selectedSource.capability === 'base-llm' ? t('Use as Base LLM') : t('Add as Tool AI')}</Button>}</div>
            </section> : baseLlmSource === null && !selectedRegistrySource && <section style={sourceHintStyle}><div style={{ color: C.text, fontWeight: 600, fontSize: 13 }}>{t('Select a Base LLM source')}</div><div style={{ color: C.dim, fontSize: 12, lineHeight: 1.6 }}>{t('Choose Federated Task LLM or Hugging Face LLM before configuring the model.')}</div></section>}
          </div>
        </main>
      </div>
    </div>
  )
}

function SourceRow({ source, selected, select }: { source: AgentModelSource; selected: boolean; select: () => void }) {
  const { t } = useI18n()
  return <button onClick={select} style={taskRowStyle(selected)}><div style={sourceRowHeaderStyle}><div style={sourceTypeIconStyle(source.capability)}>{source.capability === 'base-llm' ? 'LLM' : 'AI'}</div><div style={{ minWidth: 0, flex: 1 }}><strong style={{ display: 'block', color: selected ? C.accent : C.text, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{source.modelName}</strong><span style={bindingIdentityStyle}>{source.projectName}</span></div><span style={{ color: source.ready ? C.green : C.orange, fontSize: 10, whiteSpace: 'nowrap' }}>{source.ready ? t('Ready') : t('Needs setup')}</span><span style={{ color: selected ? C.accent : C.dim }}>›</span></div></button>
}

function RegistrySourceRow({ source, selected, select }: { source: AgentRegistryModelSource; selected: boolean; select: () => void }) {
  const { t } = useI18n()
  const state = registryAccessLabel(source.accessState)
  return <button onClick={select} style={taskRowStyle(selected)}><div style={sourceRowHeaderStyle}><div style={sourceTypeIconStyle(source.capability)}>{source.capability === 'base-llm' ? 'LLM' : 'AI'}</div><div style={{ minWidth: 0, flex: 1 }}><strong style={{ display: 'block', color: selected ? C.accent : C.text, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{source.modelName}</strong><span style={bindingIdentityStyle}>@{source.ownerHandle || 'unknown'} / {source.slug || source.registryId}</span></div><span style={{ color: source.accessState === 'workspace-required' ? C.green : source.accessState === 'join-required' ? C.accent : source.accessState === 'approval-pending' ? C.orange : C.dim, fontSize: 9.5, whiteSpace: 'nowrap' }}>{t(state)}</span><span style={{ color: selected ? C.accent : C.dim }}>›</span></div></button>
}

function registryAccessLabel(state: AgentRegistryModelSource['accessState']) {
  return {
    'workspace-required': 'Open Workspace',
    'approval-pending': 'Pending',
    'join-required': 'Join',
    unavailable: 'View only',
  }[state]
}

function SourceGroupLabel({ label, count }: { label: string; count: number }) {
  return <div style={sourceGroupStyle}><span>{label}</span><span style={sourceCountStyle}>{count}</span></div>
}

function CompactMeta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return <div style={compactMetaRowStyle}><span style={compactMetaLabelStyle}>{label}</span><span title={value} style={{ ...compactMetaValueStyle, fontFamily: mono ? 'JetBrains Mono, monospace' : 'Inter, sans-serif' }}>{value}</span></div>
}

function llmName(llm: AgentLlm) {
  return llm.source === 'huggingface' ? llm.displayName : llm.modelName
}

function llmIdentity(llm: AgentLlm) {
  return llm.source === 'huggingface'
    ? `${llm.repoId}@${llm.revision}${llm.fileName ? ` · ${llm.fileName}` : ''}`
    : `${llm.taskTitle} · ${llm.modelVersionId || llm.releaseId || llm.modelSha256.slice(0, 12)}`
}

function HarnessTab({ draft, update }: { draft: AgentDraft; update: (draft: AgentDraft) => void }) {
  const { t } = useI18n()
  const harness = draft.harness
  function patchHarness(value: Partial<AgentDraft['harness']>) {
    update({ ...draft, harness: { ...harness, ...value }, validation: null, status: draft.buildRevision ? 'modified' : 'draft' })
  }
  return (
    <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
      <section style={{ ...modelCardStyle, maxWidth: 900, margin: '0 auto' }}>
        <div style={eyebrowStyle}>{t('AGENT HARNESS')}</div>
        <h2 style={{ ...titleStyle, fontSize: 20 }}>{t('Instructions and runtime policy')}</h2>
        <label style={labelStyle}>{t('Instructions')}</label>
        <textarea value={harness.instructions} onChange={event => patchHarness({ instructions: event.target.value })} rows={9} style={{ ...inputStyle, width: '100%', resize: 'vertical', lineHeight: 1.6 }} />
        <div style={formGridStyle}>
          <Field label={t('Tool routing')}><select value={harness.toolRouting} onChange={event => patchHarness({ toolRouting: event.target.value as 'automatic' | 'explicit' })} style={inputStyle}><option value="automatic">automatic</option><option value="explicit">explicit</option></select></Field>
          <Field label={t('Memory')}><select value={harness.memory} onChange={event => patchHarness({ memory: event.target.value as 'none' | 'session' })} style={inputStyle}><option value="session">session</option><option value="none">none</option></select></Field>
          <Field label={t('Safety')}><select value={harness.safety} onChange={event => patchHarness({ safety: event.target.value as 'standard' | 'strict' })} style={inputStyle}><option value="standard">standard</option><option value="strict">strict</option></select></Field>
          <Field label={t('Context window')}><input type="number" value={harness.contextWindow} onChange={event => patchHarness({ contextWindow: Number(event.target.value) })} style={inputStyle} /></Field>
          <Field label={t('Temperature')}><input type="number" min="0" max="2" step="0.1" value={harness.temperature} onChange={event => patchHarness({ temperature: Number(event.target.value) })} style={inputStyle} /></Field>
          <Field label={t('Max output tokens')}><input type="number" value={harness.maxTokens} onChange={event => patchHarness({ maxTokens: Number(event.target.value) })} style={inputStyle} /></Field>
        </div>
      </section>
    </div>
  )
}

function TestBuildTab({
  draft,
  busy,
  validate,
  build,
  prepare,
  test,
  chat,
  openAgents,
}: {
  draft: AgentDraft
  busy: string | null
  validate: () => void
  build: () => void
  prepare: (onProgress: (progress: AgentLlmPreparation) => void) => Promise<AgentLlmPreparation | null>
  test: (prompt: string, toolInput: Record<string, unknown> | null, toolId: string | null) => Promise<AgentChatResponse | null>
  chat: (prompt: string, toolInput: Record<string, unknown> | null, toolId: string | null, history: AgentChatMessage[], onEvent: (event: AgentChatStreamEvent) => void, signal: AbortSignal) => Promise<AgentChatResponse | null>
  openAgents: () => void
}) {
  const { t } = useI18n()
  const checks = draft.validation?.checks ?? []
  const [prompt, setPrompt] = useState('Classify the provided image and explain the verified result.')
  const [toolJson, setToolJson] = useState('')
  const [selectedToolId, setSelectedToolId] = useState<string | null>(draft.tools[0]?.localProjectId ?? null)
  const [playgroundError, setPlaygroundError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<AgentChatResponse | null>(null)
  const [runtime, setRuntime] = useState<AgentLlmPreparation | null>(null)
  const [conversation, setConversation] = useState<AgentChatMessage[]>([])
  const generationController = useRef<AbortController | null>(null)

  useEffect(() => () => generationController.current?.abort(), [])

  function parsedToolInput(): Record<string, unknown> | null {
    if (!toolJson.trim()) return null
    const parsed: unknown = JSON.parse(toolJson)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error(t('Tool input must be a JSON object.'))
    }
    return parsed as Record<string, unknown>
  }

  async function prepareModel() {
    setPlaygroundError(null)
    const next = await prepare(setRuntime)
    if (next) setRuntime(next)
  }

  async function runTest() {
    setPlaygroundError(null)
    try {
      const result = await test(prompt.trim() || 'Run the configured Agent draft test.', parsedToolInput(), toolJson.trim() ? selectedToolId : null)
      if (result) setTestResult(result)
    } catch (reason) {
      setPlaygroundError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  async function sendMessage() {
    const text = prompt.trim()
    if (!text) return
    setPlaygroundError(null)
    const controller = new AbortController()
    generationController.current = controller
    try {
      const prior = conversation.slice(-40)
      setConversation(current => [
        ...current,
        { role: 'user', content: text },
        { role: 'assistant', content: '' },
      ].slice(-40) as AgentChatMessage[])
      const result = await chat(text, parsedToolInput(), toolJson.trim() ? selectedToolId : null, prior, event => {
        if (event.type !== 'delta') return
        setConversation(current => current.map((item, index) => (
          index === current.length - 1 && item.role === 'assistant'
            ? { ...item, content: item.content + event.content }
            : item
        )))
      }, controller.signal)
      if (result) {
        setConversation(current => current.map((item, index) => (
          index === current.length - 1 && item.role === 'assistant'
            ? { ...item, content: result.response }
            : item
        )))
        setTestResult(result)
        setPrompt('')
      } else {
        setConversation(current => current.filter((item, index) => (
          index !== current.length - 1 || item.role !== 'assistant' || item.content.length > 0
        )))
      }
    } catch (reason) {
      if (!controller.signal.aborted) setPlaygroundError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (generationController.current === controller) generationController.current = null
    }
  }

  return (
    <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
      <div style={{ maxWidth: 1040, margin: '0 auto', display: 'grid', gap: 14 }}>
        <section style={modelCardStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <div><div style={eyebrowStyle}>{t('DRAFT PLAYGROUND')}</div><h2 style={{ ...titleStyle, fontSize: 19 }}>{t('Test while composing')}</h2></div>
            <div className="studio-responsive-actions">
              <Button variant="ghost" disabled={Boolean(busy) || !draft.llm} onClick={() => void prepareModel()}>{busy === 'draft-prepare' ? t('Preparing…') : t('Prepare Base LLM')}</Button>
              <Button variant="ghost" disabled={Boolean(busy)} onClick={() => { setConversation([]); setTestResult(null) }}>{t('Clear')}</Button>
            </div>
          </div>
          <p style={subtitleStyle}>{t('Run Tool AI smoke tests and chat with the current saved Draft before creating an immutable Build.')}</p>
          <MetaRow label={t('Base LLM')} value={draft.llm ? llmName(draft.llm) : t('Not selected')} mono />
          {draft.llm && <MetaRow label={t('Model identity')} value={llmIdentity(draft.llm)} mono />}
          <MetaRow label={t('Model storage')} value={runtime?.localPath || draft.llm?.localPath || t('Prepare the model to create its local directory.')} mono />
          {runtime && <div style={{ ...dashedStyle, textAlign: 'left', margin: '10px 0' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}><strong style={{ color: runtime.status === 'succeeded' ? C.green : runtime.status === 'failed' ? C.red : C.accent }}>{t(runtime.stage)}</strong><strong style={{ color: C.text }}>{runtime.percent.toFixed(1)}%</strong></div>
            <div style={{ height: 7, margin: '8px 0', overflow: 'hidden', borderRadius: C.pillRadius, background: C.surface2 }}><div style={{ width: `${Math.max(0, Math.min(100, runtime.percent))}%`, height: '100%', background: runtime.status === 'failed' ? C.red : runtime.status === 'succeeded' ? C.green : C.accent, transition: 'width .25s ease' }} /></div>
            <div style={{ color: C.muted, fontSize: 11 }}>{formatBytes(runtime.downloadedBytes)}{runtime.totalBytes ? ` / ${formatBytes(runtime.totalBytes)}` : ''}</div>
            <div style={{ marginTop: 4 }}>{runtime.detail}</div>
          </div>}
          <TaskDataPanel
            tools={draft.tools}
            selectedToolId={selectedToolId}
            onSelectTool={setSelectedToolId}
            onSample={sample => {
              if (!sample) { setToolJson(''); return }
              setSelectedToolId(sample.localProjectId)
              setToolJson(JSON.stringify(sample.payload, null, 2))
            }}
            disabled={Boolean(busy)}
          />
          <div style={{ marginTop: 12 }}>
            <div style={{ minHeight: 230, maxHeight: 390, overflowY: 'auto', padding: 10, border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.bg }}>
              {conversation.length === 0 && <div style={{ color: C.dim, fontSize: 12, padding: 12 }}>{t('Send a message to start a Draft conversation. Previous turns are included in the next request.')}</div>}
              {conversation.map((item, index) => <div key={`${item.role}-${index}`} style={chatBubbleStyle(item.role)}><strong style={{ display: 'block', fontSize: 10, marginBottom: 4, color: item.role === 'user' ? C.accent : C.purple }}>{item.role === 'user' ? t('You') : t('Agent')}</strong><div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.55 }}>{item.content}</div></div>)}
            </div>
          </div>
          <details style={{ marginTop: 10 }}>
            <summary style={{ color: C.muted, cursor: 'pointer', fontSize: 11 }}>{t('Advanced · Manual Tool JSON')}</summary>
            <textarea value={toolJson} onChange={event => setToolJson(event.target.value)} rows={8} placeholder='{"feature": "value"}' style={{ ...inputStyle, width: '100%', resize: 'vertical', marginTop: 7, fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }} />
          </details>
          <label style={{ ...labelStyle, display: 'block', marginTop: 12 }}>{t('Message')}</label>
          <textarea value={prompt} onChange={event => setPrompt(event.target.value)} rows={3} onKeyDown={event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') void sendMessage() }} style={{ ...inputStyle, width: '100%', resize: 'vertical', marginTop: 6 }} />
          {playgroundError && <div style={{ color: C.red, fontSize: 12, marginTop: 8 }}>{playgroundError}</div>}
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <Button variant="ghost" disabled={Boolean(busy)} onClick={() => void runTest()}>{busy === 'draft-test' ? t('Checking…') : t('Check Draft & Tool AI')}</Button>
            {busy === 'draft-chat'
              ? <Button variant="danger" onClick={() => generationController.current?.abort()}>{t('Stop generating')}</Button>
              : <Button variant="primary" disabled={Boolean(busy) || !prompt.trim() || !draft.llm} onClick={() => void sendMessage()}>{t('Generate Response')}</Button>}
            <span style={{ color: C.dim, fontSize: 11, alignSelf: 'center' }}>{t('Ctrl/Cmd + Enter to send')}</span>
          </div>
          {testResult && <details style={{ marginTop: 12 }}><summary style={{ color: C.muted, cursor: 'pointer', fontSize: 12 }}>{t('Latest execution trace')}</summary><pre style={resultStyle}>{JSON.stringify({ toolResult: testResult.toolResult, trace: testResult.trace }, null, 2)}</pre></details>}
        </section>
        <section style={modelCardStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
            <div><div style={eyebrowStyle}>{t('VALIDATION')}</div><h2 style={{ ...titleStyle, fontSize: 19 }}>{t('Build readiness')}</h2></div>
            <Badge variant={draft.validation?.ok ? 'green' : draft.validation ? 'red' : 'gray'}>{draft.validation?.ok ? t('Passed') : draft.validation ? t('Failed') : t('Not run')}</Badge>
          </div>
          {checks.map(check => <div key={check.id} style={checkRowStyle}><span style={{ color: check.status === 'passed' ? C.green : C.red }}>{check.status === 'passed' ? '✓' : '×'}</span><div><strong style={{ color: C.text, fontSize: 12 }}>{t(check.label)}</strong><div style={{ color: C.dim, fontSize: 11 }}>{t(check.detail)}</div></div></div>)}
          {checks.length === 0 && <p style={subtitleStyle}>{t('Validation checks the selected Base LLM and the current local Workspace source and model checksums.')}</p>}
          <Button variant="ghost" disabled={Boolean(busy)} onClick={validate}>{busy === 'validate' ? t('Validating…') : t('Run Validation')}</Button>
        </section>
        <section style={modelCardStyle}>
          <div style={eyebrowStyle}>{t('IMMUTABLE BUILD')}</div>
          <p style={subtitleStyle}>{t('Build records the exact local Workspace code and model identity. The prepared Base LLM remains in the host-mounted account model directory.')}</p>
          <MetaRow label={t('Base LLM')} value={draft.llm ? llmName(draft.llm) : t('Not selected')} mono />
          <MetaRow label={t('Tool AI models')} value={String(draft.tools.length)} />
          <MetaRow label={t('Latest build')} value={draft.buildRevision ? `r${draft.buildRevision}` : '—'} mono />
          <div className="studio-responsive-actions"><Button variant="primary" disabled={Boolean(busy) || !draft.llm} onClick={build}>{busy === 'build' ? t('Building…') : t('Build Agent')}</Button>{draft.buildRevision > 0 && <Button variant="ghost" onClick={openAgents}>{t('Open in Agents')}</Button>}</div>
        </section>
      </div>
    </div>
  )
}

function Notice({ error, message }: { error: string | null; message: string | null }) {
  if (!error && !message) return null
  return <div style={{ padding: '7px 14px', background: error ? C.redDim : C.greenDim, borderBottom: `1px solid ${error ? C.redBorder : C.greenBorder}`, color: error ? C.red : C.green, fontSize: 12 }}>{error || message}</div>
}

function Empty({ title, detail }: { title: string; detail: string }) {
  return <div style={{ padding: 24, textAlign: 'center' }}><strong style={{ color: C.text, fontSize: 14 }}>{title}</strong><p style={{ color: C.dim, fontSize: 12, lineHeight: 1.6 }}>{detail}</p></div>
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label style={{ display: 'grid', gap: 5 }}><span style={labelStyle}>{label}</span>{children}</label> }

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  const power = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** power).toFixed(power > 2 ? 2 : 1)} ${units[power]}`
}

const pageStyle: React.CSSProperties = { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: C.bg, fontFamily: 'Inter, sans-serif' }
const pageHeaderStyle: React.CSSProperties = { padding: '16px 18px', borderBottom: `1px solid ${C.border}`, background: C.surface, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }
const eyebrowStyle: React.CSSProperties = { color: C.purple, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, letterSpacing: '0.08em', marginBottom: 5 }
const titleStyle: React.CSSProperties = { color: C.text, fontSize: 24, margin: 0, lineHeight: 1.2 }
const subtitleStyle: React.CSSProperties = { color: C.muted, fontSize: 12, lineHeight: 1.6, margin: '7px 0 12px' }
const inputStyle: React.CSSProperties = { boxSizing: 'border-box', background: C.inputBg, color: C.text, border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, padding: '8px 10px', fontFamily: 'Inter, sans-serif', fontSize: 13, outline: 'none' }
const labelStyle: React.CSSProperties = { color: C.muted, fontSize: 12, fontWeight: 600 }
const draftCardStyle: React.CSSProperties = { textAlign: 'left', background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 14, cursor: 'pointer', fontFamily: 'Inter, sans-serif', boxShadow: C.shadow }
const modelCardStyle: React.CSSProperties = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 14, marginBottom: 10, boxShadow: C.shadow }
const monoDimStyle: React.CSSProperties = { display: 'block', color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, margin: '4px 0 9px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
const railHeaderStyle: React.CSSProperties = { minHeight: 86, padding: '14px 16px 13px', borderBottom: `1px solid ${C.border}`, background: C.surface }
const railDescriptionStyle: React.CSSProperties = { color: C.dim, fontSize: 11, lineHeight: 1.55, margin: 0 }
const railBodyStyle: React.CSSProperties = { padding: '14px 14px 24px' }
const sectionHeadingStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 18 }
const bindingCardStyle: React.CSSProperties = { overflow: 'hidden', background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, marginBottom: 10, boxShadow: C.shadow }
const bindingCardHeaderStyle: React.CSSProperties = { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, padding: '12px 12px 10px' }
const bindingTitleStyle: React.CSSProperties = { display: 'block', color: C.text, fontSize: 13, lineHeight: 1.35, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
const bindingIdentityStyle: React.CSSProperties = { display: 'block', maxWidth: '100%', marginTop: 4, color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 9.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
const bindingDetailsStyle: React.CSSProperties = { display: 'grid', gap: 7, padding: '10px 12px', borderTop: `1px solid ${C.borderSubtle}`, background: C.bg }
const bindingFooterStyle: React.CSSProperties = { display: 'flex', justifyContent: 'flex-end', padding: '8px 10px', borderTop: `1px solid ${C.borderSubtle}` }
const compactButtonStyle: React.CSSProperties = { minHeight: 28, padding: '3px 10px', fontSize: 11 }
const compactMetaRowStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: '76px minmax(0, 1fr)', alignItems: 'baseline', gap: 8, minWidth: 0 }
const compactMetaLabelStyle: React.CSSProperties = { color: C.dim, fontSize: 10.5, whiteSpace: 'nowrap' }
const compactMetaValueStyle: React.CSSProperties = { minWidth: 0, color: C.text, fontSize: 10.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
const sourceGroupStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '10px 14px 6px', color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 9.5, fontWeight: 600, letterSpacing: '0.06em' }
const sourceCountStyle: React.CSSProperties = { minWidth: 20, padding: '1px 5px', textAlign: 'center', border: `1px solid ${C.border}`, borderRadius: C.pillRadius, background: C.surface2, color: C.muted }
const emptySourceStyle: React.CSSProperties = { margin: '4px 12px 8px', padding: '12px', color: C.dim, fontSize: 11, lineHeight: 1.5, border: `1px dashed ${C.border}`, borderRadius: C.radius }
const sourceRowHeaderStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }
const sourceTypeIconStyle = (capability: AgentModelSource['capability']): React.CSSProperties => ({ display: 'grid', placeItems: 'center', width: 28, height: 28, flexShrink: 0, borderRadius: C.radius, border: `1px solid ${capability === 'base-llm' ? C.purpleBorder : C.accentBorder}`, background: capability === 'base-llm' ? C.purpleDim : C.accentDim, color: capability === 'base-llm' ? C.purple : C.accent, fontFamily: 'JetBrains Mono, monospace', fontSize: 8.5, fontWeight: 700 })
const configContentStyle: React.CSSProperties = { width: '100%', maxWidth: 980, margin: '0 auto', display: 'grid', gap: 14 }
const configurationCardStyle: React.CSSProperties = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 18, boxShadow: C.shadow }
const configurationHeaderStyle: React.CSSProperties = { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 14 }
const baseLlmSourceGridStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 10, marginTop: 14 }
const baseLlmSourceCardStyle = (active: boolean, source: BaseLlmSource): React.CSSProperties => ({ display: 'grid', gridTemplateColumns: '38px minmax(0, 1fr) 20px', alignItems: 'center', gap: 11, minWidth: 0, padding: 13, textAlign: 'left', color: C.text, border: `1px solid ${active ? (source === 'federated-task' ? C.accentBorder : C.purpleBorder) : C.border}`, borderRadius: C.radius, background: active ? (source === 'federated-task' ? C.accentDim : C.purpleDim) : C.bg, cursor: 'pointer', fontFamily: 'Inter, sans-serif' })
const baseLlmSourceIconStyle = (source: BaseLlmSource): React.CSSProperties => ({ display: 'grid', placeItems: 'center', width: 36, height: 36, borderRadius: C.radius, border: `1px solid ${source === 'federated-task' ? C.accentBorder : C.purpleBorder}`, background: source === 'federated-task' ? C.accentDim : C.purpleDim, color: source === 'federated-task' ? C.accent : C.purple, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, fontWeight: 700 })
const baseLlmSourceTitleStyle: React.CSSProperties = { display: 'block', color: C.text, fontSize: 13, lineHeight: 1.35 }
const baseLlmSourceDetailStyle: React.CSSProperties = { display: 'block', marginTop: 3, color: C.dim, fontSize: 10.5, lineHeight: 1.45 }
const baseLlmSourceCheckStyle = (active: boolean): React.CSSProperties => ({ color: active ? C.accent : C.dim, fontSize: 15, textAlign: 'center' })
const modelConfigurationGridStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 12, marginTop: 16 }
const configurationFooterStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14, marginTop: 16, paddingTop: 14, borderTop: `1px solid ${C.borderSubtle}` }
const selectedSourceDetailsStyle: React.CSSProperties = { display: 'grid', gap: 8, marginTop: 14, padding: 12, border: `1px solid ${C.borderSubtle}`, borderRadius: C.radius, background: C.bg }
const sourceHintStyle: React.CSSProperties = { display: 'grid', gap: 5, padding: 18, border: `1px dashed ${C.border}`, borderRadius: C.radius, background: C.surface }
const dashedStyle: React.CSSProperties = { border: `1px dashed ${C.border}`, borderRadius: C.radius, color: C.dim, padding: 14, textAlign: 'center', fontSize: 12, lineHeight: 1.5 }
const tabsStyle: React.CSSProperties = { display: 'flex', borderBottom: `1px solid ${C.border}`, background: C.surface, flexShrink: 0 }
const tabStyle = (active: boolean): React.CSSProperties => ({ background: 'none', color: active ? C.text : C.muted, border: 'none', borderBottom: active ? `2px solid ${C.accent}` : '2px solid transparent', padding: '9px 16px', cursor: 'pointer', fontSize: 13 })
const backStyle: React.CSSProperties = { background: 'none', border: 'none', color: C.muted, fontSize: 24, cursor: 'pointer' }
const taskRowStyle = (active: boolean): React.CSSProperties => ({ width: 'calc(100% - 16px)', minWidth: 0, margin: '3px 8px', textAlign: 'left', padding: '10px', background: active ? C.accentDim : 'transparent', border: `1px solid ${active ? C.accentBorder : 'transparent'}`, borderRadius: C.radius, cursor: 'pointer', fontFamily: 'Inter, sans-serif' })
const formGridStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 12, marginTop: 14 }
const checkRowStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: '18px 1fr', gap: 8, padding: '8px 0', borderBottom: `1px solid ${C.borderSubtle}` }
const chatBubbleStyle = (role: AgentChatMessage['role']): React.CSSProperties => ({ maxWidth: '86%', margin: role === 'user' ? '8px 0 8px auto' : '8px auto 8px 0', padding: '9px 11px', borderRadius: C.radius, border: `1px solid ${role === 'user' ? C.accentBorder : C.border}`, background: role === 'user' ? C.accentDim : C.surface, color: C.text, fontSize: 12 })
const resultStyle: React.CSSProperties = { margin: '8px 0 0', padding: 10, maxHeight: 260, overflow: 'auto', border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.bg, color: C.muted, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, whiteSpace: 'pre-wrap' }
const modalBackdropStyle: React.CSSProperties = { position: 'fixed', inset: 0, zIndex: 80, display: 'grid', placeItems: 'center', padding: 24, background: 'rgba(4, 8, 14, 0.55)', backdropFilter: 'blur(3px)' }
const modalStyle: React.CSSProperties = { width: 'min(460px, 100%)', background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 22, boxShadow: '0 24px 80px rgba(0,0,0,.28)' }
