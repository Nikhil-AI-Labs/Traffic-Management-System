// The incident log: every alert the network has raised - recorded and live -
// with who acknowledged it, who resolved it and what they wrote. The record a
// control room hands over at the end of a shift.

import { Download } from 'lucide-react'
import { useState } from 'react'
import { Callout, ErrorState, PageSkeleton, Segmented, Tile, Tiles } from '../components/ui'
import { AlertCard } from '../components/widgets'
import { useApi } from '../lib/api'
import type { IncidentsPayload } from '../lib/types'

type StatusFilter = 'open' | 'acknowledged' | 'resolved' | 'cleared' | 'all'
type SourceFilter = 'all' | 'recorded' | 'live'

export default function Incidents() {
  const [status, setStatus] = useState<StatusFilter>('open')
  const [source, setSource] = useState<SourceFilter>('all')
  const q = useApi<IncidentsPayload>(`/incidents?status=${status}&source=${source}`, {
    refetchInterval: 5000,
    placeholderData: (prev) => prev,
  })
  const [limit, setLimit] = useState(40)

  if (q.isLoading) return <PageSkeleton />
  if (!q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const { items, counts } = q.data
  const shown = items.slice(0, limit)

  return (
    <div className="page">
      <Tiles>
        <Tile label="Open" value={counts.open}>
          {counts.open_critical} critical waiting for someone to pick them up
        </Tile>
        <Tile label="Acknowledged" value={counts.acknowledged}>
          someone is on it
        </Tile>
        <Tile label="Resolved" value={counts.resolved}>
          closed, with what was done
        </Tile>
        <Tile label="Cleared by itself" value={counts.cleared}>
          live alerts whose condition ended before anyone acted
        </Tile>
      </Tiles>

      <div className="row mt-3" style={{ marginBottom: 14 }}>
        <Segmented
          label="Status"
          value={status}
          onChange={(v) => {
            setStatus(v)
            setLimit(40)
          }}
          options={[
            { value: 'open', label: `Open ${counts.open}` },
            { value: 'acknowledged', label: `Acknowledged ${counts.acknowledged}` },
            { value: 'resolved', label: `Resolved ${counts.resolved}` },
            { value: 'cleared', label: `Cleared ${counts.cleared}` },
            { value: 'all', label: 'All' },
          ]}
        />
        <Segmented
          label="Source"
          value={source}
          onChange={setSource}
          options={[
            { value: 'all', label: 'All sources' },
            { value: 'recorded', label: 'Recorded' },
            { value: 'live', label: 'Live' },
          ]}
        />
        <span className="spacer" />
        <a className="btn btn-sm" href="/api/incidents.csv" download>
          <Download size={15} /> Incident log (CSV)
        </a>
      </div>

      {items.length === 0 ? (
        <Callout tone="good" title="Nothing here">
          No {status === 'all' ? '' : status} incidents{source !== 'all' ? ` from ${source} sources` : ''}.
        </Callout>
      ) : (
        <>
          {shown.map((a) => (
            <AlertCard key={a.id} a={a} />
          ))}
          {items.length > limit && (
            <div className="row mt-2" style={{ justifyContent: 'center' }}>
              <button className="btn" onClick={() => setLimit((n) => n + 40)}>
                Show more ({items.length - limit} left)
              </button>
            </div>
          )}
        </>
      )}

      <div className="mt-3">
        <Callout tone="neutral" title="How the log works">
          <p>
            Alerts are derived from the measurements, so they carry no state of their own; this log is the state — an
            append-only history of who acknowledged or resolved each one, when, and why. It survives restarts, and a
            live alert's details are kept with its entry once someone acts on it, so the record still reads correctly
            after the alert itself has cleared.
          </p>
          <p>
            Recorded alerts keep a stable identity — the same condition on the same clip is the same incident across
            restarts — so acknowledging one sticks.
          </p>
        </Callout>
      </div>
    </div>
  )
}
