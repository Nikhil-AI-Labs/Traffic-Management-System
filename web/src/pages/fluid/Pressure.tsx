import { keepPreviousData } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Callout, Chip, ErrorState, Field, PageSkeleton, Section, Spinner, Tile, Tiles } from '../../components/ui'
import { DataTable } from '../../components/widgets'
import { qs, useApi } from '../../lib/api'
import { num } from '../../lib/format'
import { FluidGate, RecordingSelect, useRecording } from './common'

interface Instant {
  t: number
  t_min: number
  t_max: number
  observed: number
  movements_total: number
  decision: {
    mode: string
    usable: boolean
    chosen_label: string
    chosen_pressure: number | null
    greedy_label: string
    all_non_positive: boolean
    note: string
    warnings: string[]
  }
  phases: { phase: string; phase_id: string; pressure: number | null; observed: number; total: number; serve: boolean; note: string }[]
  movements: { label: string; queue_in: number; queue_out: number | null; turn_ratio: number; pressure: number | null }[]
  actual_green: string[]
}

export default function Pressure() {
  const r = useRecording()
  return (
    <FluidGate loading={r.loading} available={r.available}>
      <div className="page">
        <Callout title="What max-pressure does">
          <p>
            Max-pressure serves the phase with the greatest <b>queue difference</b> between an approach and the link it
            discharges into:
          </p>
          <p className="mono" style={{ color: 'var(--text)' }}>
            w(l,m) = x<sub>l</sub> − Σ<sub>k</sub> r<sub>mk</sub> x<sub>k</sub> &nbsp;·&nbsp; P(phase) = Σ s<sub>lm</sub> · w(l,m)
          </p>
          <p>
            It is <b>throughput-optimal</b>: it stabilises any demand the junction can physically clear, using only local
            queues and no demand forecast. The downstream term is what stops it discharging into a link that is already
            full — the spillback that gridlocks a network. <b>Varaiya (2013)</b>, <i>Transportation Research Part C</i>{' '}
            36:177–195.
          </p>
        </Callout>
        <Section title="Phase pressure at one instant" note="every approach and every receiving link in one frame" />
        <div className="controls">
          <RecordingSelect recs={r.recs} rid={r.rid} setRid={r.setRid} />
        </div>
        {r.rid && <InstantView key={r.rid} rid={r.rid} />}
      </div>
    </FluidGate>
  )
}

function InstantView({ rid }: { rid: string }) {
  const [t, setT] = useState<number | null>(null)
  const [tq, setTq] = useState<number | null>(null)
  useEffect(() => {
    const h = setTimeout(() => setTq(t), 200)
    return () => clearTimeout(h)
  }, [t])
  const q = useApi<Instant>(`/fluid/instant${qs({ rid, t: tq })}`, { placeholderData: keepPreviousData })

  if (q.isLoading) return <PageSkeleton />
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const d = q.data
  const full = d.decision.mode === 'max-pressure'
  const scoreCol = full ? 'Pressure P(p)' : 'Score (queue only)'
  const serveCol = full ? 'Serve' : 'Greedy would serve'

  return (
    <>
      <div className="controls">
        <Field label="Instant" value={`t = ${num(t ?? d.t)} s`}>
          <input type="range" min={d.t_min} max={d.t_max} step={1} value={t ?? d.t} onChange={(e) => setT(+e.target.value)} />
        </Field>
        {q.isFetching && <Spinner label="Ranking phases…" />}
      </div>
      <Tiles cols={3}>
        <Tile label="Downstream queues observed" value={`${d.observed} / ${d.movements_total}`} meter={[d.observed / Math.max(1, d.movements_total), 'var(--good)']}>
          The drone sees the whole intersection, so every receiving link is measured.
        </Tile>
        <Tile label="Controller mode" value={d.decision.mode.replace('-', ' ')}>
          {full ? 'Genuine max-pressure — the downstream term is real.' : 'Downstream term missing — not max-pressure.'}
        </Tile>
        <Tile label="Max-pressure would serve" value={d.decision.chosen_label || 'none'}>
          {d.decision.chosen_pressure !== null ? `P = ${num(d.decision.chosen_pressure, 1)}` : 'every phase non-positive — nothing to relieve'}
        </Tile>
      </Tiles>
      <div className="row mt-2">
        <Chip color={full ? 'var(--good)' : 'var(--critical)'}>MODE: {d.decision.mode.toUpperCase()}</Chip>
        <Chip color="var(--s1)">Drone view downstream data: ACTIVE</Chip>
      </div>

      <Section title="Phase ranking" />
      <DataTable
        columns={['Phase', scoreCol, 'Movements observed', serveCol, 'Note']}
        rows={d.phases.map((p) => ({
          Phase: p.phase,
          [scoreCol]: p.pressure === null ? 'not computable' : Number(p.pressure.toFixed(2)),
          'Movements observed': `${p.observed} / ${p.total}`,
          [serveCol]: p.serve ? '← serve' : '',
          Note: p.note,
        }))}
        highlight={(row) => row[serveCol] === '← serve'}
      />
      <div className="note mt-1 prose">
        At <b>t = {num(d.t)} s</b> the recorded signal was holding <b>{d.actual_green.join(', ') || 'no'}</b> green. A
        negative pressure means the link that phase discharges into was fuller than the approach feeding it — serving it
        would push traffic into a queue that cannot accept it, which is the spillback max-pressure exists to avoid and the
        term greedy queue control cannot see.
      </div>
      {d.decision.note && (
        <div className="mt-2">
          <Callout tone="warning">{d.decision.note}</Callout>
        </div>
      )}

      <Section title="Movements" note="the terms behind every pressure" />
      <DataTable
        rows={d.movements.map((m) => ({
          Movement: m.label,
          'Approach queue x_l (PCU)': m.queue_in,
          'Receiving queue x_m (PCU)': m.queue_out,
          'Turn ratio r': m.turn_ratio,
          'Weight w(l,m)': m.pressure,
        }))}
        format={{ 'Approach queue x_l (PCU)': 2, 'Receiving queue x_m (PCU)': 2, 'Turn ratio r': 3, 'Weight w(l,m)': 2 }}
      />
      <div className="mt-2">
        <Callout tone="neutral">
          This is the phase ranking at one instant. <b>Validation</b> replays it second by second against what each signal
          actually did, and <b>Head-to-head</b> runs the controlled comparison against a fixed-time plan.
        </Callout>
      </div>
    </>
  )
}
