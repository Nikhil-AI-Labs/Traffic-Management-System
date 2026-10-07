import { Callout, Chip, ErrorState, Hero, PageSkeleton, Section, Tile, Tiles } from '../../components/ui'
import { DataTable } from '../../components/widgets'
import { useApi } from '../../lib/api'
import { num } from '../../lib/format'

interface Payload {
  mode: string
  usable: boolean
  observed: number
  total: number
  junctions: string[]
  warnings: string[]
  movements: {
    label: string
    queue_in: number
    queue_in_source: string
    queue_out: number | null
    queue_out_source: string
    pressure: number | null
  }[]
}

const MODE_COLOR: Record<string, string> = {
  'max-pressure': 'var(--good)',
  partial: 'var(--warning)',
  'greedy-queue': 'var(--critical)',
}

export default function MpMethod() {
  const q = useApi<Payload>('/mp/method')
  if (q.isLoading) return <PageSkeleton />
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const d = q.data

  return (
    <div className="page">
      <Section title="Why Surat's junctions run Webster's instead" note="a property of the camera layout, not of the algorithm" />
      <div className="grid-hero">
        <Hero label="Movements with a downstream queue" value={`${d.observed} / ${d.total}`}>
          Max-pressure needs the queue on the receiving link of every movement. Without it the algorithm is not
          max-pressure at all.
        </Hero>
        <Tiles cols={3}>
          <Tile label="Mode" value={d.mode.replace('-', ' ')}>
            {d.usable
              ? 'Every downstream queue observed — genuine max-pressure.'
              : 'Downstream queues are missing, so any ranking would be on incoming queue alone.'}
          </Tile>
          <Tile label="Junction cameras" value={d.junctions.length}>
            {d.junctions.join(', ') || '—'}
          </Tile>
          <Tile label="Downstream cameras" value="0">
            None configured. A downstream camera is a configuration entry, not a code change.
          </Tile>
        </Tiles>
      </div>
      <div className="row mt-2">
        <Chip color={MODE_COLOR[d.mode] ?? 'var(--muted)'}>MODE: {d.mode.toUpperCase()}</Chip>
      </div>
      <div className="mt-2">
        {d.warnings.map((w) => (
          <Callout key={w} tone="critical" title="Why this cannot run here">
            {w}
          </Callout>
        ))}
      </div>

      <Section title="What the Surat cameras can and cannot supply" />
      <DataTable
        rows={d.movements.map((m) => ({
          Movement: m.label,
          'Incoming queue (PCU)': m.queue_in,
          'Incoming source': m.queue_in_source,
          'Downstream queue': m.queue_out === null ? '—' : num(m.queue_out, 2),
          'Downstream source': m.queue_out_source,
          Pressure: m.pressure === null ? 'not computable' : num(m.pressure, 2),
        }))}
        format={{ 'Incoming queue (PCU)': 2 }}
      />

      <div className="mt-3">
        <Callout tone="warning" title="Why we do not fake the missing term">
          <p>
            This is the same refusal the metric scale gets. Substituting zero for an unobserved downstream queue turns
            max-pressure into <b>greedy queue control</b> — serve the longest queue — which is a different algorithm, is{' '}
            <b>not</b> throughput-optimal, and cannot see a blocked link, the exact failure max-pressure exists to prevent.
            Varaiya's §3.1 Example 2 proves it fails to stabilise demands max-pressure handles. Shipping that under the name
            "max-pressure" would be the most flattering and least honest thing this project could do.
          </p>
          <p>
            <b>What would fix it:</b> one camera on the receiving link of each controlled movement, registered to the same
            junction. That is a deployment decision for the city, and the code is already waiting for it.
          </p>
        </Callout>
      </div>
    </div>
  )
}
