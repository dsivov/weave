import { useCallback, useEffect, useMemo, useState } from 'react'
import { KeyRoundIcon, PlusIcon, RefreshCwIcon, SearchIcon, Trash2Icon } from 'lucide-react'
import {
  createUser,
  deleteUser,
  listUsers,
  setUserPassword,
  setUserWorkspaces,
  updateUser,
  type WeaveUser
} from '@/api/weave'
import { Blockers } from '@/features/next/governance/ActionFeedback'

/**
 * Admin ▸ Users — the screen that closes the gap this project exists to close.
 *
 * The source had no user store and no user routes: adding a person meant editing
 * an environment variable and restarting the server. Everything here is a thin
 * call into the same service the CLI and the migration use, so the three cannot
 * drift (A9).
 *
 * Nothing on this screen can render a password hash: the server has no field for
 * one (R17). Passwords are only ever *sent*.
 */

const ROLES = ['manager', 'architect', 'developer', 'integrator', 'admin', 'user']

type Draft = {
  username: string
  password: string
  role: string
  display_name: string
  email: string
  workspaces: string
}

const EMPTY: Draft = {
  username: '',
  password: '',
  role: 'developer',
  display_name: '',
  email: '',
  workspaces: ''
}

const parseWorkspaces = (raw: string): string[] =>
  raw
    .split(',')
    .map((w) => w.trim())
    .filter(Boolean)

