/**
 * Propose → diff → sign, for any ledger-owned artifact (CR-001).
 *
 * **The missing half of the CR's headline.** `Ontology` and `Rules` were
 * `<textarea>` elements of `JSON.stringify` whose Save button wrote straight
 * through. The capability to do better was finished server-side three phases
 * ago — D-032/033/034 made all seven ledger kinds sign through `DiffEngine`,
 * and the wizard proved the interaction in P4 — and the UI never caught up.
 *
 * This is the adapter between the two: it proposes a draft through
 * `/studio/propose`, hands the resulting diff to the shared `SignOffPanel`, and
 * applies it through `/studio/apply` with the reason the user typed.
 *
 * **Why `/studio/*` rather than `POST /ontology`.** Both sign correctly since
 * D-038. Only the studio pair produces a **diff to look at first**, which is the
 * whole point: what is being signed changes who may do what, and a Save button
 * that shows nothing before writing is the thing CR-001 exists to replace.
 *
 * **`sign()` returns `null` when it refuses** — empty reason, nothing to sign,
 * or already in flight. Callers must treat that as *nothing happened*; a screen
 * that assumes success on `null` reports a save that never occurred, which is
 * the failure D-038 was made of.
 */

import { useCallback, useState } from 'react'

import {
  studioApply, studioPropose,
  type ArtifactDiff, type StudioKind, type WizardApplied, type WizardDiff
} from '@/api/weave'
import { useSignOff, type SignOffState } from './SignOff'

export interface GovernedArtifact {
  /** The pending change, or null when there is nothing proposed. */
  diff: ArtifactDiff | null
  /** Compute a diff for *draft* without writing anything. */
  propose: (draft: unknown) => Promise<ArtifactDiff | null>
  /** Reason, busy, error, applied versions, and `sign()`. */
  signOff: SignOffState
  /** Abandon the proposal — used by "Discard" and on a workspace change. */
  clear: () => void
  proposing: boolean
  proposeError: string | null
}

const detailOf = (e: unknown, fallback: string) => {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail ? String(detail) : fallback
}

export function useGovernedArtifact(
  kind: StudioKind,
  artifactId: string
): GovernedArtifact {
  const [diff, setDiff] = useState<ArtifactDiff | null>(null)
  const [proposing, setProposing] = useState(false)
  const [proposeError, setProposeError] = useState<string | null>(null)

  // One diff at a time, so the shared panel's array is always length 1 here.
  // `studioApply` re-assesses server-side before applying, so a diff edited in
  // flight is rejected rather than trusted (`routers/studio.py` — "anti-tamper").
  const apply = useCallback(
    async (diffs: WizardDiff[], reason: string): Promise<{ applied: WizardApplied[] }> => {
      const result = await studioApply(diffs[0] as unknown as ArtifactDiff, { reason })
      return { applied: [result as unknown as WizardApplied] }
    },
    []
  )

  const signOff = useSignOff(apply)

  const propose = useCallback(async (draft: unknown) => {
    setProposing(true)
    setProposeError(null)
    signOff.reset()
    try {
      const { diff: proposed } = await studioPropose({ kind, artifact_id: artifactId, draft })
      setDiff(proposed)
      return proposed
    } catch (e) {
      setProposeError(detailOf(e, 'Could not work out what this would change.'))
      setDiff(null)
      return null
    } finally {
      setProposing(false)
    }
  }, [kind, artifactId, signOff])

  const clear = useCallback(() => {
    setDiff(null); setProposeError(null); signOff.reset()
  }, [signOff])

  return { diff, propose, signOff, clear, proposing, proposeError }
}
