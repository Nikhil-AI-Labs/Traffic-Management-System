import { Chart, ErrorState, Note, PageSkeleton, Section, Tile, Tiles } from '../components/ui'
import { TableView } from '../components/widgets'
import { useThemedApi } from '../lib/api'
import { num } from '../lib/format'
import type { Figure, Row } from '../lib/types'

interface Payload {
  worst: { id: string; short: string; detections: number }
  busiest: { id: string; short: string; detections: number }
  fleet: { groups: string[]; rows: Row[] }
  junction_sites: string[]
  area_sites: string[]
  charts: { occlusion: Figure; count_vs_occupancy: Figure; fleet: Figure }
}

const STEPS = [
  ['1 · Detect', 'YOLO11-S on UVH-26', 'Weights fine-tuned on 26,646 annotated frames of Indian Safe-City CCTV — the same domain as this footage. Fourteen India-specific classes, including the three-wheeler that COCO cannot represent at all.'],
  ['2 · Track', 'ByteTrack', 'Identity across frames, so speed and stopped-fraction are measurable. Track IDs are used for motion, never for counting arrivals — the tracker reissues IDs freely in dense traffic.'],
  ['3 · Locate', 'Zones + perspective scale', 'Each detection is assigned by its bottom-centre ground contact point, so a tall bus is not credited to the lane behind it. An anisotropic scale model converts pixels to ground distance row by row.'],
  ['4 · Measure', 'PCU density, speed, occupancy', 'Density in Passenger Car Units per IRC:106-1990, because twelve two-wheelers and twelve buses are not the same amount of traffic. Flow comes from q = k·v rather than from counting track IDs.'],
  ['5 · Decide', 'Webster · detectors · trend', "Signal timing from Webster's method on the critical lane, five sustained-pattern bottleneck detectors, and a damped trend with a prediction interval."],
]

const sharePct = (v: unknown) => (typeof v === 'number' ? `${num(v, 1)}%` : '—')

export default function Method() {
  const q = useThemedApi<Payload>('/method')
  if (q.isLoading) return <PageSkeleton />
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const d = q.data

  return (
    <div className="page">
      <div className="note prose" style={{ fontSize: '0.93rem' }}>
        PS-08 asks for lane density, adaptive signal timing, bottleneck detection and predictive recommendations from
        CCTV. The pipeline below produces all four. This view is the reasoning behind the numbers the other views show —
        what is measured, what is inferred, and what this dataset cannot support.
      </div>

      <Section title="The pipeline" />
      <Tiles cols={5}>
        {STEPS.map(([n, t, body]) => (
          <Tile key={n} label={n}>
            <div style={{ fontSize: '0.95rem', fontWeight: 650, color: 'var(--text)', marginBottom: 6 }}>{t}</div>
            {body}
          </Tile>
        ))}
      </Tiles>

      <Section title="The finding that shaped the design" note="detection collapses exactly where congestion peaks" />
      <div className="grid-2">
        <Chart figure={d.charts.occlusion} label="Detections against measured congestion, per site" />
        <Note>
          <p>
            <b>{d.worst.short}</b> is the most gridlocked site in the set and returns <b>{num(d.worst.detections, 1)}</b>{' '}
            detections per window. <b>{d.busiest.short}</b>, a freely moving arterial, returns{' '}
            <b>{num(d.busiest.detections, 1)}</b>.
          </p>
          <p>
            Vehicles packed bumper to bumper occlude each other until the detector can no longer separate them. A
            congestion index built on raw counts would therefore rank Surat's worst street among its emptiest —
            confidently, and backwards.
          </p>
          <p>
            So counts do not drive the index. A texture-based occupancy measure does: edge density over the zone,
            normalised against absolute bounds measured across all seven cameras. It has no notion of an object and
            cannot be defeated by occlusion.
          </p>
        </Note>
      </div>

      <Section title="The same failure, over time" note={`${d.worst.short} — the worst-affected site`} />
      <div className="grid-2">
        <Chart figure={d.charts.count_vs_occupancy} label="Detections and occupancy over time at the worst site" />
        <Note>
          <p>
            Occupancy sits pinned near its ceiling for the whole clip while the detection count stays on the floor. Read
            the top panel alone and this street is empty; read the bottom panel and it is immovable. The footage settles
            it — the street is immovable.
          </p>
          <p>
            The system flags these windows as <b>saturated</b> and says so on every readout, rather than quietly
            reporting a count it knows is wrong.
          </p>
        </Note>
      </div>

      <Section title="What is on these roads" note="share of detections by class group" />
      <div className="grid-2">
        <Chart figure={d.charts.fleet} label="Vehicle class share by site" />
        <Note>
          The reason a domain-matched detector matters. Two-wheelers and auto-rickshaws dominate most of these sites, and
          a stock COCO-trained model has no auto-rickshaw class at all — it either misses them or files them as cars,
          which then propagates into every PCU figure downstream (an auto is 1.2 PCU, a car 1.0).
        </Note>
      </div>
      <TableView
        title="Table view — class share by site (%)"
        rows={d.fleet.rows}
        format={Object.fromEntries(d.fleet.groups.map((g) => [g, sharePct]))}
      />

      <Section title="Junction mode against area mode" />
      <Tiles cols={2}>
        <Tile label={`Junction mode — ${d.junction_sites.length} sites`}>
          Lane-marked carriageway with a signal to control. Reports per-lane PCU density, speed, queue and an adaptive
          timing recommendation.
          <div className="mt-1">
            <b>{d.junction_sites.join(', ')}</b>
          </div>
        </Tile>
        <Tile label={`Area mode — ${d.area_sites.length} sites`}>
          Market streets, a depot platform, a station forecourt. No lane discipline and no signal, so reporting "lane
          density" here would be inventing a number. Zone occupancy, crowding and bottleneck alerts instead.
          <div className="mt-1">
            <b>{d.area_sites.join(', ')}</b>
          </div>
        </Tile>
      </Tiles>

      <Section title="What this dataset cannot tell us" />
      <div className="note prose">
        <p>
          Converting pixels to metres needs one true ground reference — a surveyed width, the camera's height and focal
          length, or a landmark of known size. This dataset ships none, and three independent attempts to recover the
          scale disagreed with each other by a factor of two to four:
        </p>
        <ul>
          <li>assuming the traced carriageway is N lanes × 3.5 m — but one traced polygon follows a works-zone narrowing;</li>
          <li>regressing on known vehicle widths — biased in the near field, because a box's width is set by the vehicle's widest point, not its ground contact;</li>
          <li>fitting the horizon from road-edge convergence — the traced edges do not converge at a rate consistent with either of the above.</li>
        </ul>
        <p>
          Rather than pick the most flattering of the three, everything in metres, km/h or PCU/km/lane is marked <b>~</b>{' '}
          and treated as an estimate carrying an unknown multiplier. The congestion ranking, the level-of-service bands,
          the saturation flags and the signal before/after comparison are scale-free and unaffected. One measured road
          width per camera promotes the rest to measured values — a configuration change, not a code change.
        </p>
      </div>
    </div>
  )
}
