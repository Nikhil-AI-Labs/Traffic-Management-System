// Number formatting. Every helper returns an em dash for missing values, so a
// null from the API (an unmeasured or non-finite figure) is shown as absent
// rather than as 0 or NaN.

const DASH = '—'

export const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

export function num(v: unknown, digits = 0): string {
  if (!isNum(v)) return DASH
  return v.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export const int = (v: unknown) => num(v, 0)
export const pct = (v: unknown, digits = 0) => (isNum(v) ? `${num(v, digits)}%` : DASH)
export const secs = (v: unknown, digits = 0) => (isNum(v) ? `${num(v, digits)} s` : DASH)
export function signed(v: unknown, digits = 0): string {
  if (!isNum(v)) return DASH
  // Sign from the value as displayed, so -0.2 at 0 digits reads "0", not "−0".
  const r = Number(v.toFixed(digits))
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${num(Math.abs(r), digits)}`
}

/** "~" before a scale-dependent figure when the camera's scale is unverified. */
export const est = (verified: boolean) => (verified ? '' : '~')

export type Los = 'Free' | 'Moderate' | 'Heavy' | 'Jam'
export type Severity = 'critical' | 'warning' | 'info'

export function losOf(ci: number): Los {
  return ci >= 0.75 ? 'Jam' : ci >= 0.5 ? 'Heavy' : ci >= 0.25 ? 'Moderate' : 'Free'
}

export const LOS_COLOR: Record<Los, string> = {
  Free: 'var(--good)',
  Moderate: 'var(--warning)',
  Heavy: 'var(--serious)',
  Jam: 'var(--critical)',
}

export const SEV_COLOR: Record<Severity, string> = {
  critical: 'var(--critical)',
  warning: 'var(--warning)',
  info: 'var(--s1)',
}

export const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s)
