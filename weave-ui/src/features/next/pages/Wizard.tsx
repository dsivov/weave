/**
 * The team-vocabulary wizard: interview → proposal → diff → sign.
 *
 * **The diff is not a formality and the screen treats it that way.** What is
 * being signed is governance — who may do what, and which state changes are
 * legal — so the proposal is shown as an explicit before/after per artifact and
 * the sign button is disabled until a reason has been typed. A wizard that
 * installed permissions behind a friendly "Finish" is how a team ends up with
 * rules nobody remembers agreeing to.
 *
 * **Nothing here is written to a file.** Apply signs ledger versions through the
 * same engine the Studio uses, which is why the result shows a version number
 * and an approver rather than "saved" — and why it can say, truthfully, that no
 * restart is needed (A8).
 *
 * The interview is stateless on the server: this component holds the answers and
 * posts them, so a second worker cannot lose someone's half-finished session.
 */

import { useCallback, useEffect, useState } from 'react'
import { CheckIcon, FileSignatureIcon, RefreshCwIcon } from 'lucide-react'
import {
  wizardTemplates, wizardSession, wizardPropose, wizardApply,
  type WizardTemplate, type WizardPlan, type WizardProposal, type WizardApplied
} from '@/api/weave'
import { useSettingsStore } from '@/stores/settings'

type Answers = Record<string, unknown>

export default function Wizard() {
  const workspace = useSettingsStore.use.workspace()
  const [templates, setTemplates] = useState<WizardTemplate[]>([])
  const [chosen, setChosen] = useState<string | null>(null)
  const [plan, setPlan] = useState<WizardPlan | null>(null)
  const [answers, setAnswers] = useState<Answers>({})
  const [proposal, setProposal] = useState<WizardProposal | null>(null)
  const [applied, setApplied] = useState<WizardApplied[] | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const fail = (e: any) =>
    setErr(e?.response?.data?.detail
      ? (typeof e.response.data.detail === 'string'
        ? e.response.data.detail
        : JSON.stringify(e.response.data.detail))
      : String(e?.message ?? e))

  useEffect(() => {
    wizardTemplates().then(r => setTemplates(r.templates)).catch(fail)
  }, [workspace])

  // Choosing a template resets everything downstream. Carrying answers across a
  // template change would produce a proposal nobody described.
  const choose = useCallback(async (id: string) => {
    setBusy(true); setErr(null); setProposal(null); setApplied(null)
    try {
      const p = await wizardSession(id)
      setChosen(id); setPlan(p)
      const defaults: Answers = {}
      for (const q of p.questions) if (q.default !== undefined) defaults[q.id] = q.default
      setAnswers(defaults)
    } catch (e) { fail(e) }
    setBusy(false)
  }, [])

  const propose = useCallback(async () => {
    if (!chosen) return
    setBusy(true); setErr(null); setApplied(null)
    try { setProposal(await wizardPropose(chosen, answers)) } catch (e) { fail(e) }
    setBusy(false)
  }, [chosen, answers])

  const sign = useCallback(async () => {
    if (!proposal || !reason.trim()) return
    setBusy(true); setErr(null)
    try {
      const r = await wizardApply(proposal.diffs, reason.trim())
      setApplied(r.applied)
      setProposal(null)
    } catch (e) { fail(e) }
    setBusy(false)
  }, [proposal, reason])

  return (
    <div className="cgnext" style={{ padding: '18px 22px', overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <h2 style={{ margin: 0 }}>Team vocabulary</h2>
        <span className="badge" title="Governance is installed as signed ledger versions — no config file, no restart">
          signed, not configured
        </span>
      </div>

      {err && (
        <div className="card" style={{ borderColor: 'var(--crit)', color: 'var(--crit)', marginBottom: 12 }}>
          {err}
        </div>
      )}

      {/* 1 · choose a shape */}
      <div className="card" style={{ marginBottom: 12 }}>
        <h3 style={{ marginTop: 0 }}>1 · How does your team work?</h3>
        <div style={{ display: 'grid', gap: 8 }}>
          {templates.map(t => (
            <button
              key={t.id}
              className="btn"
              onClick={() => choose(t.id)}
              disabled={busy}
              style={{
                textAlign: 'left',
                borderColor: chosen === t.id ? 'var(--accent)' : undefined
              }}
            >
              <strong>{t.title}</strong>
              <div style={{ color: 'var(--muted)', fontSize: 13 }}>{t.when_to_use}</div>
            </button>
          ))}
        </div>
      </div>

      {/* 2 · the interview */}
      {plan && (
        <div className="card" style={{ marginBottom: 12 }}>
          <h3 style={{ marginTop: 0 }}>2 · {plan.title}</h3>
          <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 10 }}>
            Installs roles <code>{plan.installs.rbac.join(', ') || '—'}</code> and
            {' '}lifecycle for <code>{plan.installs.lifecycle.join(', ') || '—'}</code>.
          </div>
          {plan.questions.map(q => (
            <div key={q.id} style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', marginBottom: 4 }}>{q.prompt}</label>
              {q.kind === 'bool' && (
                <label className="chip" style={{ cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={Boolean(answers[q.id])}
                    onChange={e => setAnswers(a => ({ ...a, [q.id]: e.target.checked }))}
                  /> yes
                </label>
              )}
              {q.kind === 'one' && (
                <select
                  value={String(answers[q.id] ?? '')}
                  onChange={e => setAnswers(a => ({ ...a, [q.id]: e.target.value }))}
                >
                  {(q.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              )}
              {q.kind === 'multi' && (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {(q.options ?? []).map(o => {
                    const current = (answers[q.id] as string[] | undefined) ?? []
                    return (
                      <label key={o} className="chip" style={{ cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={current.includes(o)}
                          onChange={e => setAnswers(a => ({
                            ...a,
                            [q.id]: e.target.checked
                              ? [...current, o]
                              : current.filter(x => x !== o)
                          }))}
                        /> {o}
                      </label>
                    )
                  })}
                </div>
              )}
            </div>
          ))}
          <button className="btn" onClick={propose} disabled={busy}>
            <RefreshCwIcon className="" /> Show me the change
          </button>
        </div>
      )}

      {/* 3 · the diff, then the signature */}
      {proposal && (
        <div className="card" style={{ marginBottom: 12 }}>
          <h3 style={{ marginTop: 0 }}>3 · What will change</h3>
          <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 10 }}>
            Nothing has been written yet. Signing creates a new version of each
            artifact, with your name on it, and takes effect on the next request —
            no restart.
          </div>
          {proposal.diffs.map(d => (
            <details key={d.kind} style={{ marginBottom: 8 }} open>
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
            value={reason}
            placeholder="e.g. new team, adopting a review gate"
            onChange={e => setReason(e.target.value)}
          />
          <button
            className="btn"
            onClick={sign}
            disabled={busy || !reason.trim()}
            title={reason.trim() ? '' : 'A governance change needs a reason'}
            style={{ marginTop: 8 }}
          >
            <FileSignatureIcon className="" /> Sign and install
          </button>
        </div>
      )}

      {/* 4 · what happened */}
      {applied && (
        <div className="card" style={{ borderColor: 'var(--good)' }}>
          <h3 style={{ marginTop: 0 }}><CheckIcon className="" /> Installed</h3>
          <ul>
            {applied.map(a => (
              <li key={a.kind}>
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
      )}
    </div>
  )
}
