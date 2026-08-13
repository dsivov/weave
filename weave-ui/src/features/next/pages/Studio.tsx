/**
 * Studio — the governance ledger's history (CR-001).
 *
 * **Authoring has moved out of here, and that is the whole change.** Governance
 * is now authored where it lives: the ontology on its own page, rules on theirs,
 * the team vocabulary in the wizard — each through the one shared
 * propose → diff → sign flow in `governance/SignOff.tsx`. Two places to author
 * one artifact is how surfaces drift, and the ledger is the one thing that must
 * not.
 *
 * What is left is the question this screen answers better than any other:
 * **what changed, who signed it, and how do I put it back.** Reverting stays,
 * because rolling a version back *is* reading history and acting on it — and it
 * re-applies an old snapshot as a new signed version rather than erasing
 * anything.
 *
 * The approver text box is gone (D-038). It let a user type any name and have it
 * recorded as the signer, while validating only that the name was non-empty —
 * a check that made the illusion look like a guarantee. The server derives the
 * signer from the token now; a reason is still asked for, because that part was
 * always legitimately the caller's.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { RefreshCwIcon, HistoryIcon, RotateCcwIcon, NetworkIcon } from 'lucide-react'
import { useSettingsStore } from '@/stores/settings'
import Mermaid from '@/features/next/Mermaid'
import {
  studioArtifacts, studioHistory, studioRevert, studioGraph,
  type StudioKind, type StudioArtifactRow, type StudioVersion,
  type StudioGraphNode, type StudioGraphEdge
} from '@/api/weave'

const errMsg = (e: any) => e?.response?.data?.detail || e?.message || String(e)

const rowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  gap: 8, fontSize: 13, padding: '5px 2px'
}

export default function Studio() {
  const workspace = useSettingsStore.use.workspace()
  const [busy, setBusy] = useState<string | null>(null)
  const [artifacts, setArtifacts] = useState<StudioArtifactRow[] | null>(null)
  const [selected, setSelected] = useState<{ kind: string; id: string } | null>(null)
  const [history, setHistory] = useState<StudioVersion[] | null>(null)
  const [graph, setGraph] = useState<{ nodes: StudioGraphNode[]; edges: StudioGraphEdge[] } | null>(null)

  const refreshArtifacts = useCallback(async () => {
    try { setArtifacts((await studioArtifacts()).artifacts) } catch { setArtifacts([]) }
    try { setGraph(await studioGraph()) } catch { setGraph(null) }
  }, [])

  useEffect(() => {
    refreshArtifacts()
    setSelected(null); setHistory(null)
  }, [refreshArtifacts, workspace])

  const openHistory = useCallback(async (k: string, id: string) => {
    setSelected({ kind: k, id }); setHistory(null)
    try { setHistory((await studioHistory(k as StudioKind, id)).history) }
    catch (e) { toast.error(errMsg(e)) }
  }, [])

  const revert = useCallback(async (v: StudioVersion) => {
    // A reason, and only a reason. Who signs comes from the token (D-038) — this
    // used to prompt for an approver, which meant the person undoing a change
    // chose whose name went on the undo.
    const why = window.prompt('Reason for the revert:', `roll back to v${v.version}`)
    if (!why) return
    setBusy('revert')
    const tid = toast.loading('Reverting…')
    try {
      const r = await studioRevert(v.kind as StudioKind, v.artifact_id, v.version, why)
      toast.success(`Reverted → new v${r.version}`, { id: tid })
      refreshArtifacts(); openHistory(v.kind, v.artifact_id)
    } catch (e) { toast.error(errMsg(e), { id: tid }) }
    finally { setBusy(null) }
  }, [refreshArtifacts, openHistory])

  const mermaid = useMemo(() => graph ? toMermaid(graph.nodes, graph.edges) : '', [graph])

  return (
    <div className="view">
      <div className="phead">
        <div>
          <div className="eyebrow">Governance · History</div>
          <h1>What changed, and who signed it</h1>
          <p>Every governed artifact keeps every version. Authoring happens on the
            ontology, rules and team-vocabulary screens; this is the record.</p>
        </div>
        <div className="actions">
          {busy && <span className="chip accent" style={{ alignSelf: 'center' }}><span className="cgspin" />Working…</span>}
          <button className="btn ghost" onClick={refreshArtifacts} disabled={busy !== null}>
            <RefreshCwIcon className="" />Refresh
          </button>
        </div>
      </div>

      <div className="card"><span className="stripe comm" />
        <div className="chead">
          <h3>Tracked artifacts</h3>{artifacts && <span className="sub">{artifacts.length}</span>}
        </div>
        <div className="cbody" style={{ paddingTop: 0 }}>
          <div className="box" style={{ maxHeight: 220, overflow: 'auto' }}>
            {!artifacts && <div className="empty" style={{ padding: 12 }}>Loading…</div>}
            {artifacts && artifacts.length === 0 && (
              <div className="empty" style={{ padding: 12 }}>
                Nothing signed yet. Install the team vocabulary, or edit the ontology or rules.
              </div>
            )}
            {artifacts?.map((a) => (
              <div key={a.kind + ':' + a.artifact_id} style={rowStyle}>
                <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  <span className="chip">{a.kind}</span> <code className="mono">{a.artifact_id}</code>{' '}
                  <span className="sub">v{a.version} · {a.revisions} rev</span>
                </span>
                <button className="btn sm ghost" onClick={() => openHistory(a.kind, a.artifact_id)}>
                  <HistoryIcon className="" />History
                </button>
              </div>
            ))}
          </div>

          {selected && (
            <>
              <div className="divider" />
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <strong style={{ fontSize: 13 }}>{selected.kind}:{selected.id} — ledger</strong>
                <button className="btn sm ghost" onClick={() => { setSelected(null); setHistory(null) }}>Close</button>
              </div>
              <div className="box" style={{ maxHeight: 300, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
                {!history && <div className="empty" style={{ padding: 12 }}>Loading…</div>}
                {history && history.length === 0 && <div className="empty" style={{ padding: 12 }}>No versions.</div>}
                {history && [...history].reverse().map((v) => (
                  <div key={v.version} className="ledrow">
                    <div style={{ minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span className="chip accent">v{v.version}</span>
                        <span className={'dot ' + (v.behaviour_changed ? 'warn' : 'good')} />
                        <span className="sub">{v.origin}</span>
                      </div>
                      {v.sign_off && (
                        <div className="sub" style={{ marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {v.sign_off.approver} — “{v.sign_off.reason}”
                        </div>
                      )}
                    </div>
                    <button className="btn sm ghost" disabled={busy !== null} onClick={() => revert(v)}>
                      <RotateCcwIcon className="" />Revert
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}><span className="stripe crit" />
        <div className="chead">
          <h3><NetworkIcon className="" style={{ verticalAlign: '-3px', marginRight: 6 }} />Component map</h3>
          <span className="sub">{graph ? `${graph.nodes.length} components · ${graph.edges.length} links` : '—'}</span>
        </div>
        <div className="cbody">
          {!graph || graph.nodes.length === 0
            ? <div className="empty" style={{ padding: 16 }}>No components yet — sign a flow, rule, action or ontology to see how they wire together.</div>
            : <div className="mermaid-wrap"><Mermaid chart={mermaid} /></div>}
        </div>
      </div>
    </div>
  )
}

// Build a mermaid graph from the component nodes/edges.
function toMermaid(nodes: StudioGraphNode[], edges: StudioGraphEdge[]): string {
  const key: Record<string, string> = {}
  nodes.forEach((n, i) => { key[n.id] = 'n' + i })
  const esc = (s: string) => (s || '').replace(/"/g, '\'')
  const shape = (n: StudioGraphNode) => {
    const l = `"${esc(n.label)}"`
    if (n.kind === 'action') return `(${l})`
    if (n.kind === 'rule') return `{{${l}}}`
    if (n.kind === 'object') return `([${l}])`
    return `[${l}]`
  }
  const lines = [
    'graph LR',
    'classDef flow fill:#6366f1,stroke:#4f46e5,color:#fff;',
    'classDef action fill:#0ea5e9,stroke:#0284c7,color:#fff;',
    'classDef rule fill:#f59e0b,stroke:#b45309,color:#111;',
    'classDef object fill:#10b981,stroke:#059669,color:#fff;'
  ]
  nodes.forEach((n) => lines.push(`${key[n.id]}${shape(n)}:::${n.kind}`))
  edges.forEach((e) => { if (key[e.src] && key[e.dst]) lines.push(`${key[e.src]} -->|${esc(e.rel)}| ${key[e.dst]}`) })
  return lines.join('\n')
}
