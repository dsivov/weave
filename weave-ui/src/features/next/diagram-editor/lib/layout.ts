import dagre from '@dagrejs/dagre'
import type { Edge, Node } from '@xyflow/react'
import type { Direction, FlowNodeData } from './store'

const NODE_WIDTH = 150
const NODE_HEIGHT = 60
const SUBGRAPH_PADDING = 40

const RANKDIR: Record<Direction, string> = {
  TD: 'TB',
  LR: 'LR',
  BT: 'BT',
  RL: 'RL',
}

/** Somewhere to put the nodes when dagre will not arrange them. */
function gridFallback(nodes: Node<FlowNodeData>[]): Node<FlowNodeData>[] {
  const perRow = Math.max(1, Math.ceil(Math.sqrt(nodes.length)))
  return nodes.map((node, i) => ({
    ...node,
    position: {
      x: (i % perRow) * (NODE_WIDTH + 60),
      y: Math.floor(i / perRow) * (NODE_HEIGHT + 80),
    },
  }))
}

export function applyDagreLayout(
  nodes: Node<FlowNodeData>[],
  edges: Edge[],
  direction: Direction = 'TD'
): Node<FlowNodeData>[] {
  if (nodes.length === 0) return nodes

  const g = new dagre.graphlib.Graph({ compound: true })
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: RANKDIR[direction], nodesep: 60, ranksep: 80 })

  const subgraphIds = new Set(nodes.filter((n) => n.data?.isSubgraph).map((n) => n.id))

  // Add all nodes
  for (const node of nodes) {
    if (node.data?.isSubgraph) {
      // Let dagre auto-size subgraphs from children; provide padding
      g.setNode(node.id, {
        width: 0,
        height: 0,
        paddingX: SUBGRAPH_PADDING,
        paddingY: SUBGRAPH_PADDING,
      })
    } else {
      const w = typeof node.style?.width === 'number' ? node.style.width : NODE_WIDTH
      const h = typeof node.style?.height === 'number' ? node.style.height : NODE_HEIGHT
      g.setNode(node.id, { width: w, height: h })
    }
  }

  // Set parent relationships for compound layout
  for (const node of nodes) {
    if (node.parentId) {
      g.setParent(node.id, node.parentId)
    }
  }

  // ── Edges, with cluster endpoints resolved to a member (U19) ───────────────
  //
  // `daemon --> hub`, where `hub` is a subgraph, is ordinary mermaid and the
  // viewer renders it. dagre does not lay out an edge that *ends on a cluster*:
  // it walks the endpoint's rank and throws `Cannot set properties of undefined
  // (setting 'rank')`, which took the whole open path down and reached the
  // reader as the explanation.
  //
  // So the layout is given an edge to a member of the cluster instead. The real
  // edge is untouched — React Flow draws it to the subgraph box, which is what
  // was asked for; this substitution only decides where things sit.
  const memberOf = new Map<string, string>()
  for (const node of nodes) {
    if (!node.parentId || node.data?.isSubgraph) continue
    if (!memberOf.has(node.parentId)) memberOf.set(node.parentId, node.id)
  }
  // A subgraph whose only children are subgraphs resolves through them, so a
  // nested cluster is not a dead end.
  const resolve = (id: string, seen = new Set<string>()): string | null => {
    if (!subgraphIds.has(id)) return id
    if (seen.has(id)) return null
    seen.add(id)
    const direct = memberOf.get(id)
    if (direct) return direct
    for (const node of nodes) {
      if (node.parentId === id) {
        const inner = resolve(node.id, seen)
        if (inner) return inner
      }
    }
    return null   // an empty subgraph has nothing to anchor the edge to
  }

  for (const edge of edges) {
    const source = resolve(edge.source)
    const target = resolve(edge.target)
    // A dropped edge costs this edge some influence over the arrangement. A
    // thrown one costs the reader the whole diagram.
    if (!source || !target || source === target) continue
    g.setEdge(source, target)
  }

  try {
    dagre.layout(g)
  } catch (err) {
    // Defence in depth, not a substitute for the fix above. If dagre refuses an
    // arrangement for a reason we have not met yet, the diagram still opens —
    // laid out badly, which the reader can see and work with, rather than not
    // at all with a stack frame for a reason.
    console.error('dagre layout failed; falling back to a grid', err)
    return gridFallback(nodes)
  }

  return nodes.map((node) => {
    const layout = g.node(node.id)
    if (!layout) return node

    if (node.data?.isSubgraph) {
      return {
        ...node,
        position: {
          x: layout.x - layout.width / 2,
          y: layout.y - layout.height / 2,
        },
        style: {
          ...node.style,
          width: layout.width,
          height: layout.height,
        },
      }
    }

    if (node.parentId) {
      // Convert dagre absolute coords to parent-relative for React Flow
      const parentLayout = g.node(node.parentId)
      if (!parentLayout) return node
      const w = typeof node.style?.width === 'number' ? node.style.width : NODE_WIDTH
      const h = typeof node.style?.height === 'number' ? node.style.height : NODE_HEIGHT
      const parentTopLeftX = parentLayout.x - parentLayout.width / 2
      const parentTopLeftY = parentLayout.y - parentLayout.height / 2
      return {
        ...node,
        position: {
          x: layout.x - w / 2 - parentTopLeftX,
          y: layout.y - h / 2 - parentTopLeftY,
        },
      }
    }

    // Top-level non-subgraph node
    const w = typeof node.style?.width === 'number' ? node.style.width : NODE_WIDTH
    const h = typeof node.style?.height === 'number' ? node.style.height : NODE_HEIGHT
    return {
      ...node,
      position: {
        x: layout.x - w / 2,
        y: layout.y - h / 2,
      },
    }
  })
}
