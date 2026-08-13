/**
 * *What did we learn* and *why* — `/ask/learnings` and `/ask/why`.
 *
 * Paired because of an asymmetry worth stating: **`why` is the one question that
 * requires an anchor** (`ANCHORS` in `routers/ask.py`), so it cannot open a page
 * on its own. `learnings` answers the whole workspace with no anchor, which
 * makes it the natural way to *find* the node you then ask `why` about — click a
 * learning, get the decision record behind it.
 *
 * That is also why there is no "Why" entry in the navigation. A menu item
 * leading to a screen that demands an identifier before it can say anything is a
 * dead end; anchored on something a user just clicked, it is an answer.
 */

import { useCallback, useEffect, useState } from 'react'
import { RefreshCwIcon } from 'lucide-react'

import { ask, type Answer, type AnswerNode } from '@/api/weave'
import { useSettingsStore } from '@/stores/settings'
import { AnswerList } from '@/features/next/governance/AnswerView'

export default function Learnings() {
  const workspace = useSettingsStore.use.workspace()
  const [learnings, setLearnings] = useState<Answer | null>(null)
  const [why, setWhy] = useState<Answer | null>(null)
  const [anchor, setAnchor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [whyLoading, setWhyLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [whyError, setWhyError] = useState<string | null>(null)

  const fail = (e: unknown, fallback: string) => {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    return detail ? String(detail) : fallback
  }

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setLearnings(await ask('learnings')) }
    catch (e) { setError(fail(e, 'The answer surface is unavailable.')); setLearnings(null) }
    setLoading(false)
  }, [])

  useEffect(() => { void load() }, [load, workspace])

  const askWhy = useCallback(async (node: AnswerNode) => {
    const id = typeof node.id === 'string' ? node.id : null
    // `why` is anchor-required, so a node with no id cannot be asked about. Say
    // that rather than sending an empty anchor and rendering the 400.
    if (!id) { setWhyError('This node has no id to anchor the question on.'); setWhy(null); return }
    setAnchor(id); setWhyLoading(true); setWhyError(null)
    try { setWhy(await ask('why', id)) }
    catch (e) { setWhyError(fail(e, 'No decision record for that node.')); setWhy(null) }
    setWhyLoading(false)
  }, [])

  return (
    <div className="cgnext" style={{ padding: '18px 22px', overflow: 'auto' }}>
      <div className="phead">
        <div>
          <div className="eyebrow">Weave · Answers</div>
          <h1>Learnings</h1>
          <p>What reviews taught us — and, for any of them, the decision behind it.</p>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={() => void load()} disabled={loading}>
            <RefreshCwIcon className="" />Refresh
          </button>
        </div>
      </div>

      <div className="grid-cards">
        <div className="card"><span className="stripe accent" />
          <div className="chead">
            <h3>What did we learn</h3><span className="sub">/ask/learnings · pick one to ask why</span>
          </div>
          <div className="cbody">
            <AnswerList
              answer={learnings} loading={loading} error={error} onPick={askWhy}
              empty="No insights recorded yet. They appear as reviews are written and learnings are captured."
            />
          </div>
        </div>

        <div className="card"><span className="stripe" />
          <div className="chead">
            <h3>Why</h3>
            <span className="sub">{anchor ? <code>{anchor}</code> : '/ask/why — needs an anchor'}</span>
          </div>
          <div className="cbody">
            {!anchor ? (
              <div className="empty">
                Pick a learning on the left. This question is the only one that
                needs a node to anchor on, so it has no answer until you choose one.
              </div>
            ) : (
              <AnswerList
                answer={why} loading={whyLoading} error={whyError}
                empty="Nothing justifies this node yet — no decision record points at it."
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
