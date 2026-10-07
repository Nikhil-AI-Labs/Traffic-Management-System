import { useQueryClient } from '@tanstack/react-query'
import { Download, Upload as UploadIcon, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Callout, Chart, Chip, ErrorState, Field, Hero, LosChip, ScaleBanner, Section, Skeleton, Tile, Tiles } from '../components/ui'
import { AlertsSection, DataTable, JobProgress, Media, useJob } from '../components/widgets'
import { ApiError, del, post, uploadFile, useApi, useThemedApi } from '../lib/api'
import { LOS_COLOR, int, num, pct, type Los } from '../lib/format'
import type { Alert, Figure, Job, Meta, Row, TrendMetric, TrendPayload } from '../lib/types'

interface UploadView {
  id: string
  name: string
  demo: boolean
  media: { kind: 'video' | 'image'; ok: boolean; error: string; width: number; height: number; fps: number; duration_s: number; label: string; size_mb: number }
  roi: { method: string; note: string; coverage: number; anchors: number; zones: number; mode: string; n_lanes: number } | null
  analysed: boolean
}

const ACCEPT = '.mp4,.avi,.mov,.mkv,.m4v,.mpg,.mpeg,.webm,.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff'

export default function Analyse() {
  const meta = useApi<Meta>('/meta', { staleTime: 15_000 })
  const [upload, setUpload] = useState<UploadView | null>(null)
  const [roiJob, setRoiJob] = useState<string | null>(null)
  const [runJob, setRunJob] = useState<string | null>(null)
  const [stamp, setStamp] = useState(0)

  const reset = async () => {
    if (upload) del(`/uploads/${upload.id}`).catch(() => {})
    setUpload(null)
    setRoiJob(null)
    setRunJob(null)
  }

  if (meta.data && !meta.data.detector.ready)
    return (
      <Callout tone="warning" title="Detection model not downloaded">
        <p>
          The detection weights are not on this machine, so nothing can be detected until they are downloaded. One
          command, about 55 MB: <code>python scripts/download_models.py</code>
        </p>
        <p>
          Expected at <code>{meta.data.detector.path}</code>. Everything else in this dashboard reads from metrics already
          on disk and works without them.
        </p>
      </Callout>
    )

  return (
    <div className="page">
      <Callout title="Analyse footage the system has never seen">
        Drop in a clip or a still from any camera — it does not have to be one of the seven. The pipeline finds the road
        from where vehicles actually travel, measures it the same way it measures the dataset cameras, and gives you a
        report you can download.
      </Callout>

      <Section title="1 · Source" />
      {upload ? (
        <div className="row card card-pad">
          <Chip color="var(--s1)">{upload.media.kind.toUpperCase()}</Chip>
          <span className="kv">
            <b>{upload.name}</b> — {upload.media.label}, {num(upload.media.size_mb, 1)} MB
          </span>
          <span className="spacer" />
          <button className="btn btn-sm btn-ghost" onClick={reset}>
            <X size={15} /> Clear
          </button>
        </div>
      ) : (
        <SourcePicker meta={meta.data} onReady={(u) => setUpload(u)} />
      )}

      {upload && (
        <SceneStep
          key={upload.id}
          upload={upload}
          roiJob={roiJob}
          setRoiJob={setRoiJob}
          onChange={(u) => {
            setUpload(u)
            setRunJob(null)
            setStamp((s) => s + 1)
          }}
        />
      )}

      {upload?.roi && (
        <RunStep
          upload={upload}
          runJob={runJob}
          setRunJob={setRunJob}
          stamp={stamp}
          onDone={(u) => setUpload(u)}
        />
      )}
    </div>
  )
}

