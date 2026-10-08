// The incident workflow on an alert card: its status, the actions that move
// it on, and the trail of who did what. The server owns the state
// (outputs/incidents.json via /api/incidents); this only presents and posts.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, CheckCheck, Undo2 } from 'lucide-react'
import { useState } from 'react'
import { post } from '../lib/api'
import type { IncidentAction, IncidentStatus } from '../lib/types'
import { Chip } from './ui'

const STATUS: Record<IncidentStatus, { label: string; color: string }> = {
  open: { label: 'Open', color: 'var(--axis)' },
  acknowledged: { label: 'Acknowledged', color: 'var(--warning)' },
  resolved: { label: 'Resolved', color: 'var(--good)' },
  cleared: { label: 'Cleared by itself', color: 'var(--s1)' },
}

export function StatusChip({ status }: { status: IncidentStatus }) {
  const s = STATUS[status]
  return <Chip color={s.color}>{s.label}</Chip>
}

// Everything that shows an alert's status: the incident log, the alert views,
// the sidebar badge (/meta).
const AFFECTED = /^\/(incidents|bottlenecks|sites|meta)/

type Action = 'acknowledge' | 'resolve' | 'reopen'

const LABEL: Record<Action, string> = { acknowledge: 'Acknowledge', resolve: 'Resolve', reopen: 'Reopen' }

export function IncidentActions({ id, status }: { id: string; status: IncidentStatus }) {
  const qc = useQueryClient()
  const [pending, setPending] = useState<Action | null>(null)
  const [note, setNote] = useState('')
  const m = useMutation({
    mutationFn: (action: Action) => post(`/incidents/${encodeURIComponent(id)}/action`, { action, note }),
    onSuccess: () => {
      setPending(null)
      setNote('')
      qc.invalidateQueries({ predicate: (q) => typeof q.queryKey[0] === 'string' && AFFECTED.test(q.queryKey[0]) })
    },
  })
  // A cleared live alert can still be resolved, to put on record what was done.
  const actions: Action[] =
    status === 'open'
      ? ['acknowledge', 'resolve']
      : status === 'acknowledged'
        ? ['resolve', 'reopen']
        : status === 'cleared'
          ? ['resolve']
          : ['reopen']

  if (pending)
    return (
      <form
        className="inc-form"
        onSubmit={(e) => {
          e.preventDefault()
          m.mutate(pending)
        }}
      >
        <input
          type="text"
          autoFocus
          maxLength={500}
          placeholder={pending === 'resolve' ? 'What was done? (optional)' : 'Note (optional)'}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          aria-label="Note for the incident log"
        />
        <button className="btn btn-sm btn-primary" type="submit" disabled={m.isPending}>
          {LABEL[pending]}
        </button>
        <button className="btn btn-sm btn-ghost" type="button" onClick={() => setPending(null)}>
          Cancel
        </button>
        {m.error && <span className="small" style={{ color: 'var(--critical)' }}>{m.error.message}</span>}
      </form>
    )

  return (
    <div className="row inc-actions">
      {actions.map((a) => (
        <button key={a} className="btn btn-sm" onClick={() => setPending(a)}>
          {a === 'acknowledge' ? <Check size={14} /> : a === 'resolve' ? <CheckCheck size={14} /> : <Undo2 size={14} />}
          {LABEL[a]}
        </button>
      ))}
    </div>
  )
}

const VERB: Record<string, string> = { acknowledge: 'acknowledged', resolve: 'resolved', reopen: 'reopened' }

export function IncidentHistory({ history }: { history: IncidentAction[] }) {
  if (!history.length) return null
  return (
    <ol className="inc-history">
      {history.map((h, i) => (
        <li key={i}>
          <b>{h.by}</b> {VERB[h.action] ?? h.action}{' '}
          <time dateTime={h.at}>{new Date(h.at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}</time>
          {h.note && <> — “{h.note}”</>}
        </li>
      ))}
    </ol>
  )
}
