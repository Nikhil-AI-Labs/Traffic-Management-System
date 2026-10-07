import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Callout, Chip, ErrorState, Field, Hero, PageSkeleton, Section, Spinner, Steps, Tile, Tiles } from '../components/ui'
import { DataTable, JobProgress, Media, useJob } from '../components/widgets'
import { del, post, useApi } from '../lib/api'
import { num } from '../lib/format'
import type { Job, Row } from '../lib/types'

interface Overview {
  cameras: {
    id: string
    name: string
    short: string
    mode: string
    verified: boolean
    widths: { row: number; metres: number }[]
    camera_height_m: number | null
    hfov_deg: number | null
    applied_at: string | null
    model: string | null
    has_video: boolean
  }[]
  verified: number
  detector_ready: boolean
}

interface CamDetail {
  id: string
  name: string
  verified: boolean
  record: { applied_at?: string } | null
  rows: { row: number; px: number; prior_m: number }[]
  frame_width: number
  camera_height_m: number
  hfov_deg: number
  has_reference: boolean
}

interface FitResult {
  ok: boolean
  message: string
  rows: Row[] | null
  zone_areas: Row[] | null
  warnings: string[] | null
  focal_px: number
}

export default function Calibration() {
  const qc = useQueryClient()
  const overview = useApi<Overview>('/calibration')
  const [picked, setCam] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useJob(jobId)
  const cam = picked ?? overview.data?.cameras[0]?.id ?? null

  // When a re-measure finishes, every figure for that camera has changed.
  useEffect(() => {
    if (job.data?.status === 'done') qc.invalidateQueries()
  }, [job.data?.status, qc])

  if (overview.isLoading) return <PageSkeleton />
  if (overview.error || !overview.data) return <ErrorState error={overview.error} onRetry={() => overview.refetch()} />
  const o = overview.data

  return (
    <div className="page">
      <div className="grid-hero">
        <Hero label="Cameras with a measured scale" value={o.verified} unit={`/ ${o.cameras.length}`}>
          Until a camera has one real-world distance behind it, its speeds and PCU densities are estimates carrying an
          unknown multiplier. Everything else the system reports is scale-free and unaffected.
        </Hero>
        <Tiles cols={2}>
          <Tile label="What it fixes">
            <b>km/h</b> · <b>PCU/km/lane</b> · <b>PCU/hr</b> · zone areas in m² — all promoted from <b>~estimate</b> to
            measured.
          </Tile>
          <Tile label="What it needs" value="1" unit="measurement">
            The real width of the road across one marked line. Two lines at different depths is better — two determine
            the horizon, and the horizon sets every speed.
          </Tile>
        </Tiles>
      </div>

      <Section title="How to do it" note="about two minutes, no site visit needed" />
      <Steps
        items={[
          <>
            <b>Pick a camera below.</b> The system draws candidate measurement lines straight across the carriageway and
            tells you how many pixels wide each one is.
          </>,
          <>
            <b>Measure one of those lines for real.</b> Open Google Maps in satellite view, find the same stretch of
            road, right-click → <b>Measure distance</b>, and drag across the carriageway between the same two edges the
            line touches. A tape measure on site works too.
          </>,
          <>
            <b>Type the metres in.</b> Leave a line empty to skip it. Two lines at different depths remove the one
            remaining assumption.
          </>,
          <>
            <b>Check the fit, then apply.</b> The perspective model is fitted, checked for physical plausibility and
            stored, then the clip is re-measured and the <b>~</b> prefixes disappear.
          </>,
        ]}
      />

      <Section title="Camera" />
      <div className="controls">
        <Field label="Camera">
          <select value={cam ?? ''} onChange={(e) => setCam(e.target.value)}>
            {o.cameras.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {cam && (
        <CameraForm
          key={cam}
          cam={cam}
          canApply={o.detector_ready && !!o.cameras.find((c) => c.id === cam)?.has_video}
          busy={job.data?.status === 'running' || job.data?.status === 'queued'}
          onJob={(j) => setJobId(j.id)}
        />
      )}
      {job.data && (
        <div className="mt-2">
          <JobProgress job={job.data} />
          {job.data.status === 'done' && (
            <div className="mt-1">
              <Callout tone="good" title="Done">
                The camera has been re-measured. Every view now reports its new figures.
              </Callout>
            </div>
          )}
        </div>
      )}

      <Section title="Calibration state" note="every camera" />
      <DataTable
        rows={o.cameras.map((c) => ({
          Camera: c.name,
          Mode: c.mode,
          Scale: c.verified ? 'measured' : 'estimated',
          'Widths supplied': c.widths.map((w) => `y=${w.row}→${w.metres} m`).join(', ') || '—',
          'Camera height (m)': c.camera_height_m,
          'FOV (°)': c.hfov_deg,
          Applied: c.applied_at ? c.applied_at.slice(0, 16).replace('T', ' ') : '—',
          Model: c.model ?? '—',
        }))}
      />
      <UndoPanel cameras={o.cameras.filter((c) => c.verified)} onJob={(j) => setJobId(j?.id ?? null)} />
    </div>
  )
}

function CameraForm({
  cam,
  canApply,
  busy,
  onJob,
}: {
  cam: string
  canApply: boolean
  busy: boolean
  onJob: (j: Job) => void
}) {
  const q = useApi<CamDetail>(`/calibration/${encodeURIComponent(cam)}`)
  const [widths, setWidths] = useState<Record<number, number>>({})
  const [height, setHeight] = useState<number | null>(null)
  const [hfov, setHfov] = useState<number | null>(null)
  const [fit, setFit] = useState<FitResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  if (q.isLoading) return <Spinner label="Loading camera…" />
  if (q.error || !q.data) return <ErrorState error={q.error} />
  const d = q.data
  const h = height ?? d.camera_height_m
  const f = hfov ?? d.hfov_deg
  const entered = d.rows
    .map((r) => [r.row, widths[r.row] ?? r.prior_m] as [number, number])
    .filter(([, w]) => w > 0)
  const body = { measurements: entered, camera_height_m: h, hfov_deg: f }

  const run = async (apply: boolean) => {
    setErr(null)
    setPending(true)
    try {
      setFit(await post<FitResult>(`/calibration/${encodeURIComponent(cam)}/fit`, body))
      if (apply) {
        const r = await post<{ job: Job }>(`/calibration/${encodeURIComponent(cam)}/apply`, body)
        onJob(r.job)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        {d.verified ? <Chip color="var(--good)">Scale measured</Chip> : <Chip color="var(--warning)">Scale not measured</Chip>}
        {d.record?.applied_at && <span className="kv">recorded {d.record.applied_at.slice(0, 10)}</span>}
      </div>
      <div className="grid-2">
        <Media
          src={d.has_reference ? `/api/images/guide/${encodeURIComponent(cam)}?w=1600` : null}
          alt={`${d.name}: measurement lines`}
          caption="Measure the real width along any coloured line, between the two end markers. Click to enlarge."
          missing={
            <>
              No reference frame — run <code>python src/reference_frames.py</code>.
            </>
          }
        />
        <div className="card card-pad">
          <div className="kv" style={{ marginBottom: 10 }}>
            <b>Measured widths</b>
          </div>
          {d.rows.length === 0 && (
            <Callout tone="warning">No carriageway zones are traced on this camera, so there is nothing to measure across.</Callout>
          )}
          <div className="stack">
            {d.rows.map((r, i) => (
              <Field key={r.row} label={`Line ${i + 1} — row y=${r.row}`} value={`${r.px} px wide`}>
                <input
                  type="number"
                  min={0}
                  max={200}
                  step={0.1}
                  defaultValue={r.prior_m || ''}
                  placeholder="metres — leave empty to skip"
                  onChange={(e) => setWidths((w) => ({ ...w, [r.row]: parseFloat(e.target.value) || 0 }))}
                />
              </Field>
            ))}
          </div>
          <div className="row mt-2" style={{ alignItems: 'flex-start' }}>
            <Field label="Camera height (m)" hint="Typical Indian traffic poles are 6–9 m.">
              <input type="number" min={2} max={25} step={0.5} value={h} onChange={(e) => setHeight(parseFloat(e.target.value) || 7)} />
            </Field>
            <Field label="Field of view (°)" hint="From the spec sheet; 60° is typical at 1080p.">
              <input type="number" min={10} max={140} step={1} value={f} onChange={(e) => setHfov(parseFloat(e.target.value) || 60)} />
            </Field>
          </div>
          <div className="field-h">
            Height and field of view together set how fast ground distance grows up the frame — {num(f)}° works out to{' '}
            {num(d.frame_width / 2 / Math.tan(((Math.min(170, Math.max(5, f)) * Math.PI) / 180) / 2))} px focal length.
            Both are assumptions; the measured width is not.
          </div>
          {!entered.length && (
            <div className="mt-2">
              <Callout tone="neutral">
                Enter at least one measured width above to fit the scale. Nothing is changed until you press a button.
              </Callout>
            </div>
          )}
          <div className="row mt-2">
            <button className="btn" disabled={!entered.length || pending} onClick={() => run(false)}>
              Check the fit
            </button>
            <button
              className="btn btn-primary"
              disabled={!entered.length || pending || busy || !canApply}
              onClick={() => run(true)}
              title={canApply ? '' : "Needs the detection weights and this camera's source video"}
            >
              Apply and re-measure
            </button>
            {pending && <Spinner />}
          </div>
          {!canApply && (
            <div className="field-h mt-1">
              Applying re-measures the clip, which needs the detection weights and this camera's source video on this
              machine.
            </div>
          )}
        </div>
      </div>

      {err && (
        <div className="mt-2">
          <Callout tone="critical" title="Could not fit">
            {err}
          </Callout>
        </div>
      )}
      {fit && (
        <>
          <Section title="Fitted model" note={fit.message} />
          {!fit.ok && <Callout tone="critical">{fit.message}</Callout>}
          {(fit.warnings ?? []).map((w) => (
            <Callout key={w} tone="warning" title="Check this">
              {w}
            </Callout>
          ))}
          {fit.ok && (
            <div className="grid-equal mt-2">
              <div>
                <div className="kv" style={{ marginBottom: 8 }}>
                  <b>Ground scale by image row</b> · focal length {num(fit.focal_px)} px
                </div>
                <DataTable
                  rows={(fit.rows ?? []).map((r) => ({
                    'Image row': r.row,
                    'Road (px)': r.road_px,
                    'Road (m)': r.road_m,
                    'Across (cm/px)': r.lateral_cm_per_px,
                    'Along (cm/px)': r.longitudinal_cm_per_px,
                    'Along ÷ across': r.anisotropy,
                  }))}
                />
                <div className="note mt-1">
                  The last column is why an isotropic scale gives wrong speeds: one pixel up the road covers several times
                  the ground distance of one pixel across it.
                </div>
              </div>
              <div>
                <div className="kv" style={{ marginBottom: 8 }}>
                  <b>What each zone becomes</b>
                </div>
                <DataTable
                  rows={(fit.zone_areas ?? []).map((z) => ({
                    Zone: z.zone,
                    Kind: z.kind,
                    'Area (m²)': z.area_m2,
                    '≈ road length (m)': z.road_length_m,
                  }))}
                />
                <div className="note mt-1">A single camera view rarely covers more than about 150 m of road.</div>
              </div>
            </div>
          )}
        </>
      )}
    </>
  )
}

function UndoPanel({ cameras, onJob }: { cameras: { id: string; name: string }[]; onJob: (j: Job | null) => void }) {
  const qc = useQueryClient()
  const [sel, setSel] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  if (!cameras.length) return null
  const target = sel || cameras[0].id

  const undo = async () => {
    setMsg(null)
    try {
      const r = await del<{ job: Job | null }>(`/calibration/${encodeURIComponent(target)}`)
      onJob(r.job)
      setConfirm(false)
      setMsg('Calibration removed — the camera is back to its seeded estimate.')
      qc.invalidateQueries()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <details className="expander">
      <summary>Undo a calibration</summary>
      <div className="exp-body">
        <div className="controls">
          <Field label="Camera">
            <select value={target} onChange={(e) => setSel(e.target.value)}>
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
          {confirm ? (
            <>
              <button className="btn btn-danger" onClick={undo}>
                Confirm — remove and re-measure
              </button>
              <button className="btn btn-ghost" onClick={() => setConfirm(false)}>
                Cancel
              </button>
            </>
          ) : (
            <button className="btn btn-danger" onClick={() => setConfirm(true)}>
              Remove the stored scale
            </button>
          )}
        </div>
        {msg && <div className="note">{msg}</div>}
      </div>
    </details>
  )
}
