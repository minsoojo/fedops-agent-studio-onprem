import { useEffect, useMemo, useRef, useState } from 'react'
import {
  configureAgentDirectToolServing,
  deleteBuiltAgent,
  deleteAgentServingDataSource,
  disableAgentServing,
  enableAgentServing,
  getAgentLlmPreparation,
  listAgentRequests,
  listAgentServingDataSources,
  listBuiltAgents,
  prepareAgentLlm,
  rotateAgentServingToken,
  saveAgentServingDataSource,
  streamBuiltAgentChat,
  testBuiltAgent,
  testAgentServingDataSource,
  type AgentChatMessage,
  type AgentChatResponse,
  type AgentRequestRecord,
  type AgentServingDataSource,
  type AgentToolPredictionResponse,
  type AgentLlm,
  type AgentLlmPreparation,
  type AgentToolModel,
  type BuiltAgent,
  updateAgentServingPort,
} from '../../api/agents'
import { openWorkspaceDataFolder } from '../../api/workspace'
import { listFederatedParticipations } from '../../api/federatedLearning'
import type { FederatedParticipation } from '../../api/federatedLearning'
import type { Screen } from '../../app/types'
import { useI18n } from '../../app/i18n'
import { Badge, Button, C, MetaRow, SectionLabel } from '../../ui/UIKit'
import TaskDataPanel from '../../ui/TaskDataPanel'

type AgentTab = 'Overview' | 'Test' | 'Serving API' | 'Requests'

type EndpointRequestPreview = {
  label: string
  detail?: string
  body?: Record<string, unknown>
}

