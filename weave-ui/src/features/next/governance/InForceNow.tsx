/**
 * What governance is in force — **derived, never stored** (U17).
 *
 * dsivov changed Team vocabulary from Solo to Reviewed, signed it, and it
 * worked: `/rbac` and `/lifecycle` both read back `name: "reviewed", version: 2`
 * with the architect's approval gate enforced. **No screen said so.** The wizard
 * has four sections and every one is about *changing* governance; the "what
 * happened" panel exists only in the session that applied it. Come back tomorrow
 * and there is nothing, and the board's chip says `installed` rather than which.
 *
 * Same family as U10's silent save and one step worse: U10 was silent about an
 * **event**, so waiting resolved it. This is silent about **state**, so it is
 * wrong every time anyone looks.
 *
 * **The design constraint is the whole thing.** A `current_mode` field would be
 * a second source of truth, which is exactly what A8 forbids — edit the ontology
 * through Studio and the label goes on claiming Reviewed while the runtime
 * enforces something else. So the mode is read off the artifacts the runtime
 * itself enforces: they cannot disagree with reality, because they *are* it.
 */

import { useCallback, useEffect, useState } from 'react'

import {
  getLifecycle, getRbac,
  type LifecycleSummary, type RbacSummary
} from '@/api/weave'
import { useSettingsStore } from '@/stores/settings'

export interface InForce {
  /** The signed shape's name — `solo`, `reviewed`, … — or null if none is installed. */
  mode: string | null
  rbac: RbacSummary | null
  lifecycle: LifecycleSummary | null
  loading: boolean
}

/**
 * Read what is in force. Shared so the wizard and the board give one answer.
 *
 * The mode comes from RBAC's `name`, with lifecycle's as the fallback: they are
 * signed together by the wizard, and if they ever disagree that is worth seeing
 * rather than smoothing over — `disagreement` below says so on screen.
 */
export function useInForce(): InForce & { disagreement: boolean; refresh: () => void } {
  const workspace = useSettingsStore.use.workspace()
  const [rbac, setRbac] = useState<RbacSummary | null>(null)
  const [lifecycle, setLifecycle] = useState<LifecycleSummary | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    const [r, l] = await Promise.all([
      getRbac().catch(() => null),
      getLifecycle().catch(() => null),
    ])
    setRbac(r); setLifecycle(l); setLoading(false)
  }, [])

  useEffect(() => { void refresh() }, [refresh, workspace])

  const mode = rbac?.exists ? (rbac.name ?? null) : (lifecycle?.exists ? lifecycle.name ?? null : null)
  const disagreement = Boolean(
    rbac?.exists && lifecycle?.exists && rbac.name && lifecycle.name &&
    rbac.name !== lifecycle.name
  )

  return { mode, rbac, lifecycle, loading, disagreement, refresh }
}

/** A compact chip — for the board, where the answer is one word. */
export function InForceChip({ state }: { state: ReturnType<typeof useInForce> }) {
  if (state.loading) return <span className="chip">checking…</span>
  if (!state.mode) return <span className="chip">no governance installed</span>
  return (
    <span className="chip good" title={`RBAC v${state.rbac?.version ?? '?'} · lifecycle v${state.lifecycle?.version ?? '?'}`}>
      <span className="cd" />{state.mode} · v{state.rbac?.version ?? '?'}
    </span>
  )
}

/**
 * The full panel — what is in force, who may do what, and what the lifecycle
 * requires. The question dsivov could only answer by curling the ledger.
 */
export function InForceNow({ state }: { state: ReturnType<typeof useInForce> }) {
  if (state.loading) {
    return <div className="card"><div className="empty">Reading what is in force…</div></div>
  }

  if (!state.mode) {
    return (
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Nothing is in force yet</h3>
        <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 0 }}>
          This workspace has no signed governance. Choose a shape below; nothing
          is written until you sign it.
        </p>
      </div>
    )
  }

  const roles = Object.entries(state.rbac?.roles ?? {})
  const machines = Object.entries(state.lifecycle?.machines ?? {})

  return (
    <div className="card"><span className="stripe good" />
      <div className="chead">
        <h3>In force now — {state.mode}</h3>
        <span className="sub">
          RBAC v{state.rbac?.version ?? '?'} · lifecycle v{state.lifecycle?.version ?? '?'}
        </span>
      </div>
      <div className="cbody">
        {state.disagreement && (
          // Not smoothed over: the wizard signs both together, so different
          // names mean something else wrote one of them — which is precisely the
          // state a stored label would have hidden.
          <div className="callout warn" role="alert" style={{ marginBottom: 10 }}>
            RBAC says <strong>{state.rbac?.name}</strong> and the lifecycle says{' '}
            <strong>{state.lifecycle?.name}</strong>. They are signed together, so
            one of them has been changed separately — check Studio history.
          </div>
        )}

        <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))' }}>
          <div>
            <strong style={{ fontSize: 13 }}>Who may do what</strong>
            {roles.length === 0 && <div className="empty">No roles defined.</div>}
            {roles.map(([role, grants]) => (
              <div key={role} style={{ padding: '5px 0', borderBottom: '1px solid var(--line)' }}>
                <code>{role}</code>{' '}
                <span className="sub">
                  {grants.includes('*')
                    ? 'everything'
                    : `${grants.length} action${grants.length === 1 ? '' : 's'}`}
                </span>
              </div>
            ))}
          </div>

          <div>
            <strong style={{ fontSize: 13 }}>Lifecycles enforced</strong>
            {machines.length === 0 && <div className="empty">No state machines.</div>}
            {machines.map(([object, machine]) => (
              <div key={object} style={{ padding: '5px 0', borderBottom: '1px solid var(--line)' }}>
                <code>{object}</code>{' '}
                <span className="sub">
                  {Array.isArray((machine as { states?: unknown[] })?.states)
                    ? `${(machine as { states: unknown[] }).states.length} states`
                    : 'defined'}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 10 }}>
          Read from the signed artifacts themselves, not from a stored setting —
          so this cannot claim one thing while the runtime enforces another.
        </div>
      </div>
    </div>
  )
}
