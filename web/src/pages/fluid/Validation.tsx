import { keepPreviousData } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useState } from 'react'
import { Callout, Chart, ErrorState, Expander, Field, Hero, PageSkeleton, Section, Skeleton, Spinner, Tile, Tiles } from '../../components/ui'
import { DataTable, TableView } from '../../components/widgets'
import { qs, useThemedApi } from '../../lib/api'
import { int, num, pct, signed } from '../../lib/format'
import type { Figure, Row } from '../../lib/types'
import { FluidGate, RecordingSelect, useRecording } from './common'

interface SummaryRow {
  site: string
  label: string
  wasted_actual: number
  wasted_mp: number
  press_actual: number
  press_mp: number
  agreement: number
  cycle_s: number
  phases: number
  steps: number
}

interface Summary {
  rows: SummaryRow[]
  wasted_actual: { min: number; max: number }
  wasted_mp: { min: number; max: number }
  relief_ratio: { min: number; max: number }
  worst: { site: string; label: string; wasted: number }
  genuine: boolean
  steps: number
  chart: Figure
}

interface Replay {
  recording: { id: string; site: string; name: string; site_label: string }
  cycle_s: number
  phases: number
  cycles: number
  movements: number
  queue_in: { mean: number; max: number }
  queue_out: { mean: number }
  turns: Row[]
  result: {
    mode: string
    steps: number
    agreement_pct: number
    mean_pressure_actual: number
    mean_pressure_maxpressure: number
    mean_pressure_best: number
    wasted_green_pct_actual: number
    wasted_green_pct_maxpressure: number
  }
  phase_share: { phase: string; actual: number; maxpressure: number }[]
  top_diff: { phase: string; pp: number } | null
  catalogue: { phase: string; movements: string[]; times_run: number; mean_green_s: number; total_green_s: number }[]
  charts: { turns: Figure; phase_share: Figure; timeline: Figure }
}

export default function Validation() {
  const r = useRecording()
  return (
    <FluidGate loading={r.loading} available={r.available}>
      <div className="page">
        <Callout title="What you are looking at">
          <p>
            This whole track is the <b>FLUID</b> dataset — 14 drone recordings of three real signalised intersections in
            Xuancheng, Anhui, obtained specifically so the max-pressure implementation could be validated on data that
            contains the downstream queue it is defined by. <b>No Surat figure appears anywhere in this track.</b>
          </p>
          <p>
            The whole intersection sits in one aerial frame, so every approach <i>and</i> every receiving link is
            measured at the same instant, turn ratios are counted per vehicle, and the recorded signal gives per-second
            ground truth to compare against.
          </p>
        </Callout>
        <Headline />
        <Section title="Validated against the real signals" note="measured queues, measured turn ratios, recorded signal" />
        <Drilldown recs={r.recs} rid={r.rid} setRid={r.setRid} />
      </div>
    </FluidGate>
  )
}

