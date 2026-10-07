import { useEffect, useState } from 'react'
import { Callout, Chart, ErrorState, Field, Note, PageSkeleton, Section, Skeleton, Tabs, Tile, Tiles } from '../../components/ui'
import { Media } from '../../components/widgets'
import { qs, useThemedApi } from '../../lib/api'
import { num } from '../../lib/format'
import type { Figure } from '../../lib/types'
import { FluidGate, RecordingSelect, useRecording } from './common'

interface ScenePayload {
  px_per_m: number | null
  residual_px: number | null
  cycle_s: number
  phases: number
  has_video: boolean
  video_mb: number | null
  queue_speed_mps: number
  t_min: number
  t_max: number
  t_default: number
  chart: Figure
}

type Tab = 'zones' | 'instant' | 'heatmap' | 'video'

function useDebounced<T>(v: T, ms: number): T {
  const [d, setD] = useState(v)
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms)
    return () => clearTimeout(t)
  }, [v, ms])
  return d
}

export default function Scene() {
  const r = useRecording()
  return (
    <FluidGate loading={r.loading} available={r.available}>
      <div className="page">
        <div className="controls">
          <RecordingSelect recs={r.recs} rid={r.rid} setRid={r.setRid} />
        </div>
        {r.rid && <SceneView key={r.rid} rid={r.rid} />}
      </div>
    </FluidGate>
  )
}

function SceneView({ rid }: { rid: string }) {
  const q = useThemedApi<ScenePayload>('/fluid/scene', { rid })
  const [t, setT] = useState<number | null>(null)
  const [tab, setTab] = useState<Tab>('zones')
  const tShown = t ?? q.data?.t_default ?? 0
  const tImg = useDebounced(tShown, 250)

  if (q.isLoading) return <PageSkeleton />
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const d = q.data

  return (
    <>
      <Tiles cols={3}>
        <Tile label="Aerial ground resolution" value={num(d.px_per_m, 1)} unit="px/m">
          Fitted from the trajectory file's own pixel and metre columns; median residual {num(d.residual_px, 1)} px.
          Distances are true metres — unlike the Surat cameras, this needs no scale calibration.
        </Tile>
        <Tile label="Signal cycle" value={num(d.cycle_s)} unit="s">
          {d.phases} vehicle phases, recorded per second
        </Tile>
        <Tile label="Drone video" value={d.has_video ? 'available' : 'not downloaded'}>
          {d.has_video
            ? `3840×2160 at 10 fps (${num(d.video_mb)} MB) — the footage the trajectories were extracted from.`
            : 'The derived CSVs are enough for every number here; the video is ~1 GB and optional.'}
        </Tile>
      </Tiles>

      <Section title="Scene" note="where the measurement happens" />
      <div className="controls">
        <Field label="Instant" value={`t = ${num(tShown)} s`} hint="Moves the queue shading and the vehicle snapshot.">
          <input type="range" min={d.t_min} max={d.t_max} step={1} value={tShown} onChange={(e) => setT(+e.target.value)} />
        </Field>
      </div>
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'zones', label: 'Zones' },
          { id: 'instant', label: 'At this instant' },
          { id: 'heatmap', label: 'Where traffic actually goes' },
          { id: 'video', label: 'Drone video' },
        ]}
      />
      <div className="grid-2">
        <div>
          {tab === 'zones' && (
            <Media
              src={`/api/fluid/image/zones${qs({ rid, t: tImg, w: 1600 })}`}
              alt="Approach and receiving zones"
              caption="Approach zones shaded by the queue standing on them at this instant; receiving links in grey; the junction box outlined."
            />
          )}
          {tab === 'instant' && <InstantImage rid={rid} t={tImg} />}
          {tab === 'heatmap' && (
            <Media
              src={`/api/fluid/image/heatmap${qs({ rid, w: 1600 })}`}
              alt="Trajectory heatmap"
              caption="Every non-pedestrian trajectory point, accumulated over the whole recording."
            />
          )}
          {tab === 'video' &&
            (d.has_video ? (
              <video controls preload="metadata" src={`/api/fluid/video${qs({ rid })}`} />
            ) : (
              <Callout tone="neutral" title="Video not present">
                The drone video for this recording was not downloaded. Every figure in this track comes from the derived
                trajectory and signal CSVs; the videos are about 1 GB each and optional.
              </Callout>
            ))}
        </div>
        <Note>
          {tab === 'zones' && (
            <>
              <p>
                This is the geometry behind every pressure score. A movement's queue <b>x<sub>l</sub></b> is counted on the
                coloured approach it entered from; its downstream term <b>x<sub>m</sub></b> on the grey link it will leave
                by. Vehicles inside the outlined box are in the junction and belong to neither.
              </p>
              <p>
                <b>This is exactly the picture the Surat cameras cannot produce.</b> Each sees one approach; none sees the
                link that approach discharges into — which is why max-pressure is validated here and Webster's runs
                there.
              </p>
            </>
          )}
          {tab === 'instant' && (
            <p>
              A vehicle counts as queued below <b>{d.queue_speed_mps} m/s</b> — above walking pace, so a creeping queue
              still counts, but clear of tracking jitter at rest. Count the red dots on any approach and you have
              reconstructed that movement's <b>x<sub>l</sub></b> by hand; the table on <b>Phase pressure</b> is the same
              figures in PCU.
            </p>
          )}
          {tab === 'heatmap' && (
            <p>
              The same empirical check the Surat zones were given: a corridor drawn from geometry can look right and still
              sit off the path traffic really takes. Here the bright lanes confirm the arm assignment — traffic runs along
              the arms and turns through the box, with approach and receiving carriageways clearly separated, which is
              what makes the downstream term measurable at all.
            </p>
          )}
          {tab === 'video' && (
            <p>
              The trajectories were extracted from this footage by the dataset's authors, which is why the coordinates are
              already orthorectified metres and need no calibration step.
            </p>
          )}
        </Note>
      </div>

      <Section title="Vehicle mix" note="what is on these roads" />
      <div className="grid-2">
        <Chart figure={d.chart} label="Vehicle class share" />
        <Note>
          <p>
            Chinese urban traffic: cars and mopeds dominate, with tricycles in place of the auto-rickshaw. PCU factors
            follow the same IRC:106-1990 basis the Surat side uses — moped 0.5, car and van 1.0, tricycle 1.2, bus and
            truck 2.2 — so a queue in PCU means the same thing in both tracks.
          </p>
          <p>
            This fleet is <b>not</b> Surat's, and no claim here transfers to Surat traffic. What transfers is that the
            controller implementation is correct on data containing the terms it needs.
          </p>
        </Note>
      </div>
    </>
  )
}

