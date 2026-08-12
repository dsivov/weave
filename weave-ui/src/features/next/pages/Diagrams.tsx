/**
 * Diagrams — the visual editor, on the workspace's shared diagram set.
 *
 * The canvas, inspector, palette and toolbar are the vendored
 * mermaid-visual-editor (see ../diagram-editor/README.md). What this page adds
 * is the half upstream has no concept of: a diagram here is not a local file,
 * it is a governed artifact that the whole workspace reads. So the page owns
 *
 *   - loading a shared diagram into the canvas (mermaid → nodes/edges), and
 *   - saving it back through the signed gesture, where a change to the
 *     diagram's structure needs an approver and a reason.
 *
 * Structural vs cosmetic is the server's call, not ours — we send what the
 * author typed and surface the 422 when a sign-off is missing, rather than
 * trying to predict the verdict client-side and drifting from it.
 */

import { useCallback, useEffect, useState } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import {
  ShapesIcon, FolderOpenIcon, Trash2Icon, SaveIcon, PlusIcon, HistoryIcon,
  PanelRightOpenIcon
} from 'lucide-react'
import '@xyflow/react/dist/style.css'

import { Canvas } from '@/features/next/diagram-editor/components/Canvas'
import { TopToolbar } from '@/features/next/diagram-editor/components/TopToolbar'
import { ZoomControls } from '@/features/next/diagram-editor/components/ZoomControls'
import { InspectorPanel } from '@/features/next/diagram-editor/components/Inspector/InspectorPanel'
import { CommandPalette } from '@/features/next/diagram-editor/components/CommandPalette'
import { useFlowStore } from '@/features/next/diagram-editor/lib/store'
import { serialize } from '@/features/next/diagram-editor/lib/serializer'
import { parseMermaidFlowchart } from '@/features/next/diagram-editor/lib/parser'
import '@/features/next/diagram-editor/editor.css'

import {
  listDiagrams, getDiagram, saveDiagram, deleteDiagram, diagramVersions,
  type DiagramRow, type StudioVersion
} from '@/api/weave'
import { useSettingsStore } from '@/stores/settings'

type SaveState = { busy: boolean; note: string; bad: boolean }