function Headline() {
  const q = useThemedApi<Summary>('/fluid/summary')
  if (q.isLoading)
    return (
      <>
        <div className="mt-2">
          <Spinner label="Replaying max-pressure against all three real signals… (first load only)" />
        </div>
        <PageSkeleton />
      </>
    )
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const d = q.data
  return (
    <>
      <Section title="Validation result" note="max-pressure replayed against three real signals" />
      <div className="grid-hero">
        <Hero label="Real fixed-time green spent with nothing to relieve" value={`${num(d.wasted_actual.min)}–${num(d.wasted_actual.max)}`} unit="%">
          Across three real signalised intersections. Max-pressure, on the same measured queues, does so{' '}
          <b>
            {num(d.wasted_mp.min)}–{num(d.wasted_mp.max)}%
          </b>{' '}
          of the time. Worst site: <b>{d.worst.label.split('—')[0].trim()}</b> at {num(d.worst.wasted)}%.
        </Hero>
        <Tiles cols={3}>
          <Tile label="Controller mode" value={d.genuine ? 'max-pressure' : 'degraded'} meter={[d.genuine ? 1 : 0.3, d.genuine ? 'var(--good)' : 'var(--critical)']}>
            Every downstream queue measured, on every movement — the real algorithm, not a fallback.
          </Tile>
          <Tile label="Intersections" value={d.rows.length}>
            {d.rows.map((r) => r.site).join(' · ')}
          </Tile>
          <Tile label="Decision steps" value={int(d.steps)}>
            one second each, every phase re-ranked
          </Tile>
        </Tiles>
      </div>
      <div className="mt-3">
        <DataTable
          rows={d.rows.map((r) => ({
            Intersection: r.label,
            'Cycle (s)': Math.round(r.cycle_s),
            Phases: r.phases,
            Steps: r.steps,
            'Real signal — wasted green': `${num(r.wasted_actual, 1)}%`,
            'Max-pressure — wasted green': `${num(r.wasted_mp, 1)}%`,
            'Pressure served — real': r.press_actual,
            'Pressure served — MP': r.press_mp,
            Agreement: `${num(r.agreement, 1)}%`,
          }))}
        />
      </div>
      <div className="mt-2">
        <Chart figure={d.chart} label="Wasted green: real signal against max-pressure" />
      </div>
      <div className="note mt-1 prose">
        <p>
          <b>Wasted green</b> is the share of green time spent on a phase whose pressure was zero or negative — the queue
          had already cleared, or the link it discharges into was fuller than the approach feeding it.{' '}
          <b>Mean pressure served</b> is how much queue each controller's choice was relieving; max-pressure relieves{' '}
          <b>
            {num(d.relief_ratio.min)}–{num(d.relief_ratio.max)}×
          </b>{' '}
          more. <b>Agreement</b> is how often the real signal happened to pick the same phase.
        </p>
        <p>
          These are observations about the recorded signals, made from the recorded queues. They are deliberately{' '}
          <b>not</b> a delay-reduction claim: had max-pressure actually run, the queues would have evolved differently. The
          controlled counterfactual is under <b>Head-to-head</b>.
        </p>
      </div>
    </>
  )
}

function Drilldown({ recs, rid, setRid }: Pick<ReturnType<typeof useRecording>, 'recs' | 'rid' | 'setRid'>) {
  const [minGreen, setMinGreen] = useState(15)
  const [downstream, setDownstream] = useState<'queue' | 'occupancy'>('queue')
  const params = { rid, min_green: minGreen, downstream }
  const q = useThemedApi<Replay>('/fluid/replay', params, { enabled: !!rid, placeholderData: keepPreviousData })

  return (
    <>
      <div className="controls">
        <RecordingSelect recs={recs} rid={rid} setRid={setRid} />
        <Field label="Minimum green" value={`${minGreen} s`} hint="The floor the controller must respect; without one max-pressure flickers.">
          <input type="range" min={5} max={60} step={1} value={minGreen} onChange={(e) => setMinGreen(+e.target.value)} />
        </Field>
        <Field label="Downstream term" hint="Varaiya's x_m is a stopped queue; occupancy shows how much the term is doing.">
          <select value={downstream} onChange={(e) => setDownstream(e.target.value as 'queue' | 'occupancy')}>
            <option value="queue">Stopped queue (Varaiya)</option>
            <option value="occupancy">All vehicles on link</option>
          </select>
        </Field>
        {q.isFetching && <Spinner label="Replaying second by second…" />}
      </div>
      {q.isLoading ? (
        <Skeleton height={420} />
      ) : q.error || !q.data ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <ReplayView d={q.data} csv={`/api/fluid/replay.csv${qs(params)}`} />
      )}
    </>
  )
}

