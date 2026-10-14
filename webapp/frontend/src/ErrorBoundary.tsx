import { Component, type ReactNode } from 'react';

/** One page failing to render shouldn't blank the whole app. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page">
        <div className="error">
          Something went wrong showing this page. <button className="btn small" type="button" onClick={() => location.reload()}>Reload</button>
          <div className="muted">{this.state.error.message}</div>
        </div>
      </div>
    );
  }
}
