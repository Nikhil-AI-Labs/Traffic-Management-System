// The control room: every camera at once, as it happens.
//
// One server-sent event stream carries the whole picture once a second -
// each camera's zones and level of service, the alerts in force, rising
// risks, and every alert raised or cleared since the last tick. The focused
// camera's video is a separate MJPEG stream. Both come from src/live.py.

import { useMutation } from '@tanstack/react-query'
import {
  Bell,
  BellOff,
  CircleStop,
  Cpu,
  Pause,
  Play,
  Radio,
  RotateCcw,
  ScanEye,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useSearchParams } from 'react-router-dom'
import CameraMap, { type MapPoint } from '../components/CameraMap'
import Sparkline from '../components/Sparkline'
import { Callout, Chip, ErrorState, LosChip, Meter, PageSkeleton, Section, Segmented, Tile, Tiles } from '../components/ui'
import { AlertCard, DataTable } from '../components/widgets'
import { del, post, useApi } from '../lib/api'
import { requestSignIn, useOperator } from '../lib/auth'
import { LOS_COLOR, int, num } from '../lib/format'
import type {
  Alert,
  IncidentsPayload,
  LiveCamera,
  LiveEvent,
  LiveState,
  LocationsPayload,
  Meta,
} from '../lib/types'

// ------------------------------------------------------------------ stream

function useLiveStream(onEvents: (evs: LiveEvent[]) => void) {
  const [state, setState] = useState<LiveState | null>(null)
  const [log, setLog] = useState<LiveEvent[]>([])
  const [conn, setConn] = useState<'connecting' | 'open' | 'lost'>('connecting')
  const handler = useRef(onEvents)
  useEffect(() => {
    handler.current = onEvents
  }, [onEvents])

  useEffect(() => {
    const es = new EventSource('/api/live/events')
    es.addEventListener('tick', (e) => {
      const d = JSON.parse((e as MessageEvent).data) as { state: LiveState; events: LiveEvent[] }
      setState(d.state)
      setConn('open')
      if (d.events.length) {
        setLog((prev) => [...[...d.events].reverse(), ...prev].slice(0, 80))
        handler.current(d.events)
      }
    })
    // EventSource reconnects by itself, resuming from the last event id.
    es.onerror = () => setConn(es.readyState === EventSource.CLOSED ? 'lost' : 'connecting')
    return () => es.close()
  }, [])
  return { state, log, conn }
}

function usePageVisible() {
  const [v, setV] = useState(() => typeof document === 'undefined' || !document.hidden)
  useEffect(() => {
    const on = () => setV(!document.hidden)
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  }, [])
  return v
}

// --------------------------------------------------------------- alerting

let audio: AudioContext | null = null

function chime(critical: boolean) {
  try {
    audio ??= new AudioContext()
    const t = audio.currentTime
    for (const [i, f] of (critical ? [880, 660, 880] : [660]).entries()) {
      const o = audio.createOscillator()
      const g = audio.createGain()
      o.frequency.value = f
      g.gain.setValueAtTime(0.0001, t + i * 0.18)
      g.gain.exponentialRampToValueAtTime(0.18, t + i * 0.18 + 0.02)
      g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.18 + 0.16)
      o.connect(g).connect(audio.destination)
      o.start(t + i * 0.18)
      o.stop(t + i * 0.18 + 0.17)
    }
  } catch {
    /* no audio device */
  }
}

// ------------------------------------------------------------------ pieces

function CameraRow({ c, active, alerts, onClick }: { c: LiveCamera; active: boolean; alerts: number; onClick: () => void }) {
  return (
    <button className="cam-row" aria-pressed={active} onClick={onClick}>
      <span className="dot" style={{ background: LOS_COLOR[c.los], width: 12, height: 12 }} />
      <span style={{ minWidth: 0 }}>
        <div className="cr-n">
          {c.short} {c.source === 'detect' && <Chip color="var(--critical)">LIVE</Chip>}
        </div>
        <div className="cr-s">
          {c.los} · {c.mode}
          {alerts > 0 && ` · ${alerts} alert${alerts > 1 ? 's' : ''}`}
        </div>
      </span>
      <Sparkline values={c.spark} width={96} height={28} />
      <span className="cr-v">{num(c.ci, 2)}</span>
    </button>
  )
}

function eventLine(e: LiveEvent): string {
  switch (e.type) {
    case 'alert':
      return `${e.alert.severity.toUpperCase()} raised — ${e.alert.camera_short}, ${e.alert.zone_label}: ${e.alert.headline}`
    case 'clear':
      return `Cleared — ${e.camera_short}, ${e.zone_label} (${e.kind.replace(/_/g, ' ')})`
    case 'detect':
      return `Live detection on ${e.session.camera_short}: ${e.session.status} — ${e.session.message}`
    case 'control':
      return e.playing ? `Replay running at ${e.speed}×` : 'Replay paused'
  }
}

