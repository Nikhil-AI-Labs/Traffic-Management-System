import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Callout, PageSkeleton } from '../../components/ui'
import { useApi } from '../../lib/api'
import type { Meta, Recording } from '../../lib/types'

/** The chosen FLUID recording lives in the URL, so a view can be linked to. */
export function useRecording() {
  const meta = useApi<Meta>('/meta', { staleTime: 15_000 })
  const [params, setParams] = useSearchParams()
  const recs = meta.data?.fluid.recordings ?? []
  const wanted = params.get('rec')
  const rid = wanted && recs.some((r) => r.id === wanted) ? wanted : (recs[0]?.id ?? null)
  const setRid = (r: string) =>
    setParams(
      (p) => {
        p.set('rec', r)
        return p
      },
      { replace: true },
    )
  return { recs, rid, setRid, loading: meta.isLoading, available: !!meta.data?.fluid.available }
}

export function RecordingSelect({ recs, rid, setRid }: { recs: Recording[]; rid: string | null; setRid: (r: string) => void }) {
  return (
    <label className="field" style={{ maxWidth: 460 }}>
      <span className="field-l">Recording</span>
      <select value={rid ?? ''} onChange={(e) => setRid(e.target.value)}>
        {recs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.site_label} · {r.name}
          </option>
        ))}
      </select>
    </label>
  )
}

export function FluidGate({ loading, available, children }: { loading: boolean; available: boolean; children: ReactNode }) {
  if (loading) return <PageSkeleton />
  if (!available)
    return (
      <Callout tone="warning" title="Real-intersection data not found">
        <p>
          The FLUID dataset is not on this machine. It carries what the Surat cameras cannot: a whole signalised
          intersection in one aerial frame, so every approach <b>and</b> every receiving link is visible at once, plus
          per-second signal state and measurable turn ratios.
        </p>
        <p>
          <b>FLUID</b> — figshare DOI <code>10.6084/m9.figshare.29974954</code>, CC BY 4.0, ~18 GB. Set{' '}
          <code>FLUID_ROOT</code> to wherever it was extracted, then restart the API.
        </p>
      </Callout>
    )
  return <>{children}</>
}
