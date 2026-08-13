/**
 * The ontology as a node/link canvas (CR-001 §4b).
 *
 * **A LinkType is not an edge, and this file exists to stop the canvas implying
 * that it is.** `LinkType.source_types` and `target_types` are *lists* — empty
 * means *any* — so one link type is N concrete edges. Measured on the shipped
 * preset: **23 link types → 37 edges**, 9 of them fanning out, and a single
 * `ANY→ANY` link would be 18×18 = **324**.
 *
 * `@xyflow` has no hyperedges, so the two honest options fail differently:
 * draw 23 and lie about how they attach, or draw 37 and let deleting one edge
 * silently change three others. §4b chose **draw 37 and make the sharing
 * visible**: edges from one link type select together, and the inspector names
 * the other pairs. An *edit-one-change-many* surprise is the same class of
 * defect as a signature promising a distinction its body never makes — the fix
 * is not to hide the sharing but to stop pretending it is absent.
 *
 * The grouping hangs on one fact about the diagram editor, verified before this
 * was written rather than assumed: selection there is a plain `selected` boolean
 * on each edge, applied through `onEdgesChange` (`lib/store.ts:250`). Not opaque
 * library state — so widening a selection is something we can simply do.
 */

import { useCallback, useMemo, useState } from 'react'
import {
  ReactFlow, Background, Controls, applyEdgeChanges, applyNodeChanges,
  type Edge, type EdgeChange, type Node, type NodeChange
} from '@xyflow/react'

import type { OntologyDoc } from '@/api/weave'

/** One concrete edge, plus the link type it is one instance of. */
export interface LinkEdgeData extends Record<string, unknown> {
  linkType: string
  cardinality: string
  /** Every `source → target` pair this same link type also connects. */
  siblings: string[]
  wildcard: boolean
}

export interface TypeNodeData extends Record<string, unknown> {
  label: string
  description: string
  propertyCount: number
}

/**
 * Expand the ontology into what a canvas can actually draw.
 *
 * Exported because the fixture tests drive it directly: the preset has **no**
 * link-type properties and **no** wildcard, so a test built from the preset
 * proves nothing about either. This is the function those cases have to reach.
 */
export function expandOntology(doc: OntologyDoc): {
  nodes: Node<TypeNodeData>[]
  edges: Edge<LinkEdgeData>[]
} {
  const typeNames = doc.object_types.map((t) => t.name)

  // A grid rather than a layout engine: `applyDagreLayout` is typed for the
  // diagram editor's own node data, and coercing this through it would couple
  // the ontology view to the diagram's shape for the sake of reuse that is not
  // really reuse. Positions are a starting point; the canvas is draggable.
  const columns = Math.max(1, Math.ceil(Math.sqrt(typeNames.length)))
  const nodes: Node<TypeNodeData>[] = doc.object_types.map((t, i) => ({
    id: t.name,
    position: { x: (i % columns) * 220, y: Math.floor(i / columns) * 140 },
    data: {
      label: t.name,
      description: t.description ?? '',
      // 99 properties across 18 types in the preset — mean 5.5, max 9. Far too
      // many to draw inside a node, so the node shows the count and the panel
      // shows the properties.
      propertyCount: (t.properties ?? []).length
    },
    type: 'default'
  }))

  const edges: Edge<LinkEdgeData>[] = []
  for (const link of doc.link_types) {
    // Empty list means *any type*, which is where 23 becomes 324. Expanded
    // honestly rather than skipped: a wildcard link really does connect
    // everything to everything, and a canvas that quietly drew nothing would be
    // hiding the most surprising thing in the document.
    const sources = link.source_types?.length ? link.source_types : typeNames
    const targets = link.target_types?.length ? link.target_types : typeNames
    const wildcard = !link.source_types?.length || !link.target_types?.length

    const pairs: Array<[string, string]> = []
    for (const s of sources) for (const t of targets) pairs.push([s, t])

    for (const [s, t] of pairs) {
      edges.push({
        id: `${link.name}:${s}->${t}`,
        source: s,
        target: t,
        label: link.name,
        data: {
          linkType: link.name,
          cardinality: link.cardinality ?? 'N:M',
          siblings: pairs.filter(([a, b]) => !(a === s && b === t)).map(([a, b]) => `${a} → ${b}`),
          wildcard
        }
      })
    }
  }
  return { nodes, edges }
}

