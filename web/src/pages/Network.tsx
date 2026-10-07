import { Link } from 'react-router-dom'
import { Chart, ErrorState, Hero, LosChip, Note, PageSkeleton, ScaleBanner, Section, Tile, Tiles } from '../components/ui'
import { TableView } from '../components/widgets'
import { useThemedApi } from '../lib/api'
import { int, num, pct } from '../lib/format'
import type { NetworkPayload } from '../lib/types'

export default function Network() {
  const q = useThemedApi<NetworkPayload>('/network')
  if (q.isLoading) return <PageSkeleton />
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const { kpi, sites, charts, scale_verified_any } = q.data

  return (
    <div className="page">
      <div className="grid-hero">
        <Hero label="Monitored time at or beyond capacity" value={num(kpi.over_capacity_pct)} unit="%">
          Across {kpi.sites} sites and {int(kpi.windows)} two-second measurement windows. The worst is{' '}
          <b>{kpi.worst.short}</b> at an index of {num(kpi.worst.mean, 2)}.
        </Hero>
        <Tiles>
          <Tile label="Sites" value={kpi.sites}>
            {kpi.junction_sites} junction · {kpi.area_sites} area
          </Tile>
          <Tile label="Zones" value={kpi.zones}>
            lanes, carriageways, kerbs, platforms
          </Tile>
          <Tile label="Windows" value={int(kpi.windows)}>
            2 s each
          </Tile>
          <Tile label="Saturated" value={int(kpi.saturated)} meter={[kpi.saturated_pct / 100, 'var(--critical)']}>
            {pct(kpi.saturated_pct)} of windows where counting breaks down
          </Tile>
        </Tiles>
      </div>

      <Section title="Level of service over time" note="worst zone at each site, second by second" />
      <Chart figure={charts.los_timeline} label="Level of service over time for every site" />
      <div className="note mt-1">
        Each band is one site; reading left to right is reading the clip. These bands take the <b>worst</b> zone at
        each site, so a site can show heavy bands and still rank lower below, where the index is averaged across every
        zone and window.
      </div>

      <Section title="Congestion ranking" note="mean and peak, absolute scale" />
      <div className="grid-2">
        <Chart figure={charts.ranking} label="Mean and peak congestion by site" />
        <div className="stack">
          <Note>
            The index is normalised against <b>absolute</b> occupancy bounds measured once across all seven cameras —
            not against each zone's own range. That is what makes these bars comparable: a self-normalised index would
            rate an empty arterial "busy" whenever it drifted above its own average, and a permanently packed market
            "clear" at its quietest moment.
          </Note>
          <div className="stack" style={{ gap: 7 }}>
            {sites.map((s) => (
              <Link key={s.id} to={`/surat/site/${encodeURIComponent(s.id)}`} className="row" style={{ color: 'inherit' }}>
                <LosChip los={s.los} />
                <span className="kv">
                  {s.short} — <b>{num(s.mean_congestion, 2)}</b>
                </span>
              </Link>
            ))}
          </div>
        </div>
      </div>
      <TableView
        title="Table view — all sites"
        columns={['Site', 'Mode', 'Mean congestion', 'Peak congestion', 'Worst zone', '~Mean speed (km/h)', 'Saturated windows', 'Windows']}
        rows={sites.map((s) => ({
          Site: s.name,
          Mode: s.mode,
          'Mean congestion': s.mean_congestion,
          'Peak congestion': s.peak_congestion,
          'Worst zone': s.worst_zone,
          '~Mean speed (km/h)': s.mean_speed_kmph,
          'Saturated windows': s.saturated_windows,
          Windows: s.total_windows,
        }))}
        format={{ 'Mean congestion': 3, 'Peak congestion': 3, '~Mean speed (km/h)': 1 }}
      />
      {!scale_verified_any && <ScaleBanner />}

      <Section title="Sites" note="zones shaded by average level of service — open one for detail" />
      <div className="grid-cards">
        {sites.map((s) => (
          <Link key={s.id} to={`/surat/site/${encodeURIComponent(s.id)}`} className="card site-card">
            {s.has_overlay ? (
              <img src={`/api/images/overlay/${encodeURIComponent(s.id)}?w=640`} alt={`${s.name} with zones shaded`} loading="lazy" />
            ) : (
              <div className="placeholder">No reference frame</div>
            )}
            <div className="site-body">
              <div className="site-n">{s.name}</div>
              <div className="site-m">
                {s.mode} mode · {int(s.total_windows)} windows
              </div>
              <div className="row mt-1">
                <LosChip los={s.los} />
                <span className="kv">
                  index <b>{num(s.mean_congestion, 2)}</b>
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
