/**
 * The one propose → diff → sign flow (CR-001, R10).
 *
 * **Extracted from `Wizard.tsx`, not copied beside it.** The wizard proved this
 * pattern in P4 and was the only screen that used it; ontology and rules were
 * `<textarea>` elements of `JSON.stringify`. Writing a second copy for them
 * would have meant two implementations of "what does signing governance look
 * like", which is exactly the shape D-032 through D-034 spent three phases
 * removing on the server side. `Wizard.tsx` now renders these components, so
 * there is one implementation and the wizard is its own regression test.
 *
 * **The invariant this file exists to hold: you cannot sign without a reason.**
 * Governance is who may do what. An unattributed change makes *"who took away my
 * access"* unanswerable, which is the question the ledger exists to answer — so
 * the button is disabled until a reason is typed, and `useSignOff` refuses the
 * call as well. Two guards rather than one, because a disabled button is a
 * suggestion and a caller with a keyboard is not obliged to take it.
 *
 * **Nothing here writes.** `apply` is passed in by the screen and is always a
 * governed endpoint that signs through `DiffEngine` server-side (A8). This
 * module renders a proposal and collects a reason; it has no opinion about
 * where the proposal came from.
 */

import { useCallback, useState } from 'react'
import { CheckIcon, FileSignatureIcon } from 'lucide-react'

import type { WizardApplied, WizardDiff } from '@/api/weave'

/** What a screen must give us to sign: the diffs, and how to apply them. */
export type ApplyFn = (diffs: WizardDiff[], reason: string) => Promise<{ applied: WizardApplied[] }>

/**
 * **The rule, as one function both call sites use.**
 *
 * There is no DOM harness in this project — `@types/bun` and nothing else — and
 * adding `@testing-library/react` to assert that a button is disabled would be a
 * new dependency, which A11 forbids without a `D-NN` and CR-001 names as drift.
 * So the decision is lifted out of the component instead of the assertion being
 * lifted into a browser: a pure predicate can be tested by `bun test`, and *that
 * both places use it* can be checked by reading the source.
 *
 * Better than the alternative anyway. Before this, "can this be signed?" existed
 * twice — once as `canSign` in the panel and once as an early return in the
 * hook — and D-038 was precisely a case of the right rule living in one file
 * while the wrong one shipped in another.
 */
export function canSign(reason: string, diffs: WizardDiff[], busy = false): boolean {
  return !busy && reason.trim().length > 0 && diffs.length > 0
}

export interface SignOffState {
  reason: string
  setReason: (r: string) => void
  busy: boolean
  error: string | null
  applied: WizardApplied[] | null
  /** Sign the given diffs. Resolves to the applied versions, or null if refused. */
  sign: (diffs: WizardDiff[]) => Promise<WizardApplied[] | null>
  reset: () => void
}

/**
 * The sign-off state machine, shared by every governance screen.
 *
 * `apply` is the screen's own endpoint — `wizardApply` for the wizard, the
 * governance editors for ontology and rules. Keeping it a parameter is what
 * lets one flow serve several artifact kinds without this module knowing any of
 * them.
 */
export function useSignOff(apply: ApplyFn): SignOffState {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [applied, setApplied] = useState<WizardApplied[] | null>(null)

  const sign = useCallback(async (diffs: WizardDiff[]) => {
    // The second guard. `SignOffPanel` disables its button without a reason, but
    // a disabled button is a suggestion — this is the one that holds.
    if (!canSign(reason, diffs)) return null
    setBusy(true)
    setError(null)
    try {
      const r = await apply(diffs, reason.trim())
      setApplied(r.applied)
      return r.applied
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ? String(detail) : 'The change was not signed.')
      return null
    } finally {
      setBusy(false)
    }
  }, [apply, reason])

  const reset = useCallback(() => {
    setReason(''); setError(null); setApplied(null)
  }, [])

  return { reason, setReason, busy, error, applied, sign, reset }
}

/**
 * The diff, then the signature — in that order, deliberately.
 *
 * The diff is not a formality. What is being signed changes who may do what and
 * which state transitions are legal, so the screen shows the whole of it before
 * offering the button, and says plainly that nothing has been written yet.
 */
export function SignOffPanel({
  diffs, state, title = 'What will change', signLabel = 'Sign and install'
}: {
  diffs: WizardDiff[]
  state: SignOffState
  title?: string
  signLabel?: string
}) {
  if (!diffs.length) return null
  const enabled = canSign(state.reason, diffs, state.busy)

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 10 }}>
        Nothing has been written yet. Signing creates a new version of each
        artifact, with your name on it, and takes effect on the next request —
        no restart.
      </div>

      {diffs.map((d) => (
        <details key={`${d.kind}:${d.artifact_id ?? ''}`} style={{ marginBottom: 8 }} open>
          <summary>
            <strong>{d.kind}</strong>{' '}
            <span className="badge">
              {d.from_version === null ? 'new' : `v${d.from_version} → v${d.to_version}`}
            </span>
            {d.behaviour_changed && (
              <span className="badge" style={{ background: 'var(--warn-dim)', marginLeft: 6 }}>
                changes behaviour
              </span>
            )}
          </summary>
          <pre style={{ maxHeight: 260, overflow: 'auto', fontSize: 12 }}>
            {JSON.stringify(d.delta.after, null, 2)}
          </pre>
        </details>
      ))}

      <label style={{ display: 'block', margin: '10px 0 4px' }}>
        Why are you making this change? It is recorded against your name.
      </label>
      <input
        style={{ width: '100%' }}
        value={state.reason}
        placeholder="e.g. new team, adopting a review gate"
        onChange={(e) => state.setReason(e.target.value)}
      />

      {state.error && (
        <div style={{ color: 'var(--bad)', fontSize: 13, marginTop: 8 }}>{state.error}</div>
      )}

      <button
        className="btn"
        onClick={() => void state.sign(diffs)}
        disabled={!enabled}
        // Derived from the same decision, not a second guess at it. It also
        // used to be wrong while a request was in flight: a busy button said
        // "needs a reason" to someone who had typed one.
        title={enabled ? '' : (state.busy ? 'Signing…' : 'A governance change needs a reason')}
        style={{ marginTop: 8 }}
      >
        <FileSignatureIcon className="" /> {signLabel}
      </button>
    </div>
  )
}

/**
 * What happened — the versions, who signed them, and that nothing restarted.
 *
 * Worth stating on screen because it is the part people do not believe: the
 * change is in force now, and rolling the version back restores the behaviour it
 * replaced.
 */
export function AppliedPanel({ applied }: { applied: WizardApplied[] | null }) {
  if (!applied?.length) return null
  return (
    <div className="card" style={{ borderColor: 'var(--good)' }}>
      <h3 style={{ marginTop: 0 }}><CheckIcon className="" /> Installed</h3>
      <ul>
        {applied.map((a) => (
          <li key={`${a.kind}:${a.artifact_id}`}>
            <code>{a.kind}</code> is now <strong>v{a.version}</strong>, signed by{' '}
            <strong>{a.sign_off.approver}</strong> — “{a.sign_off.reason}”
          </li>
        ))}
      </ul>
      <div style={{ color: 'var(--muted)', fontSize: 13 }}>
        In force now; no restart was needed. Every version is in the Studio
        history, and rolling one back restores the behaviour it replaced.
      </div>
    </div>
  )
}