export function OntologyCanvas({ doc }: { doc: OntologyDoc }) {
  const initial = useMemo(() => expandOntology(doc), [doc])
  const [nodes, setNodes] = useState<Node<TypeNodeData>[]>(initial.nodes)
  const [edges, setEdges] = useState<Edge<LinkEdgeData>[]>(initial.edges)

  const onNodesChange = useCallback(
    (changes: NodeChange<Node<TypeNodeData>>[]) =>
      setNodes((ns) => applyNodeChanges(changes, ns)),
    []
  )

  /**
   * **Where §4b actually happens.** A `select` change arriving for one edge is
   * widened to every edge sharing its link type before the array is committed,
   * so selecting one selects the group and the shared object is visible as a
   * shared object.
   */
  const onEdgesChange = useCallback((changes: EdgeChange<Edge<LinkEdgeData>>[]) => {
    setEdges((current) => {
      const applied = applyEdgeChanges(changes, current)
      const selecting = changes.filter(
        (c): c is EdgeChange<Edge<LinkEdgeData>> & { type: 'select'; id: string; selected: boolean } =>
          c.type === 'select'
      )
      if (!selecting.length) return applied

      // Which link types were just selected or deselected, and to what.
      const wanted = new Map<string, boolean>()
      for (const change of selecting) {
        const edge = applied.find((e) => e.id === change.id)
        if (edge?.data) wanted.set(edge.data.linkType, change.selected)
      }
      if (!wanted.size) return applied

      return applied.map((e) => {
        const want = e.data ? wanted.get(e.data.linkType) : undefined
        return want === undefined ? e : { ...e, selected: want }
      })
    })
  }, [])

  const selectedEdge = edges.find((e) => e.selected)
  const selectedNode = nodes.find((n) => n.selected)
  const objectType = selectedNode
    ? doc.object_types.find((t) => t.name === selectedNode.id)
    : undefined
  const linkType = selectedEdge?.data
    ? doc.link_types.find((l) => l.name === selectedEdge.data!.linkType)
    : undefined

  return (
    <div style={{ display: 'flex', height: 520, gap: 12 }}>
      <div className="box" style={{ flex: 1, minWidth: 0 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>

      {/* The inspector.
          NOT `diagram-editor/components/Inspector/`: that panel takes no props,
          reads `useFlowStore` directly, and edits mermaid *styling* — node
          shape, edge arrows. It is a diagram inspector. Reusing it here would
          mean binding the ontology to the diagram's store and still not being
          able to edit a typed property. Different job, so a different panel;
          R10 forbids a second implementation of one job, not a first
          implementation of another. */}
      <div className="card" style={{ width: 320, overflow: 'auto' }}>
        {!selectedEdge && !selectedNode && (
          <div className="empty" style={{ padding: 12 }}>
            Select a type or a link. {edges.length} edges from {doc.link_types.length} link
            types — several of them are the same link type drawn more than once.
          </div>
        )}

        {objectType && (
          <div className="cbody">
            <h3 style={{ marginTop: 0 }}>{objectType.name}</h3>
            {objectType.description && (
              <p style={{ color: 'var(--muted)', fontSize: 13 }}>{objectType.description}</p>
            )}
            <div style={{ fontSize: 13 }}>
              {(objectType.properties ?? []).map((p) => (
                <div key={p.name} style={{ padding: '4px 0', borderBottom: '1px solid var(--line)' }}>
                  <code>{p.name}</code> <span className="badge">{p.kind}</span>
                  {p.required && <span className="badge" style={{ marginLeft: 4 }}>required</span>}
                </div>
              ))}
              {!(objectType.properties ?? []).length && (
                <div className="empty">No properties.</div>
              )}
            </div>
          </div>
        )}

        {linkType && selectedEdge?.data && (
          <div className="cbody">
            <h3 style={{ marginTop: 0 }}>{linkType.name}</h3>
            <div>
              <span className="badge">{selectedEdge.data.cardinality}</span>
              {selectedEdge.data.wildcard && (
                <span className="badge" style={{ background: 'var(--warn-dim)', marginLeft: 4 }}
                  title="This link type names no source or target types, so it connects every type to every type">
                  wildcard
                </span>
              )}
            </div>

            {/* The heading §4b asks for: editing this edge edits the others. */}
            {selectedEdge.data.siblings.length > 0 && (
              <div style={{ marginTop: 10, fontSize: 13 }}>
                <strong>This link type also connects:</strong>
                <ul style={{ margin: '4px 0 0 16px' }}>
                  {selectedEdge.data.siblings.map((s) => <li key={s}><code>{s}</code></li>)}
                </ul>
                <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 6 }}>
                  They are one object, not {selectedEdge.data.siblings.length + 1} — a change
                  here changes all of them.
                </div>
              </div>
            )}

            {(linkType.properties ?? []).length > 0 && (
              <div style={{ marginTop: 10, fontSize: 13 }}>
                <strong>Link properties</strong>
                {(linkType.properties ?? []).map((p) => (
                  <div key={p.name} style={{ padding: '4px 0' }}>
                    <code>{p.name}</code> <span className="badge">{p.kind}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