function SourcePicker({ meta, onReady }: { meta?: Meta; onReady: (u: UploadView) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [demo, setDemo] = useState('')
  const demos = meta?.cameras.filter((c) => c.dataset && c.has_video) ?? []

  const send = async (file: File) => {
    setErr(null)
    setProgress(0)
    try {
      onReady(await uploadFile<UploadView>(file, setProgress))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setProgress(null)
    }
  }

  const loadDemo = async () => {
    setErr(null)
    try {
      onReady(await post<UploadView>('/uploads/demo', { camera: demo || demos[0]?.id }))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="grid-2">
      <div>
        <div
          className={`dropzone ${drag ? 'drag' : ''}`}
          role="button"
          tabIndex={0}
          onClick={() => input.current?.click()}
          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault()
            setDrag(true)
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDrag(false)
            const f = e.dataTransfer.files[0]
            if (f) send(f)
          }}
        >
          <UploadIcon size={26} style={{ margin: '0 auto 8px', color: 'var(--accent)' }} />
          <b>Drop a video or image here</b>, or click to choose
          <div className="small muted mt-1">MP4 · AVI · MOV · MKV · JPG · PNG — up to 1 GB</div>
          <input
            ref={input}
            type="file"
            accept={ACCEPT}
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) send(f)
              e.target.value = ''
            }}
          />
        </div>
        {progress !== null && (
          <div className="mt-2">
            <div className="small muted">Uploading… {pct(progress * 100)}</div>
            <div className="progress mt-1">
              <i style={{ width: `${progress * 100}%` }} />
            </div>
          </div>
        )}
        {err && (
          <div className="mt-2">
            <Callout tone="critical" title="Could not use this file">
              {err}
            </Callout>
          </div>
        )}
        <div className="mt-2" />
        <Tiles cols={2}>
          <Tile label="From a video">
            Per-zone <b>PCU density</b>, <b>speed</b>, <b>queue</b> and <b>flow</b>; level of service second by second;
            saturation flags; sustained <b>bottleneck alerts</b>; a 60-second <b>forecast</b>; and a downloadable report.
          </Tile>
          <Tile label="From a still">
            Vehicle count and class mix, <b>PCU load</b>, zone occupancy and level of service at that instant. Speed,
            flow, queue and forecast need two moments in time, so they are reported as unavailable rather than estimated.
          </Tile>
        </Tiles>
      </div>
      <Callout title="No file to hand?">
        <p>
          Run one of the dataset clips through the same path a stranger's upload takes — zones found automatically from
          vehicle movement, not from the hand-traced polygons the Site detail view uses.
        </p>
        {demos.length ? (
          <div className="row mt-2">
            <select value={demo || demos[0].id} onChange={(e) => setDemo(e.target.value)} style={{ flex: 1, minWidth: 180 }}>
              {demos.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <button className="btn" onClick={loadDemo}>
              Load this clip
            </button>
          </div>
        ) : (
          <p className="small">The dataset videos are not on this machine.</p>
        )}
      </Callout>
    </div>
  )
}