export default function Agents({ onNavigate, onOpenFederation }: { onNavigate: (screen: Screen) => void; onOpenFederation: (source: AgentToolModel | Extract<AgentLlm, { source: 'federated-task' }>) => void }) {
  const { t } = useI18n()
  const [agents, setAgents] = useState<BuiltAgent[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tab, setTab] = useState<AgentTab>('Overview')
  const [requests, setRequests] = useState<AgentRequestRecord[]>([])
  const [token, setToken] = useState<string | null>(null)
  const [message, setMessage] = useState('Run the selected Tool AI smoke test with its safe example input.')
  const [toolInput, setToolInput] = useState('')
  const [selectedToolId, setSelectedToolId] = useState<string | null>(null)
  const [participations, setParticipations] = useState<FederatedParticipation[]>([])
  const [testResult, setTestResult] = useState<AgentChatResponse | null>(null)
  const [conversation, setConversation] = useState<AgentChatMessage[]>([])
  const [llmPreparation, setLlmPreparation] = useState<AgentLlmPreparation | null>(null)
  const [servingSources, setServingSources] = useState<AgentServingDataSource[]>([])
  const [servingPort, setServingPort] = useState('24400')
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const generationController = useRef<AbortController | null>(null)

  const selected = agents.find(agent => agent.agentId === selectedId) ?? null
  const endpoint = selected?.serving.endpointUrl || ''

  useEffect(() => {
    setServingPort(String(selected?.serving.port ?? 24400))
  }, [selectedId, selected?.serving.port])

  useEffect(() => {
    const controller = new AbortController()
    listBuiltAgents(controller.signal).then(result => {
      setAgents(result.items)
      setSelectedId(current => current ?? result.items[0]?.agentId ?? null)
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason))
    })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    listFederatedParticipations(controller.signal).then(result => setParticipations(result.items)).catch(() => undefined)
    return () => controller.abort()
  }, [])

  useEffect(() => {
    setSelectedToolId(selected?.tools[0]?.localProjectId ?? null)
    setToolInput('')
  }, [selectedId])

  useEffect(() => () => generationController.current?.abort(), [])

  useEffect(() => {
    if (!selectedId || tab !== 'Requests') return
    const controller = new AbortController()
    listAgentRequests(selectedId, controller.signal).then(result => setRequests(result.items)).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason))
    })
    return () => controller.abort()
  }, [selectedId, tab])

  useEffect(() => {
    setServingSources([])
    if (!selectedId || tab !== 'Serving API') return
    const controller = new AbortController()
    listAgentServingDataSources(selectedId, controller.signal)
      .then(response => setServingSources(response.items))
      .catch(cause => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause))
      })
    return () => controller.abort()
  }, [selectedId, selected?.buildRevision, tab])

  function replaceAgent(agent: BuiltAgent) {
    setAgents(current => current.map(item => item.agentId === agent.agentId ? agent : item))
  }

  async function enableServing() {
    if (!selected) return
    const port = Number(servingPort)
    if (!Number.isInteger(port) || port < 24400 || port > 24499) {
      setError(t('Choose an Agent serving port between 24400 and 24499.'))
      return
    }
    setBusy('serve'); setError(null); setNotice(null)
    try {
      const result = await enableAgentServing(selected.agentId, port)
      replaceAgent({ ...selected, serving: result.serving })
      setToken(result.token)
      setNotice(t('Serving API enabled. Copy the bearer token now; it is not stored in plaintext.'))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(null) }
  }

  async function rotateToken() {
    if (!selected) return
    setBusy('rotate'); setError(null); setNotice(null)
    try {
      const result = await rotateAgentServingToken(selected.agentId)
      replaceAgent({ ...selected, serving: result.serving })
      setToken(result.token)
      setNotice(t('The previous token was revoked. Copy the replacement token now.'))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(null) }
  }

  async function disableServing() {
    if (!selected) return
    setBusy('stop'); setError(null); setNotice(null)
    try {
      const serving = await disableAgentServing(selected.agentId)
      replaceAgent({ ...selected, serving })
      setToken(null)
      setNotice(t('Serving API disabled and its bearer token revoked.'))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(null) }
  }

  async function changeServingPort() {
    if (!selected) return
    const port = Number(servingPort)
    if (!Number.isInteger(port) || port < 24400 || port > 24499) {
      setError(t('Choose an Agent serving port between 24400 and 24499.'))
      return
    }
    setBusy('serving-port'); setError(null); setNotice(null)
    try {
      const serving = await updateAgentServingPort(selected.agentId, port)
      replaceAgent({ ...selected, serving })
      setNotice(t('Serving port updated without rotating the bearer token.'))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(null) }
  }

  async function runTest() {
    if (!selected) return
    let parsed: Record<string, unknown> | null = null
    if (toolInput.trim()) {
      try { parsed = JSON.parse(toolInput) as Record<string, unknown> }
      catch { setError(t('Tool input must be a JSON object.')); return }
    }
    setBusy('test'); setError(null); setNotice(null); setTestResult(null)
    try {
      const result = await testBuiltAgent(selected.agentId, message, parsed, toolInput.trim() ? selectedToolId : null)
      setTestResult(result)
      setNotice(t('Local Agent build smoke test completed.'))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(null) }
  }

  async function prepareLlm() {
    if (!selected) return
    setBusy('prepare-llm'); setError(null); setNotice(null)
    try {
      let runtime = await prepareAgentLlm(selected.agentId)
      setLlmPreparation(runtime)
      while (runtime.status === 'queued' || runtime.status === 'running') {
        await new Promise(resolve => window.setTimeout(resolve, 800))
        runtime = await getAgentLlmPreparation(selected.agentId)
        setLlmPreparation(runtime)
      }
      if (runtime.status === 'failed') throw new Error(runtime.detail)
      const llm: AgentLlm = selected.llm.source === 'huggingface'
        ? { ...selected.llm, runtimeStatus: 'installed', localPath: runtime.localPath || selected.llm.localPath }
        : { ...selected.llm, runtimeStatus: 'installed', localPath: runtime.localPath || selected.llm.localPath }
      replaceAgent({ ...selected, llm })
      setNotice(runtime.detail)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(null) }
  }

  async function runChat() {
    if (!selected) return
    let parsed: Record<string, unknown> | null = null
    if (toolInput.trim()) {
      try { parsed = JSON.parse(toolInput) as Record<string, unknown> }
      catch { setError(t('Tool input must be a JSON object.')); return }
    }
    const prompt = message.trim()
    if (!prompt) return
    setBusy('chat'); setError(null); setNotice(null)
    const controller = new AbortController()
    generationController.current = controller
    const prior = conversation.slice(-40)
    setConversation(current => [
      ...current,
      { role: 'user', content: prompt },
      { role: 'assistant', content: '' },
    ].slice(-40) as AgentChatMessage[])
    try {
      const completion: { result: AgentChatResponse | null } = { result: null }
      await streamBuiltAgentChat(selected.agentId, prompt, parsed, prior, event => {
        if (event.type === 'error') throw new Error(event.detail)
        if (event.type === 'completed') completion.result = event.result
        if (event.type !== 'delta') return
        setConversation(current => current.map((item, index) => (
          index === current.length - 1 && item.role === 'assistant'
            ? { ...item, content: item.content + event.content }
            : item
        )))
      }, controller.signal, toolInput.trim() ? selectedToolId : null)
      const completed = completion.result
      if (!completed) throw new Error(t('The Agent response stream ended before completion.'))
      setTestResult(completed)
      setConversation(current => current.map((item, index) => (
        index === current.length - 1 && item.role === 'assistant'
          ? { ...item, content: completed.response }
          : item
      )))
      setMessage('')
      setNotice(t('Agent response completed with the selected Base LLM.'))
    } catch (reason) {
      if (controller.signal.aborted) {
        setNotice(t('Response generation stopped.'))
        setConversation(current => current.filter((item, index) => (
          index !== current.length - 1 || item.role !== 'assistant' || item.content.length > 0
        )))
        return
      }
      setError(reason instanceof Error ? reason.message : String(reason))
      setConversation(current => current.filter((item, index) => (
        index !== current.length - 1 || item.role !== 'assistant' || item.content.length > 0
      )))
      try {
        const refreshed = await listBuiltAgents()
        setAgents(refreshed.items)
      } catch { /* Preserve the inference error as the actionable message. */ }
    }
    finally {
      if (generationController.current === controller) generationController.current = null
      setBusy(null)
    }
  }

  async function removeAgent() {
    if (!selected || !window.confirm(t('Delete this Built Agent and its immutable build files?'))) return
    setBusy('delete'); setError(null)
    try {
      await deleteBuiltAgent(selected.agentId)
      const remaining = agents.filter(agent => agent.agentId !== selected.agentId)
      setAgents(remaining); setSelectedId(remaining[0]?.agentId ?? null)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(null) }
  }

  const servingTool = selected?.tools[0] ?? null
  const servingSource = servingSources.find(item => item.toolId === servingTool?.localProjectId) ?? null
  const servingInlineInput = servingTool ? {
    input: {
      type: 'inline',
      toolId: servingTool.localProjectId,
      payload: manifestExample(servingTool),
    },
  } : null
  const servingSourceInput = servingSource ? {
    input: {
      type: 'data-source',
      sourceId: servingSource.sourceId,
      ...(servingSource.selectionMode === 'request' ? { sampleIndex: servingSource.sampleIndex } : {}),
    },
  } : null
  const chatPreviews: EndpointRequestPreview[] = [
    ...(servingSourceInput ? [{
      label: t('Local Data Source'),
      detail: t('Only the Data Source reference is sent. The sample is loaded from Task Data on this device.'),
      body: {
        message: 'Use the selected Tool AI and explain the verified result.',
        history: [],
        ...servingSourceInput,
      },
    }] : []),
    ...(servingInlineInput ? [{
      label: t('Inline JSON'),
      detail: t('The complete Tool AI input is included directly in the request body.'),
      body: {
        message: 'Use the selected Tool AI and explain the verified result.',
        history: [],
        ...servingInlineInput,
      },
    }] : [{
      label: t('Agent message'),
      body: { message: 'Hello', history: [] },
    }]),
  ]

  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', overflow: 'hidden', background: C.bg, fontFamily: 'Inter, sans-serif' }}>
      <aside style={{ width: 280, flexShrink: 0, borderRight: `1px solid ${C.border}`, background: C.surface, display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: 14, borderBottom: `1px solid ${C.border}` }}>
          <div style={eyebrowStyle}>{t('AGENTS')}</div>
          <h1 style={titleStyle}>{t('Built Agents')}</h1>
          <p style={subtitleStyle}>{t('Test immutable builds and create local Serving APIs.')}</p>
          <Button variant="primary" onClick={() => onNavigate('agent')}>{t('Open Agent Builder')}</Button>
        </div>
        <div className="scroll-area" style={{ flex: 1, overflowY: 'auto' }}>
          {agents.map(agent => (
            <button key={agent.agentId} onClick={() => { generationController.current?.abort(); setSelectedId(agent.agentId); setToken(null); setConversation([]); setTestResult(null); setLlmPreparation(null); setTab('Overview') }} style={agentRowStyle(agent.agentId === selectedId)}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}><strong style={{ color: agent.agentId === selectedId ? C.accent : C.text, fontSize: 13 }}>{agent.name}</strong><Badge variant={agent.serving.enabled ? 'green' : 'gray'}>{agent.serving.enabled ? t('Serving') : `r${agent.buildRevision}`}</Badge></div>
              <span style={monoDimStyle}>{agent.agentId}</span>
              <span style={{ color: C.dim, fontSize: 11 }}>{llmName(agent.llm)} · {agent.tools.length} Tool AI</span>
            </button>
          ))}
          {agents.length === 0 && <Empty title={t('No Built Agents')} detail={t('Validate and Build an Agent in Agent Builder first.')} />}
        </div>
      </aside>
      {!selected ? (
        <main style={{ flex: 1, display: 'grid', placeItems: 'center' }}><Empty title={t('Select a Built Agent')} detail={t('Its exact models, Harness, test, and Serving API will appear here.')} /></main>
      ) : (
        <main style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <header style={headerStyle}>
            <div><div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><strong style={{ color: C.text, fontSize: 15 }}>{selected.name}</strong><Badge variant="green">Built r{selected.buildRevision}</Badge><Badge variant={['installed', 'ready'].includes(selected.llm.runtimeStatus) ? 'green' : 'yellow'}>LLM {selected.llm.runtimeStatus}</Badge></div><span style={monoDimStyle}>{selected.agentId} · {new Date(selected.builtAt).toLocaleString()}</span></div>
            <div style={{ flex: 1 }} />
            <Button variant="ghost" disabled={Boolean(busy)} onClick={() => onNavigate('agent')}>{t('Open Builder')}</Button>
            <Button variant="danger" disabled={Boolean(busy) || selected.serving.enabled} onClick={() => void removeAgent()}>{t('Delete')}</Button>
          </header>
          <div style={tabsStyle}>{(['Overview', 'Test', 'Serving API', 'Requests'] as AgentTab[]).map(item => <button key={item} onClick={() => setTab(item)} style={tabStyle(tab === item)}>{t(item)}</button>)}</div>
          {(error || notice) && <div style={{ padding: '7px 14px', color: error ? C.red : C.green, background: error ? C.redDim : C.greenDim, borderBottom: `1px solid ${error ? C.redBorder : C.greenBorder}`, fontSize: 12 }}>{error || notice}</div>}
          <div className="scroll-area" style={{ flex: 1, overflowY: 'auto', padding: 18 }}>
            {tab === 'Overview' && <Overview agent={selected} participations={participations} onOpenFederation={onOpenFederation} />}
            {tab === 'Test' && (
              <div className="agent-serving-layout">
                <section className="agent-serving-card" style={panelStyle}>
                  <div style={eyebrowStyle}>{t('BUILD SMOKE TEST')}</div>
                  <p style={subtitleStyle}>{t('This verifies the local Workspace code and model checksum, then runs an optional Tool AI adapter in its project uv environment.')}</p>
                  <label style={labelStyle}>{t('Message')}</label><textarea value={message} onChange={event => setMessage(event.target.value)} onKeyDown={event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') void runChat() }} rows={3} style={{ ...inputStyle, width: '100%', resize: 'vertical' }} />
                  <TaskDataPanel tools={selected.tools} selectedToolId={selectedToolId} onSelectTool={setSelectedToolId} onSample={sample => {
                    if (!sample) { setToolInput(''); return }
                    setSelectedToolId(sample.localProjectId)
                    setToolInput(JSON.stringify(sample.payload, null, 2))
                  }} disabled={Boolean(busy)} />
                  <details style={{ margin: '10px 0' }}><summary style={{ color: C.muted, cursor: 'pointer', fontSize: 11 }}>{t('Advanced · Manual Tool JSON')}</summary><textarea value={toolInput} onChange={event => setToolInput(event.target.value)} rows={8} placeholder='{"feature": "value"}' style={{ ...inputStyle, width: '100%', resize: 'vertical', fontFamily: 'JetBrains Mono, monospace', marginTop: 7 }} /></details>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <Button variant="ghost" disabled={Boolean(busy)} onClick={() => void runTest()}>{busy === 'test' ? t('Preparing environment and testing…') : t('Run build smoke test')}</Button>
                    {!['installed', 'ready'].includes(selected.llm.runtimeStatus) && <Button variant="success" disabled={Boolean(busy)} onClick={() => void prepareLlm()}>{busy === 'prepare-llm' ? t('Downloading Base LLM…') : t('Prepare Base LLM locally')}</Button>}
                    {busy === 'chat'
                      ? <Button variant="danger" onClick={() => generationController.current?.abort()}>{t('Stop generating')}</Button>
                      : <Button variant="primary" disabled={Boolean(busy) || !message.trim() || !['installed', 'ready'].includes(selected.llm.runtimeStatus)} onClick={() => void runChat()}>{t('Send to Agent')}</Button>}
                    <Button variant="ghost" disabled={Boolean(busy)} onClick={() => { setConversation([]); setTestResult(null) }}>{t('Clear')}</Button>
                  </div>
                  {llmPreparation && <div style={{ marginTop: 12, padding: 10, border: `1px solid ${C.border}`, borderRadius: C.radius, background: C.bg }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}><strong style={{ color: llmPreparation.status === 'succeeded' ? C.green : llmPreparation.status === 'failed' ? C.red : C.accent, fontSize: 11 }}>{t(llmPreparation.stage)}</strong><strong style={{ color: C.text, fontSize: 11 }}>{llmPreparation.percent.toFixed(1)}%</strong></div>
                    <div style={{ height: 7, margin: '8px 0', overflow: 'hidden', borderRadius: C.pillRadius, background: C.surface2 }}><div style={{ width: `${Math.max(0, Math.min(100, llmPreparation.percent))}%`, height: '100%', background: llmPreparation.status === 'failed' ? C.red : llmPreparation.status === 'succeeded' ? C.green : C.accent, transition: 'width .25s ease' }} /></div>
                    <div style={{ color: C.muted, fontSize: 10 }}>{formatBytes(llmPreparation.downloadedBytes)}{llmPreparation.totalBytes ? ` / ${formatBytes(llmPreparation.totalBytes)}` : ''}</div>
                    <div style={{ color: C.dim, fontSize: 10, marginTop: 4 }}>{llmPreparation.detail}</div>
                  </div>}
                </section>
                <section style={panelStyle}>
                  <SectionLabel>{t('AGENT CONVERSATION')}</SectionLabel>
                  <div style={{ minHeight: 260, maxHeight: 460, overflowY: 'auto', padding: 8, background: C.bg, border: `1px solid ${C.border}`, borderRadius: C.radius }}>
                    {conversation.length === 0 && <p style={subtitleStyle}>{t('Send a message to start a conversation with this completed Agent.')}</p>}
                    {conversation.map((item, index) => <div key={`${item.role}-${index}`} style={chatBubbleStyle(item.role)}><strong style={{ display: 'block', fontSize: 10, marginBottom: 4, color: item.role === 'user' ? C.accent : C.purple }}>{item.role === 'user' ? t('You') : t('Agent')}</strong><div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.55 }}>{item.content}</div></div>)}
                  </div>
                  {testResult && <details style={{ marginTop: 10 }}><summary style={{ color: C.muted, cursor: 'pointer', fontSize: 11 }}>{t('Latest execution result')}</summary><pre style={preStyle}>{JSON.stringify(testResult, null, 2)}</pre></details>}
                </section>
              </div>
            )}
            {tab === 'Serving API' && (
              <div className="agent-serving-layout">
                <section className="agent-serving-card" style={panelStyle}>
                  <div className="agent-serving-header"><div><div style={eyebrowStyle}>{t('LOCAL SERVING')}</div><h2 style={{ ...titleStyle, fontSize: 19 }}>{selected.serving.enabled ? t('API enabled') : t('API disabled')}</h2></div><Badge variant={selected.serving.enabled ? 'green' : 'gray'}>{selected.serving.enabled ? '● ACTIVE' : '○ OFF'}</Badge></div>
                  <p style={subtitleStyle}>{t('Assign a dedicated localhost port to each Agent. Ports already used by another Agent cannot be selected.')}</p>
                  <div className="agent-serving-controls">
                    <label style={labelStyle}>{t('Serving port')}<input type="number" min={24400} max={24499} value={servingPort} onChange={event => setServingPort(event.target.value)} style={{ ...inputStyle, width: '100%', marginTop: 5 }} /></label>
                    {!selected.serving.enabled && <Button variant="success" disabled={busy === 'serve'} onClick={() => void enableServing()}>{t('Enable Serving API')}</Button>}
                    {selected.serving.enabled && <Button variant="primary" disabled={Boolean(busy) || selected.serving.port === Number(servingPort)} onClick={() => void changeServingPort()}>{busy === 'serving-port' ? t('Applying…') : t('Apply port')}</Button>}
                  </div>
                  {selected.serving.enabled && <div className="agent-serving-actions"><Button variant="ghost" disabled={Boolean(busy)} onClick={() => void rotateToken()}>{t('Rotate token')}</Button><Button variant="danger" disabled={Boolean(busy)} onClick={() => void disableServing()}>{t('Disable API')}</Button></div>}
                  <div className="agent-serving-facts">
                    <div className="agent-serving-fact"><span>{t('Serving port')}</span><strong>{selected.serving.port ? String(selected.serving.port) : t('Shared Studio port (legacy)')}</strong></div>
                    <div className="agent-serving-fact agent-serving-fact-wide"><span>{t('Endpoint')}</span><code>{endpoint || '—'}</code>{endpoint && <button type="button" onClick={() => void navigator.clipboard.writeText(endpoint)}>{t('Copy')}</button>}</div>
                  </div>
                  {token && <div style={{ marginTop: 14, padding: 12, background: C.yellowDim, border: `1px solid ${C.yellowBorder}`, borderRadius: C.radius }}><strong style={{ color: C.yellow, fontSize: 12 }}>{t('Copy this token now')}</strong><pre style={{ ...preStyle, marginTop: 7 }}>{token}</pre><Button variant="ghost" onClick={() => void navigator.clipboard.writeText(token)}>{t('Copy token')}</Button></div>}
                </section>
                <section className="agent-serving-card" style={panelStyle}>
                  <SectionLabel>{t('Endpoints')}</SectionLabel>
                  <p style={subtitleStyle}>{t('Use Chat for normal Agent requests. It applies the selected Base LLM, Agent Harness, and Tool routing.')}</p>
                  <Endpoint method="GET" path={`${endpoint}/health`} previews={[{ label: t('Health check') }]} />
                  <Endpoint method="GET" path={`${endpoint}/info`} previews={[{ label: t('Agent information') }]} />
                  <Endpoint method="POST" path={`${endpoint}/chat`} previews={chatPreviews} />
                  <div style={{ color: C.yellow, fontSize: 11, lineHeight: 1.6 }}>{t('Health and info work after API enablement. Chat returns 409 until the selected Base LLM is locally available; placeholder answers are never returned.')}</div>
                </section>
                <ServingDataSources agent={selected} endpoint={endpoint} sources={servingSources} onSourcesChange={setServingSources} disabled={Boolean(busy)} onServingChange={serving => replaceAgent({ ...selected, serving })} />
              </div>
            )}
            {tab === 'Requests' && <Requests records={requests} />}
          </div>
        </main>
      )}
    </div>
  )
}

