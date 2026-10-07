// Data-bearing widgets: the table twin every chart gets, images with a
// lightbox, the alert feed, and progress for long-running server jobs.

import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, ImageOff } from 'lucide-react'
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { get } from '../lib/api'
import { SEV_COLOR, int, isNum, num, type Severity } from '../lib/format'
import type { Alert, Job, Row } from '../lib/types'
import { Expander, Segmented, SevChip, Tile, Tiles } from './ui'

// ------------------------------------------------------------------ table

type Fmt = (v: Row[string], row: Row) => ReactNode

export function DataTable({
  rows,
  columns,
  format = {},
  highlight,
  maxHeight,
}: {
  rows: Row[]
  columns?: string[]
  format?: Record<string, Fmt | number>
  highlight?: (r: Row) => boolean
  maxHeight?: number
}) {
  const cols = useMemo(() => columns ?? (rows[0] ? Object.keys(rows[0]) : []), [columns, rows])
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 } | null>(null)

  const sorted = useMemo(() => {
    if (!sort) return rows
    const { col, dir } = sort
    return [...rows].sort((a, b) => {
      const x = a[col]
      const y = b[col]
      if (x === null || x === undefined) return 1
      if (y === null || y === undefined) return -1
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * dir
      return String(x).localeCompare(String(y)) * dir
    })
  }, [rows, sort])

  const numeric = useMemo(
    () => Object.fromEntries(cols.map((c) => [c, rows.some((r) => typeof r[c] === 'number')])),
    [cols, rows],
  )

  const cell = (c: string, r: Row): ReactNode => {
    const f = format[c]
    const v = r[c]
    if (typeof f === 'function') return f(v, r)
    if (typeof f === 'number') return num(v, f)
    if (v === null || v === undefined || v === '') return '—'
    if (typeof v === 'boolean') return v ? 'yes' : 'no'
    if (isNum(v)) return Number.isInteger(v) ? int(v) : num(v, 2)
    return String(v)
  }

  if (!rows.length) return <p className="note">No rows.</p>
  return (
    <div className="table-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className="dt">
        <thead>
          <tr>
            {cols.map((c) => (
              <th
                key={c}
                className={numeric[c] ? 'num' : ''}
                aria-sort={sort?.col === c ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}
                onClick={() =>
                  setSort((s) => (s?.col === c ? (s.dir === 1 ? { col: c, dir: -1 } : null) : { col: c, dir: 1 }))
                }
              >
                {c}
                {sort?.col === c &&
                  (sort.dir === 1 ? (
                    <ArrowUp size={12} style={{ marginLeft: 4, verticalAlign: -1 }} />
                  ) : (
                    <ArrowDown size={12} style={{ marginLeft: 4, verticalAlign: -1 }} />
                  ))}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={i} className={highlight?.(r) ? 'hl' : ''}>
              {cols.map((c) => (
                <td key={c} className={numeric[c] ? 'num' : ''}>
                  {cell(c, r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The table twin of a chart: every value reachable without hover or colour. */
export function TableView(props: Parameters<typeof DataTable>[0] & { title?: string; open?: boolean }) {
  const { title = 'Table view', open, ...rest } = props
  return (
    <Expander title={`${title} · ${rest.rows.length} rows`} open={open}>
      <DataTable {...rest} />
    </Expander>
  )
}

// ------------------------------------------------------------------ media

export function Media({
  src,
  alt,
  caption,
  missing,
}: {
  src: string | null
  alt: string
  caption?: ReactNode
  missing?: ReactNode
}) {
  // Load state is recorded against the src it belongs to, so a new src reads
  // as loading without an effect having to reset it.
  const [settled, setSettled] = useState<{ src: string | null; ok: boolean } | null>(null)
  const state: 'loading' | 'ok' | 'error' =
    settled && settled.src === src ? (settled.ok ? 'ok' : 'error') : 'loading'
  const setState = (s: 'ok' | 'error') => setSettled({ src, ok: s === 'ok' })
  const [zoom, setZoom] = useState(false)

  useEffect(() => {
    if (!zoom) return
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setZoom(false)
    }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [zoom])

  if (!src || state === 'error')
    return (
      <div className="placeholder">
        <div>
          <ImageOff size={22} style={{ margin: '0 auto 8px' }} />
          {missing ?? 'Image not available.'}
        </div>
      </div>
    )

  return (
    <figure style={{ margin: 0 }}>
      <div className={`media ${state === 'loading' ? 'skeleton' : ''}`}>
        <img
          src={src}
          alt={alt}
          loading="lazy"
          onLoad={() => setState('ok')}
          onError={() => setState('error')}
          onClick={() => setZoom(true)}
          style={state === 'loading' ? { minHeight: 200, opacity: 0 } : undefined}
        />
      </div>
      {caption && <figcaption className="media-cap">{caption}</figcaption>}
      {zoom && (
        <div className="lightbox" onClick={() => setZoom(false)} role="dialog" aria-label={alt}>
          <img src={src.replace(/([?&])w=\d+/, '$1w=3840')} alt={alt} />
        </div>
      )}
    </figure>
  )
}

// ------------------------------------------------------------------ alerts

export function AlertCard({ a, showSite = true }: { a: Alert; showSite?: boolean }) {
  return (
    <div className="alert" style={{ '--c': SEV_COLOR[a.severity] } as CSSProperties}>
      <div className="alert-top">
        <SevChip sev={a.severity} />
        <span className="alert-m">
          {showSite && `${a.camera_name} · `}
          {int(a.start_s)}–{int(a.end_s)} s ({int(a.duration_s)} s)
        </span>
      </div>
      <div className="alert-h">{a.headline}</div>
      <div className="alert-b">
        <b>Evidence</b> — {a.evidence}
      </div>
      <div className="alert-b">
        <b>Recommendation</b> — {a.recommendation}
      </div>
    </div>
  )
}

export function AlertFeed({ alerts, showSite = true, limit = 8 }: { alerts: Alert[]; showSite?: boolean; limit?: number }) {
  const [sev, setSev] = useState<'all' | Severity>('all')
  const [all, setAll] = useState(false)
  const present = (['critical', 'warning', 'info'] as Severity[]).filter((s) => alerts.some((a) => a.severity === s))
  const shown = sev === 'all' ? alerts : alerts.filter((a) => a.severity === sev)
  const list = all ? shown : shown.slice(0, limit)
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <Segmented
          label="Filter by severity"
          value={sev}
          onChange={setSev}
          options={[
            { value: 'all', label: `All ${alerts.length}` },
            ...present.map((s) => ({
              value: s,
              label: `${s[0].toUpperCase()}${s.slice(1)} ${alerts.filter((a) => a.severity === s).length}`,
            })),
          ]}
        />
        <span className="spacer" />
        {shown.length > limit && (
          <button className="btn btn-sm btn-ghost" onClick={() => setAll((v) => !v)}>
            {all ? `Show the ${limit} most severe` : `Show all ${shown.length}`}
          </button>
        )}
      </div>
      {list.map((a, i) => (
        <AlertCard key={`${a.camera_id}-${a.zone_id}-${a.kind}-${a.start_s}-${i}`} a={a} showSite={showSite} />
      ))}
    </>
  )
}

/**
 * The full alerts block the Streamlit dashboard shows wherever alerts appear:
 * severity tiles, a table view holding every alert, and the filterable feed.
 */
export function AlertsSection({ alerts, showSite = true }: { alerts: Alert[]; showSite?: boolean }) {
  const count = (s: Severity) => alerts.filter((a) => a.severity === s).length
  return (
    <>
      <Tiles cols={3}>
        <Tile label="Critical" value={count('critical')}>
          condition sustained and severe
        </Tile>
        <Tile label="Warning" value={count('warning')}>
          sustained, below the critical threshold
        </Tile>
        <Tile label="Info" value={count('info')}>
          worth knowing, not urgent
        </Tile>
      </Tiles>
      <TableView
        title="Table view — all alerts"
        rows={alerts.map((a) => ({
          Severity: a.severity,
          ...(showSite ? { Site: a.camera_short } : {}),
          Zone: a.zone_label,
          Kind: a.kind.replace(/_/g, ' '),
          'From (s)': Math.round(a.start_s),
          'To (s)': Math.round(a.end_s),
          'Duration (s)': Math.round(a.duration_s),
          Evidence: a.evidence,
        }))}
      />
      <div className="mt-2">
        <AlertFeed alerts={alerts} showSite={showSite} />
      </div>
    </>
  )
}

// ------------------------------------------------------------------ jobs

/** Poll a background job until it finishes. */
export function useJob<R = unknown>(id: string | null) {
  return useQuery<Job<R>>({
    queryKey: ['job', id],
    queryFn: () => get<Job<R>>(`/jobs/${id}`),
    enabled: !!id,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'done' || s === 'error' ? false : 700
    },
  })
}

export function JobProgress({ job }: { job: Job | undefined }) {
  if (!job) return null
  const pct = Math.round(job.progress * 100)
  return (
    <div className="card card-pad">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <b className="small">{job.label}</b>
        <span className="small muted">
          {job.status === 'error' ? 'failed' : job.status === 'done' ? 'done' : `${pct}%`} · {Math.round(job.elapsed_s)} s
        </span>
      </div>
      <div className="progress mt-1" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <i style={{ width: `${job.status === 'done' ? 100 : pct}%` }} />
      </div>
      <div className="small muted mt-1">{job.status === 'error' ? job.error : job.message}</div>
    </div>
  )
}
