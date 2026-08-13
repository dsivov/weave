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
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ? String(detail) : 'The answer surface is unavailable.')
      setFeatures(null); setChanges(null)
    }
    setLoading(false)
  }, [])

  useEffect(() => { void load(applied) }, [load, applied, workspace])

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
          <input
            style={{ flex: 1 }}
            value={feature}
            placeholder="Narrow to one feature — leave empty for the whole workspace"
            onChange={(e) => setFeature(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') setApplied(feature.trim()) }}
          />
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
            <AnswerList
              answer={features} loading={loading} error={error}
              empty="No capabilities recorded yet. They appear as features and the documents describing them are ingested."
            />
          </div>
        </div>

        <div className="card"><span className="stripe" />
          <div className="chead"><h3>What changed</h3><span className="sub">/ask/changes</span></div>
          <div className="cbody">
            <AnswerList
              answer={changes} loading={loading} error={error}
              empty="No delivery chain yet — this fills in as tasks are claimed, reviewed and merged."
            />
          </div>
        </div>
      </div>
    </div>
  )
}