function ServingDataSources({ agent, endpoint, sources, onSourcesChange, disabled, onServingChange }: { agent: BuiltAgent; endpoint: string; sources: AgentServingDataSource[]; onSourcesChange: (sources: AgentServingDataSource[]) => void; disabled: boolean; onServingChange: (serving: BuiltAgent['serving']) => void }) {
  const { t } = useI18n()
  const [toolId, setToolId] = useState(agent.tools[0]?.localProjectId ?? '')
  const [name, setName] = useState('')
  const [dataPath, setDataPath] = useState('')
  const [sampleIndex, setSampleIndex] = useState(0)
  const [selectionMode, setSelectionMode] = useState<'fixed' | 'request'>('fixed')
  const [allowed, setAllowed] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AgentToolPredictionResponse | null>(null)
  const tool = agent.tools.find(item => item.localProjectId === toolId) ?? agent.tools[0] ?? null
  const source = sources.find(item => item.toolId === tool?.localProjectId) ?? null

  useEffect(() => {
    setToolId(agent.tools[0]?.localProjectId ?? '')
  }, [agent.agentId, agent.buildRevision])

  useEffect(() => {
    setName(source?.name ?? tool?.modelName ?? '')
    setDataPath(source?.dataPath ?? '')
    setSampleIndex(source?.sampleIndex ?? 0)
    setSelectionMode(source?.selectionMode ?? 'fixed')
    setAllowed(source?.enabledForServing ?? false)
    setResult(null)
  }, [source?.sourceId, source?.updatedAt, tool?.localProjectId])

  async function save() {
    if (!tool) return
    setBusy('save'); setError(null)
    try {
      const saved = await saveAgentServingDataSource(agent.agentId, {
        toolId: tool.localProjectId,
        name,
        dataPath,
        sampleIndex,
        selectionMode,
        enabledForServing: allowed,
      })
      onSourcesChange([...sources.filter(item => item.sourceId !== saved.sourceId), saved])
    } catch (cause) { setError(message(cause)) }
    finally { setBusy(null) }
  }

  async function testSource() {
    if (!source) return
    setBusy('test'); setError(null); setResult(null)
    try {
      setResult(await testAgentServingDataSource(
        agent.agentId,
        source.sourceId,
        source.selectionMode === 'request' ? sampleIndex : undefined,
      ))
    } catch (cause) { setError(message(cause)) }
    finally { setBusy(null) }
  }

  async function remove() {
    if (!source) return
    setBusy('delete'); setError(null)
    try {
      await deleteAgentServingDataSource(agent.agentId, source.sourceId)
      onSourcesChange(sources.filter(item => item.sourceId !== source.sourceId))
    } catch (cause) { setError(message(cause)) }
    finally { setBusy(null) }
  }

  async function configureDirectTool(enabled: boolean) {
    if (!tool) return
    setBusy('direct-tool'); setError(null)
    try {
      onServingChange(await configureAgentDirectToolServing(agent.agentId, tool.localProjectId, enabled))
    } catch (cause) { setError(message(cause)) }
    finally { setBusy(null) }
  }

  const inlinePayload = tool ? manifestExample(tool) : {}
  const inlineBody = { input: { type: 'inline', toolId: tool?.localProjectId, payload: inlinePayload } }
  const sourceBody = source ? {
    input: {
      type: 'data-source',
      sourceId: source.sourceId,
      ...(source.selectionMode === 'request' ? { sampleIndex } : {}),
    },
  } : null
  const invokeEndpoint = tool ? `${endpoint}/tools/${encodeURIComponent(tool.localProjectId)}/invoke` : `${endpoint}/tools/{toolId}/invoke`
  const sourceChatBody = sourceBody ? { message: 'Use the selected Tool AI and explain the verified result.', history: [], input: sourceBody.input } : null
  const inlineChatBody = { message: 'Use the selected Tool AI and explain the verified result.', history: [], input: inlineBody.input }
  const chatRequestPreviews: EndpointRequestPreview[] = [
    ...(sourceChatBody ? [{
      label: t('Local Data Source'),
      detail: t('Only the Data Source reference is sent. The sample is loaded from Task Data on this device.'),
      body: sourceChatBody,
    }] : []),
    {
      label: t('Inline JSON'),
      detail: t('The complete Tool AI input is included directly in the request body.'),
      body: inlineChatBody,
    },
  ]
  const directRequestPreviews: EndpointRequestPreview[] = [
    ...(sourceBody ? [{
      label: t('Local Data Source'),
      detail: t('Only the Data Source reference is sent. The sample is loaded from Task Data on this device.'),
      body: sourceBody,
    }] : []),
    {
      label: t('Inline JSON'),
      detail: t('The complete Tool AI input is included directly in the request body.'),
      body: inlineBody,
    },
  ]
  const directEnabled = Boolean(tool && agent.serving.directToolIds?.includes(tool.localProjectId))

  if (!agent.tools.length) return <section className="agent-serving-data-panel" style={panelStyle}><Empty title={t('No Tool AI')} detail={t('Add a Tool AI to configure a Serving Data Source.')} /></section>
  return <section className="agent-serving-data-panel" style={panelStyle}>
    <div className="agent-serving-section-header">
      <div><div style={eyebrowStyle}>{t('SERVING DATA SOURCES')}</div><h2 style={{ ...titleStyle, fontSize: 17 }}>{t('Local data for Tool AI inference')}</h2><p style={subtitleStyle}>{t('Only the relative path and sample policy are stored. Task Data remains on this device.')}</p></div>
      <Badge variant={source?.enabledForServing ? 'green' : 'gray'}>{source?.enabledForServing ? t('Available to Serving API') : t('Local test only')}</Badge>
    </div>
    <div className="agent-serving-data-form">
      <label className="agent-serving-field agent-serving-field-tool" style={labelStyle}>{t('Tool AI')}<select value={tool?.localProjectId ?? ''} onChange={event => setToolId(event.target.value)} disabled={disabled || Boolean(busy)} style={{ ...inputStyle, width: '100%', marginTop: 5 }}>{agent.tools.map(item => <option key={item.localProjectId} value={item.localProjectId}>{item.modelName}</option>)}</select></label>
      <label className="agent-serving-field agent-serving-field-name" style={labelStyle}>{t('Data Source name')}<input value={name} onChange={event => setName(event.target.value)} disabled={disabled || Boolean(busy)} style={{ ...inputStyle, width: '100%', marginTop: 5 }} /></label>
      <label className="agent-serving-field agent-serving-field-path" style={labelStyle}>{t('Relative path inside Task Data')}<input value={dataPath} onChange={event => setDataPath(event.target.value)} disabled={disabled || Boolean(busy)} placeholder="MNIST/raw or records.csv" style={{ ...inputStyle, width: '100%', marginTop: 5 }} /></label>
      <label className="agent-serving-field agent-serving-field-index" style={labelStyle}>{t('Default sample index')}<input type="number" min={0} value={sampleIndex} onChange={event => setSampleIndex(Math.max(0, Math.trunc(Number(event.target.value) || 0)))} disabled={disabled || Boolean(busy)} style={{ ...inputStyle, width: '100%', marginTop: 5 }} /></label>
      <label className="agent-serving-field agent-serving-field-selection" style={labelStyle}>{t('Sample selection')}<select value={selectionMode} onChange={event => setSelectionMode(event.target.value as 'fixed' | 'request')} disabled={disabled || Boolean(busy)} style={{ ...inputStyle, width: '100%', marginTop: 5 }}><option value="fixed">{t('Fixed by owner')}</option><option value="request">{t('Request may select index')}</option></select></label>
      <label className="agent-serving-allow" style={labelStyle}><input type="checkbox" checked={allowed} onChange={event => setAllowed(event.target.checked)} disabled={disabled || Boolean(busy)} /><span>{t('Allow this Data Source in Serving API')}</span></label>
    </div>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 11 }}>
      <Button variant="ghost" disabled={disabled || Boolean(busy) || !tool} onClick={() => tool && void openWorkspaceDataFolder(tool.localProjectId).catch(cause => setError(message(cause)))}>{t('Open Data Folder')}</Button>
      <Button variant="primary" disabled={disabled || Boolean(busy) || !tool} onClick={() => void save()}>{busy === 'save' ? t('Saving…') : source ? t('Update Data Source') : t('Register Data Source')}</Button>
      <Button variant="success" disabled={disabled || Boolean(busy) || !source?.available || !source.hasEntries} onClick={() => void testSource()}>{busy === 'test' ? t('Testing…') : t('Test Sample')}</Button>
      {source && <Button variant="danger" disabled={disabled || Boolean(busy)} onClick={() => void remove()}>{t('Remove')}</Button>}
    </div>
    {source && <div style={{ ...monoDimStyle, whiteSpace: 'normal' }}>{source.sourceId} · {source.fileCount} {t('files')} · {source.available ? t('Ready') : t('Data path unavailable')}</div>}
    {error && <div style={{ marginTop: 10, padding: 9, border: `1px solid ${C.redBorder}`, borderRadius: C.radius, background: C.redDim, color: C.red, fontSize: 11 }}>{error}</div>}
    {result && <details open style={{ marginTop: 10 }}><summary style={{ color: C.green, cursor: 'pointer', fontSize: 11 }}>{t('Test prediction succeeded')}</summary><pre style={preStyle}>{JSON.stringify(result, null, 2)}</pre></details>}
    <RequestPreviewDetails
      method="POST"
      path={`${endpoint}/chat`}
      previews={chatRequestPreviews}
      title={t('Requests for this Tool AI')}
    />
    <details className="agent-serving-direct-tool">
      <summary>{t('Advanced · Direct Tool API')}</summary>
      <div className="agent-serving-direct-warning">
        <strong>{t('This API bypasses the Base LLM and Agent Harness.')}</strong>
        <span>{t('Enable it only when an application needs the selected Tool AI structured result directly.')}</span>
      </div>
      <label className="agent-serving-direct-toggle">
        <input type="checkbox" checked={directEnabled} onChange={event => void configureDirectTool(event.target.checked)} disabled={disabled || Boolean(busy) || !tool} />
        <span>{busy === 'direct-tool' ? t('Applying…') : t('Enable Direct Tool API for this Tool AI')}</span>
      </label>
      <Endpoint method="POST" path={invokeEndpoint} previews={directRequestPreviews} />
      {!directEnabled && <div style={{ color: C.yellow, fontSize: 11, marginTop: 8 }}>{t('Direct invocation is currently blocked. Normal Agent Chat remains available.')}</div>}
    </details>
  </section>
}

