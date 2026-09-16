import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import { useEffect, useRef, useState } from 'react'
import { io } from 'socket.io-client'
import type { Socket } from 'socket.io-client'

import type {
  PythonEnvironment,
  PythonEnvironmentStatus,
  UvRuntimeInformation,
} from '../../api/environments'
import { prepareWorkspaceTerminal } from '../../api/workspace'
import type { WorkspaceProject } from '../../api/workspace'
import { THEME_CHANGE_EVENT } from '../../app/theme'
import { useI18n } from '../../app/i18n'

type TerminalStatus = 'connecting' | 'connected' | 'disconnected' | 'error'
type TerminalSession = {
  terminalId: string
  localProjectId: string
  title: string
  shellName: string
  environmentId: string | null
  environmentStatus: PythonEnvironmentStatus
  environmentLabel: string | null
  createdAt: string
}
type TerminalResponse = {
  status: 'ready' | 'error'
  message?: string
  item?: TerminalSession
  items?: TerminalSession[]
}
type PointerCoordinates = Pick<MouseEvent, 'clientX' | 'clientY'>
type XtermMouseReport = {
  col: number
  row: number
  x: number
  y: number
}
type XtermMouseService = {
  getCoords: (
    event: PointerCoordinates,
    element: HTMLElement,
    colCount: number,
    rowCount: number,
    isSelection?: boolean,
  ) => [number, number] | undefined
  getMouseReportCoords: (
    event: MouseEvent,
    element: HTMLElement,
  ) => XtermMouseReport | undefined
}

const MAX_TERMINALS = 8
const scrollDistances = new Map<string, number>()

