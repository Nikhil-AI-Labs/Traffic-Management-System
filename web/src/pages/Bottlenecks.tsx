import { Callout, Chart, ErrorState, Hero, PageSkeleton, Section, Tile, Tiles } from '../components/ui'
import { AlertsSection } from '../components/widgets'
import { useThemedApi } from '../lib/api'
import { num } from '../lib/format'
import type { AlertsPayload, Figure } from '../lib/types'

interface Payload extends AlertsPayload {
  kinds: string[]
  sites: number
  longest_s: number
  min_sustained_windows: number
  charts: { gantt: Figure; mix: Figure }
}

export default function Bottlenecks() {
  const q = useThemedApi<Payload>('/bottlenecks')
  if (q.isLoading) return <PageSkeleton />
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const d = q.data

  return (
    <div className="page">
      <div className="grid-hero">
        <Hero label="Critical bottlenecks" value={d.counts.critical}>
          Conditions that held long enough to be a state of the road rather than a passing platoon, at the severe end of
          the scale.
        </Hero>
        <Tiles cols={3}>
          <Tile label="Alerts total" value={d.alerts.length}>
            across {d.sites} sites · {d.counts.critical} critical · {d.counts.warning} warning · {d.counts.info} info
          </Tile>
          <Tile label="Patterns fired" value={d.kinds.length}>
            {d.kinds.join(', ')} · of 5 detectors
          </Tile>
          <Tile label="Longest episode" value={num(d.longest_s)} unit="s">
            unbroken run of the same condition
          </Tile>
        </Tiles>
      </div>

      <Section title="Sustained bottlenecks" note={d.alerts.length ? `${d.alerts.length} raised` : 'none raised'} />
      {d.alerts.length === 0 ? (
        <Callout tone="good" title="All clear">
          No sustained bottleneck patterns in the analysed footage.
        </Callout>
      ) : (
        <>
          <div className="grid-2">
            <Chart figure={d.charts.gantt} label="Every alert on the clip clock, by site" />
            <Chart figure={d.charts.mix} label="Alerts by kind" />
          </div>
          <div className="note mt-1">
            Every alert placed on its clip clock. An alert fires only after the pattern holds for at least{' '}
            {d.min_sustained_windows} consecutive windows — a busy lane moving freely is working as designed, and a single
            dense frame is a platoon, not a bottleneck.
          </div>
          <Section title="By severity" note="evidence and recommendation for each" />
          <AlertsSection alerts={d.alerts} />
        </>
      )}
    </div>
  )
}
