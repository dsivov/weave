/**
 * One renderer for the four canonical questions (CR-001).
 *
 * `/ask/{changes,why,features,learnings}` all return the same shape —
 * `{ question, nodes, count, truncated }` — so they get one view rather than
 * four that drift. The pages differ in which questions they ask and what they
 * put around them, never in how a node is drawn.
 *
 * **Every node links to its locator, and a node that cannot is shown saying so.**
 * A5 is the reason: an artifact references its source by `repo · path · rev` and
 * never embeds a copy, so a node whose locator failed to resolve is a node
 * pointing at something that moved. Rendering it as ordinary text would hide
 * exactly the case worth seeing, so `locator_error` gets its own visible state
 * rather than a blank space.
 */

import type { Answer, AnswerNode } from '@/api/weave'

/** `repo · path · rev` in the form a person can act on. */
function locatorLabel(node: AnswerNode): string | null {
  const loc = node.locator
  if (!loc || typeof loc !== 'object') return null
  const path = typeof loc.path === 'string' ? loc.path : null
  if (!path) return null
  const rev = typeof loc.rev === 'string' && loc.rev ? ` @ ${loc.rev.slice(0, 8)}` : ''
  const repo = typeof loc.repo === 'string' && loc.repo ? `${loc.repo}: ` : ''
  return `${repo}${path}${rev}`
}

function nodeTitle(node: AnswerNode): string {
  for (const key of ['title', 'name', 'entity_name', 'id']) {
    const v = node[key]
    if (typeof v === 'string' && v.trim()) return v
  }
  return '(untitled)'
}

export function AnswerList({
  answer, loading, error, empty, onPick
}: {
  answer: Answer | null
  loading?: boolean
  error?: string | null
  /** What to say when the question genuinely has no answer yet. */
  empty?: string
  /** Clicking a node — used by Learnings to anchor `why` on it. */
  onPick?: (node: AnswerNode) => void
}) {
  if (error) return <div className="empty" style={{ color: 'var(--bad)' }}>{error}</div>
  if (loading) return <div className="empty">Asking…</div>
  if (!answer) return null
  if (!answer.nodes.length) {
    return <div className="empty">{empty ?? 'Nothing yet — this question has no answer in this workspace.'}</div>
  }

  return (
    <div>
      <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 8 }}>
        {answer.count} {answer.count === 1 ? 'node' : 'nodes'}
        {answer.truncated && ' (truncated — the answer is larger than this)'}
      </div>
      <div className="box" style={{ maxHeight: 460, overflow: 'auto' }}>
        {answer.nodes.map((node, i) => {
          const loc = locatorLabel(node)
          return (
            <div
              key={typeof node.id === 'string' ? node.id : i}
              onClick={onPick ? () => onPick(node) : undefined}
              style={{
                padding: '8px 10px',
                borderBottom: '1px solid var(--line)',
                cursor: onPick ? 'pointer' : 'default'
              }}
            >
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                {node.type && <span className="badge">{String(node.type)}</span>}
                <strong>{nodeTitle(node)}</strong>
              </div>
              {loc && (
                <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>
                  <code>{loc}</code>
                </div>
              )}
              {node.locator_error && (
                // Visible on purpose. A node whose locator will not resolve is
                // pointing at something that moved, and that is the case worth
                // seeing rather than the one to hide (A5).
                <div style={{ color: 'var(--warn)', fontSize: 12, marginTop: 2 }}>
                  locator unresolved — {String(node.locator_error)}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
