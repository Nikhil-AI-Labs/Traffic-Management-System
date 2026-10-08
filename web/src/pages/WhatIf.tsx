// What-if on the signal plan: how long the retiming lasts, what a lost lane
// does to it, and what the delay it saves is worth. Lives on the Signal
// timing view and shares its baseline, delay model and saturation settings.

import { keepPreviousData } from '@tanstack/react-query'
import { useState } from 'react'
import { Callout, Chart, Field, Hero, Section, Spinner, Tile, Tiles } from '../components/ui'
import { DataTable, TableView } from '../components/widgets'
import { useThemedApi } from '../lib/api'
import { isNum, num, pct, signed } from '../lib/format'
import { useDebounced } from '../lib/hooks'
import type { WhatIfPayload } from '../lib/types'

const rupees = (v: number) =>
  v >= 1e7 ? `₹${num(v / 1e7, 2)} crore` : v >= 1e5 ? `₹${num(v / 1e5, 1)} lakh` : `₹${num(v)}`

export default function WhatIf({
  cycle,
  model,
  source,
  widths,
}: {
  cycle: number
  model: string
  source: string
  widths: Record<string, number>
}) {
  const [growth, setGrowth] = useState(0)
  const [lossOn, setLossOn] = useState('')
  const [loss, setLoss] = useState(10)
  const [rate, setRate] = useState(5)
  const [hours, setHours] = useState(6)
  const params = useDebounced(
    {
      growth,
      loss_approach: lossOn || null,
      loss_pct: lossOn ? loss : 0,
      growth_per_year: rate,
      hours_per_day: hours,
      baseline_cycle: cycle,
      delay_model: model,
      sat_source: source,
      widths: JSON.stringify(widths),
    },
    250,
  )
  const q = useThemedApi<WhatIfPayload>('/signal/whatif', params, { placeholderData: keepPreviousData })
  if (!q.data) return q.error ? <Callout tone="warning" title="What-if unavailable">{q.error.message}</Callout> : null
  const d = q.data
  const h = d.today.headroom
  const s = d.scenario
  const b = d.benefits
  const failing = isNum(h.reserve_ultimate_pct) && h.reserve_ultimate_pct <= 0
  const past = isNum(h.reserve_practical_pct) && h.reserve_practical_pct < 0

  return (
    <>
      <Section title="How long does the retiming last?" note="reserve capacity, from Webster's closed form" />
      <div className="grid-hero">
        <Hero
          label="Headroom before no timing can cope"
          value={isNum(h.reserve_ultimate_pct) ? `${signed(h.reserve_ultimate_pct, 1)}%` : '—'}
          unit="demand"
        >
          At the longest acceptable cycle ({num(h.max_cycle_s)} s) every approach reaches capacity once Y ={' '}
          {num(h.Y_ultimate, 3)}; today Y = {num(h.Y, 3)}.{' '}
          {failing ? (
            <b>Demand already exceeds what any timing can serve.</b>
          ) : (
            <>
              At {num(h.growth_per_year_pct)}% growth a year that is{' '}
              <b>{isNum(h.years_to_failure) ? `${num(h.years_to_failure, 1)} years` : '—'}</b> before retiming stops being
              an answer.
            </>
          )}
        </Hero>
        <Tiles>
          <Tile
            label="Practical limit"
            value={isNum(h.reserve_practical_pct) ? `${signed(h.reserve_practical_pct, 1)}%` : '—'}
            unit=""
          >
            to x = {num(h.x_practical, 2)} at the longest cycle{past ? ' — already past it' : ''}
          </Tile>
          <Tile label="Best x at max cycle" value={num(h.best_x_at_max_cycle, 3)}>
            Y·C/(C − L) with C = {num(h.max_cycle_s)} s, L = {num(h.lost_time_s)} s
          </Tile>
          <Tile label="Growth assumed" value={`${num(rate, rate % 1 ? 1 : 0)}%`} unit="a year">
            <input
              type="range"
              min={1}
              max={12}
              step={0.5}
              value={rate}
              aria-label="Annual traffic growth"
              onChange={(e) => setRate(+e.target.value)}
            />
          </Tile>
          <Tile
            label="Critical share"
            value={h.approaches.length ? `${num(Math.max(...h.approaches.map((a) => a.share_pct)))}%` : '—'}
          >
            of Y on the busiest approach
          </Tile>
        </Tiles>
      </div>
      <div className="mt-2">
        <Chart figure={d.chart} label="Worst degree of saturation as demand grows" />
        <div className="note mt-1">
          The adaptive line is Webster's delay-optimal plan. Near capacity its cycle reaches the {num(h.max_cycle_s)} s
          maximum, where no split of the green does better — so where it crosses capacity the fix is a wider approach,
          a banned turn or less demand, not a better cycle. The fixed timer already crosses at today's demand. At low
          demand a long fixed cycle shows a lower x than Webster's shorter one, bought with more delay.
        </div>
        <TableView
          title="Table view — saturation and delay by demand change"
          rows={d.curve}
          format={{ Y: 3, x_adaptive: 3, x_fixed: 3, delay_adaptive: 1, delay_fixed: 1, cycle_s: 1 }}
        />
      </div>

      <Section title="Scenario" note="grow the demand, or take capacity away from one approach" />
      <div className="card card-pad">
        <div className="controls" style={{ marginBottom: 0 }}>
          <Field label="Demand change" value={`${signed(growth)}%`} hint="Applied to every approach.">
            <input type="range" min={-30} max={50} step={1} value={growth} onChange={(e) => setGrowth(+e.target.value)} />
          </Field>
          <Field label="Capacity lost on" hint="Kerb parking, an encroachment, a stalled bus, a lane closed for works.">
            <select value={lossOn} onChange={(e) => setLossOn(e.target.value)}>
              <option value="">No approach</option>
              {d.approaches.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.short}
                </option>
              ))}
            </select>
          </Field>
          {lossOn && (
            <Field
              label="Saturation flow lost"
              value={`${loss}%`}
              hint="A stopped vehicle in one lane of a two-lane approach takes roughly half its discharge."
            >
              <input type="range" min={5} max={50} step={5} value={loss} onChange={(e) => setLoss(+e.target.value)} />
            </Field>
          )}
          {q.isFetching && <Spinner label="Recomputing…" />}
        </div>
      </div>
      <div className="mt-2">
        <Tiles>
          <Tile
            label="Worst x, fixed timer"
            value={num(s.worst_x_fixed, 3)}
            meter={[Math.min(1, s.worst_x_fixed), s.worst_x_fixed >= 1 ? 'var(--critical)' : 'var(--good)']}
          >
            {s.worst_x_fixed >= 1 ? 'over capacity — the queue grows every cycle' : 'under capacity'}
          </Tile>
          <Tile
            label="Worst x, adaptive"
            value={num(s.worst_x_adaptive, 3)}
            meter={[
              Math.min(1, s.worst_x_adaptive),
              s.worst_x_adaptive >= 1 ? 'var(--critical)' : s.worst_x_adaptive > 0.9 ? 'var(--warning)' : 'var(--good)',
            ]}
          >
            {s.worst_x_adaptive >= 1
              ? 'no timing clears this demand'
              : s.worst_x_adaptive > 0.9
                ? 'workable, past the practical limit'
                : 'comfortable'}
          </Tile>
          <Tile label="Adaptive cycle" value={num(s.opt.cycle_s)} unit="s">
            {s.opt.feasible ? 'Webster optimum' : `capped at the ${num(h.max_cycle_s)} s maximum`}
          </Tile>
          <Tile label="Mean delay" value={num(s.opt.mean_delay_s_per_pcu, 1)} unit="s/PCU">
            fixed timer {num(s.base.mean_delay_s_per_pcu, 1)} s/PCU in the same scenario
          </Tile>
        </Tiles>
        {s.opt.note && (
          <div className="mt-2">
            <Callout tone="critical" title="Capacity">
              {s.opt.note}
            </Callout>
          </div>
        )}
      </div>

      <Section title="What the retiming is worth" note="today's demand, indicative" />
      <div className="grid-hero">
        <Hero label="Saved a year, this junction" value={rupees(b.rupees_per_year)}>
          in idling fuel, from {num(b.pcu_hours_per_hour, 1)} PCU-hours of delay saved per hour at the measured demand,
          held for{' '}
          <input
            type="number"
            min={1}
            max={24}
            value={hours}
            onChange={(e) => {
              const v = +e.target.value
              if (v >= 1 && v <= 24) setHours(v)
            }}
            style={{ width: 64, minHeight: 28, padding: '2px 6px', display: 'inline-block' }}
            aria-label="Hours a day at this demand"
          />{' '}
          hours a day, {num(b.assumptions.days_per_year)} days a year.
        </Hero>
        <Tiles>
          <Tile label="Vehicle-hours" value={num(b.vehicle_hours_per_year)} unit="a year">
            {num(b.vehicle_hours_per_day)} a day at {num(b.assumptions.pcu_per_vehicle, 2)} PCU per vehicle
          </Tile>
          <Tile label="Fuel" value={num(b.fuel_l_per_year)} unit="L a year">
            idling, by the measured fleet mix
          </Tile>
          <Tile label="CO₂" value={num(b.co2_t_per_year, 1)} unit="t a year">
            {num(b.assumptions.co2_kg_per_l.petrol, 2)} kg/L petrol, {num(b.assumptions.co2_kg_per_l.diesel, 2)} kg/L diesel
          </Tile>
          <Tile label="Delay saved" value={num(b.pcu_hours_per_hour, 1)} unit="PCU·h/h">
            {b.per_approach
              .map((p) => `${d.approaches.find((a) => a.id === p.id)?.short}: ${signed(p.saved_s_per_pcu, 1)} s/PCU`)
              .join(' · ')}
          </Tile>
        </Tiles>
      </div>
      <div className="grid-equal mt-2">
        <DataTable
          rows={b.fleet.map((f) => ({
            Group: f.group,
            Share: f.share_pct,
            'Idle fuel (L/h)': f.idle_l_per_h,
            Fuel: f.fuel,
          }))}
          format={{ Share: (v) => pct(v, 1), 'Idle fuel (L/h)': 2 }}
        />
        <Callout tone="warning" title="Read these as orders of magnitude">
          <p>{b.assumptions.note}</p>
          <p>
            The saving is the adaptive plan's model delay against the fixed timer's, so it moves with the delay model
            chosen above. One approach can gain a little delay while the other loses a lot: the plan moves green towards
            the approach that was over capacity, which is the point.
          </p>
        </Callout>
      </div>
    </>
  )
}
