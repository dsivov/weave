/**
 * One rule for controls that will not act, fail, or succeed (U2 · U6 · U7 · U10).
 *
 * **The rule:** *a control that will not act says why, in place, before it is
 * clicked; an action that fails says so where the click happened; an action that
 * succeeds says that too.*
 *
 * Four sites broke it four different ways, which is why this is one change
 * rather than four fixes:
 *
 * - **U2** — `WeaveBoard`'s Approve sits inside a `Modal`, and `act()` renders
 *   its error on the page *behind* it. The 403 was correct and invisible.
 * - **U6 · U7** — `SignOff`'s button disables correctly and explains only
 *   through a `title` tooltip **on a disabled control**, which touch devices
 *   never show and several browsers suppress anyway.
 * - **U10** — `AdminUsers`' create button hides a password-length rule behind
 *   the same disabled-plus-tooltip pattern, and its role change saved in
 *   silence.
 *
 * The shape they share: **the application knew the answer and did not put it
 * where the person was looking.** So the primitive is not a toast component —
 * it is (a) a way to say what is blocking *before* the click, and (b) a place
 * for the outcome *at the control*.
 *
 * **Why not a toast.** A toast is elsewhere and it expires. The 403 in U2 was
 * already being rendered somewhere; being rendered somewhere is the bug.
 */

import { useCallback, useState } from 'react'

export interface ActionState {
  error: string | null
  notice: string | null
  busy: boolean
  /** Run an action; report failure, and success when `succeeded` is given. */
  run: (fn: () => Promise<unknown>, succeeded?: string) => Promise<boolean>
  clear: () => void
}

const detailOf = (e: unknown, fallback: string) => {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail ? String(detail) : fallback
}

/**
 * The outcome half of the rule.
 *
 * `run` returns whether it worked, so callers can branch without inspecting
 * state that has not re-rendered yet — the mistake that makes "refresh on
 * success" refresh on failure too.
 */
export function useAction(): ActionState {
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const run = useCallback(async (fn: () => Promise<unknown>, succeeded?: string) => {
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      await fn()
      // Silence on success is a defect in its own right (U10): the role change
      // that started this saved correctly and told nobody, so the only way to
      // find out was to look for a consequence that had not happened yet.
      if (succeeded) setNotice(succeeded)
      return true
    } catch (e: unknown) {
      setError(detailOf(e, 'That did not work.'))
      return false
    } finally {
      setBusy(false)
    }
  }, [])

  const clear = useCallback(() => { setError(null); setNotice(null) }, [])

  return { error, notice, busy, run, clear }
}

/**
 * The outcome, rendered **where the click happened**.
 *
 * Put this inside the same container as the button — inside the modal, inside
 * the card, inside the form. U2 is precisely what happens when it lives at the
 * page level instead: correct message, wrong side of an overlay.
 */
export function ActionMessages({ state }: { state: ActionState }) {
  if (!state.error && !state.notice) return null
  return (
    <div style={{ marginTop: 8 }}>
      {state.error && (
        <div className="callout warn" role="alert" style={{ marginBottom: 6 }}>
          {state.error}
        </div>
      )}
      {state.notice && (
        <div className="callout" role="status">{state.notice}</div>
      )}
    </div>
  )
}

/**
 * The other half: **why a control will not act, before it is clicked.**
 *
 * Takes the same list of blockers the caller uses to disable the button, so the
 * two cannot disagree — a disabled button whose explanation has drifted is the
 * `off(_k)` shape again. Renders nothing when there is nothing blocking.
 *
 * Deliberately **not** a `title` attribute. A tooltip on a disabled control is
 * unreachable on touch, suppressed by some browsers, and invisible to anyone
 * who does not think to hover the thing that is not working.
 */
export function Blockers({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return null
  return (
    <div className="sub" role="note" style={{ marginTop: 6, color: 'var(--muted)' }}>
      {reasons.length === 1 ? reasons[0] : (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {reasons.map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
    </div>
  )
}