/** The instant snapshot, plus the vehicle counts the server sends as headers. */
function InstantImage({ rid, t }: { rid: string; t: number }) {
  // Each result is stored with the request it answers; a result for an older
  // instant is never shown as the current one, and no effect has to clear it.
  const key = `${rid}|${t}`
  const [res, setRes] = useState<{ key: string; url?: string; all?: number; queued?: number; err?: string } | null>(null)

  useEffect(() => {
    let url: string | null = null
    let alive = true
    fetch(`/api/fluid/image/instant${qs({ rid, t, w: 1600 })}`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`)
        const all = Number(r.headers.get('X-Vehicles') || 0)
        const queued = Number(r.headers.get('X-Queued') || 0)
        url = URL.createObjectURL(await r.blob())
        if (alive) setRes({ key: `${rid}|${t}`, url, all, queued })
      })
      .catch((e) => {
        if (alive) setRes({ key: `${rid}|${t}`, err: String(e.message || e) })
      })
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [rid, t])

  const state = res && res.key === key ? res : null
  if (state?.err) return <Callout tone="warning">{state.err}</Callout>
  if (!state || !state.url) return <Skeleton height={360} />
  return (
    <Media
      src={state.url}
      alt={`Vehicles at t = ${t} s`}
      caption={`t = ${num(t)} s — ${state.all} vehicles, ${state.queued} queued (red) and ${(state.all ?? 0) - (state.queued ?? 0)} moving (green)`}
    />
  )
}