export default function AdminUsers() {
  const [rows, setRows] = useState<WeaveUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<Draft>(EMPTY)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await listUsers())
    } catch (e: unknown) {
      // A 403 here is not a bug — it is the answer. Say which it is rather than
      // showing an empty table, which reads as "no users exist".
      const status = (e as { response?: { status?: number } })?.response?.status
      setError(
        status === 403
          ? 'Your account is not permitted to administer users.'
          : 'Could not load users.'
      )
      setRows([])
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase()
    if (!t) return rows
    return rows.filter((u) =>
      [u.username, u.display_name, u.email, u.role, ...u.workspaces]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(t)
    )
  }, [rows, q])

  // What stops the create button, as reasons rather than a boolean (U10).
  const createBlockers = [
    ...(draft.username.trim() ? [] : ['A username is required.']),
    ...(draft.password.length >= 8
      ? []
      : [`The password needs at least 8 characters — ${draft.password.length} so far.`]),
  ]

  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    setNotice(null)
    try {
      await fn()
      await load()
      return true
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail
      setError(detail || 'That did not work.')
      return false
    }
  }

  const onCreate = async () => {
    const ok = await act(() =>
      createUser({
        username: draft.username.trim(),
        password: draft.password,
        role: draft.role,
        display_name: draft.display_name.trim(),
        email: draft.email.trim(),
        workspaces: parseWorkspaces(draft.workspaces)
      })
    )
    if (ok) {
      setDraft(EMPTY)
      setCreating(false)
    }
  }

  const onResetPassword = async (user: WeaveUser) => {
    const next = window.prompt(`New password for ${user.username} (8+ characters)`)
    if (!next) return
    await act(() => setUserPassword(user.id, next))
  }

  const onToggleStatus = (user: WeaveUser) =>
    act(() =>
      updateUser(user.id, { status: user.status === 'active' ? 'disabled' : 'active' })
    )

  // A role change is saved immediately and takes effect at the user's **next
  // sign-in** — the server enforces the role carried in the token (D5), and the
  // token they are holding was minted before this (D-040).
  //
  // Saying so is the whole of U1. Without it the change looks applied, nothing
  // behaves differently, and the owner concludes the write was lost — it was
  // not; it is on disk and out of force. Silence here is what turned three
  // small defects into an installation nobody could configure.
  const onChangeRole = async (user: WeaveUser, role: string) => {
    if (!(await act(() => updateUser(user.id, { role })))) return
    setNotice(
      `${user.username} is now ${role}. It takes effect the next time they sign in — ` +
      'the role is carried in their token, and the one they are holding was issued ' +
      'before this change.'
    )
  }

  const onEditWorkspaces = async (user: WeaveUser) => {
    const next = window.prompt(
      `Workspaces for ${user.username}, comma separated.\n\nThey can see only what is listed here.`,
      user.workspaces.join(', ')
    )
    if (next === null) return
    await act(() => setUserWorkspaces(user.id, parseWorkspaces(next)))
  }

  const onDelete = async (user: WeaveUser) => {
    if (!window.confirm(`Delete ${user.username}? This cannot be undone.`)) return
    await act(() => deleteUser(user.id))
  }

  return (
    <div className="view">
      <div className="phead">
        <div>
          <div className="eyebrow">Admin · Users</div>
          <h1>Users</h1>
          <p>
            People who can sign in, what each one <em>is</em>, and which workspaces they
            can reach. A user sees only the workspaces granted here.
          </p>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={load}>
            <RefreshCwIcon />
            Refresh
          </button>
          <button className="btn" onClick={() => setCreating((v) => !v)}>
            <PlusIcon />
            New user
          </button>
        </div>
      </div>

      {error && (
        <div className="callout warn" role="alert">
          {error}
        </div>
      )}

      {notice && (
        // In place, next to the control that caused it — not a toast that has
        // gone by the time someone looks up (U10).
        <div className="callout" role="status" style={{ marginBottom: 10 }}>
          {notice}
        </div>
      )}

      {creating && (
        <div className="card">
          <div className="card-head">
            <h2>New user</h2>
          </div>
          <div className="grid two">
            <label>
              <span>Username</span>
              <input
                value={draft.username}
                onChange={(e) => setDraft({ ...draft, username: e.target.value })}
                placeholder="alice"
                autoFocus
              />
            </label>
            <label>
              <span>Password</span>
              <input
                type="password"
                value={draft.password}
                onChange={(e) => setDraft({ ...draft, password: e.target.value })}
                placeholder="at least 8 characters"
              />
            </label>
            <label>
              <span>Display name</span>
              <input
                value={draft.display_name}
                onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
              />
            </label>
            <label>
              <span>Email</span>
              <input
                value={draft.email}
                onChange={(e) => setDraft({ ...draft, email: e.target.value })}
              />
            </label>
            <label>
              <span>Role</span>
              <select
                value={draft.role}
                onChange={(e) => setDraft({ ...draft, role: e.target.value })}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Workspaces</span>
              <input
                value={draft.workspaces}
                onChange={(e) => setDraft({ ...draft, workspaces: e.target.value })}
                placeholder="alpha, beta"
              />
            </label>
          </div>
          <div className="actions">
            <button className="btn ghost" onClick={() => setCreating(false)}>
              Cancel
            </button>
            <button
              className="btn"
              onClick={onCreate}
              disabled={createBlockers.length > 0}
            >
              Create
            </button>
          </div>

          {/* The rules, before the click (U10). They were enforced by a disabled
              button and stated nowhere — so "I cannot add users" was the only
              conclusion available, and it was correct. Same list disables the
              button and explains it, so the two cannot drift. */}
          <Blockers reasons={createBlockers} />
        </div>
      )}

      <div className="toolbar">
        <div className="search">
          <SearchIcon />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter by name, role or workspace"
          />
        </div>
        <div className="muted">
          {loading ? 'Loading…' : `${filtered.length} of ${rows.length}`}
        </div>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>User</th>
            <th>Role</th>
            <th>Workspaces</th>
            <th>Status</th>
            <th>Last signed in</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {!loading && filtered.length === 0 && (
            <tr>
              <td colSpan={6} className="muted">
                No users yet. Everyone gets a guest token until the first one exists.
              </td>
            </tr>
          )}
          {filtered.map((u) => (
            <tr key={u.id} className={u.status === 'disabled' ? 'dim' : undefined}>
              <td>
                <strong>{u.username}</strong>
                {u.display_name && u.display_name !== u.username && (
                  <div className="muted">{u.display_name}</div>
                )}
                {u.email && <div className="muted mono">{u.email}</div>}
              </td>
              <td>
                <select value={u.role} onChange={(e) => onChangeRole(u, e.target.value)}>
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <button className="btn link" onClick={() => onEditWorkspaces(u)}>
                  {u.workspaces.length ? u.workspaces.join(', ') : <em>none</em>}
                </button>
              </td>
              <td>
                <button className="btn ghost small" onClick={() => onToggleStatus(u)}>
                  {u.status}
                </button>
              </td>
              <td className="muted mono">{u.last_login_at || '—'}</td>
              <td className="actions">
                <button
                  className="btn ghost small"
                  title="Reset password"
                  onClick={() => onResetPassword(u)}
                >
                  <KeyRoundIcon />
                </button>
                <button
                  className="btn ghost small danger"
                  title="Delete user"
                  onClick={() => onDelete(u)}
                >
                  <Trash2Icon />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
