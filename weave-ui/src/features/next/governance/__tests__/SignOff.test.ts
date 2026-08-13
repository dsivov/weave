/**
 * A proposal with no reason cannot be signed (CR-001, A8).
 *
 * **Why this tests a predicate rather than a button.** There is no DOM harness
 * in this project — `@types/bun` and nothing else — and adding
 * `@testing-library/react` to assert that a button is `disabled` would be a new
 * dependency, which A11 forbids without a `D-NN` and CR-001 explicitly names as
 * drift. So the rule was lifted out of the component into `canSign`, which is
 * pure and testable, and **both** the hook and the panel call it. That the two
 * call sites use it is asserted separately, in
 * `tests/test_governance_flow_has_one_rule.py`, by reading the source.
 *
 * Two claims, deliberately split:
 *
 * * *the rule is right* — here;
 * * *the rule is wired to the screen* — the Python test.
 *
 * D-038 was exactly a case of the right rule living in one file while the wrong
 * one shipped in another, so proving only the first would prove the wrong half.
 *
 * **Written here, not run here.** bun is not installed in the container this was
 * written in, and after D-036 nothing runs it automatically either.
 */

import { describe, expect, test } from 'bun:test'

import { canSign } from '../SignOff'
import type { WizardDiff } from '@/api/weave'

const DIFF: WizardDiff = {
  kind: 'ontology',
  artifact_id: 'ontology',
  from_version: 1,
  to_version: 2,
  behaviour_changed: true,
  delta: { before: {}, after: { name: 'team' } }
}

describe('canSign', () => {
  test('a reason and a diff is the only combination that signs', () => {
    expect(canSign('adopting a review gate', [DIFF])).toBe(true)
  })

  test('no reason, no signature — the invariant this file exists for', () => {
    expect(canSign('', [DIFF])).toBe(false)
  })

  test('whitespace is not a reason', () => {
    // The check is `.trim()`, and it matters: a space bar is the easiest way
    // past a required field, and "who took away my access — ' '" answers
    // nothing.
    expect(canSign('   ', [DIFF])).toBe(false)
    expect(canSign('\n\t ', [DIFF])).toBe(false)
  })

  test('nothing to sign is not signable either', () => {
    // Guards an empty apply: a request carrying zero diffs would otherwise
    // record a ledger version for a change nobody made.
    expect(canSign('a good reason', [])).toBe(false)
  })

  test('a request already in flight cannot be signed again', () => {
    // Double-click protection is part of the rule rather than a UI nicety —
    // two applies of one diff race each other into the ledger, and the second
    // loses to a stale-write 409 at best.
    expect(canSign('a good reason', [DIFF], true)).toBe(false)
  })

  test('busy defaults to false, so callers that do not track it still work', () => {
    expect(canSign('a good reason', [DIFF])).toBe(true)
  })

  test('several diffs sign together', () => {
    // The wizard applies a whole preset at once: five artifacts, one reason,
    // one signature.
    expect(canSign('install the team vocabulary', [DIFF, { ...DIFF, kind: 'rbac' }])).toBe(true)
  })
})
