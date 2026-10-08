// A crash inside one view must not blank the whole dashboard. Without a
// boundary React unmounts the entire tree on a render error, so a single bad
// payload on one page would take the navigation, the theme toggle and every
// other view down with it - in front of whoever is watching the screen.

import { CircleAlert } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Changing this (the route) clears a caught error. */
  resetKey?: string
}

interface State {
  error: Error | null
  key?: string
}

// After a redeploy the old index.html can ask for a page chunk that no longer
// exists. The fix is a reload, not an error message.
const STALE_CHUNK =
  /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, key: this.props.resetKey }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('View crashed:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const stale = STALE_CHUNK.test(error.message)
    return (
      <div className="empty" role="alert">
        <CircleAlert size={30} color="var(--warning)" style={{ margin: '0 auto' }} />
        <h3>{stale ? 'The dashboard was updated' : 'This view hit an error'}</h3>
        <p className="note" style={{ whiteSpace: 'pre-wrap' }}>
          {stale
            ? 'A newer version was deployed while this page was open. Reload to pick it up.'
            : `${error.message}\n\nThe rest of the dashboard is unaffected — pick another view, or try again.`}
        </p>
        <div className="row mt-2" style={{ justifyContent: 'center' }}>
          <button className="btn" onClick={() => location.reload()}>
            Reload
          </button>
          {!stale && (
            <button className="btn btn-ghost" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
          )}
        </div>
      </div>
    )
  }
}