function SceneStep({
  upload,
  roiJob,
  setRoiJob,
  onChange,
}: {
  upload: UploadView
  roiJob: string | null
  setRoiJob: (id: string | null) => void
  onChange: (u: UploadView) => void
}) {
  const [lanes, setLanes] = useState(0)
  const [method, setMethod] = useState<'traffic' | 'whole'>(upload.media.kind === 'image' ? 'whole' : 'traffic')
  const [err, setErr] = useState<string | null>(null)
  const job = useJob<UploadView>(roiJob)
  const handled = useRef<string | null>(null)

  useEffect(() => {
    if (job.data?.status === 'done' && job.data.result && handled.current !== job.data.id) {
      handled.current = job.data.id
      onChange(job.data.result)
      setRoiJob(null)
    }
  }, [job.data, onChange, setRoiJob])

  const find = async () => {
    setErr(null)
    try {
      const r = await post<{ upload: UploadView; job: Job | null }>(`/uploads/${upload.id}/roi`, { method, n_lanes: lanes })
      if (r.job) setRoiJob(r.job.id)
      else onChange(r.upload)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const running = job.data && (job.data.status === 'running' || job.data.status === 'queued')
  return (
    <>
      <Section title="2 · Scene" note="where is the road, and is it lane-marked?" />
      <div className="controls">
        <Field label="Marked lanes on this road" value={lanes === 0 ? 'area mode' : `${lanes} lanes`} hint="0 treats the road as one shared space — the honest default for footage we know nothing about.">
          <input type="range" min={0} max={8} step={1} value={lanes} onChange={(e) => setLanes(+e.target.value)} />
        </Field>
        {upload.media.kind === 'video' && (
          <Field label="Find the road by" hint="Samples the clip and accumulates vehicles' ground-contact points.">
            <select value={method} onChange={(e) => setMethod(e.target.value as 'traffic' | 'whole')}>
              <option value="traffic">Where vehicles travel</option>
              <option value="whole">Whole frame</option>
            </select>
          </Field>
        )}
        <button className="btn btn-primary" onClick={find} disabled={!!running}>
          Find the road
        </button>
      </div>
      {lanes > 0 && (method === 'whole' || upload.media.kind === 'image') && (
        <div className="mt-1">
          <Callout tone="warning">
            Lanes are split along the corridor that vehicles were seen to use, so they need the road found{' '}
            <b>from traffic</b>. With the whole frame there is no corridor to split, and the frame is analysed as one
            shared zone in area mode.
          </Callout>
        </div>
      )}
      {err && <Callout tone="critical">{err}</Callout>}
      {running && <JobProgress job={job.data} />}
      {job.data?.status === 'error' && <Callout tone="critical" title="Could not find the road">{job.data.error}</Callout>}
      {upload.roi && !running && (
        <div className="grid-2">
          <Media
            src={`/api/uploads/${upload.id}/zones.jpg?w=1600&k=${upload.roi.method}-${upload.roi.n_lanes}-${upload.roi.zones}`}
            alt="Zones the analysis will use"
            caption="Zones the analysis will use"
          />
          <div className="stack">
            <Tiles cols={2}>
              <Tile label="Road found by" value={upload.roi.method.replace('-', ' ')}>
                {upload.roi.note}
              </Tile>
              <Tile label="Coverage" value={num(upload.roi.coverage * 100)} unit="% of frame" meter={[upload.roi.coverage, 'var(--s1)']}>
                {upload.roi.zones} zone(s), {upload.roi.mode} mode
              </Tile>
            </Tiles>
            {upload.roi.method === 'whole-frame' && (
              <Callout tone="warning" title="Read with care">
                Occupancy is normalised against absolute bounds measured on carriageway. Applied to a whole frame that
                includes sky and buildings it stays internally consistent but is no longer comparable with the dataset
                cameras.
              </Callout>
            )}
          </div>
        </div>
      )}
    </>
  )
}

function RunStep({
  upload,
  runJob,
  setRunJob,
  stamp,
  onDone,
}: {
  upload: UploadView
  runJob: string | null
  setRunJob: (id: string | null) => void
  stamp: number
  onDone: (u: UploadView) => void
}) {
  const isVideo = upload.media.kind === 'video'
  const [stride, setStride] = useState(3)
  const [seconds, setSeconds] = useState(Math.round(Math.min(120, Math.max(10, upload.media.duration_s || 60))))
  const [err, setErr] = useState<string | null>(null)
  const job = useJob<UploadView>(runJob)
  const finished = job.data?.status === 'done'
  // Hand each finished job's result up exactly once. `onDone` is a new
  // function on every parent render, so keying the effect on it alone would
  // re-fire after the parent updated and loop.
  const handled = useRef<string | null>(null)

  useEffect(() => {
    if (finished && job.data?.result && handled.current !== job.data.id) {
      handled.current = job.data.id
      onDone(job.data.result)
    }
  }, [finished, job.data, onDone])

  const start = async () => {
    setErr(null)
    try {
      const r = await post<{ job: Job }>(`/uploads/${upload.id}/analyze`, { stride, seconds })
      setRunJob(r.job.id)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const running = job.data && (job.data.status === 'running' || job.data.status === 'queued')
  const estS = isVideo ? (Math.min(seconds, upload.media.duration_s || seconds) * (upload.media.fps || 25)) / stride / 3 : 0
  return (
    <>
      <Section title="3 · Analyse" />
      <div className="controls">
        {isVideo && (
          <>
            <Field label="Sampling" hint="Every 3rd frame of 25 fps footage is ~8 measurements a second — ample for density and queues.">
              <select value={stride} onChange={(e) => setStride(+e.target.value)}>
                {[2, 3, 4, 6, 8].map((s) => (
                  <option key={s} value={s}>
                    every {s}
                    {s === 2 ? 'nd' : s === 3 ? 'rd' : 'th'} frame
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Seconds to analyse" value={`${seconds} s`}>
              <input
                type="range"
                min={5}
                max={Math.max(10, Math.min(1800, Math.ceil(upload.media.duration_s || 120)))}
                step={5}
                value={seconds}
                onChange={(e) => setSeconds(+e.target.value)}
              />
            </Field>
          </>
        )}
        <button className="btn btn-primary" onClick={start} disabled={!!running}>
          {isVideo ? 'Run the full analysis' : 'Analyse this frame'}
        </button>
      </div>
      {isVideo && !running && !finished && (
        <div className="note">
          Roughly <b>{Math.max(5, Math.round(estS / 5) * 5)} s</b> of processing on a CPU-only machine (much faster with a
          CUDA GPU).
        </div>
      )}
      {err && <Callout tone="critical">{err}</Callout>}
      {running && <JobProgress job={job.data} />}
      {job.data?.status === 'error' && <Callout tone="critical" title="Analysis failed">{job.data.error}</Callout>}
      {upload.analysed && <Report key={`${upload.id}-${stamp}-${job.data?.id ?? ''}`} uploadId={upload.id} />}
    </>
  )
}

interface VideoReport {
  kind: 'video'
  empty: boolean
  scale_verified: boolean
  windows: number
  seconds: number
  frames: number
  elapsed_s: number
  over_capacity_pct: number
  worst: { zone: string; ci: number; t: number }
  mean_congestion: number
  los: Los
  zones: number
  mode: string
  vehicles: number
  saturated: number
  saturated_pct: number
  groups: { group: string; detections: number }[]
  has_preview: boolean
  trend_metrics: TrendMetric[]
  charts: { zone_los: Figure; count_vs_occupancy: Figure; forecast: Figure | null }
  alerts: Alert[]
  counts: Record<string, number>
}

interface ImageReport {
  kind: 'image'
  vehicles: number
  pcu: number
  zones: Row[]
  worst: { zone: string; ci: number; los: Los } | null
  mode: string
  classes: Row[]
  not_measurable: string[]
}

function Report({ uploadId }: { uploadId: string }) {
  const qc = useQueryClient()
  const q = useThemedApi<VideoReport | ImageReport>(`/uploads/${uploadId}/report`)
  const [metric, setMetric] = useState('congestion_index')
  const trend = useThemedApi<TrendPayload>(`/uploads/${uploadId}/trend`, { metric }, { enabled: q.data?.kind === 'video' })
  const [kept, setKept] = useState<string | null>(null)

  if (q.isLoading) return <Skeleton height={300} style={{ marginTop: 24 }} />
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const r = q.data

  if (r.kind === 'image') {
    return (
      <>
        <Section title="Report" note="single frame" />
        <div className="grid-hero">
          <Hero label="Occupancy at this instant" value={r.worst ? num(r.worst.ci, 2) : '—'}>
            {r.worst && (
              <>
                Busiest zone: <b>{r.worst.zone}</b> — {r.worst.los}.
              </>
            )}
          </Hero>
          <Tiles cols={3}>
            <Tile label="Vehicles" value={r.vehicles}>
              detected in this frame
            </Tile>
            <Tile label="Traffic load" value={num(r.pcu, 1)} unit="PCU">
              Passenger Car Units, IRC:106-1990
            </Tile>
            <Tile label="Zones" value={r.zones.length}>
              {r.mode} mode
            </Tile>
          </Tiles>
        </div>
        <div className="grid-2 mt-3">
          <Media src={`/api/uploads/${uploadId}/preview.jpg?w=1600`} alt="Detections on the frame" caption="Zones shaded, detections boxed" />
          <Callout tone="warning" title="What one frame cannot tell you">
            <p>
              A still frame fixes an <b>instant</b>, not a <b>rate</b>. Everything below needs two moments in time, so it
              is reported as unavailable rather than estimated:
            </p>
            <p>{r.not_measurable.map((x) => <b key={x}>{x} · </b>)}</p>
            <p>Upload a video clip instead and all of it becomes measurable.</p>
          </Callout>
        </div>
        <Section title="By zone" />
        <DataTable rows={r.zones} />
        <Section title="Classes detected" />
        <DataTable rows={r.classes} />
      </>
    )
  }

  if (r.empty) return <Callout tone="critical">No measurement windows were produced. The clip may be shorter than one window.</Callout>

  const keep = async () => {
    try {
      const res = await post<{ file: string }>(`/uploads/${uploadId}/persist`)
      setKept(res.file)
      qc.invalidateQueries()
    } catch (e) {
      setKept(e instanceof ApiError ? `Error: ${e.message}` : String(e))
    }
  }

  return (
    <>
      <Section
        title="Report"
        note={`${r.windows} windows · ${num(r.seconds)} s of footage · ${int(r.frames)} frames in ${num(r.elapsed_s)} s`}
      />
      <div className="grid-hero">
        <Hero label="Time at or beyond capacity" value={num(r.over_capacity_pct)} unit="%">
          Worst moment: <b>{r.worst.zone}</b> at an index of {num(r.worst.ci, 2)} ({num(r.worst.t)} s into the clip).
        </Hero>
        <Tiles>
          <Tile label="Mean congestion" value={num(r.mean_congestion, 2)} meter={[r.mean_congestion, LOS_COLOR[r.los]]}>
            <LosChip los={r.los} /> overall
          </Tile>
          <Tile label="Zones" value={r.zones}>
            {r.mode} mode
          </Tile>
          <Tile label="Vehicles seen" value={int(r.vehicles)}>
            detections across all processed frames
          </Tile>
          <Tile label="Saturated" value={r.saturated} meter={[r.saturated_pct / 100, 'var(--critical)']}>
            {pct(r.saturated_pct)} of windows where counting breaks down
          </Tile>
        </Tiles>
      </div>

      {r.has_preview && (
        <>
          <Section title="Detection" />
          <div className="grid-2">
            <Media src={`/api/uploads/${uploadId}/preview.jpg?w=1600`} alt="A processed frame" caption="A processed frame — zones shaded, detections boxed" />
            <div>
              <div className="kv" style={{ marginBottom: 8 }}>
                <b>What was on the road</b>
              </div>
              <DataTable rows={r.groups.map((g) => ({ 'Class group': g.group, Detections: g.detections }))} />
            </div>
          </div>
        </>
      )}

      <Section title="Level of service over time" />
      <Chart figure={r.charts.zone_los} label="Level of service per zone over time" />

      <Section title="Trend" />
      <div className="controls">
        <Field label="Metric">
          <select value={metric} onChange={(e) => setMetric(e.target.value)}>
            {r.trend_metrics.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {trend.data ? <Chart figure={trend.data.chart} label="Trend by zone" /> : <Skeleton height={300} />}

      <Section title="Counting against occupancy" note="does detection hold up here?" />
      <div className="grid-2">
        <Chart figure={r.charts.count_vs_occupancy} label="Detections and occupancy over time" />
        <div className="note">
          Two panels, one time axis — never two y-scales. This clip spends <b>{pct(r.saturated_pct)}</b> of its windows
          saturated: packed densely enough that vehicles stop being individually separable. Where the top panel sags
          while the bottom stays high, the detector is losing vehicles to occlusion and occupancy is the measure that
          still holds.
        </div>
      </div>

      <Section title="Bottlenecks" note={r.alerts.length ? `${r.alerts.length} raised` : 'none raised'} />
      {r.alerts.length ? (
        <AlertsSection alerts={r.alerts} showSite={false} />
      ) : (
        <Callout tone="good">No sustained bottleneck pattern in this footage.</Callout>
      )}

      {r.charts.forecast && (
        <>
          <Section title="Outlook" note="60 seconds ahead" />
          <Chart figure={r.charts.forecast} label="Forecast per zone" />
        </>
      )}

      <Section title="Take it away" />
      <div className="row">
        <a className="btn" href={`/api/uploads/${uploadId}/metrics.csv`}>
          <Download size={16} /> Metrics CSV
        </a>
        <a className="btn" href={`/api/uploads/${uploadId}/report.html`}>
          <Download size={16} /> Full report (HTML)
        </a>
        <button className="btn" onClick={keep} disabled={!!kept && !kept.startsWith('Error')}>
          Keep alongside the dataset cameras
        </button>
        {kept && <span className="small muted">{kept.startsWith('Error') ? kept : `Saved as ${kept} — it now appears in the Network view.`}</span>}
      </div>
      {!r.scale_verified && <ScaleBanner />}
    </>
  )
}
