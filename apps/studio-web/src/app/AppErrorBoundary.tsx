import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { error: Error | null }

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('FedOps Agent Studio UI error', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <main style={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center', padding: 24, boxSizing: 'border-box', color: 'var(--studio-text)', background: 'var(--studio-bg)', fontFamily: 'Inter, sans-serif' }}>
        <section style={{ width: 'min(560px, 100%)', padding: 24, border: '1px solid var(--studio-red-border)', borderRadius: 'var(--studio-radius)', background: 'var(--studio-surface)', boxShadow: 'var(--studio-shadow)' }}>
          <div style={{ color: 'var(--studio-red)', fontSize: 13, fontWeight: 700 }}>FedOps Agent Studio</div>
          <h1 style={{ margin: '8px 0', color: 'var(--studio-text)', fontSize: 20 }}>The interface encountered an unexpected error.</h1>
          <p style={{ margin: '0 0 18px', color: 'var(--studio-muted)', fontSize: 13, lineHeight: 1.6 }}>
            Your Workspace files are not removed by this screen. Reload Studio to restore the interface.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{ minHeight: 32, padding: '6px 16px', border: '1px solid var(--studio-primary-border)', borderRadius: 'var(--studio-pill-radius)', color: 'var(--studio-primary-text)', background: 'var(--studio-primary-bg)', fontFamily: 'Inter, sans-serif', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
          >
            Reload Studio
          </button>
          <details style={{ marginTop: 18, color: 'var(--studio-dim)', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>
            <summary style={{ cursor: 'pointer' }}>Error details</summary>
            <pre style={{ margin: '10px 0 0', padding: 10, maxHeight: 160, overflow: 'auto', whiteSpace: 'pre-wrap', border: '1px solid var(--studio-border)', background: 'var(--studio-surface-2)' }}>{this.state.error.message}</pre>
          </details>
        </section>
      </main>
    )
  }
}