function manifestExample(tool: AgentToolModel): Record<string, unknown> {
  const schema = tool.toolManifest?.input?.jsonSchema
  if (schema && typeof schema === 'object') {
    const value = schemaValue(schema)
    if (value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length) return value as Record<string, unknown>
  }
  return Object.fromEntries((tool.toolManifest?.features ?? []).map(feature => [feature, 0]))
}

function schemaValue(schema: Record<string, unknown>): unknown {
  if ('example' in schema) return schema.example
  if ('default' in schema) return schema.default
  if (schema.type === 'object') {
    const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties as Record<string, unknown> : {}
    return Object.fromEntries(Object.entries(properties).map(([key, value]) => [key, schemaValue(value as Record<string, unknown>)]))
  }
  if (schema.type === 'array') {
    const count = Math.max(1, Math.min(Number(schema.minItems) || 1, 32))
    const child = schema.items && typeof schema.items === 'object' ? schema.items as Record<string, unknown> : {}
    return Array.from({ length: count }, () => schemaValue(child))
  }
  if (schema.type === 'string') return ''
  if (schema.type === 'boolean') return false
  if (schema.type === 'null') return null
  return 0
}

function Overview({ agent, participations, onOpenFederation }: { agent: BuiltAgent; participations: FederatedParticipation[]; onOpenFederation: (source: AgentToolModel | Extract<AgentLlm, { source: 'federated-task' }>) => void }) {
  const { t } = useI18n()
  const federated = [
    ...(agent.llm.source === 'federated-task' ? [agent.llm] : []),
    ...agent.tools,
  ]
  return <div style={{ display: 'grid', gap: 14 }}>
    <section style={panelStyle}>
      <div style={eyebrowStyle}>{t('FEDERATED COMPONENTS')}</div>
      <h2 style={{ ...titleStyle, fontSize: 18 }}>{t('Models that continue learning')}</h2>
      <p style={subtitleStyle}>{t('Open a component in Federated Learning to check its server, local training, and latest Global Model.')}</p>
      {agent.llm.source === 'huggingface' && <div style={componentRowStyle}>
        <div style={{ minWidth: 0, flex: 1 }}><strong style={{ color: C.text }}>{llmName(agent.llm)}</strong><span style={monoDimStyle}>{t('External Base LLM · not federated')}</span></div><Badge variant="gray">Hugging Face</Badge>
      </div>}
      {federated.map(component => {
        const participation = participations.find(item => item.localProjectId === component.localProjectId || (component.taskId && item.task.taskId === component.taskId))
        const serverLive = Boolean(participation?.server?.ready && participation.server.aggregationServer)
        const latest = participation?.globalModel?.modelVersionId
        const current = component.modelVersionId
        const updateAvailable = Boolean(latest && current && latest !== current)
        return <div key={`${component.localProjectId}-${component === agent.llm ? 'llm' : 'tool'}`} style={componentRowStyle}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}><strong style={{ color: C.text }}>{component.modelName}</strong><Badge variant={component === agent.llm ? 'purple' : 'blue'}>{component === agent.llm ? 'Base LLM' : 'Tool AI'}</Badge>{updateAvailable && <Badge variant="yellow">{t('Global Model update available')}</Badge>}</div>
            <span style={monoDimStyle}>{component.taskTitle} · {current ?? component.releaseId ?? '—'}</span>
          </div>
          <Badge variant={serverLive ? 'green' : 'gray'}>{serverLive ? `● ${t('Server Live')}` : `○ ${t('Server Offline')}`}</Badge>
          <Button variant="ghost" onClick={() => onOpenFederation(component)}>{t('Open Federated Learning')}</Button>
        </div>
      })}
      {!federated.length && <div style={{ color: C.dim, fontSize: 12 }}>{t('This Agent has no Federated Task component.')}</div>}
    </section>
    <div style={twoColumnStyle}>
      <section style={panelStyle}><div style={eyebrowStyle}>{t('MODEL COMPOSITION')}</div><MetaRow label={t('Base LLM')} value={llmName(agent.llm)} mono /><MetaRow label={t('Model identity')} value={llmIdentity(agent.llm)} mono /><MetaRow label={t('Source')} value={agent.llm.source === 'huggingface' ? 'Hugging Face' : 'Federated Task'} /><MetaRow label={t('LLM runtime')} value={agent.llm.runtimeStatus} /><MetaRow label={t('Tool AI models')} value={String(agent.tools.length)} /></section>
      <section style={panelStyle}><div style={eyebrowStyle}>{t('AGENT HARNESS')}</div><p style={{ ...subtitleStyle, whiteSpace: 'pre-wrap' }}>{agent.harness.instructions}</p><MetaRow label={t('Tool routing')} value={agent.harness.toolRouting} /><MetaRow label={t('Context')} value={String(agent.harness.contextWindow)} mono /><MetaRow label={t('Memory')} value={agent.harness.memory} /><MetaRow label={t('Safety')} value={agent.harness.safety} /></section>
    </div>
  </div>
}

