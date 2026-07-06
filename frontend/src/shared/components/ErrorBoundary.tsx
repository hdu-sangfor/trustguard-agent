import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props { children: ReactNode; }
interface State { error: Error | null; errorInfo: ErrorInfo | null; }

class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({ errorInfo });
    console.error('[ErrorBoundary]', error, errorInfo);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          width: '100vw', height: '100vh',
          background: 'linear-gradient(180deg, #0f172a 0%, #1e1b4b 50%, #0f172a 100%)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexDirection: 'column', gap: 14, padding: 32, boxSizing: 'border-box',
        }}>
          <div style={{
            color: '#f87171', fontSize: 13, fontFamily: 'monospace',
            fontWeight: 700, letterSpacing: '0.08em',
          }}>
            ✗ 页面发生错误
          </div>
          <div style={{
            maxWidth: 520, padding: '10px 14px', borderRadius: 8,
            background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.3)',
            color: '#fca5a5', fontSize: 11, fontFamily: 'monospace',
            maxHeight: 140, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {this.state.error.message || String(this.state.error)}
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button
              type="button"
              onClick={() => this.setState({ error: null, errorInfo: null })}
              style={{
                padding: '8px 22px', borderRadius: 8, fontSize: 12, fontWeight: 700,
                border: '1px solid rgba(34,211,238,0.5)', background: 'rgba(2,8,20,0.7)',
                color: '#a5f3fc', cursor: 'pointer',
              }}
            >
              重试
            </button>
            <button
              type="button"
              onClick={() => { window.location.href = '/'; }}
              style={{
                padding: '8px 22px', borderRadius: 8, fontSize: 12,
                border: '1px solid rgba(148,163,184,0.3)', background: 'transparent',
                color: '#64748b', cursor: 'pointer',
              }}
            >
              返回首页
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
