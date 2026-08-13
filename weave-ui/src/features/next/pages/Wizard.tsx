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
import { RefreshCwIcon } from 'lucide-react'
import {
  wizardTemplates, wizardSession, wizardPropose, wizardApply,
  type WizardTemplate, type WizardPlan, type WizardProposal
} from '@/api/weave'
import { useSettingsStore } from '@/stores/settings'
// The diff/sign half of this screen now lives in `governance/`, so ontology and
// rules use the same one rather than a copy of it (CR-001, R10). The wizard
// keeps the interview; everything from "what will change" down is shared.
import { AppliedPanel, SignOffPanel, useSignOff } from '@/features/next/governance/SignOff'
import { InForceNow, useInForce } from '@/features/next/governance/InForceNow'

type Answers = Record<string, unknown>

export default function Wizard() {
  const workspace = useSettingsStore.use.workspace()
  const [templates, setTemplates] = useState<WizardTemplate[]>([])
  const [chosen, setChosen] = useState<string | null>(null)
  const [plan, setPlan] = useState<WizardPlan | null>(null)
  const [answers, setAnswers] = useState<Answers>({})
  const [proposal, setProposal] = useState<WizardProposal | null>(null)
  const signOff = useSignOff(wizardApply)
  // What is already in force — read from the signed artifacts, so the chooser
  // below can mark the installed shape rather than presenting five equal
  // options to someone who has already chosen (U17).
  const inForce = useInForce()
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
    setBusy(true); setErr(null); setProposal(null); signOff.reset()
    try {
      const p = await wizardSession(id)
      setChosen(id); setPlan(p)
      const defaults: Answers = {}
      for (const q of p.questions) if (q.default !== undefined) defaults[q.id] = q.default
      setAnswers(defaults)
    } catch (e) { fail(e) }
    setBusy(false)
  }, [signOff])

  const propose = useCallback(async () => {
    if (!chosen) return
    setBusy(true); setErr(null); signOff.reset()
    try { setProposal(await wizardPropose(chosen, answers)) } catch (e) { fail(e) }
    setBusy(false)
  }, [chosen, answers, signOff])

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

      {/* Before the chooser, because the first question a returning user has is
          "what did I install last time?" — and until now the only way to answer
          it was to curl the ledger (U17). */}
      <div style={{ marginBottom: 12 }}>
        <InForceNow state={inForce} />
      </div>

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
              {inForce.mode === t.id && (
                <span className="badge" style={{ marginLeft: 6 }}>in force</span>
              )}
              <div style={{ color: 'var(--muted)', fontSize: 13 }}>{t.when_to_use}</div>
              {inForce.mode && inForce.mode !== t.id && (
                // Re-picking is a real change to a workspace that already has
                // governance, and the diff shows the *target* rather than the
                // delta — so the warning is here, where the choice is made.
                <div style={{ color: 'var(--warn)', fontSize: 12, marginTop: 4 }}>
                  Replaces <strong>{inForce.mode}</strong> — read the diff before signing.
                </div>
              )}
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

      {/* 3 · the diff, then the signature — the shared flow (CR-001) */}
      {proposal && (
        <SignOffPanel
          diffs={proposal.diffs}
          // Signing changes what is in force, so the panel above has to be
          // re-read — otherwise the screen that exists to say "this is current"
          // is stale the moment it matters most (U17).
          state={{
            ...signOff,
            sign: async (diffs) => {
              const applied = await signOff.sign(diffs)
              if (applied) { setProposal(null); inForce.refresh() }
              return applied
            },
          }}
          title="3 · What will change"
        />
      )}

      {/* 4 · what happened */}
      <AppliedPanel applied={signOff.applied} />
    </div>
  )
}