function llmName(llm: AgentLlm) { return llm.source === 'huggingface' ? llm.displayName : llm.modelName }
function llmIdentity(llm: AgentLlm) {
  return llm.source === 'huggingface'
    ? `${llm.repoId}@${llm.revision}${llm.fileName ? ` · ${llm.fileName}` : ''}`
    : `${llm.taskTitle} · ${llm.modelVersionId || llm.releaseId || llm.modelSha256.slice(0, 12)}`
}

function Requests({ records }: { records: AgentRequestRecord[] }) {
  const { t } = useI18n()
  if (!records.length) return <Empty title={t('No requests yet')} detail={t('Agent tests, health/info calls, and serving requests are recorded here without prompt or token contents.')} />
  return <section style={panelStyle}><table style={{ width: '100%', borderCollapse: 'collapse' }}><thead><tr>{['Time', 'Source', 'Method', 'Path', 'Status', 'Latency'].map(label => <th key={label} style={thStyle}>{t(label)}</th>)}</tr></thead><tbody>{records.map(record => <tr key={record.requestId} style={{ borderTop: `1px solid ${C.borderSubtle}` }}><td style={tdStyle}>{new Date(record.createdAt).toLocaleTimeString()}</td><td style={tdStyle}>{record.source}</td><td style={tdStyle}>{record.method}</td><td style={tdStyle}>{record.path}</td><td style={{ ...tdStyle, color: record.status < 400 ? C.green : C.red }}>{record.status}</td><td style={tdStyle}>{record.latencyMs} ms</td></tr>)}</tbody></table></section>
}

