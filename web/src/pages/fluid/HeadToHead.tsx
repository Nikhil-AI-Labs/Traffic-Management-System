import { useMutation } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { useState } from 'react'
import { Callout, Chart, ErrorState, Field, Hero, Section, Spinner, Tile, Tiles } from '../../components/ui'
import { DataTable } from '../../components/widgets'
import { post, qs, useApi } from '../../lib/api'
import { num, signed } from '../../lib/format'
import { useTheme } from '../../lib/theme'
import type { Figure, Row } from '../../lib/types'

interface Scenarios {
  scenarios: { id: string; label: string; blurb: string }[]
}

interface Ctl {
  name: string
  delay: number
  switches: number
  gain_pct: number
}

interface Run {
  scenario: { id: string; label: string }
  Y: number
  cycle_s: number
  fixed: Ctl
  cyclic: Ctl
  free: Ctl
  best: Ctl
  table: Row[]
  charts: { comparison: Figure; queues: Figure }
}

export default function HeadToHead() {
  const sc = useApi<Scenarios>('/sim/scenarios')
  const { resolved } = useTheme()
  const [scenario, setScenario] = useState('steady')
  // The corridor's own demand — the setting REPORT.md's table was produced at,
  // so an untouched run reproduces the published figures.
  const [main, setMain] = useState(700)
  const [cross, setCross] = useState(300)
  const run = useMutation<Run, Error, void>({
    mutationFn: () => post<Run>(`/sim/run${qs({ theme: resolved })}`, { scenario, main, cross }),
  })

  if (sc.error) return <ErrorState error={sc.error} />
  const blurb = sc.data?.scenarios.find((s) => s.id === scenario)?.blurb
  const r = run.data

  return (
    <div className="page">
      <Callout title="What is measured and what is not">
        <p>
          <b>Measured from the Surat clips:</b> arrival flow per approach, the vehicle mix behind it, and the PCU
          conversion. <b>Constructed for the model:</b> the two-junction topology, the link between them and its storage
          capacity.
        </p>
        <p>
          A simulator lets us demonstrate the controller; it does not let us claim a measured result, and that split keeps
          the two apart. One junction is not enough — with no receiving link to fill, max-pressure's downstream term is
          constant and it collapses to serving the longest queue.
        </p>
      </Callout>

      <Section title="Controlled head-to-head" note="measured demand, constructed network" />
      <div className="card card-pad">
        <div className="controls" style={{ marginBottom: 0 }}>
          <Field label="Demand scenario">
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              {sc.data?.scenarios.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Main-road demand" value={`${main} PCU/hr`}>
            <input type="range" min={400} max={1400} step={50} value={main} onChange={(e) => setMain(+e.target.value)} />
          </Field>
          <Field label="Cross-street demand" value={`${cross} PCU/hr`}>
            <input type="range" min={150} max={700} step={25} value={cross} onChange={(e) => setCross(+e.target.value)} />
          </Field>
          <button className="btn btn-primary" onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? <span className="spinner" /> : <Play size={16} />} Run both controllers
          </button>
        </div>
        {blurb && <div className="note mt-2">{blurb}</div>}
      </div>

      {run.isPending && (
        <div className="mt-2">
          <Spinner label="Simulating 30 minutes under each controller…" />
        </div>
      )}
      {run.error && (
        <div className="mt-2">
          <Callout tone="critical" title="Simulation failed">
            {run.error.message}
          </Callout>
        </div>
      )}
      {!r && !run.isPending && (
        <div className="note mt-2">
          Press <b>Run both controllers</b>. Nothing is simulated until you do.
        </div>
      )}

      {r && (
        <>
          <Section title="Result" note={`${r.scenario.label} · Y = ${num(r.Y, 2)} · cycle ${num(r.cycle_s)} s`} />
          <div className="grid-hero">
            <Hero label="Delay against the fixed-time plan" value={signed(-r.best.gain_pct)} unit="%">
              Best max-pressure variant: <b>{r.best.name}</b> (negative = less delay). Both controllers ran the same
              network, the same demand and the same lost time per phase change.
            </Hero>
            <Tiles cols={3}>
              <Tile label="Fixed-time" value={num(r.fixed.delay)} unit="s/PCU">
                {r.fixed.switches} phase changes
              </Tile>
              <Tile label="MP — cyclic splits" value={num(r.cyclic.delay)} unit="s/PCU">
                {signed(-r.cyclic.gain_pct)}% delay · {r.cyclic.switches} changes
              </Tile>
              <Tile label="MP — free-running" value={num(r.free.delay)} unit="s/PCU">
                {signed(-r.free.gain_pct)}% delay · {r.free.switches} changes
              </Tile>
            </Tiles>
          </div>
          <div className="mt-3">
            <Chart figure={r.charts.comparison} label="Delay, queue and throughput by controller" />
          </div>
          <div className="mt-2">
            <DataTable rows={r.table} />
          </div>

          <Section title="Queues over time" />
          <Chart figure={r.charts.queues} label="Total network queue over time by controller" />

          <div className="mt-3">
            <Callout tone="neutral" title="Why the two max-pressure variants differ">
              Free-running max-pressure switched <b>{r.free.switches}</b> times against the fixed plan's{' '}
              <b>{r.fixed.switches}</b>. Every change costs the same start-up and clearance time, so an unmodified
              controller can spend more capacity on inter-greens than it recovers by adapting. That gap between Varaiya's
              slotted model, where switching is free, and a real signal is why the cyclic variant keeps a cycle and lets
              pressure set only the splits.
            </Callout>
            <Callout tone="good" title="When to choose which">
              <p>
                The pattern across the three scenarios is the one the theory predicts, and it is narrower than the
                literature's enthusiasm suggests. <b>Max-pressure loses on steady demand</b>, because a plan designed for a
                demand that does not change is already right and every extra switch pays an inter-green. It{' '}
                <b>wins when the split of demand moves</b> between approaches, and{' '}
                <b>wins most when something downstream backs up</b> — the one regime where its distinguishing term, the
                downstream queue, carries information.
              </p>
              <p>
                That is the operator's decision rule, and it is why this is a toggle rather than a replacement. Run each
                scenario and read the numbers off them rather than taking this summary on trust.
              </p>
            </Callout>
          </div>
        </>
      )}
    </div>
  )
}