export default function WorkspaceTerminal({
  accountKey,
  project,
  environment,
  uv,
  active,
}: {
  accountKey: string
  project: WorkspaceProject
  environment: PythonEnvironment | null
  uv: UvRuntimeInformation | null
  active: boolean
}) {
  const { t } = useI18n()
  const translateRef = useRef(t)
  const hostRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const socketRef = useRef<Socket | null>(null)
  const activeViewRef = useRef(active)
  const environmentRef = useRef(environment)
  const sessionsRef = useRef<TerminalSession[]>([])
  const activeTerminalIdRef = useRef<string | null>(null)
  const contextReadyRef = useRef(false)
  const operationPendingRef = useRef(false)
  const selectTerminalRef = useRef<(terminalId: string) => void>(() => undefined)
  const createTerminalRef = useRef<() => void>(() => undefined)
  const closeTerminalRef = useRef<(terminalId: string) => void>(() => undefined)
  const restartTerminalRef = useRef<(terminalId: string) => void>(() => undefined)
  const automaticRestartKeyRef = useRef<string | null>(null)
  const [status, setStatus] = useState<TerminalStatus>('connecting')
  const [sessions, setSessions] = useState<TerminalSession[]>([])
  const [activeTerminalId, setActiveTerminalId] = useState<string | null>(null)
  const [operationPending, setOperationPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  activeViewRef.current = active
  environmentRef.current = environment
  translateRef.current = t

  useEffect(() => {
    const mountedHost = hostRef.current
    if (!mountedHost) return
    const host: HTMLDivElement = mountedHost
    let disposed = false
    operationPendingRef.current = false

    const terminal = new Terminal({
      allowTransparency: false,
      convertEol: false,
      cursorBlink: true,
      cursorStyle: 'block',
      drawBoldTextInBrightColors: true,
      fontFamily: 'JetBrains Mono, ui-monospace, monospace',
      fontSize: 12,
      lineHeight: 1.28,
      minimumContrastRatio: 4.5,
      rightClickSelectsWord: true,
      scrollback: 10_000,
      scrollOnUserInput: true,
      smoothScrollDuration: 80,
      theme: terminalTheme(),
    })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(host)
    const restorePointerScale = installXtermPointerScaleAdapter(terminal)
    terminalRef.current = terminal
    fitRef.current = fit

    const socket = io({
      autoConnect: false,
      withCredentials: true,
      transports: ['websocket', 'polling'],
    })
    socketRef.current = socket

    function fitTerminal() {
      if (!activeViewRef.current || host.clientWidth < 20 || host.clientHeight < 20) return
      try {
        fit.fit()
      } catch {
        // A hidden or transitioning pane can briefly have no measurable geometry.
      }
    }

    function replaceSessions(items: TerminalSession[]) {
      sessionsRef.current = items
      if (!disposed) setSessions(items)
    }

    function selectTerminal(terminalId: string) {
      if (!socket.connected || (activeTerminalIdRef.current === terminalId && contextReadyRef.current)) return
      const selected = sessionsRef.current.find(item => item.terminalId === terminalId)
      if (!selected) return

      const previousId = activeTerminalIdRef.current
      if (previousId) {
        scrollDistances.set(
          previousId,
          Math.max(0, terminal.buffer.active.baseY - terminal.buffer.active.viewportY),
        )
      }
      activeTerminalIdRef.current = terminalId
      contextReadyRef.current = false
      setActiveTerminalId(terminalId)
      setError(null)
      window.localStorage.setItem(activeTerminalStorageKey(accountKey, project.localProjectId), terminalId)
      terminal.reset()
      terminal.clear()

      socket.emit(
        'terminal_context',
        { localProjectId: project.localProjectId, terminalId },
        (response: TerminalResponse) => {
          if (disposed || activeTerminalIdRef.current !== terminalId) return
          if (response.status === 'error') {
            contextReadyRef.current = false
            setError(response.message ?? translateRef.current('The terminal could not be selected.'))
            return
          }
          contextReadyRef.current = true
          window.requestAnimationFrame(() => {
            fitTerminal()
            terminal.focus()
          })
        },
      )
    }

    function createTerminal() {
      if (!socket.connected || operationPendingRef.current) return
      operationPendingRef.current = true
      setOperationPending(true)
      setError(null)
      socket.emit(
        'terminal_create',
        {
          localProjectId: project.localProjectId,
          environmentId: environmentRef.current?.environmentId ?? null,
        },
        (response: TerminalResponse) => {
          operationPendingRef.current = false
          if (disposed) return
          setOperationPending(false)
          if (response.status === 'error' || !response.item) {
            setError(response.message ?? translateRef.current('The terminal could not be created.'))
            return
          }
          const items = [...sessionsRef.current, response.item]
          replaceSessions(items)
          selectTerminal(response.item.terminalId)
        },
      )
    }

    function closeTerminal(terminalId: string) {
      if (!socket.connected || operationPendingRef.current) return
      const previousItems = sessionsRef.current
      const closingIndex = previousItems.findIndex(item => item.terminalId === terminalId)
      if (closingIndex < 0) return
      operationPendingRef.current = true
      setOperationPending(true)
      setError(null)
      socket.emit(
        'terminal_close',
        { localProjectId: project.localProjectId, terminalId },
        (response: TerminalResponse) => {
          operationPendingRef.current = false
          if (disposed) return
          setOperationPending(false)
          if (response.status === 'error') {
            setError(response.message ?? translateRef.current('The terminal could not be closed.'))
            return
          }
          const items = response.items ?? []
          replaceSessions(items)
          scrollDistances.delete(terminalId)
          if (activeTerminalIdRef.current !== terminalId) return

          activeTerminalIdRef.current = null
          contextReadyRef.current = false
          setActiveTerminalId(null)
          terminal.reset()
          terminal.clear()
          const next = items[Math.min(closingIndex, items.length - 1)]
          if (next) {
            selectTerminal(next.terminalId)
          } else {
            window.localStorage.removeItem(activeTerminalStorageKey(accountKey, project.localProjectId))
          }
        },
      )
    }

    function restartTerminal(terminalId: string) {
      const targetEnvironment = environmentRef.current
      if (
        !socket.connected
        || operationPendingRef.current
        || !targetEnvironment
        || targetEnvironment.status !== 'ready'
      ) return

      operationPendingRef.current = true
      setOperationPending(true)
      setError(null)
      socket.emit(
        'terminal_restart',
        {
          localProjectId: project.localProjectId,
          terminalId,
          environmentId: targetEnvironment.environmentId,
        },
        (response: TerminalResponse) => {
          operationPendingRef.current = false
          if (disposed) return
          setOperationPending(false)
          if (response.status === 'error' || !response.item) {
            setError(response.message ?? translateRef.current('The terminal could not be restarted with the selected Python environment.'))
            return
          }

          scrollDistances.delete(terminalId)
          activeTerminalIdRef.current = null
          contextReadyRef.current = false
          setActiveTerminalId(null)
          terminal.reset()
          terminal.clear()
          replaceSessions(response.items ?? sessionsRef.current.map(item => (
            item.terminalId === terminalId ? response.item as TerminalSession : item
          )))
          selectTerminal(response.item.terminalId)
        },
      )
    }

    selectTerminalRef.current = selectTerminal
    createTerminalRef.current = createTerminal
    closeTerminalRef.current = closeTerminal
    restartTerminalRef.current = restartTerminal

    function loadTerminals() {
      socket.emit(
        'terminal_list',
        { localProjectId: project.localProjectId },
        (response: TerminalResponse) => {
          if (disposed) return
          if (response.status === 'error') {
            setStatus('error')
            setError(response.message ?? translateRef.current('The terminal list could not be loaded.'))
            return
          }
          const items = response.items ?? []
          replaceSessions(items)
          if (!items.length) {
            createTerminal()
            return
          }
          const storedId = window.localStorage.getItem(activeTerminalStorageKey(accountKey, project.localProjectId))
          const selected = items.find(item => item.terminalId === storedId) ?? items[items.length - 1]
          selectTerminal(selected.terminalId)
        },
      )
    }

    socket.on('connect', () => {
      setStatus('connected')
      loadTerminals()
    })
    socket.on('disconnect', () => {
      contextReadyRef.current = false
      setStatus('disconnected')
    })
    socket.on('connect_error', () => setStatus('error'))
    socket.on('terminal_output', (data: { terminalId?: string; output?: string; replay?: boolean }) => {
      if (data?.terminalId !== activeTerminalIdRef.current) return
      const output = String(data?.output ?? '')
      if (!output) return
      if (data.replay) {
        const distance = scrollDistances.get(data.terminalId) ?? 0
        terminal.reset()
        terminal.clear()
        terminal.write(output, () => {
          const target = Math.max(0, terminal.buffer.active.baseY - distance)
          terminal.scrollToLine(target)
        })
        return
      }
      terminal.write(output)
    })

    const dataDisposable = terminal.onData(data => {
      if (socket.connected && contextReadyRef.current) socket.emit('terminal_input', { input: data })
    })
    const resizeDisposable = terminal.onResize(size => {
      if (socket.connected && contextReadyRef.current) socket.emit('terminal_resize', size)
    })
    const scrollDisposable = terminal.onScroll(viewportY => {
      const terminalId = activeTerminalIdRef.current
      if (terminalId) {
        scrollDistances.set(terminalId, Math.max(0, terminal.buffer.active.baseY - viewportY))
      }
    })
    terminal.attachCustomKeyEventHandler(event => {
      if (event.type !== 'keydown') return true
      const copyShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'c'
      if (copyShortcut && terminal.hasSelection()) {
        void navigator.clipboard?.writeText(terminal.getSelection())
        return false
      }
      return true
    })

    const observer = new ResizeObserver(fitTerminal)
    observer.observe(host)
    const themeObserver = new MutationObserver(() => {
      terminal.options.theme = terminalTheme()
    })
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    })
    const updateTerminalTheme = () => {
      terminal.options.theme = terminalTheme()
    }
    window.addEventListener(THEME_CHANGE_EVENT, updateTerminalTheme)

    prepareWorkspaceTerminal()
      .then(() => socket.connect())
      .catch(() => setStatus('error'))
    window.requestAnimationFrame(fitTerminal)

    return () => {
      disposed = true
      const terminalId = activeTerminalIdRef.current
      if (terminalId) {
        scrollDistances.set(
          terminalId,
          Math.max(0, terminal.buffer.active.baseY - terminal.buffer.active.viewportY),
        )
      }
      observer.disconnect()
      themeObserver.disconnect()
      window.removeEventListener(THEME_CHANGE_EVENT, updateTerminalTheme)
      dataDisposable.dispose()
      resizeDisposable.dispose()
      scrollDisposable.dispose()
      socket.disconnect()
      restorePointerScale()
      terminal.dispose()
      socketRef.current = null
      terminalRef.current = null
      fitRef.current = null
      sessionsRef.current = []
      activeTerminalIdRef.current = null
      contextReadyRef.current = false
      operationPendingRef.current = false
      restartTerminalRef.current = () => undefined
    }
  }, [accountKey, project.localProjectId])

  useEffect(() => {
    if (active) {
      window.requestAnimationFrame(() => {
        try {
          fitRef.current?.fit()
          terminalRef.current?.focus()
        } catch {
          // The pane will be measured again by ResizeObserver.
        }
      })
    }
  }, [active])

  useEffect(() => {
    const session = sessions.find(item => item.terminalId === activeTerminalId)
    if (
      status !== 'connected'
      || operationPending
      || !session
      || !environment
      || environment.status !== 'ready'
    ) return

    const syncedAt = environment.lastSyncedAt ? Date.parse(environment.lastSyncedAt) : Number.NaN
    const terminalCreatedAt = Date.parse(session.createdAt)
    const synchronizedAfterTerminalStarted = (
      Number.isFinite(syncedAt)
      && Number.isFinite(terminalCreatedAt)
      && syncedAt > terminalCreatedAt
    )
    const needsSelectedEnvironment = (
      session.environmentId !== environment.environmentId
      || session.environmentStatus !== 'ready'
      || synchronizedAfterTerminalStarted
    )
    if (!needsSelectedEnvironment) {
      automaticRestartKeyRef.current = null
      return
    }

    const restartKey = `${session.terminalId}:${environment.environmentId}:${environment.lastSyncedAt ?? 'ready'}`
    if (automaticRestartKeyRef.current === restartKey) return
    automaticRestartKeyRef.current = restartKey
    restartTerminalRef.current(session.terminalId)
  }, [
    activeTerminalId,
    environment,
    operationPending,
    sessions,
    status,
  ])

  function clearTerminal() {
    const terminalId = activeTerminalIdRef.current
    if (!terminalId) return
    terminalRef.current?.clear()
    scrollDistances.set(terminalId, 0)
    socketRef.current?.emit('terminal_clear')
  }

  const activeSession = sessions.find(item => item.terminalId === activeTerminalId)
  const sessionEnvironmentStatus = activeSession?.environmentStatus ?? environment?.status ?? 'missing'
  const sessionEnvironmentLabel = activeSession?.environmentLabel ?? environment?.name ?? t('No environment')
  const environmentText = `${sessionEnvironmentLabel} · ${t(environmentStatusMessage(sessionEnvironmentStatus))}`
  const environmentTone = environmentStatusTone(sessionEnvironmentStatus)
  const uvText = uv?.available
    ? `uv ${uv.version ?? t('available')}`
    : uv
      ? t('uv CLI unavailable')
      : t('uv CLI checking')
  const restartAvailable = Boolean(
    activeSession
    && environment?.status === 'ready'
    && (
      activeSession.environmentId !== environment.environmentId
      || activeSession.environmentStatus !== 'ready'
    ),
  )

  return (
    <div className="workspace-terminal">
      <div className="workspace-terminal__header">
        <span className="workspace-terminal__icon" aria-hidden>&gt;_</span>
        <span className="workspace-terminal__title">{t('TERMINAL')}</span>
        <div className="workspace-terminal__tabs" role="tablist" aria-label={t('Project terminals')}>
          {sessions.map(session => (
            <div
              className={`workspace-terminal__tab${session.terminalId === activeTerminalId ? ' workspace-terminal__tab--active' : ''}`}
              key={session.terminalId}
            >
              <button
                className="workspace-terminal__tab-select"
                type="button"
                role="tab"
                aria-selected={session.terminalId === activeTerminalId}
                title={`${session.title} · ${session.shellName} · ${t('Python environment')} ${session.environmentLabel ?? t('none')} · ${t(environmentStatusMessage(session.environmentStatus))}`}
                onClick={() => selectTerminalRef.current(session.terminalId)}
              >
                <span aria-hidden>$</span> {session.title}
              </button>
              <button
                className="workspace-terminal__tab-close"
                type="button"
                aria-label={t('Close {{name}}', { name: session.title })}
                title={t('Close {{name}}', { name: session.title })}
                disabled={operationPending}
                onClick={() => closeTerminalRef.current(session.terminalId)}
              >
                ×
              </button>
            </div>
          ))}
          <button
            className="workspace-terminal__new"
            type="button"
            aria-label={t('New terminal')}
            title={sessions.length >= MAX_TERMINALS ? t('Maximum {{count}} terminals', { count: MAX_TERMINALS }) : t('New terminal')}
            disabled={operationPending || status !== 'connected' || sessions.length >= MAX_TERMINALS}
            onClick={() => createTerminalRef.current()}
          >
            +
          </button>
        </div>
        <span className="workspace-terminal__path" title={project.path}>{project.path}</span>
        <span className={`workspace-terminal__uv${uv?.available ? ' workspace-terminal__uv--ready' : uv ? ' workspace-terminal__uv--error' : ''}`} title={uv?.executable ?? undefined}>
          {uvText}
        </span>
        <span className={`workspace-terminal__environment workspace-terminal__environment--${environmentTone}`}>
          {environmentText}
        </span>
        {restartAvailable && activeSession && (
          <button
            className="workspace-terminal__restart"
            type="button"
            disabled={operationPending || status !== 'connected'}
            title={t('Restart this terminal with {{name}}', { name: environment?.name ?? t('the selected environment') })}
            onClick={() => restartTerminalRef.current(activeSession.terminalId)}
          >
            {t('Restart with {{name}}', { name: environment?.name ?? t('the selected environment') })}
          </button>
        )}
        <span className={`workspace-terminal__status workspace-terminal__status--${status}`}>
          <span className="workspace-terminal__status-dot" aria-hidden />
          {t(status)}
        </span>
        <button className="workspace-terminal__clear" type="button" disabled={!activeSession} onClick={clearTerminal}>{t('Clear')}</button>
      </div>
      {error && <div className="workspace-terminal__error">{error}</div>}
      {!activeSession && !operationPending && (
        <div className="workspace-terminal__empty">
          {t('No terminal is running. Use + to create one in the selected uv environment.')}
        </div>
      )}
      <div
        ref={hostRef}
        className="workspace-terminal__screen"
        title={t('Tab: autocomplete · Ctrl+C: interrupt (copy when text is selected) · Ctrl/Cmd+V: paste')}
      />
    </div>
  )
}