function Endpoint({ method, path, previews = [] }: { method: string; path: string; previews?: EndpointRequestPreview[] }) {
  const { t } = useI18n()
  return <div className="agent-serving-endpoint-shell">
    <div className="agent-serving-endpoint">
      <span className={`agent-serving-method agent-serving-method-${method.toLowerCase()}`}>{method}</span>
      <code title={path}>{path}</code>
      <button type="button" onClick={() => void navigator.clipboard.writeText(path)}>{t('Copy')}</button>
    </div>
    {previews.length > 0 && <RequestPreviewDetails method={method} path={path} previews={previews} />}
  </div>
}

function RequestPreviewDetails({ method, path, previews, title }: { method: string; path: string; previews: EndpointRequestPreview[]; title?: string }) {
  const { t } = useI18n()
  return <details className="agent-serving-request-preview">
    <summary>
      <span>{title ?? t('Request preview')}</span>
      {previews.length > 1 && <span className="agent-serving-preview-count">{previews.length} {t('input methods')}</span>}
    </summary>
    <div className="agent-serving-preview-list">
      {previews.map((preview, index) => {
        const bodyText = preview.body ? JSON.stringify(preview.body, null, 2) : ''
        const curl = requestCurl(method, path, preview.body)
        return <section className="agent-serving-preview-item" key={`${preview.label}-${index}`}>
          <div className="agent-serving-preview-heading">
            <div><strong>{preview.label}</strong>{preview.detail && <p>{preview.detail}</p>}</div>
            <div className="agent-serving-preview-actions">
              {bodyText && <button type="button" onClick={() => void navigator.clipboard.writeText(bodyText)}>{t('Copy JSON')}</button>}
              <button type="button" onClick={() => void navigator.clipboard.writeText(curl)}>{t('Copy curl')}</button>
            </div>
          </div>
          {bodyText && <details className="agent-serving-preview-code"><summary>{t('JSON body')}</summary><pre>{bodyText}</pre></details>}
          <details className="agent-serving-preview-code"><summary>{t('curl request')}</summary><pre>{curl}</pre></details>
        </section>
      })}
    </div>
  </details>
}

