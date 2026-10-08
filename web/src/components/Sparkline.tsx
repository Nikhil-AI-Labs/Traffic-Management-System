// A congestion trend small enough to sit in a camera tile. Plotly would be a
// sledgehammer for this and redraws far too slowly for a once-a-second feed.

import { LOS_COLOR, losOf } from '../lib/format'

export default function Sparkline({
  values,
  width = 120,
  height = 30,
}: {
  values: number[]
  width?: number
  height?: number
}) {
  if (values.length < 2) return <svg width={width} height={height} aria-hidden />
  const n = values.length
  const x = (i: number) => 2 + (i / (n - 1)) * (width - 6)
  const y = (v: number) => height - 2 - Math.min(1, Math.max(0, v)) * (height - 4)
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')
  const last = values[n - 1]
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Congestion trend, now ${last.toFixed(2)} (${losOf(last)})`}
    >
      {[0.25, 0.5, 0.75].map((t) => (
        <line key={t} x1={0} x2={width} y1={y(t)} y2={y(t)} stroke="var(--hairline)" strokeWidth={1} />
      ))}
      <path d={d} fill="none" stroke="var(--text2)" strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={x(n - 1)} cy={y(last)} r={3.2} fill={LOS_COLOR[losOf(last)]} stroke="var(--surface)" strokeWidth={1.5} />
    </svg>
  )
}
