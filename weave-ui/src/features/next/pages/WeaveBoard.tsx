import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCwIcon, PauseIcon, PlayIcon, SquareIcon, GitPullRequestIcon, CircleDotIcon } from 'lucide-react'
import {
  weaveStatus, weaveTasks, weaveWorkers, weaveEnvironments, weaveChain,
  weaveControlWorker, weaveAdvanceTask, weavePromote, weaveReviewAuto,
  type WeaveTask, type WeaveWorker, type WeaveEnvironment
} from '@/api/weave'
import { useSettingsStore } from '@/stores/settings'
import { useLiveStream } from '@/hooks/useLiveStream'
import Modal from '@/features/next/Modal'
import WeaveProjectPanel from '@/features/next/WeaveProjectPanel'

// The lifecycle columns, in flow order, each with a house token.
const COLUMNS: { key: string; label: string; tone: string }[] = [
  { key: 'pending', label: 'Pending', tone: 'var(--muted)' },
  { key: 'in_progress', label: 'In progress', tone: 'var(--accent)' },
  { key: 'review', label: 'Review', tone: 'var(--warn)' },
  { key: 'approved', label: 'Approved', tone: 'var(--comm)' },
  { key: 'testing', label: 'Testing', tone: 'var(--warn)' },
  { key: 'done', label: 'Done', tone: 'var(--good)' },
  { key: 'blocked', label: 'Blocked', tone: 'var(--crit)' }
]

const workerTone = (w: WeaveWorker) =>
  w.status === 'offline' ? 'var(--muted)'
    : w.status === 'stopped' ? 'var(--crit)'
      : w.status === 'paused' ? 'var(--warn)' : 'var(--good)'