function activeTerminalStorageKey(accountKey: string, localProjectId: string) {
  return `fedops:${accountKey}:terminal-active:${localProjectId}`
}

function environmentStatusMessage(status: PythonEnvironmentStatus) {
  if (status === 'ready') return 'Ready'
  if (status === 'missing' || status === 'outdated') return 'Sync required'
  if (status === 'syncing') return 'Syncing'
  return 'Error'
}

function environmentStatusTone(status: PythonEnvironmentStatus) {
  if (status === 'ready') return 'ready'
  if (status === 'error') return 'error'
  return 'warning'
}

/**
 * xterm measures pointer positions from getBoundingClientRect (visually scaled),
 * while its cell dimensions remain in layout pixels. Adapt only the two xterm
 * mouse-service entry points so selection, links and mouse reports use one
 * coordinate space at every interface size.
 */
function installXtermPointerScaleAdapter(terminal: Terminal) {
  const core = (terminal as unknown as {
    _core?: { _mouseService?: XtermMouseService }
  })._core
  const mouseService = core?._mouseService
  if (!mouseService) return () => undefined

  const originalGetCoords = mouseService.getCoords.bind(mouseService)
  const originalGetMouseReportCoords = mouseService.getMouseReportCoords.bind(mouseService)

  const scaledGetCoords: XtermMouseService['getCoords'] = (
    event,
    element,
    colCount,
    rowCount,
    isSelection,
  ) => originalGetCoords(
    pointerInLayoutCoordinates(event, element),
    element,
    colCount,
    rowCount,
    isSelection,
  )
  const scaledGetMouseReportCoords: XtermMouseService['getMouseReportCoords'] = (
    event,
    element,
  ) => originalGetMouseReportCoords(
    pointerInLayoutCoordinates(event, element) as MouseEvent,
    element,
  )

  mouseService.getCoords = scaledGetCoords
  mouseService.getMouseReportCoords = scaledGetMouseReportCoords

  return () => {
    if (mouseService.getCoords === scaledGetCoords) {
      mouseService.getCoords = originalGetCoords
    }
    if (mouseService.getMouseReportCoords === scaledGetMouseReportCoords) {
      mouseService.getMouseReportCoords = originalGetMouseReportCoords
    }
  }
}

