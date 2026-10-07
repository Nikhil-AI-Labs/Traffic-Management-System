// Layout and display primitives. Each mirrors a component from the original
// Streamlit theme (theme.py) so the two front ends read the same way, with the
// same rule underneath: a status colour never carries its meaning alone.

import { ChevronRight, CircleAlert, Info } from 'lucide-react'
import { lazy, Suspense, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { LOS_COLOR, SEV_COLOR, cap, type Los, type Severity } from '../lib/format'
import type { Figure } from '../lib/types'
import { ApiError } from '../lib/api'

const PlotlyChart = lazy(() => import('./PlotlyChart'))

export function Section({ title, note, children }: { title: string; note?: ReactNode; children?: ReactNode }) {
  return (
    <>
      <div className="section">
        <span className="section-t">{title}</span>
        <span className="section-r" />
        {note && <span className="section-n">{note}</span>}
      </div>
      {children}
    </>
  )
}

export function Hero({ label, value, unit, children }: { label: string; value: ReactNode; unit?: string; children?: ReactNode }) {
  return (
    <div>
      <div className="hero-l">{label}</div>
      <div className="hero-v">
        {value}
        {unit && <span className="hero-u">{unit}</span>}
      </div>
      {children && <div className="hero-d">{children}</div>}
    </div>
  )
}

export function Meter({ frac, color }: { frac: number; color: string }) {
  const f = Math.max(0, Math.min(1, Number.isFinite(frac) ? frac : 0))
  return (
    <div className="meter" style={{ background: `color-mix(in srgb, ${color} 16%, transparent)` }}>
      <i style={{ width: `${(f * 100).toFixed(1)}%`, background: color }} />
    </div>
  )
}

export function Tile({
  label,
  value,
  unit,
  children,
  meter,
}: {
  label: ReactNode
  value?: ReactNode
  unit?: string
  children?: ReactNode
  meter?: [number, string]
}) {
  return (
    <div className="tile">
      <div className="tile-l">{label}</div>
      {value !== undefined && value !== '' && (
        <div className="tile-v">
          {value}
          {unit && <span className="tile-u">{unit}</span>}
        </div>
      )}
      {meter && <Meter frac={meter[0]} color={meter[1]} />}
      {children && <div className="tile-d">{children}</div>}
    </div>
  )
}

export function Tiles({ cols = 4, children }: { cols?: number; children: ReactNode }) {
  return (
    <div className="tiles" style={{ '--cols': cols } as CSSProperties}>
      {children}
    </div>
  )
}

export function Chip({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span className="chip" style={{ background: `color-mix(in srgb, ${color} 15%, transparent)` }}>
      <span className="dot" style={{ background: color }} />
      {children}
    </span>
  )
}

export const LosChip = ({ los }: { los: Los }) => <Chip color={LOS_COLOR[los]}>{los}</Chip>
export const SevChip = ({ sev }: { sev: Severity }) => <Chip color={SEV_COLOR[sev]}>{sev.toUpperCase()}</Chip>

type Tone = 'info' | 'warning' | 'critical' | 'good' | 'neutral'
const TONE: Record<Tone, string> = {
  info: 'var(--s1)',
  warning: 'var(--warning)',
  critical: 'var(--critical)',
  good: 'var(--good)',
  neutral: 'var(--axis)',
}

export function Callout({ title, tone = 'info', children }: { title?: string; tone?: Tone; children: ReactNode }) {
  return (
    <div className="callout" style={{ '--c': TONE[tone] } as CSSProperties}>
      {title && (
        <div className="callout-t">
          {tone === 'warning' || tone === 'critical' ? <CircleAlert size={14} /> : <Info size={14} />}
          {title}
        </div>
      )}
      {children}
    </div>
  )
}

export const Note = ({ children }: { children: ReactNode }) => <div className="note">{children}</div>

export function Steps({ items }: { items: ReactNode[] }) {
  return (
    <div className="steps">
      {items.map((s, i) => (
        <div className="step" key={i}>
          {s}
        </div>
      ))}
    </div>
  )
}

export function Card({ children, pad = true, className = '' }: { children: ReactNode; pad?: boolean; className?: string }) {
  return <div className={`card ${pad ? 'card-pad' : ''} ${className}`}>{children}</div>
}

export function Expander({ title, children, open }: { title: ReactNode; children: ReactNode; open?: boolean }) {
  return (
    <details className="expander" open={open}>
      <summary>
        <ChevronRight size={16} />
        {title}
      </summary>
      <div className="exp-body">{children}</div>
    </details>
  )
}

export function Skeleton({ height = 120, style }: { height?: number; style?: CSSProperties }) {
  return <div className="skeleton" style={{ height, ...style }} />
}

export function Chart({ figure, label, card = true }: { figure: Figure | null | undefined; label?: string; card?: boolean }) {
  const h = Number((figure?.layout as { height?: number } | undefined)?.height) || 300
  const body = figure ? (
    <Suspense fallback={<Skeleton height={h} />}>
      <PlotlyChart figure={figure} label={label} />
    </Suspense>
  ) : (
    <Skeleton height={h} />
  )
  return card ? <div className="card chart-card">{body}</div> : body
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row muted small">
      <span className="spinner" />
      {label}
    </span>
  )
}

/** Placeholder for a whole view while its payload loads. */
export function PageSkeleton() {
  return (
    <div className="page" aria-busy="true">
      <div className="grid-hero mt-1">
        <Skeleton height={130} />
        <Tiles>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} height={110} />
          ))}
        </Tiles>
      </div>
      <Skeleton height={260} style={{ marginTop: 34 }} />
      <Skeleton height={220} style={{ marginTop: 22 }} />
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error)
  const status = error instanceof ApiError ? error.status : 0
  const title =
    status === 0
      ? 'Cannot reach the analysis server'
      : status === 409
        ? 'Nothing to show yet'
        : status === 404
          ? 'Not found'
          : 'Something went wrong'
  return (
    <div className="empty">
      <CircleAlert size={30} color="var(--warning)" style={{ margin: '0 auto' }} />
      <h3>{title}</h3>
      <p className="note" style={{ whiteSpace: 'pre-wrap' }}>
        {msg}
      </p>
      {onRetry && (
        <button className="btn mt-2" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: { value: T; label: ReactNode }[]
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.value)} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, value, hint, children }: { label: string; value?: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-l">
        {label}
        {value !== undefined && <span>{value}</span>}
      </span>
      {children}
      {hint && <span className="field-h">{hint}</span>}
    </label>
  )
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string }[]
  value: T
  onChange: (t: T) => void
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={t.id === value} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function ScaleBanner() {
  return (
    <div className="banner mt-2">
      <CircleAlert size={18} />
      <div>
        <b>Figures marked ~ carry an uncalibrated scale.</b> Nothing in this footage fixes pixels to metres, so
        anything in km/h, PCU/km/lane or PCU/hr is an estimate with an unknown multiplier — read those as
        relative, not absolute. The congestion ranking, level-of-service bands, saturation flags and the signal
        before/after comparison are scale-free and unaffected. <b>One measured road width fixes it</b> — see{' '}
        <Link to="/surat/calibration">Calibration</Link>.
      </div>
    </div>
  )
}

export const kindLabel = (k: string) => cap(k.replace(/_/g, ' '))