export default function WeaveBoard() {
  const workspace = useSettingsStore.use.workspace()
  const [status, setStatus] = useState<any>(null)
  const [tasks, setTasks] = useState<WeaveTask[]>([])
  const [workers, setWorkers] = useState<WeaveWorker[]>([])
  const [envs, setEnvs] = useState<WeaveEnvironment[]>([])
  const [loading, setLoading] = useState(true)
  const [auto, setAuto] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)   // task id whose chain is shown
  const [chain, setChain] = useState<any>(null)

  const load = useCallback(async () => {
    try {
      const s = await weaveStatus()
      setStatus(s)
      if (s?.installed) {
        const [t, w, e] = await Promise.all([weaveTasks(), weaveWorkers(), weaveEnvironments()])
        setTasks(t?.tasks ?? []); setWorkers(w?.workers ?? []); setEnvs(e?.environments ?? [])
      } else {
        setTasks([]); setWorkers([]); setEnvs([])
      }
      setErr(null)
    } catch (e: any) {
      setErr(e?.response?.data?.detail ? String(e.response.data.detail) : 'Weave is unavailable (is ENABLE_WEAVE set?)')
    }
    setLoading(false)
  }, [])

  useEffect(() => { setLoading(true); load() }, [load, workspace])

  // Live, not polled (R32). The board used to reload every 4 seconds, which is
  // not merely wasteful: for up to 4 seconds two people could each act on the
  // same task believing it was unclaimed. Now the server says when something
  // changed and the board reloads then.
  //
  // `auto` still gates it, so someone reading the board mid-incident can freeze
  // it — the difference is that pausing now means "stop applying updates"
  // rather than "stop asking".
  const onLive = useCallback((event: { type: string }) => {
    if (!auto) return
    // Presence is people moving around, not work changing state; reloading the
    // whole board for it would reintroduce a poll with extra steps.
    if (event.type === 'live.presence') return
    void load()
  }, [auto, load])

  const { connected } = useLiveStream(onLive)

  const openChain = useCallback(async (id: string) => {
    setOpen(id); setChain(null)
    try { setChain(await weaveChain(id)) } catch { setChain({ error: true }) }
  }, [])

  const act = useCallback(async (fn: () => Promise<any>) => {
    try { await fn(); await load(); if (open) await openChain(open) }
    catch (e: any) { setErr(e?.response?.data?.detail ? String(e.response.data.detail) : 'Action failed') }
  }, [load, open, openChain])

  const byStatus = useMemo(() => {
    const m: Record<string, WeaveTask[]> = {}
    for (const c of COLUMNS) m[c.key] = []
    for (const t of tasks) (m[t.status] ??= []).push(t)
    return m
  }, [tasks])

  const openTask = open ? tasks.find((t) => t.id === open) : null

  return (
    <div className="cgnext" style={{ padding: '18px 22px', overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <div>
          <div className="eyebrow">Weave · distributed AI dev team</div>
          <h2 style={{ margin: '2px 0 0', fontSize: 22 }}>Team board</h2>
        </div>
        <div style={{ flex: 1 }} />
        {status && (
          <span className="badge" style={{ background: status.installed ? 'var(--good-dim)' : 'var(--warn-dim)' }}>
            {status.enabled ? (status.installed ? 'installed' : 'not bootstrapped') : 'disabled'}
          </span>
        )}
        {/* The stream's state, shown rather than assumed. A dropped connection
            looks exactly like a quiet system — the board just stops changing —
            so a reader is told when what they see may be stale. */}
        <span
          className="badge"
          title={connected
            ? 'Live — the server pushes changes as they happen'
            : 'Reconnecting — this board may be out of date'}
          style={{ background: connected ? 'var(--good-dim)' : 'var(--warn-dim)' }}
        >
          {connected ? 'live' : 'reconnecting…'}
        </span>
        <label className="chip" style={{ cursor: 'pointer' }}>
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto
        </label>
        <button className="btn" onClick={load}><RefreshCwIcon className="" /> Refresh</button>
      </div>

      {err && <div className="card" style={{ borderColor: 'var(--crit)', color: 'var(--crit)', marginBottom: 12 }}>{err}</div>}
      {loading && !status && <div className="empty">Loading…</div>}
      {status && !status.installed && (
        <div className="card">This workspace isn’t bootstrapped for Weave yet. Run <code>POST /weave/bootstrap</code> as a manager/architect.</div>
      )}

      {status?.installed && (
        <>
          {/* What the team builds, and the machines that build it */}
          <WeaveProjectPanel onError={setErr} />

          {/* Fleet */}
          <div style={{ marginBottom: 8, fontWeight: 700, color: 'var(--text2)' }}>Fleet · {workers.length}</div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 18 }}>
            {workers.length === 0 && <div className="empty">No workers registered.</div>}
            {workers.map((w) => (
              <div key={w.id} className="card" style={{ minWidth: 210, padding: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="dot" style={{ background: workerTone(w) }} />
                  <b style={{ fontSize: 14 }}>{w.id}</b>
                  <span className="chip">{w.role}</span>
                </div>
                <div style={{ color: 'var(--muted)', fontSize: 12, margin: '6px 0' }}>
                  {w.status}{w.current_task ? ` · on ${w.current_task}` : ''}{w.host ? ` · ${w.host}` : ''}
                </div>
                <div className="btns">
                  <button className="btn" title="pause" onClick={() => act(() => weaveControlWorker(w.id, 'pause'))}><PauseIcon className="" /></button>
                  <button className="btn" title="resume" onClick={() => act(() => weaveControlWorker(w.id, 'resume'))}><PlayIcon className="" /></button>
                  <button className="btn" title="stop" onClick={() => act(() => weaveControlWorker(w.id, 'stop'))}><SquareIcon className="" /></button>
                </div>
              </div>
            ))}
          </div>

          {/* Environments */}
          {envs.length > 0 && (
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 18 }}>
              {envs.map((e) => (
                <div key={e.id} className="chip" title={e.url}>
                  <CircleDotIcon className="" /> {e.name} · <span style={{ color: 'var(--good)' }}>{e.status}</span>
                  {e.url && <> · <a href={e.url} target="_blank" rel="noreferrer">open</a></>}
                </div>
              ))}
            </div>
          )}

          {/* Kanban */}
          <div style={{ display: 'grid', gridAutoFlow: 'column', gridAutoColumns: 'minmax(180px,1fr)', gap: 10, overflowX: 'auto' }}>
            {COLUMNS.map((c) => (
              <div key={c.key} style={{ background: 'var(--surface2)', border: '1px solid var(--line)', borderRadius: 'var(--r)', padding: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '2px 4px 8px', fontWeight: 700, fontSize: 13 }}>
                  <span className="dot" style={{ background: c.tone }} /> {c.label}
                  <span style={{ color: 'var(--muted)', fontWeight: 500 }}>{byStatus[c.key]?.length ?? 0}</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {(byStatus[c.key] ?? []).map((t) => (
                    <button key={t.id} className="card" onClick={() => openChain(t.id)}
                      style={{ textAlign: 'left', padding: 10, cursor: 'pointer', borderLeft: `3px solid ${c.tone}` }}>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                        <b style={{ fontSize: 13 }}>{t.id}</b>
                        {t.priority !== 'normal' && <span className="chip">{t.priority}</span>}
                        {t.pull_request && <GitPullRequestIcon className="" style={{ color: 'var(--comm)', width: 14 }} />}
                      </div>
                      <div style={{ fontSize: 12.5, color: 'var(--text2)', margin: '3px 0' }}>{t.title || '—'}</div>
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {t.assignee && <span className="chip">{t.assignee}</span>}
                        {t.touches.map((m) => <span key={m} className="chip" style={{ color: 'var(--muted)' }}>{m}</span>)}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {open && (
        <Modal title={openTask ? `${openTask.id} · ${openTask.title}` : open}
          subtitle={openTask ? `status: ${openTask.status}` : ''} onClose={() => setOpen(null)} width={640}>
          {!chain && <div className="empty">Loading chain…</div>}
          {chain?.error && <div className="err">Could not load the chain.</div>}
          {chain && !chain.error && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {chain.change_request && <div><b>Change request:</b> {chain.change_request}</div>}
              <ChainSection title="Commits" empty="no commits">
                {chain.commits?.map((c: any, i: number) => (
                  <div key={i} className="chip"><code>{c.sha?.slice(0, 8)}</code> {c.subject}</div>
                ))}
              </ChainSection>
              <ChainSection title="Pull request" empty="no PR">
                {chain.pull_request && (
                  <div className="chip"><GitPullRequestIcon className="" /> {chain.pull_request.branch} · {chain.pull_request.status}</div>
                )}
              </ChainSection>
              <ChainSection title="Reviews" empty="no reviews">
                {chain.reviews?.map((r: any, i: number) => (
                  <div key={i} className="chip" style={{ color: r.verdict === 'approve' ? 'var(--good)' : 'var(--warn)' }}>
                    {r.verdict} · {r.by}{r.notes ? ` — ${r.notes}` : ''}
                  </div>
                ))}
              </ChainSection>
              {chain.learnings?.length > 0 && (
                <ChainSection title="Learnings" empty="">
                  {chain.learnings.map((l: string, i: number) => <div key={i} className="chip">{l}</div>)}
                </ChainSection>
              )}

              {/* contextual governed actions (subject to your role) */}
              <div className="btns" style={{ borderTop: '1px solid var(--line)', paddingTop: 10 }}>
                {openTask?.status === 'review' && (
                  <>
                    <button className="btn" onClick={() => act(() => weaveReviewAuto(open))}>Run auto-review</button>
                    <button className="btn" onClick={() => act(() => weaveAdvanceTask(open, 'approved'))}>Approve (architect)</button>
                  </>
                )}
                {openTask?.status === 'approved' && envs[0] && (
                  <button className="btn" onClick={() => act(() => weavePromote(open, envs[0].id))}>Promote via {envs[0].name} (integrator)</button>
                )}
              </div>
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}

function ChainSection({ title, empty, children }: { title: string; empty: string; children: React.ReactNode }) {
  const has = Array.isArray(children) ? children.some(Boolean) : !!children
  return (
    <div>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>{title}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {has ? children : <span style={{ color: 'var(--muted)', fontSize: 12 }}>{empty}</span>}
      </div>
    </div>
  )
}