function pointerInLayoutCoordinates(
  event: PointerCoordinates,
  element: HTMLElement,
): PointerCoordinates {
  const rect = element.getBoundingClientRect()
  const scaleX = element.offsetWidth > 0 ? rect.width / element.offsetWidth : 1
  const scaleY = element.offsetHeight > 0 ? rect.height / element.offsetHeight : 1
  if (approximatelyOne(scaleX) && approximatelyOne(scaleY)) return event
  return {
    clientX: rect.left + (event.clientX - rect.left) / safeScale(scaleX),
    clientY: rect.top + (event.clientY - rect.top) / safeScale(scaleY),
  }
}

function safeScale(scale: number) {
  return Number.isFinite(scale) && scale > 0 ? scale : 1
}

function approximatelyOne(value: number) {
  return Math.abs(value - 1) < 0.001
}

function terminalTheme() {
  const styles = getComputedStyle(document.documentElement)
  const value = (name: string) => styles.getPropertyValue(name).trim()
  return {
    background: value('--terminal-bg'),
    foreground: value('--terminal-text'),
    cursor: value('--terminal-caret'),
    cursorAccent: value('--terminal-bg'),
    selectionBackground: value('--terminal-selection'),
    black: value('--terminal-ansi-black'),
    red: value('--terminal-ansi-red'),
    green: value('--terminal-ansi-green'),
    yellow: value('--terminal-ansi-yellow'),
    blue: value('--terminal-ansi-blue'),
    magenta: value('--terminal-ansi-magenta'),
    cyan: value('--terminal-ansi-cyan'),
    white: value('--terminal-ansi-white'),
    brightBlack: value('--terminal-ansi-bright-black'),
    brightRed: value('--terminal-ansi-bright-red'),
    brightGreen: value('--terminal-ansi-bright-green'),
    brightYellow: value('--terminal-ansi-bright-yellow'),
    brightBlue: value('--terminal-ansi-bright-blue'),
    brightMagenta: value('--terminal-ansi-bright-magenta'),
    brightCyan: value('--terminal-ansi-bright-cyan'),
    brightWhite: value('--terminal-ansi-bright-white'),
  }
}