function requestCurl(method: string, path: string, body?: Record<string, unknown>) {
  const lines = [`curl -X ${method.toUpperCase()} '${path}'`, `  -H 'Authorization: Bearer <token>'`]
  if (body) {
    const encoded = JSON.stringify(body).replace(/'/g, `'"'"'`)
    lines.push(`  -H 'Content-Type: application/json'`, `  --data '${encoded}'`)
  }
  return lines.join(' \\\n')
}
function Empty({ title, detail }: { title: string; detail: string }) { return <div style={{ padding: 24, textAlign: 'center' }}><strong style={{ color: C.text, fontSize: 14 }}>{title}</strong><p style={subtitleStyle}>{detail}</p></div> }

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  const power = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** power).toFixed(power > 2 ? 2 : 1)} ${units[power]}`
}

function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause) }

const eyebrowStyle: React.CSSProperties = { color: C.purple, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, letterSpacing: '0.08em', marginBottom: 5 }
const titleStyle: React.CSSProperties = { color: C.text, fontSize: 22, margin: 0, lineHeight: 1.2 }
const subtitleStyle: React.CSSProperties = { color: C.muted, fontSize: 12, lineHeight: 1.6, margin: '7px 0 12px' }
const monoDimStyle: React.CSSProperties = { display: 'block', color: C.dim, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
const agentRowStyle = (active: boolean): React.CSSProperties => ({ width: '100%', textAlign: 'left', padding: '11px 12px', background: active ? C.surface2 : 'transparent', border: 'none', borderBottom: `1px solid ${C.borderSubtle}`, borderLeft: active ? `2px solid ${C.accent}` : '2px solid transparent', cursor: 'pointer', fontFamily: 'Inter, sans-serif' })
const headerStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', background: C.surface, borderBottom: `1px solid ${C.border}`, flexWrap: 'wrap' }
const tabsStyle: React.CSSProperties = { display: 'flex', background: C.surface, borderBottom: `1px solid ${C.border}` }
const tabStyle = (active: boolean): React.CSSProperties => ({ padding: '8px 14px', background: 'none', border: 'none', borderBottom: active ? `2px solid ${C.accent}` : '2px solid transparent', color: active ? C.text : C.muted, fontSize: 12, cursor: 'pointer' })
const twoColumnStyle: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 14, alignItems: 'start' }
const panelStyle: React.CSSProperties = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 14, boxShadow: C.shadow, minWidth: 0 }
const componentRowStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '10px 0', borderTop: `1px solid ${C.borderSubtle}` }
const labelStyle: React.CSSProperties = { display: 'block', color: C.muted, fontSize: 11, fontWeight: 600, margin: '10px 0 5px' }
const inputStyle: React.CSSProperties = { boxSizing: 'border-box', background: C.inputBg, color: C.text, border: `1px solid ${C.controlBorder}`, borderRadius: C.radius, padding: '8px 10px', fontSize: 12, outline: 'none' }
const preStyle: React.CSSProperties = { maxWidth: '100%', overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', background: C.surface2, color: C.text, border: `1px solid ${C.border}`, borderRadius: C.radius, padding: 10, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, lineHeight: 1.55 }
const chatBubbleStyle = (role: AgentChatMessage['role']): React.CSSProperties => ({ maxWidth: '86%', margin: role === 'user' ? '8px 0 8px auto' : '8px auto 8px 0', padding: '9px 11px', borderRadius: C.radius, border: `1px solid ${role === 'user' ? C.accentBorder : C.border}`, background: role === 'user' ? C.accentDim : C.surface, color: C.text, fontSize: 12 })
const thStyle: React.CSSProperties = { textAlign: 'left', color: C.dim, fontSize: 10, fontWeight: 500, padding: '6px 8px' }
const tdStyle: React.CSSProperties = { color: C.muted, fontFamily: 'JetBrains Mono, monospace', fontSize: 10, padding: '7px 8px' }
