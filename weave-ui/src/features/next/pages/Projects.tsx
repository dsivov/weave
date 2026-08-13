/**
 * Projects — the registry that makes a locator resolvable (CR-001, A5).
 *
 * **Why this is a Weave screen and not a settings page.** A5 says an artifact
 * references its source by `repo · path · rev` and *never embeds a copy of it*.
 * That is the right design — an embedded body rots against the repository — but
 * it has a consequence people meet before they meet the reason: **if `repo` is
 * not registered, every locator naming it fails to resolve.** The symptom is a
 * board full of nodes whose sources will not open, and the cause is one missing
 * registration.
 *
 * So this page answers the question that symptom raises: *which repositories do
 * we know about, and does this locator actually point at anything?* The resolver
 * below is the same `/projects/resolve` an agent calls, which is what makes
 * "it works here" mean "it works for the fleet" (A9).
 */

import { useCallback, useEffect, useState } from 'react'
import { RefreshCwIcon, FolderGitIcon, SearchIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  listProjects, registerProject, resolveLocator,
  type ProjectLayout, type ResolvedLocator
} from '@/api/weave'
import { useSettingsStore } from '@/stores/settings'

const errMsg = (e: unknown) => {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail ? String(detail) : (e as Error)?.message || String(e)
}

export default function Projects() {
  const workspace = useSettingsStore.use.workspace()
  const [projects, setProjects] = useState<ProjectLayout[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [name, setName] = useState('')
  const [cloneUrl, setCloneUrl] = useState('')
  const [localPath, setLocalPath] = useState('')

  const [probeRepo, setProbeRepo] = useState('')
  const [probePath, setProbePath] = useState('')
  const [resolved, setResolved] = useState<ResolvedLocator | null>(null)
  const [probeError, setProbeError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setBusy(true); setError(null)
    try { setProjects((await listProjects()).projects) }
    catch (e) { setError(errMsg(e)); setProjects(null) }
    setBusy(false)
  }, [])

  useEffect(() => { void load() }, [load, workspace])

  const register = useCallback(async () => {
    if (!name.trim()) return
    setBusy(true)
    try {
      await registerProject({
        name: name.trim(),
        clone_url: cloneUrl.trim(),
        local_path: localPath.trim()
      })
      toast.success(`'${name.trim()}' registered — locators naming it resolve now.`)
      setName(''); setCloneUrl(''); setLocalPath('')
      await load()
    } catch (e) { toast.error(errMsg(e)) }
    setBusy(false)
  }, [name, cloneUrl, localPath, load])

  const probe = useCallback(async () => {
    if (!probeRepo.trim() || !probePath.trim()) return
    setProbeError(null); setResolved(null)
    try { setResolved(await resolveLocator(probeRepo.trim(), probePath.trim())) }
    catch (e) { setProbeError(errMsg(e)) }
  }, [probeRepo, probePath])

  return (
    <div className="cgnext" style={{ padding: '18px 22px', overflow: 'auto' }}>
      <div className="phead">
        <div>
          <div className="eyebrow">Weave · Sources</div>
          <h1>Projects</h1>
          <p>Where a locator points. Artifacts reference their source and never copy it — so a
            repository nobody registered is a repository whose links all fail.</p>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={() => void load()} disabled={busy}>
            <RefreshCwIcon className="" />Refresh
          </button>
        </div>
      </div>

      <div className="grid-cards">
        <div className="card"><span className="stripe accent" />
          <div className="chead">
            <h3>Registered repositories</h3>
            <span className="sub">{projects ? `${projects.length}` : ''}</span>
          </div>
          <div className="cbody">
            {error && <div className="empty" style={{ color: 'var(--bad)' }}>{error}</div>}
            {!error && !projects?.length && (
              <div className="empty">
                Nothing registered. Every artifact locator will fail to resolve until the
                repository it names is registered here.
              </div>
            )}
            {projects?.map((p) => (
              <div key={p.name} style={{ padding: '8px 10px', borderBottom: '1px solid var(--line)' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <FolderGitIcon className="" />
                  <strong>{p.name}</strong>
                  <span className="badge">{p.default_rev}</span>
                  {p.has_local_checkout
                    ? <span className="badge" style={{ background: 'var(--good-dim)' }}>readable</span>
                    // Worth distinguishing: without a checkout a human can follow the link
                    // but an agent cannot read the file, and that is a different failure.
                    : <span className="badge" title="No server-side checkout — an agent cannot read file content from this repo">link only</span>}
                </div>
                {p.clone_url && (
                  <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>
                    <code>{p.clone_url}</code>
                  </div>
                )}
                {p.description && (
                  <div style={{ color: 'var(--muted)', fontSize: 12 }}>{p.description}</div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="card"><span className="stripe" />
          <div className="chead"><h3>Register a repository</h3><span className="sub">POST /projects</span></div>
          <div className="cbody" style={{ display: 'grid', gap: 8 }}>
            <input value={name} onChange={(e) => setName(e.target.value)}
              placeholder="name — exactly as a locator's `repo` field holds it" />
            <input value={cloneUrl} onChange={(e) => setCloneUrl(e.target.value)}
              placeholder="clone or browse URL (optional)" />
            <input value={localPath} onChange={(e) => setLocalPath(e.target.value)}
              placeholder="server-side checkout path (optional — lets agents read content)" />
            <button className="btn" onClick={() => void register()} disabled={busy || !name.trim()}>
              Register
            </button>
            <div style={{ color: 'var(--muted)', fontSize: 12 }}>
              The name must match what locators already carry. Registering under a different
              spelling leaves the existing artifacts just as unresolvable as before.
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 12 }}><span className="stripe" />
        <div className="chead">
          <h3>Does this locator resolve?</h3>
          <span className="sub">GET /projects/resolve — the same call an agent makes</span>
        </div>
        <div className="cbody">
          <div style={{ display: 'flex', gap: 8 }}>
            <input style={{ flex: 1 }} value={probeRepo} onChange={(e) => setProbeRepo(e.target.value)}
              placeholder="repo" />
            <input style={{ flex: 2 }} value={probePath} onChange={(e) => setProbePath(e.target.value)}
              placeholder="path/within/the/repo.md" />
            <button className="btn" onClick={() => void probe()}
              disabled={!probeRepo.trim() || !probePath.trim()}>
              <SearchIcon className="" />Resolve
            </button>
          </div>

          {probeError && (
            <div style={{ color: 'var(--bad)', fontSize: 13, marginTop: 8 }}>{probeError}</div>
          )}

          {resolved && (
            <div className="box" style={{ marginTop: 8, padding: 10 }}>
              <div>
                <span className="badge" style={{
                  background: resolved.exists ? 'var(--good-dim)' : 'var(--warn-dim)'
                }}>
                  {resolved.exists ? 'exists' : 'not found'}
                </span>{' '}
                <code>{resolved.repo} · {resolved.path} @ {resolved.rev || '(default)'}</code>
              </div>
              {resolved.url && (
                <div style={{ marginTop: 4 }}>
                  <a href={resolved.url} target="_blank" rel="noreferrer">{resolved.url}</a>
                </div>
              )}
              {!resolved.exists && (
                <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>
                  The repository resolves but the path does not. A link will be produced and it
                  will 404 — which is worth knowing before an artifact cites it.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