function ReplayView({ d, csv }: { d: Replay; csv: string }) {
  const r = d.result
  return (
    <>
      <Tiles>
        <Tile label="Signal cycle" value={num(d.cycle_s)} unit="s">
          {d.phases} phases · {d.cycles} cycles recorded
        </Tile>
        <Tile label="Movements measured" value={d.movements}>
          each with its own approach and receiving link
        </Tile>
        <Tile label="Approach queue" value={num(d.queue_in.mean)} unit="PCU">
          mean; peak {num(d.queue_in.max)}
        </Tile>
        <Tile
          label="Receiving-link queue"
          value={num(d.queue_out.mean, 1)}
          unit="PCU"
          meter={[Math.min(1, d.queue_out.mean / Math.max(1e-6, d.queue_in.mean)), 'var(--s2)']}
        >
          <b>the term the Surat cameras cannot see</b>
        </Tile>
      </Tiles>

      <Section title="Measured turn ratios" note="unobservable from a single approach camera" />
      <div className="grid-2">
        <Chart figure={d.charts.turns} label="Turn split per entry direction" />
        <div>
          <div className="note">
            Max-pressure needs <b>r(m,k)</b> — of everything leaving link m, what share goes where. A camera watching one
            approach sees vehicles arrive and cannot see where they go, so this is the <i>second</i> reason the Surat
            network cannot support the algorithm, after the missing downstream queue. Here it is counted directly from
            each vehicle's entry and exit direction.
          </div>
          <TableView title="Table view — turn ratios" rows={d.turns} format={{ turn_ratio: 3 }} />
        </div>
      </div>

      <Section title="What max-pressure would have done" note="replayed against the real signal" />
      <div className="grid-hero">
        <Hero label="Green spent with nothing to relieve" value={num(r.wasted_green_pct_actual)} unit="%">
          The real fixed-time signal holding green on a phase whose pressure was zero or negative. Max-pressure:{' '}
          <b>{pct(r.wasted_green_pct_maxpressure)}</b>.
        </Hero>
        <Tiles cols={3}>
          <Tile label="Mode" value={r.mode.replace('-', ' ')}>
            every downstream queue observed — genuine max-pressure
          </Tile>
          <Tile label="Mean pressure served" value={num(r.mean_pressure_actual, 2)}>
            real signal · max-pressure <b>{num(r.mean_pressure_maxpressure, 2)}</b> · best possible{' '}
            {num(r.mean_pressure_best, 2)}
          </Tile>
          <Tile label="Agreement" value={num(r.agreement_pct)} unit="%">
            of {int(r.steps)} seconds, the real signal served the phase max-pressure would have
          </Tile>
        </Tiles>
      </div>
      <div className="mt-2">
        <Callout title="Read this before quoting the figure">
          <p>
            <b>What this is.</b> The queues are the ones that really formed, under the real signal, measured from drone
            trajectories. So this establishes — by direct observation — that the fixed plan often held green on an
            approach with nothing to discharge, and which phase max-pressure would have served instead.
          </p>
          <p>
            <b>What it is not.</b> A claim that max-pressure would have reduced delay by any particular amount. Had it
            run, the queues would have evolved differently; that number needs a traffic model, which is what Head-to-head
            provides.
          </p>
        </Callout>
      </div>

      <Section title="Where the green went" />
      <div className="grid-2">
        <Chart figure={d.charts.phase_share} label="Share of green by phase, real signal against max-pressure" />
        <div>
          {d.top_diff && (
            <div className="note">
              The clearest disagreement is <b>{d.top_diff.phase}</b>: max-pressure would give it{' '}
              <b>{signed(d.top_diff.pp)} percentage points</b> of green relative to the fixed plan. A fixed cycle divides
              time by a plan set once; max-pressure divides it by what is actually queued.
            </div>
          )}
          <TableView
            title="Table view — phase share"
            rows={d.phase_share.map((p) => ({ Phase: p.phase, 'Real signal (%)': p.actual, 'Max-pressure (%)': p.maxpressure }))}
            format={{ 'Real signal (%)': 1, 'Max-pressure (%)': 1 }}
          />
        </div>
      </div>

      <Section title="Queues and pressure through the recording" />
      <Chart figure={d.charts.timeline} label="Queue and pressure over the recording" />
      <div className="note mt-1">
        The upper trace is the total approach queue observed. Below it, the pressure the real signal was relieving each
        second against the best available — the gap is the headroom a responsive controller could have used.
      </div>

      <Expander title="Phase structure recovered from the recorded signal">
        <DataTable
          rows={d.catalogue.map((p) => ({
            Phase: p.phase,
            'Movements served': p.movements.join(', '),
            'Times run': p.times_run,
            'Mean green (s)': p.mean_green_s,
            'Total green (s)': Math.round(p.total_green_s),
          }))}
          format={{ 'Mean green (s)': 1 }}
        />
        <div className="note mt-1">
          FLUID records one row per signal group per state interval, not a phase table. Groups that turn green at the
          same instant belong to the same phase, so grouping green intervals by start time recovers the real staging —
          which is what max-pressure then chooses between, so the comparison is like-for-like.
        </div>
      </Expander>
      <div className="mt-2">
        <a className="btn" href={csv}>
          <Download size={16} /> Download this replay (CSV)
        </a>
      </div>
    </>
  )
}