// -------------------------------------------------------------------- page

export default function Live() {
  const [params, setParams] = useSearchParams()
  const op = useOperator()
  const meta = useApi<Meta>('/meta', { staleTime: 15_000 })
  const locs = useApi<LocationsPayload>('/locations', { staleTime: 60_000 })
  const statuses = useApi<IncidentsPayload>('/incidents?source=live', { refetchInterval: 4000 })
  const visible = usePageVisible()
  const [sound, setSound] = useState(false)
  const [desk, setDesk] = useState(false)
  const [streamKey, setStreamKey] = useState(0)

  const onEvents = useCallback(
    (evs: LiveEvent[]) => {
      for (const e of evs) {
        if (e.type !== 'alert') continue
        const crit = e.alert.severity === 'critical'
        if (sound && (crit || e.alert.severity === 'warning')) chime(crit)
        if (desk && crit && 'Notification' in window && Notification.permission === 'granted') {
          new Notification(`Critical — ${e.alert.camera_short}`, { body: e.alert.headline, tag: e.alert.id })
        }
      }
    },
    [sound, desk],
  )
  const { state, log, conn } = useLiveStream(onEvents)

  const control = useMutation({ mutationFn: (body: object) => post('/live/control', body) })
  const detect = useMutation({
    mutationFn: (cam: string | null) => (cam ? post('/live/detect', { camera: cam }) : del('/live/detect')),
  })
  const actionError = control.error ?? detect.error

  const cams = useMemo(() => state?.cameras ?? [], [state])
  const running = state?.detect && (state.detect.status === 'running' || state.detect.status === 'starting') ? state.detect : null
  const fallback =
    running?.camera_id ??
    cams.find((c) => c.has_annotated)?.id ??
    cams.find((c) => c.mode === 'junction')?.id ??
    cams[0]?.id
  const focusId = params.get('cam') && cams.some((c) => c.id === params.get('cam')) ? (params.get('cam') as string) : fallback
  const focus = cams.find((c) => c.id === focusId)
  const setFocus = (id: string) => setParams({ cam: id }, { replace: true })

  const statusOf = useMemo(() => new Map((statuses.data?.items ?? []).map((i) => [i.id, i.status])), [statuses.data])
  const active: Alert[] = useMemo(
    () => (state?.active ?? []).map((a) => ({ ...a, status: statusOf.get(a.id as string) ?? 'open', actionable: true })),
    [state, statusOf],
  )
  const alertsBy = useMemo(() => {
    const m = new Map<string, Alert[]>()
    for (const a of active) m.set(a.camera_id, [...(m.get(a.camera_id) ?? []), a])
    return m
  }, [active])

  const points: MapPoint[] = useMemo(() => {
    const live = new Map(cams.map((c) => [c.id, c]))
    return (locs.data?.cameras ?? [])
      .filter((l) => l.lat !== null && l.lon !== null && live.has(l.id))
      .map((l) => {
        const c = live.get(l.id) as LiveCamera
        const crit = (alertsBy.get(l.id) ?? []).some((a) => a.severity === 'critical' && a.status === 'open')
        return {
          id: l.id,
          label: l.short,
          lat: l.lat as number,
          lon: l.lon as number,
          color: LOS_COLOR[c.los],
          verified: l.verified,
          pulse: crit,
          detail: `${c.name} — ${c.los} (${num(c.ci, 2)})${l.verified ? '' : ' · approximate position'}`,
        }
      })
  }, [locs.data, cams, alertsBy])

  if (!state) {
    if (conn === 'lost') return <ErrorState error={new Error('The live feed could not be opened. Is the server running?')} />
    return <PageSkeleton />
  }

  const replaying = state.playing
  const streamUrl = focus
    ? `/api/live/stream.mjpg?cam=${encodeURIComponent(focus.id)}&src=${focus.source === 'detect' ? running?.id : 'replay'}&k=${streamKey}`
    : null
  const needOp = meta.data?.auth.required && !op.signedIn
  const act = (fn: () => void) => () => (needOp ? requestSignIn() : fn())
  const worst = [...cams].sort((a, b) => b.ci - a.ci)[0]
  const openCrit = active.filter((a) => a.severity === 'critical' && a.status === 'open').length

  return (
    <div className="page">
      {/* ------------------------------------------------ status + controls */}
      <div className="card card-pad">
        <div className="live-bar">
          <span
            className={`live-dot ${conn === 'open' && (replaying || running) ? 'on' : ''}`}
            style={{ '--c': conn !== 'open' ? 'var(--warning)' : running ? 'var(--critical)' : 'var(--good)' } as CSSProperties}
          />
          <b>
            {conn !== 'open'
              ? 'Reconnecting…'
              : running
                ? `LIVE DETECTION on ${running.camera_short}`
                : replaying
                  ? `REPLAY ${state.speed}×`
                  : 'REPLAY paused'}
          </b>
          <span
            className="small muted live-desc"
            title="Recorded footage and measurements, played back on one clock as if they were arriving now"
          >
            {running
              ? `${running.model || 'detector'} · ${num(running.fps, 1)} fps · ${int(running.frames)} frames · other cameras replaying`
              : 'Recorded footage and measurements, played back on one clock as if they were arriving now'}
          </span>
          <span className="live-ctl">
          <button
            className="btn btn-sm"
            onClick={act(() => control.mutate({ playing: !state.playing }))}
            title={state.playing ? 'Pause the replay' : 'Resume the replay'}
          >
            {state.playing ? <Pause size={15} /> : <Play size={15} />}
            {state.playing ? 'Pause' : 'Play'}
          </button>
          <Segmented
            label="Replay speed"
            value={state.speed}
            onChange={(v) => (needOp ? requestSignIn() : control.mutate({ speed: v }))}
            options={state.speeds.map((s) => ({ value: s, label: `${s}×` }))}
          />
          <button className="btn btn-sm btn-ghost" onClick={act(() => control.mutate({ restart: true }))} title="Restart every clip">
            <RotateCcw size={15} />
          </button>
          <button
            className="btn btn-sm btn-ghost"
            aria-pressed={sound}
            onClick={() => {
              setSound((s) => !s)
              if (!sound) chime(false)
            }}
            title={sound ? 'Mute alert chime' : 'Chime on new alerts'}
          >
            {sound ? <Volume2 size={15} /> : <VolumeX size={15} />}
          </button>
          {'Notification' in window && (
            <button
              className="btn btn-sm btn-ghost"
              aria-pressed={desk}
              onClick={async () => {
                if (desk) return setDesk(false)
                const p = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission
                setDesk(p === 'granted')
              }}
              title={desk ? 'Stop desktop notifications' : 'Desktop notification for critical alerts'}
            >
              {desk ? <Bell size={15} /> : <BellOff size={15} />}
            </button>
          )}
          </span>
        </div>
        {actionError && (
          <div className="small mt-1" style={{ color: 'var(--critical)' }}>
            {actionError.message}
          </div>
        )}
      </div>

      <div className="mt-2">
        <Tiles>
          <Tile label="Cameras live" value={cams.length}>
            {running ? `1 detecting, ${cams.length - 1} replaying` : 'all replaying recorded windows'}
          </Tile>
          <Tile label="Alerts in force" value={active.length}>
            {openCrit ? `${openCrit} critical, unacknowledged` : 'no unacknowledged critical alerts'}
          </Tile>
          <Tile label="Worst right now" value={worst ? worst.short : '—'} meter={worst ? [worst.ci, LOS_COLOR[worst.los]] : undefined}>
            {worst ? `${worst.los} · congestion ${num(worst.ci, 2)}` : ''}
          </Tile>
          <Tile label="Rising risks" value={state.risks.length}>
            zones forecast to reach Heavy within a minute
          </Tile>
        </Tiles>
      </div>

      {/* ---------------------------------------------- video + network */}
      <div className="grid-live mt-3">
        <div className="stack">
          {focus && (
            <div className="card card-pad">
              <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
                <div>
                  <div className="site-n" style={{ fontSize: '1rem' }}>
                    {focus.name}
                  </div>
                  <div className="site-m">
                    {focus.source === 'detect'
                      ? `Real-time detection · t = ${num(focus.clip_t, 1)} s`
                      : `Replay · clip ${num(focus.clip_t, 1)} / ${num(focus.duration, 0)} s · lap ${focus.lap + 1}`}
                  </div>
                </div>
                <div className="row">
                  {focus.source === 'detect' ? (
                    <Chip color="var(--critical)">LIVE</Chip>
                  ) : (
                    <Chip color="var(--s1)">REPLAY</Chip>
                  )}
                  <LosChip los={focus.los} />
                  {focus.can_detect && meta.data?.detector.ready && (
                    running?.camera_id === focus.id ? (
                      <button className="btn btn-sm btn-danger" onClick={act(() => detect.mutate(null))}>
                        <CircleStop size={15} /> Stop detection
                      </button>
                    ) : (
                      <button
                        className="btn btn-sm btn-primary"
                        disabled={!!running || detect.isPending}
                        title={running ? `Detection is running on ${running.camera_short}` : 'Run YOLO11 + ByteTrack on this camera in real time'}
                        onClick={act(() => detect.mutate(focus.id))}
                      >
                        <ScanEye size={15} /> Run live detection
                      </button>
                    )
                  )}
                </div>
              </div>
              <div className="stream">
                {visible && streamUrl ? (
                  <img
                    key={streamUrl}
                    src={streamUrl}
                    alt={`${focus.name}, ${focus.source === 'detect' ? 'live detection' : 'replay'}`}
                    onError={() => setTimeout(() => setStreamKey((k) => k + 1), 2000)}
                  />
                ) : (
                  <div className="placeholder" style={{ height: '100%' }}>
                    Paused while this tab is in the background.
                  </div>
                )}
              </div>
              {running?.camera_id === focus.id && (
                <div className="row small muted mt-1">
                  <Cpu size={14} /> {running.model} · {num(running.fps, 1)} frames/s processed · frames the detector has
                  no time for are skipped, so the picture is always current
                </div>
              )}
              {state.detect?.status === 'error' && state.detect.camera_id === focus.id && (
                <div className="mt-1">
                  <Callout tone="critical" title="Live detection stopped">
                    {state.detect.message}
                  </Callout>
                </div>
              )}
            </div>
          )}

          {focus && (
            <DataTable
              rows={focus.zones.map((z) => ({
                Zone: z.label,
                LOS: z.los,
                Congestion: z.ci,
                Occupancy: z.occupancy,
                '~Speed (km/h)': z.speed,
                '~Density (PCU/km/ln)': z.density,
                '~Flow (PCU/hr)': z.flow,
                Queue: z.queue,
                Saturated: z.saturated,
              }))}
              format={{
                LOS: (v) => <LosChip los={v as LiveCamera['los']} />,
                Congestion: (v) => (
                  <span className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
                    {num(v, 2)}
                    <span style={{ width: 60 }}>
                      <Meter frac={Number(v)} color="var(--accent)" />
                    </span>
                  </span>
                ),
                Occupancy: 2,
                '~Speed (km/h)': 0,
                '~Density (PCU/km/ln)': 0,
                '~Flow (PCU/hr)': 0,
                Queue: 1,
              }}
            />
          )}
        </div>

        <div className="stack">
          {points.length > 0 && <CameraMap points={points} selected={focusId} onSelect={setFocus} height={320} />}
          <div className="cam-list" role="group" aria-label="Cameras">
            {cams.map((c) => (
              <CameraRow
                key={c.id}
                c={c}
                active={c.id === focusId}
                alerts={(alertsBy.get(c.id) ?? []).length}
                onClick={() => setFocus(c.id)}
              />
            ))}
          </div>
          {state.risks.length > 0 && (
            <div className="card card-pad">
              <div className="tile-l">Rising — next 60 s</div>
              {state.risks.map((r) => (
                <div key={`${r.camera_id}-${r.zone_label}`} className="event-row">
                  <b>{r.camera_short}</b> · {r.zone_label}: {num(r.current, 2)} → <b>{num(r.predicted, 2)}</b>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ------------------------------------------------------- alerts */}
      <Section title="Alerts in force" note={`${active.length} active · raised as the evidence completes, cleared when it ends`} />
      <div className="grid-2">
        <div className="feed">
          {active.length === 0 && <p className="note">Nothing in force right now. New alerts appear here the moment they fire.</p>}
          {active.map((a) => (
            <AlertCard key={a.id} a={a} />
          ))}
        </div>
        <div className="card card-pad">
          <div className="tile-l row" style={{ gap: 6 }}>
            <Radio size={14} /> Event log
          </div>
          {log.length === 0 && <p className="note">Events appear as alerts are raised and cleared.</p>}
          {log.map((e) => (
            <div key={e.seq} className="event-row">
              <span className="mono">{new Date(e.at).toLocaleTimeString('en-IN')}</span> — {eventLine(e)}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-3">
        <Callout tone="neutral" title="What is live here">
          <p>
            <b>Replay</b> plays every camera's recorded measurement windows on one clock, with the footage in step, so the
            network can be watched as it would have been on the day. Alerts fire when their evidence is complete — at the
            end of the third sustained window — and quote only what was known at that moment.
          </p>
          <p>
            <b>Live detection</b> runs the real pipeline — YOLO11 + ByteTrack + the metrics engine — on one camera at
            wall-clock speed, skipping frames rather than falling behind. It also watches individual tracks for a vehicle
            standing still in a running lane while the lane itself flows: a breakdown or an illegal stop.
          </p>
        </Callout>
      </div>
    </div>
  )
}
