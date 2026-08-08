/**
 * The project a workspace's developers work on, and the machines that carry them.
 *
 * These belong together on screen because they are two halves of one question:
 * *what* is being built, and *where*. A dev host is generic until the project
 * tells it which repository to clone — so an unconfigured workspace shows the
 * setup wizard rather than an empty fleet nobody can explain.
 *
 * Scaling here is deliberately not a "start container" button. Writing a desired
 * worker count is a statement of intent that each machine reads on its next
 * heartbeat and reconciles to; nothing dials out to a host. That is what lets a
 * box behind NAT still be run from this screen.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ServerIcon, FolderGitIcon, PlayIcon, PauseIcon, SquareIcon, DropletIcon,
  MinusIcon, PlusIcon, CheckIcon, AlertTriangleIcon, PencilIcon
} from 'lucide-react'
import {
  weaveProject, weaveSetProject, weaveHosts, weaveControlHost, weaveScaleHost,
  type WeaveProject, type WeaveHost
} from '@/api/weave'

const splitCmd = (s: string) => s.trim().split(/\s+/).filter(Boolean)

const seatTone = (seat: string) =>
  seat === 'ok' ? 'var(--good)' : seat === 'unknown' ? 'var(--muted)' : 'var(--crit)'

const hostTone = (h: WeaveHost) =>
  h.status === 'offline' ? 'var(--muted)'
    : h.status === 'stopped' ? 'var(--crit)'
    : h.status === 'paused' ? 'var(--warn)'
    : h.status === 'draining' ? 'var(--comm)' : 'var(--good)'

/** Why a machine is idle, in the words a human would use. */
function seatExplanation(h: WeaveHost): string | null {
  if (h.seat === 'ok') return null
  if (h.seat === 'missing')
    return 'No Claude subscription on this machine — run `claude auth login` on it.'
  if (h.seat === 'expired')
    return 'Logged in, but no token can reach its containers — run `claude setup-token` on it.'
  return 'This machine has not reported its subscription seat yet.'
}

