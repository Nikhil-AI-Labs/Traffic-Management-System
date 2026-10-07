import { useState } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import {
  Callout,
  Chart,
  Chip,
  ErrorState,
  LosChip,
  Note,
  PageSkeleton,
  ScaleBanner,
  Section,
  Skeleton,
  Tabs,
  Tile,
  Tiles,
} from '../components/ui'
import { AlertsSection, Media, TableView } from '../components/widgets'
import { useApi, useThemedApi } from '../lib/api'
import { LOS_COLOR, est, int, num, pct } from '../lib/format'
import type { Meta, SitePayload, TrendPayload } from '../lib/types'

type SceneTab = 'zones' | 'heatmap' | 'video'

export default function Site() {
  const { cam } = useParams()
  const meta = useApi<Meta>('/meta', { staleTime: 15_000 })
  if (!cam) {
    if (meta.isLoading) return <PageSkeleton />
    const first = meta.data?.cameras.find((c) => c.analysed)
    return first ? (
      <Navigate to={`/surat/site/${encodeURIComponent(first.id)}`} replace />
    ) : (
      <ErrorState error={meta.error ?? new Error('No camera has been analysed yet.')} />
    )
  }
  return <SiteView key={cam} cam={cam} meta={meta.data} />
}

function SiteView({ cam, meta }: { cam: string; meta?: Meta }) {
  const navigate = useNavigate()
  const q = useThemedApi<SitePayload>(`/sites/${encodeURIComponent(cam)}`)
  const [tab, setTab] = useState<SceneTab>('zones')
  const [metric, setMetric] = useState('congestion_index')
  const trend = useThemedApi<TrendPayload>(`/sites/${encodeURIComponent(cam)}/trend`, { metric })

  const picker = meta && (
    <div className="controls">
      <label className="field" style={{ maxWidth: 420 }}>
        <span className="field-l">Site</span>
        <select value={cam} onChange={(e) => navigate(`/surat/site/${encodeURIComponent(e.target.value)}`)}>
          {meta.cameras
            .filter((c) => c.analysed)
            .map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
        </select>
      </label>
    </div>
  )

  if (q.isLoading)
    return (
      <>
        {picker}
        <PageSkeleton />
      </>
    )
  if (q.error || !q.data)
    return (
      <>
        {picker}
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      </>
    )

  const d = q.data
  const e = est(d.camera.scale_verified)
  const enc = encodeURIComponent(cam)

  return (
    <div className="page">
      {picker}
      <div className="row" style={{ gap: 12 }}>
        <h2 style={{ margin: 0, fontSize: '1.35rem', letterSpacing: '-0.02em' }}>{d.camera.name}</h2>
        <LosChip los={d.los} />
        {d.camera.scale_verified && <Chip color="var(--good)">Scale measured</Chip>}
      </div>
      <div className="note mt-1">
        {d.camera.mode[0].toUpperCase() + d.camera.mode.slice(1)} mode · {d.camera.notes}
      </div>

      <Section title="Zone state" note={`${d.zones.length} zones · ${int(d.windows)} windows`} />
      <Tiles cols={Math.min(4, d.zones.length)}>
        {d.zones.map((z) => {
          const bits = [`occupancy ${num(z.occupancy, 2)}`]
          if (z.density !== null) bits.push(`${e}${num(z.density)} PCU/km/ln`)
          if (z.speed !== null) bits.push(`${e}${num(z.speed)} km/h`)
          if (z.saturated_windows) bits.push(`${z.saturated_windows} saturated`)
          return (
            <Tile key={z.zone_id} label={z.label} value={num(z.congestion, 2)} meter={[z.congestion, LOS_COLOR[z.los]]}>
              {z.los} · {bits.join(' · ')}
            </Tile>
          )
        })}
      </Tiles>

      <Section title="Scene" />
      <div className="grid-2">
        <div>
          <Tabs<SceneTab>
            value={tab}
            onChange={setTab}
            tabs={[
              { id: 'zones', label: 'Zones' },
              { id: 'heatmap', label: 'Where traffic actually goes' },
              { id: 'video', label: 'Annotated video' },
            ]}
          />
          {tab === 'zones' && (
            <Media
              src={d.assets.overlay ? `/api/images/overlay/${enc}?w=1600` : null}
              alt={`${d.camera.name}: zone polygons`}
              caption="Zone polygons shaded by average level of service — the same status colours the bands and chips use. Click to enlarge."
              missing={
                <>
                  No reference frame — run <code>python src/reference_frames.py</code>.
                </>
              }
            />
          )}
          {tab === 'heatmap' && (
            <Media
              src={d.assets.heatmap ? `/api/images/heatmap/${enc}?w=1600` : null}
              alt={`${d.camera.name}: movement heatmap`}
              caption="Every tracked vehicle's ground contact point, accumulated over the clip."
              missing={
                <>
                  No heatmap rendered yet — <code>python src/traffic_heatmap.py {cam}</code>.
                </>
              }
            />
          )}
          {tab === 'video' &&
            (d.assets.annotated_video ? (
              <video controls preload="metadata" src={`/api/media/annotated/${enc}`} />
            ) : (
              <div className="placeholder">
                <div>
                  Not rendered for this camera — <code>python src/render_demo.py {cam}</code>
                </div>
              </div>
            ))}
        </div>
        <Note>
          <p>
            <b>Zones</b> are traced once per camera and reused for every window. A detection is credited to a zone by
            its bottom-centre ground contact point rather than its box centre, so a tall bus is not assigned to the lane
            behind it.
          </p>
          <p>
            <b>The heatmap</b> is how those polygons were checked. A lane traced from a single still frame can look
            right and still sit off the path traffic actually takes; accumulating real tracks settles it. Bright patches
            that never move are stationary objects — at Rokadiya Hanuman that turned out to be kerb-side parking
            narrowing the carriageway, which is where the encroachment alert comes from.
          </p>
          <p>
            <b>The video</b> is the same pipeline that produced every number on this page, drawn frame by frame.
          </p>
        </Note>
      </div>

      <Section title="Level of service by zone" note="each zone, second by second" />
      <Chart figure={d.charts.zone_los} label="Level of service by zone over time" />

      <Section title="Trend over the clip" note="10-second bins" />
      <div className="controls">
        <label className="field">
          <span className="field-l">Metric</span>
          <select value={metric} onChange={(ev) => setMetric(ev.target.value)}>
            {d.trend_metrics.map((m) => (
              <option key={m.id} value={m.id}>
                {m.estimate ? '~' : ''}
                {m.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {trend.isLoading || !trend.data ? (
        <Skeleton height={310} />
      ) : (
        <>
          <Chart figure={trend.data.chart} label="Trend by zone" />
          <TableView title="Table view — 10-second bins" rows={trend.data.rows} columns={trend.data.columns} />
        </>
      )}

      <Section title="Counting against occupancy" note="why this site needs both measures" />
      <div className="grid-2">
        <Chart figure={d.charts.count_vs_occupancy} label="Detections and occupancy over time" />
        <Note>
          <p>
            Two panels, one time axis — never two y-scales. The scales are unrelated, and overlaying them would imply a
            fixed relationship between counts and occupancy that is exactly what fails in dense traffic.
          </p>
          <p>
            This site spends <b>{pct(d.saturated_pct)}</b> of its windows saturated: so densely packed that vehicles
            stop being individually separable. Where the panels move together, counting is sound. Where the top panel
            sags while the bottom stays pinned high, the detector is losing vehicles to occlusion and occupancy is the
            measure that still holds.
          </p>
        </Note>
      </div>

      {d.charts.fundamental && (
        <>
          <Section title="Speed against density" note="the fundamental diagram" />
          <div className="grid-2">
            <Chart figure={d.charts.fundamental} label="Speed against density" />
            <Note>
              Traffic-flow theory says speed must fall as density rises, and that flow peaks near capacity density
              before collapsing. Plotting the measurement against the theory is the check that the chain from pixels to
              PCU is behaving — a flat cloud here would mean the speed or the density figure is wrong. An earlier version
              of this project produced exactly that, and it is how the perspective-scale bug was found.
            </Note>
          </div>
          {!d.camera.scale_verified && <ScaleBanner />}
        </>
      )}

      <Section title="Bottlenecks at this site" note={d.alerts.length ? `${d.alerts.length} raised` : 'none raised'} />
      {d.alerts.length ? (
        <AlertsSection alerts={d.alerts} showSite={false} />
      ) : (
        <Callout tone="good" title="No sustained bottleneck">
          No pattern held for {d.min_sustained_windows} consecutive windows at this site.
        </Callout>
      )}
    </div>
  )
}
