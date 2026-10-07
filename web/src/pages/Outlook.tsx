import { keepPreviousData } from '@tanstack/react-query'
import { useState, type CSSProperties } from 'react'
import { Chart, Chip, ErrorState, Field, Hero, PageSkeleton, Section, Spinner, Tile, Tiles } from '../components/ui'
import { TableView } from '../components/widgets'
import { useThemedApi } from '../lib/api'
import { num } from '../lib/format'
import type { Figure, Forecast } from '../lib/types'

interface Payload {
  horizon_s: number
  forecasts: Forecast[]
  risks: Forecast[]
  reliable: number
  threshold: number
  chart: Figure
}

export default function Outlook() {
  const [horizon, setHorizon] = useState(60)
  const q = useThemedApi<Payload>('/outlook', { horizon }, { placeholderData: keepPreviousData })
  if (q.isLoading) return <PageSkeleton />
  if (!q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const d = q.data

  return (
    <div className="page">
      <div className="controls">
        <Field label="Forecast horizon" value={`${horizon} s`}>
          <input type="range" min={30} max={180} step={15} value={horizon} onChange={(e) => setHorizon(+e.target.value)} />
        </Field>
        {q.isFetching && <Spinner label="Forecasting…" />}
      </div>

      <div className="grid-hero">
        <Hero label="Zones trending toward congestion" value={d.risks.length}>
          Rising, credibly, and forecast past {d.threshold} within {horizon} s. Zones whose trend is smaller than their own
          noise are reported as steady rather than given a direction.
        </Hero>
        <Tiles cols={3}>
          <Tile label="Zones forecast" value={d.forecasts.length} />
          <Tile label="With a directional trend" value={d.reliable} meter={[d.reliable / Math.max(1, d.forecasts.length), 'var(--s1)']}>
            {d.forecasts.length - d.reliable} within noise
          </Tile>
          <Tile label="Horizon" value={horizon} unit="s">
            damped trend, interval from residuals
          </Tile>
        </Tiles>
      </div>

      <Section title="Now against forecast" note="with the prediction interval" />
      <Chart figure={d.chart} label="Current and forecast congestion per zone" />
      <div className="note mt-1 prose">
        Hollow markers are zones whose trend does not beat their own noise — shown, but not claimed as a direction. The
        thick band is the prediction interval, and it is deliberately prominent: a bare forecast number off a few minutes
        of footage would imply a confidence this data cannot support. Continuous operation would replace this with a
        time-of-day seasonal model; the interface would not change.
      </div>

      {d.risks.length > 0 && (
        <>
          <Section title="Zones trending worse" />
          {d.risks.map((f) => (
            <div className="alert" key={`${f.camera_id}-${f.zone_id}`} style={{ '--c': 'var(--warning)' } as CSSProperties}>
              <div className="alert-top">
                <Chip color="var(--warning)">RISING</Chip>
                <span className="alert-m">{f.camera_name}</span>
              </div>
              <div className="alert-h">{f.zone_label}</div>
              <div className="alert-b">
                {num(f.current, 2)} → <b>{num(f.predicted, 2)}</b> over {num(f.horizon_s)} s (interval {num(f.lower, 2)}–
                {num(f.upper, 2)})
              </div>
            </div>
          ))}
        </>
      )}

      <TableView
        title="Table view — every zone forecast"
        rows={d.forecasts.map((f) => ({
          Site: f.camera_short,
          Zone: f.zone_label,
          Now: f.current,
          Forecast: f.predicted,
          Low: f.lower,
          High: f.upper,
          Direction: f.direction,
          Reliable: f.reliable,
        }))}
      />
    </div>
  )
}