export default function WeaveProjectPanel({ onError }: { onError?: (m: string) => void }) {
  const [project, setProject] = useState<WeaveProject | null>(null)
  const [hosts, setHosts] = useState<WeaveHost[]>([])
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)

  // draft fields
  const [repo, setRepo] = useState('')
  const [branch, setBranch] = useState('main')
  const [image, setImage] = useState('')
  const [testCmd, setTestCmd] = useState('')
  const [setupCmd, setSetupCmd] = useState('')

  const load = useCallback(async () => {
    try {
      const [p, h] = await Promise.all([weaveProject(), weaveHosts()])
      setProject(p)
      setHosts(h.hosts ?? [])
      if (!editing) {
        setRepo(p.repo); setBranch(p.base_branch || 'main'); setImage(p.image)
        setTestCmd((p.test_command ?? []).join(' '))
        setSetupCmd((p.setup_command ?? []).join(' '))
      }
    } catch {
      /* Weave may not be installed in this workspace; the board says so already */
    }
  }, [editing])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const id = setInterval(() => { if (!editing) void load() }, 5000)
    return () => clearInterval(id)
  }, [load, editing])

  const save = useCallback(async () => {
    setBusy(true)
    try {
      await weaveSetProject({
        repo: repo.trim(), base_branch: branch.trim() || 'main', image: image.trim(),
        test_command: splitCmd(testCmd), setup_command: splitCmd(setupCmd)
      })
      setEditing(false)
      await load()
    } catch (e: any) {
      onError?.(e?.response?.data?.detail ? String(e.response.data.detail)
        : 'Could not save the project (supervisors only).')
    }
    setBusy(false)
  }, [repo, branch, image, testCmd, setupCmd, load, onError])

  const act = useCallback(async (fn: () => Promise<any>) => {
    try { await fn(); await load() }
    catch (e: any) {
      onError?.(e?.response?.data?.detail ? String(e.response.data.detail) : 'Action failed')
    }
  }, [load, onError])

  const unconfigured = project !== null && !project.repo
  const showForm = editing || unconfigured

  return (
    <div style={{ marginBottom: 18 }}>
      {/* ── the project ─────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 14, marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: showForm ? 10 : 0 }}>
          <FolderGitIcon className="" style={{ width: 16 }} />
          <b style={{ fontSize: 14 }}>Project</b>
          {unconfigured && (
            <span className="badge" style={{ background: 'var(--warn-dim)' }}>set this first</span>
          )}
          <div style={{ flex: 1 }} />
          {!showForm && (
            <button className="btn sm ghost" onClick={() => setEditing(true)}>
              <PencilIcon className="" />Edit
            </button>
          )}
        </div>

        {unconfigured && !editing && (
          <p style={{ fontSize: 12, color: 'var(--muted)', margin: '0 0 10px' }}>
            Dev hosts are generic until they know what to build. Set the repository once
            here and every machine that registers into this workspace picks it up on its
            next heartbeat — there is nothing to configure on the machines themselves.
          </p>
        )}

        {showForm ? (
          <div style={{ display: 'grid', gap: 8 }}>
            <label className="fieldlabel">Repository — a clone URL the hosts can reach</label>
            <input className="cgqinput" placeholder="git@github.com:acme/app.git"
                   value={repo} onChange={(e) => setRepo(e.target.value)} />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <div style={{ flex: '1 1 160px' }}>
                <label className="fieldlabel">Base branch — what each task branches from</label>
                <input className="cgqinput" style={{ width: '100%' }} placeholder="main"
                       value={branch} onChange={(e) => setBranch(e.target.value)} />
              </div>
              <div style={{ flex: '1 1 200px' }}>
                <label className="fieldlabel">Developer image</label>
                <input className="cgqinput" style={{ width: '100%' }} placeholder="weave-dev:latest"
                       value={image} onChange={(e) => setImage(e.target.value)} />
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <div style={{ flex: '1 1 200px' }}>
                <label className="fieldlabel">Test command — what proves the work is good</label>
                <input className="cgqinput" style={{ width: '100%' }} placeholder="python -m pytest -q"
                       value={testCmd} onChange={(e) => setTestCmd(e.target.value)} />
              </div>
              <div style={{ flex: '1 1 200px' }}>
                <label className="fieldlabel">Setup command — run once per worktree (optional)</label>
                <input className="cgqinput" style={{ width: '100%' }} placeholder="pip install -e ."
                       value={setupCmd} onChange={(e) => setSetupCmd(e.target.value)} />
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <button className="btn sm primary" disabled={busy || !repo.trim()} onClick={save}>
                <CheckIcon className="" />{unconfigured ? 'Set up the project' : 'Save'}
              </button>
              {!unconfigured && (
                <button className="btn sm ghost" disabled={busy}
                        onClick={() => { setEditing(false); void load() }}>Cancel</button>
              )}
            </div>
          </div>
        ) : project && (
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
            <code className="mono">{project.repo}</code> @ <b>{project.base_branch}</b>
            {project.image && <> · image <code className="mono">{project.image}</code></>}
            {project.test_command?.length > 0 &&
              <> · tests <code className="mono">{project.test_command.join(' ')}</code></>}
          </div>
        )}
      </div>

      {/* ── the machines ────────────────────────────────────────────────── */}
      <div style={{ marginBottom: 8, fontWeight: 700, color: 'var(--text2)' }}>
        Dev hosts · {hosts.length}
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {hosts.length === 0 && (
          <div className="empty" style={{ padding: 12, fontSize: 12 }}>
            No machines registered — a dev host is a box that <em>runs</em> developers in
            containers. Developers can also run as bare processes without one, so the
            fleet below may well have members. Add a machine with{' '}
            <code className="mono">weave-devhost --server … --workspace …</code> — it
            learns the rest from here.
          </div>
        )}
        {hosts.map((h) => {
          const seatWarning = seatExplanation(h)
          return (
            <div key={h.id} className="card" style={{ minWidth: 260, padding: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="dot" style={{ background: hostTone(h) }} />
                <ServerIcon className="" style={{ width: 14 }} />
                <b style={{ fontSize: 14 }}>{h.id}</b>
                <div style={{ flex: 1 }} />
                <span className="chip" title={h.seat_detail || h.seat}
                      style={{ color: seatTone(h.seat) }}>seat: {h.seat}</span>
              </div>
              <div style={{ color: 'var(--muted)', fontSize: 12, margin: '6px 0' }}>
                {h.status}{h.machine ? ` · ${h.machine}` : ''}
                {' · '}{h.workers.length}/{h.desired_workers} developers
              </div>

              {seatWarning && (
                <div style={{ fontSize: 11, color: 'var(--crit)', display: 'flex',
                              gap: 5, marginBottom: 6 }}>
                  <AlertTriangleIcon className="" style={{ width: 13, flexShrink: 0 }} />
                  <span>{seatWarning}</span>
                </div>
              )}

              {/* Intent, not a command: the machine reconciles on its next heartbeat. */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                <button className="btn sm ghost" title="one fewer developer"
                        disabled={h.desired_workers <= 0}
                        onClick={() => act(() => weaveScaleHost(h.id, h.desired_workers - 1))}>
                  <MinusIcon className="" />
                </button>
                <b style={{ fontSize: 15, minWidth: 18, textAlign: 'center' }}>{h.desired_workers}</b>
                <button className="btn sm ghost" title="one more developer"
                        onClick={() => act(() => weaveScaleHost(h.id, h.desired_workers + 1))}>
                  <PlusIcon className="" />
                </button>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>wanted here</span>
              </div>

              <div className="btns">
                <button className="btn sm" title="finish current work, take nothing new"
                        onClick={() => act(() => weaveControlHost(h.id, 'drain'))}>
                  <DropletIcon className="" />
                </button>
                <button className="btn sm" title="stop containers now"
                        onClick={() => act(() => weaveControlHost(h.id, 'pause'))}>
                  <PauseIcon className="" />
                </button>
                <button className="btn sm" title="back into service"
                        onClick={() => act(() => weaveControlHost(h.id, 'resume'))}>
                  <PlayIcon className="" />
                </button>
                <button className="btn sm" title="stop this machine for good"
                        onClick={() => act(() => weaveControlHost(h.id, 'stop'))}>
                  <SquareIcon className="" />
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