export default function Diagrams() {
  const workspace = useSettingsStore.use.workspace()
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [galleryOpen, setGalleryOpen] = useState(true)

  const [diagrams, setDiagrams] = useState<DiagramRow[] | null>(null)
  const [history, setHistory] = useState<{ id: string; rows: StudioVersion[] } | null>(null)

  // The identity of what's on the canvas. `loaded` is the version we opened, so
  // the save dialog can tell the author they are about to supersede it.
  const [id, setId] = useState('')
  const [title, setTitle] = useState('')
  const [depicts, setDepicts] = useState('')
  const [loaded, setLoaded] = useState<number | null>(null)

  const [signOpen, setSignOpen] = useState(false)
  const [approver, setApprover] = useState('')
  const [reason, setReason] = useState('')
  const [save, setSave] = useState<SaveState>({ busy: false, note: '', bad: false })

  const { nodes, edges, direction, theme, look, curveStyle, importDiagram } = useFlowStore()
  const syntax = serialize(nodes, edges, { direction, theme, look, curveStyle })

  const refresh = useCallback(async () => {
    try {
      setDiagrams((await listDiagrams()).diagrams)
    } catch {
      setDiagrams([])
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh, workspace])

  const open = useCallback(async (rowId: string, version?: number) => {
    const d = await getDiagram(rowId, version)
    const parsed = parseMermaidFlowchart(d.source)
    if (parsed.error || parsed.nodes.length === 0) {
      setSave({ busy: false, bad: true, note: `'${rowId}' could not be laid out on the canvas: ${parsed.error ?? 'no nodes found'}. Its source is still intact on the server.` })
      return
    }
    importDiagram(parsed.nodes, parsed.edges, {
      direction: parsed.direction, theme: parsed.theme,
      look: parsed.look, curveStyle: parsed.curveStyle
    })
    setId(d.id); setTitle(d.title); setDepicts(d.depicts.join(', '))
    setLoaded(d.version)
    setSave({ busy: false, bad: false, note: `opened ${d.id} v${d.version}` })
    setHistory(null)
  }, [importDiagram])

  const blank = useCallback(() => {
    importDiagram([], [], { direction, theme, look, curveStyle })
    setId(''); setTitle(''); setDepicts(''); setLoaded(null)
    setSave({ busy: false, bad: false, note: '' })
  }, [importDiagram, direction, theme, look, curveStyle])

  const commit = useCallback(async (signed: boolean) => {
    if (!id.trim()) {
      setSave({ busy: false, bad: true, note: 'give the diagram an id first — it is the key teammates fetch it by' })
      return
    }
    setSave({ busy: true, bad: false, note: 'saving…' })
    try {
      const res = await saveDiagram({
        id: id.trim(), source: syntax, title,
        depicts: depicts.split(',').map((s) => s.trim()).filter(Boolean),
        ...(signed ? { approver: approver.trim(), reason: reason.trim() } : {})
      })
      setLoaded(res.version)
      setSignOpen(false); setApprover(''); setReason('')
      setSave({ busy: false, bad: false, note: `saved as v${res.version} — the workspace sees this now` })
      void refresh()
    } catch (e: unknown) {
      const err = e as { response?: { status?: number; data?: { detail?: string } } }
      const status = err.response?.status
      const detail = err.response?.data?.detail ?? 'save failed'
      if (status === 422) {
        // The structural-change gate. Not an error — a request for a sign-off.
        setSignOpen(true)
        setSave({ busy: false, bad: false, note: 'this changes the diagram\'s structure — sign it off to share it' })
      } else {
        setSave({ busy: false, bad: true, note: status === 400 ? `rejected: ${detail}` : String(detail) })
      }
    }
  }, [id, syntax, title, depicts, approver, reason, refresh])

  const remove = useCallback(async (rowId: string) => {
    await deleteDiagram(rowId)
    if (rowId === id) blank()
    void refresh()
  }, [id, blank, refresh])

  return (
    <div className="cg-diagram-editor" style={{ position: 'relative', display: 'flex', height: '100%', width: '100%', overflow: 'hidden', background: 'var(--neu-bg)' }}>
      {/* ── the shared set ─────────────────────────────────────────────── */}
      {galleryOpen && (
        <div style={{ width: 268, flexShrink: 0, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', background: 'var(--surface)' }}>
          <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <strong style={{ fontSize: 13 }}>
              <ShapesIcon className="" style={{ verticalAlign: '-3px', marginRight: 6, width: 15, height: 15 }} />
              Shared diagrams
            </strong>
            <button className="btn sm ghost" title="New diagram" onClick={blank}><PlusIcon className="" /></button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)', padding: '6px 12px' }}>
            {diagrams ? `${diagrams.length} in ${workspace || 'this workspace'} · everyone sees these` : 'loading…'}
          </div>
          <div style={{ overflow: 'auto', flex: 1, padding: '0 8px 8px' }}>
            {diagrams?.length === 0 && (
              <div style={{ fontSize: 12, color: 'var(--muted)', padding: 12 }}>
                Nothing saved yet. Draw on the canvas, give it an id, and save — it becomes a shared artifact.
              </div>
            )}
            {diagrams?.map((d) => (
              <div key={d.id} style={{ padding: '7px 8px', borderRadius: 8, marginBottom: 4, background: d.id === id ? 'var(--accent-dim)' : 'transparent' }}>
                <div style={{ fontSize: 12, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.title || d.id}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
                  <code className="mono">{d.id}</code> · v{d.version}{d.depicts.length ? ` · ${d.depicts.join(', ')}` : ''}
                </div>
                <div style={{ display: 'flex', gap: 4 }}>
                  <button className="btn sm ghost" onClick={() => void open(d.id)}><FolderOpenIcon className="" />Open</button>
                  <button className="btn sm ghost" title="Revisions" onClick={async () => setHistory({ id: d.id, rows: (await diagramVersions(d.id)).history })}><HistoryIcon className="" /></button>
                  <button className="btn sm ghost" title="Delete" onClick={() => void remove(d.id)}><Trash2Icon className="" /></button>
                </div>
                {history?.id === d.id && (
                  <div style={{ marginTop: 6, borderTop: '1px solid var(--line)', paddingTop: 6 }}>
                    {history.rows.map((h) => (
                      <button key={h.version} className="btn sm ghost" style={{ display: 'block', width: '100%', textAlign: 'left', fontSize: 11 }}
                        onClick={() => void open(d.id, h.version)}>
                        v{h.version} · {h.sign_off?.approver || 'unsigned'}
                        {h.sign_off?.reason ? ` — ${h.sign_off.reason}` : ''}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── the canvas ─────────────────────────────────────────────────── */}
      <div style={{ position: 'relative', flex: 1, minWidth: 0, background: 'var(--neu-bg)' }}>
        {/* identity + save bar — what makes this a shared artifact, not a file */}
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 25, display: 'flex', gap: 8, alignItems: 'center', padding: '8px 10px', background: 'var(--surface)', borderBottom: '1px solid var(--line)' }}>
          {!galleryOpen && (
            <button className="btn sm ghost" title="Show shared diagrams" onClick={() => setGalleryOpen(true)}><PanelRightOpenIcon className="" /></button>
          )}
          <input className="input" style={{ width: 150 }} placeholder="diagram-id" value={id} onChange={(e) => setId(e.target.value)} />
          <input className="input" style={{ width: 190 }} placeholder="title" value={title} onChange={(e) => setTitle(e.target.value)} />
          <input className="input" style={{ width: 190 }} placeholder="depicts (comma separated)" value={depicts} onChange={(e) => setDepicts(e.target.value)} />
          <button className="btn sm" disabled={save.busy} onClick={() => void commit(false)}>
            <SaveIcon className="" />Save{loaded !== null ? ` (v${loaded} → v${loaded + 1})` : ''}
          </button>
          {save.note && (
            <span style={{ fontSize: 11, color: save.bad ? 'var(--crit)' : 'var(--muted)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{save.note}</span>
          )}
        </div>

        <div style={{ position: 'absolute', inset: '44px 0 0 0' }}>
          <Canvas onOpenPalette={() => setPaletteOpen(true)} />
          <div style={{ position: 'absolute', top: 16, left: 0, right: 0, display: 'flex', justifyContent: 'center', zIndex: 20, pointerEvents: 'none' }}>
            <TopToolbar
              inspectorOpen={inspectorOpen}
              onToggleInspector={() => setInspectorOpen((v) => !v)}
              onOpenPalette={() => setPaletteOpen(true)}
              syntax={syntax}
            />
          </div>
          <ZoomControls />
        </div>
      </div>

      {inspectorOpen && (
        <InspectorPanel syntax={syntax} onCollapse={() => setInspectorOpen(false)} />
      )}
      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}

      {/* ── the sign-off, when the server asks for one ──────────────────── */}
      {signOpen && (
        <div style={{ position: 'absolute', inset: 0, zIndex: 60, background: 'rgba(0,0,0,.45)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div className="card" style={{ width: 460, background: 'var(--surface)', padding: 16, borderRadius: 12 }}>
            <h3 style={{ marginTop: 0, fontSize: 15 }}>Sign off this structural change</h3>
            <p style={{ fontSize: 12, color: 'var(--muted)' }}>
              The nodes or connectors changed, so this supersedes what the team currently reads.
              Styling, labels and layout would not have needed this.
            </p>
            <input className="input" style={{ width: '100%', marginBottom: 8 }} placeholder="approver" value={approver} onChange={(e) => setApprover(e.target.value)} />
            <input className="input" style={{ width: '100%', marginBottom: 12 }} placeholder="reason — recorded as the sign-off" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn sm ghost" onClick={() => setSignOpen(false)}>Cancel</button>
              <button className="btn sm" disabled={!approver.trim() || !reason.trim() || save.busy} onClick={() => void commit(true)}>Sign & save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export function DiagramsPage() {
  return <ReactFlowProvider><Diagrams /></ReactFlowProvider>
}
