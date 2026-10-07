import { keepPreviousData } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  Callout,
  Chart,
  ErrorState,
  Expander,
  Field,
  Hero,
  PageSkeleton,
  ScaleBanner,
  Section,
  Spinner,
  Tile,
  Tiles,
} from '../components/ui'
import { DataTable, TableView } from '../components/widgets'
import { useThemedApi } from '../lib/api'
import { isNum, num, secs, signed } from '../lib/format'
import type { SignalPayload } from '../lib/types'

const MODEL_LABEL: Record<string, string> = {
  auto: 'Automatic (HCM above x = 0.85)',
  hcm: 'HCM control delay',
  webster: 'Webster 1958',
}

/** Debounce a value so a dragged slider sends one request, not thirty. */
function useDebounced<T>(v: T, ms = 250): T {
  const [d, setD] = useState(v)
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms)
    return () => clearTimeout(t)
  }, [v, ms])
  return d
}

export default function Signal() {
  const [cycle, setCycle] = useState(120)
  const [model, setModel] = useState('auto')
  const [source, setSource] = useState('per_lane')
  const [widths, setWidths] = useState<Record<string, number>>({})
  const params = useDebounced(
    { baseline_cycle: cycle, delay_model: model, sat_source: source, widths: JSON.stringify(widths) },
    250,
  )
  const q = useThemedApi<SignalPayload>('/signal', params, { placeholderData: keepPreviousData })

  if (q.isLoading) return <PageSkeleton />
  if (!q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const d = q.data
  const widthBased = d.factor !== null
  const reductions = d.delay_models.filter((r) => isNum(r.reduction_pct)).map((r) => r.reduction_pct as number)
  const lo = reductions.length ? Math.min(...reductions) : null
  const hi = reductions.length ? Math.max(...reductions) : null
  const cminBeyond = d.c_min === null || d.c_min > d.max_cycle

  return (
    <div className="page">
      <div className="card card-pad">
        <div className="controls" style={{ marginBottom: 0 }}>
          <Field label="Existing fixed-timer cycle" value={`${cycle} s`} hint="The untuned fixed-time cycle used as the comparison baseline.">
            <input type="range" min={60} max={150} step={5} value={cycle} onChange={(e) => setCycle(+e.target.value)} />
          </Field>
          <Field label="Delay model" hint="Webster's random-delay term diverges near capacity; HCM's stays finite above it.">
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {Object.entries(MODEL_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Saturation flow assumption" hint="IRC:SP:41-1994 gives S = 525·W; field data from Ahmedabad found 636·W–1283·W.">
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              {d.sat_sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>
          {q.isFetching && <Spinner label="Recomputing…" />}
        </div>
        {widthBased && (
          <div className="controls mt-2" style={{ marginBottom: 0 }}>
            {d.approaches.map((a) => (
              <Field key={a.id} label={`${a.short} — approach width (m)`} value={`${a.lanes} lanes`} hint="Kerb-to-median width.">
                <input
                  type="number"
                  min={2}
                  max={30}
                  step={0.5}
                  defaultValue={widths[a.id] ?? a.default_width_m}
                  onChange={(e) => {
                    const v = parseFloat(e.target.value)
                    if (Number.isFinite(v) && v >= 2 && v <= 30) setWidths((w) => ({ ...w, [a.id]: v }))
                  }}
                />
              </Field>
            ))}
          </div>
        )}
        {d.width_notes.map((n) => (
          <div className="field-h mt-1" key={n}>
            {n}
          </div>
        ))}
      </div>
      {q.error && (
        <div className="mt-2">
          <Callout tone="warning" title="Could not recompute">
            {q.error.message}
          </Callout>
        </div>
      )}

      <div className="grid-hero mt-3">
        <Hero label="Worst approach saturation" value={`${num(d.worst_before, 2)} → ${num(d.worst_after, 2)}`}>
          x is flow ÷ capacity on the critical lane. Above 1.0 the queue cannot clear within the cycle and carries over.{' '}
          {d.over_before ? (
            <>
              The fixed timer drives{' '}
              <b>
                {d.over_before} of {d.base.phases.length}
              </b>{' '}
              approaches over capacity; the adaptive plan brings every one back under.
            </>
          ) : (
            <>Both approaches stay under capacity in both plans; the adaptive plan equalises them.</>
          )}
        </Hero>
        <Tiles>
          <Tile label="Cycle length" value={num(d.opt.cycle_s)} unit="s">
            {signed(d.opt.cycle_s - d.base.cycle_s)} s against the {num(d.base.cycle_s)} s baseline
          </Tile>
          <Tile
            label="Delay reduction"
            value={isNum(d.compare.delay_reduction_pct) && isNum(d.compare.baseline_mean_delay_s) ? num(d.compare.delay_reduction_pct, 1) : '—'}
            unit={isNum(d.compare.baseline_mean_delay_s) ? '%' : ''}
          >
            {isNum(d.compare.baseline_mean_delay_s)
              ? `${num(d.compare.baseline_mean_delay_s, 1)} → ${num(d.compare.optimised_mean_delay_s, 1)} s/PCU`
              : 'baseline delay is not finite under this model'}
            , {[...new Set(d.opt.phases.map((p) => p.delay_model))].join('/')} model
          </Tile>
          <Tile label="Total flow ratio Y" value={num(d.Y, 2)} meter={[Math.min(1, d.Y), d.Y > 0.85 ? 'var(--critical)' : 'var(--good)']}>
            Y ≥ 0.95 means no cycle length can clear the demand
          </Tile>
          <Tile label="Common saturation X" value={num(d.x_common, 2)}>
            Y·C/(C−L) — the closed form every critical approach lands on
          </Tile>
        </Tiles>
      </div>
      {d.opt.note && (
        <div className="mt-2">
          <Callout tone="critical" title="Capacity">
            {d.opt.note}
          </Callout>
        </div>
      )}

      <Section title="How much does retiming actually save?" note="the answer depends on the overflow model" />
      <div className="grid-equal">
        <DataTable
          rows={d.delay_models.map((r) => ({
            'Delay model': r.label,
            'Baseline (s/PCU)': r.diverges ? '∞ (over capacity)' : r.baseline,
            'Optimised (s/PCU)': r.optimised,
            Reduction: r.reduction_pct === null ? 'not finite' : `${num(r.reduction_pct, 1)}%`,
          }))}
          format={{ 'Optimised (s/PCU)': 1, 'Baseline (s/PCU)': (v) => (typeof v === 'number' ? num(v, 1) : String(v)) }}
        />
        <Callout tone="warning" title="Why two numbers">
          <p>
            {lo !== null && hi !== null && lo !== hi ? (
              <>
                The honest answer is a range: <b>{num(lo)}% to {num(hi)}%</b>, and the spread is a finding rather than an
                embarrassment.{' '}
              </>
            ) : null}
            Webster's random-delay term <i>x²/[2q(1−x)]</i> diverges as an approach reaches capacity — and above
            capacity it has no finite value at all, which is why the baseline reads ∞ under that model. HCM's
            time-dependent term stays finite above capacity, but assumes the oversaturation lasts only{' '}
            {num(d.hcm_period_min)} minutes; over a longer peak the real delay is worse than HCM shows.
          </p>
          <p>What neither model can change is the saturation crossing above.</p>
        </Callout>
      </div>

      <Section title="How each plan spends its cycle" />
      <Chart figure={d.charts.cycle} label="Cycle composition, baseline and optimised" />
      <div className="note mt-1">
        Both plans on one time axis. The adaptive plan moves green seconds onto the approach that measured the higher
        critical-lane flow instead of splitting them evenly.
      </div>

      <Section title="Degree of saturation" note="before and after, per approach" />
      <div className="grid-2">
        <Chart figure={d.charts.dumbbell} label="Degree of saturation before and after" />
        <Callout title="Why they end up equal">
          <p>
            Webster's proportional split gives approach <i>i</i> a green of g<sub>i</sub> = (y<sub>i</sub>/Y)(C − L), so
            its degree of saturation is <b>x<sub>i</sub> = y<sub>i</sub>·C/g<sub>i</sub> = Y·C/(C − L)</b> — which has
            no <i>i</i> in it.
          </p>
          <p>
            Equal saturation is not a happy accident of the optimiser; it is what proportional allocation <i>is</i>. The
            closed form gives <b>{num(d.x_common, 3)}</b>; the optimiser lands on{' '}
            <b>{d.opt.phases.map((p) => num(p.degree_of_saturation, 2)).join(', ')}</b>.
          </p>
        </Callout>
      </div>

      <Section title="Why this cycle length" note="the optimiser auditing itself" />
      <Chart figure={d.charts.delay_curve} label="Delay against cycle length" />
      <div className="note mt-1 prose">
        <p>
          Delay swept across every cycle length from 60 s to 180 s. Under Webster's own delay model — the one his closed
          form is derived from — the proportional-split curve bottoms out at <b>{secs(d.best_cycle_webster)}</b> against
          the closed-form optimum of <b>{secs(d.opt.cycle_s, 1)}</b>. Those agreeing is the check that the optimiser does
          what the theory says; it once caught a real bug, when mixed per-lane and per-approach units gave a curve with no
          minimum at all.
        </p>
        {d.delay_model !== 'webster' && (
          <p>
            The curve above uses the <b>{MODEL_LABEL[d.delay_model]}</b> model, whose minimum is{' '}
            <b>{secs(d.best_cycle)}</b>: HCM's bounded overflow term penalises running near capacity far less than
            Webster's, so it favours a shorter cycle. That is a property of the delay model, not a disagreement in the
            optimiser.
          </p>
        )}
      </div>
      <TableView title="Table view — delay by cycle length" rows={d.sweep} format={{ 'Equal splits': 1, 'Demand-proportional splits': 1 }} />

      <Section title="The feasible cycle window" />
      <Tiles cols={3}>
        <Tile label="Minimum cycle" value={cminBeyond ? 'beyond max' : num(d.c_min)} unit={cminBeyond ? '' : 's'}>
          C<sub>min</sub> = L·X<sub>c</sub>/(X<sub>c</sub> − Y) at a target v/c of 0.90
        </Tile>
        <Tile label="Webster optimum" value={num(d.opt.cycle_s)} unit="s">
          C = (1.5L + 5)/(1 − Y)
        </Tile>
        <Tile label="Practical maximum" value={num(d.max_cycle)} unit="s">
          beyond this, waiting drivers start ignoring the signal
        </Tile>
      </Tiles>
      {cminBeyond && (
        <div className="mt-2">
          <Callout tone="critical" title="Read this before quoting the saving">
            With Y = {num(d.Y, 3)} there is <b>no cycle length</b> that holds every approach at a degree of saturation of
            0.90. That is a capacity statement, not a timing one: this junction is close enough to its ceiling that
            retiming redistributes the delay rather than removing it. The fixes that would remove it are geometric or
            demand-side.
          </Callout>
        </div>
      )}

      <Section title="Measured demand and the resulting plan" />
      <DataTable rows={d.plan_table} format={{ 'Flow ratio y': 3, 'Saturation x': 3, 'Delay (s/PCU)': 1, 'Green (s)': 1, 'Cycle (s)': 1 }} />
      <div className="note mt-1">
        Amber is a fixed {num(d.amber_s)} s and all-red a fixed {num(d.all_red_s)} s in both plans, so green is the only
        thing being redistributed. Saturation flow is{' '}
        {widthBased
          ? `S = ${num(d.factor)}·W per IRC:SP:41's width-based form, divided by the lane count.`
          : "assumed at 1,800 PCU/hr per lane — IRC:SP:41's 525·W evaluated at a 3.5 m lane."}
      </div>
      <ScaleBanner />
      <Expander title="Plans as text">
        <pre className="code">{d.plans_text}</pre>
      </Expander>

      <div className="mt-3">
        <Callout title="Method and scope" tone="neutral">
          <p>
            <b>Method.</b> Webster's, not max-pressure: max-pressure needs the queue on the <i>downstream</i> link of
            every movement, and a camera looking at one approach cannot see that — an optimum computed from a term the
            data does not contain would be a made-up number.
          </p>
          <p>
            <b>Scope.</b> These two cameras watch different corridors, not opposing approaches of one junction, so the
            combined plan demonstrates the method on real measured demand rather than being a deployable plan for one
            intersection. Feeding it every approach of one junction is a configuration change, not a code change.
          </p>
          <p>
            <b>Sources.</b> Webster (1958) Road Research Technical Paper 39 · HCM 6th edition control delay ·
            IRC:SP:41-1994 saturation flow · field range from Vijay Cross-road, Ahmedabad.
          </p>
        </Callout>
      </div>
    </div>
  )
}
