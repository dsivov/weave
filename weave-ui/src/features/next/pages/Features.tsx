/**
 * *What does it do* and *what changed* — `/ask/features` and `/ask/changes`.
 *
 * Both have existed since P2 with no UI (CR-001 §1). Neither requires an anchor,
 * so this page opens with the whole workspace answered and narrows only if the
 * user types a feature — which is the right way round for a landing surface: it
 * shows something before it asks for anything.
 *
 * The two questions share a page because they share an anchor. "What does this
 * feature do" and "what changed in it" are the same question asked of the same
 * noun at two moments, and splitting them across screens would make a reader
 * type the feature twice.
 */

import { useCallback, useEffect, useState } from 'react'
import { RefreshCwIcon, SearchIcon } from 'lucide-react'

import { ask, type Answer } from '@/api/weave'
import { useSettingsStore } from '@/stores/settings'
import { AnswerList } from '@/features/next/governance/AnswerView'

export default function Features() {
  const workspace = useSettingsStore.use.workspace()
  const [feature, setFeature] = useState('')
  const [applied, setApplied] = useState('')
  const [features, setFeatures] = useState<Answer | null>(null)
  // Every Feature node in the workspace, from the unanchored answer. Kept so the
  // anchor can be *chosen* rather than guessed at (U5).
  const [known, setKnown] = useState<string[]>([])
  const [changes, setChanges] = useState<Answer | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (anchor: string) => {
    setLoading(true); setError(null)
    try {
      const [f, c] = await Promise.all([
        ask('features', anchor || undefined),
        ask('changes', anchor || undefined)
      ])
      setFeatures(f); setChanges(c)
      // Only the unanchored answer describes the whole workspace, so that is the
      // one that can list what exists.
      if (!anchor) {
        setKnown(
          f.nodes
            .map((n) => (typeof n.id === 'string' ? n.id : ''))
            .filter(Boolean)
            .sort()
        )
      }
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ? String(detail) : 'The answer surface is unavailable.')
      setFeatures(null); setChanges(null)
    }
    setLoading(false)
  }, [])

  useEffect(() => { void load(applied) }, [load, applied, workspace])

  // An anchor does not survive a workspace change, and the known-id list must
  // not either.
  //
  // Found by the manager asking whether the datalist is populated from the
  // *unanchored* answer even when the page already has an anchor. On first load
  // it always is — `applied` starts empty. But switch workspace with an anchor
  // set and `load(applied)` runs anchored, so `known` would keep the **previous
  // workspace's** feature ids and offer them as choices where none exist. A node
  // id is meaningless across that boundary.
  //
  // Adjusted during render rather than in an effect (the pattern from
  // `ChatMessage`): as an effect this would render one frame of the new
  // workspace showing the old workspace's anchor and id list, which is the
  // wrong-data-briefly-on-screen case W13 measured 48 of.
  const [anchoredIn, setAnchoredIn] = useState(workspace)
  if (anchoredIn !== workspace) {
    setAnchoredIn(workspace)
    setFeature(''); setApplied(''); setKnown([])
  }

  return (
    <div className="cgnext" style={{ padding: '18px 22px', overflow: 'auto' }}>
      <div className="phead">
        <div>
          <div className="eyebrow">Weave · Answers</div>
          <h1>Features</h1>
          <p>What the system does, and what changed in it — the same handler an agent asks.</p>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={() => void load(applied)} disabled={loading}>
            <RefreshCwIcon className="" />Refresh
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 8 }}>
          {/* The anchor is a **node id**, not a name (U5).
              `ask_features` uses it directly as a graph seed — `seeds = [feature]`
              — so a plain word like "authentication" matches nothing and the page
              then reported an empty system. Offering the ids that exist turns an
              unguessable field into a choice; the input stays free-text because
              an id from elsewhere is legitimate. */}
          <input
            style={{ flex: 1 }}
            list="feature-ids"
            value={feature}
            placeholder={known.length
              ? `Anchor on a feature node id — e.g. ${known[0]}`
              : 'Anchor on a feature node id — leave empty for the whole workspace'}
            onChange={(e) => setFeature(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') setApplied(feature.trim()) }}
          />
          <datalist id="feature-ids">
            {known.map((id) => <option key={id} value={id} />)}
          </datalist>
          <button className="btn" onClick={() => setApplied(feature.trim())} disabled={loading}>
            <SearchIcon className="" />Ask
          </button>
        </div>
        {applied && (
          <div style={{ color: 'var(--muted)', fontSize: 13, marginTop: 6 }}>
            Anchored on <code>{applied}</code> —{' '}
            <a onClick={() => { setFeature(''); setApplied('') }} style={{ cursor: 'pointer' }}>
              ask the whole workspace
            </a>
          </div>
        )}
      </div>

      <div className="grid-cards">
        <div className="card"><span className="stripe accent" />
          <div className="chead"><h3>What does it do</h3><span className="sub">/ask/features</span></div>
          <div className="cbody">
            {/* Two different emptinesses, and saying the wrong one is the defect
                (U5). "Nothing here" describes the workspace; "that anchor matched
                nothing" describes the query. Reporting the first when the second
                is true is how a user concludes their system is empty. */}
            <AnswerList
              answer={features} loading={loading} error={error}
              empty={applied
                ? `Nothing is anchored on “${applied}”. The anchor is a node id — ` +
                  (known.length
                    ? 'pick one from the list, or clear it to see the whole workspace.'
                    : 'this workspace has no Feature nodes to anchor on yet.')
                : 'No capabilities recorded yet. They appear as features are created and the documents describing them are ingested.'}
            />
          </div>
        </div>

        <div className="card"><span className="stripe" />
          <div className="chead"><h3>What changed</h3><span className="sub">/ask/changes</span></div>
          <div className="cbody">
            <AnswerList
              answer={changes} loading={loading} error={error}
              empty={applied
                ? `Nothing changed under “${applied}”. If that is not a feature node id, the anchor matched nothing.`
                : 'No delivery chain yet — this fills in as tasks are claimed, reviewed and merged.'}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
