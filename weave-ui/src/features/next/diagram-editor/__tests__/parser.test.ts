/**
 * The editor opens what the viewer renders (U18, U19).
 *
 * **Both defects were found by using the product**, reviewing a real diagram and
 * trying to update it. The save path was faultless; opening was where the gaps
 * were, and they failed at two different stages:
 *
 * * **U18 — the grammar.** `/^flowchart\s+(TD|LR|BT|RL)/` rejected `flowchart TB`
 *   (mermaid's own preferred spelling of the same direction) and every `graph`
 *   form (the older keyword, and the commonest one in anything written before
 *   2023). A diagram pasted from documentation became an unopenable artifact
 *   with an intact source and an empty canvas.
 * * **U19 — the layout.** `daemon --> hub`, where `hub` is a subgraph, is
 *   ordinary mermaid. dagre will not lay out an edge that ends on a cluster; it
 *   threw `Cannot set properties of undefined (setting 'rank')`, and that
 *   sentence was rendered to the reader as the explanation.
 *
 * **So the fixtures assert the parse *and* the layout.** A test that only
 * checked the parse would have passed U19 — the header was fine, the nodes were
 * found, and the crash came afterwards.
 *
 * **The header list is measured, not remembered.** Every form below was run
 * through `mermaid.parse` (mermaid 11.16.1) and accepted; `flowchart XX` is the
 * only thing in the neighbourhood that mermaid itself rejects.
 *
 * **Written here, not run here.** bun is not installed in the container this was
 * written in. The same assertions were verified against the real modules under
 * node with type-stripping, including five negative controls — reverting the
 * header regex fails 21 of these, and reverting the cluster resolution fails
 * four, three of them *only* because of the fell-back-to-grid check below.
 */

import { describe, expect, mock, test } from 'bun:test'

import { parseMermaidFlowchart } from '../lib/parser'
import type { Direction } from '../lib/store'

//: Every header mermaid accepts, with the direction each one means.
const HEADERS: [string, Direction][] = [
  ['flowchart TD', 'TD'], ['flowchart TB', 'TD'], ['flowchart v', 'TD'],
  ['flowchart BT', 'BT'], ['flowchart ^', 'BT'],
  ['flowchart LR', 'LR'], ['flowchart >', 'LR'],
  ['flowchart RL', 'RL'], ['flowchart <', 'RL'],
  ['flowchart', 'TD'],
  ['graph TD', 'TD'], ['graph TB', 'TD'], ['graph v', 'TD'],
  ['graph BT', 'BT'], ['graph ^', 'BT'],
  ['graph LR', 'LR'], ['graph >', 'LR'],
  ['graph RL', 'RL'], ['graph <', 'RL'],
  ['graph', 'TD'],
  ['flowchart TD;', 'TD'], ['graph TB;', 'TD'],
]

//: Anything that looks like a stack frame rather than a sentence.
const JS_ERROR = /Cannot (set|read)|undefined|TypeError|\brank\b|at .*:\d+:\d+/

describe('U18 · the header grammar', () => {
  test.each(HEADERS)('%s opens, meaning %s', (header, direction) => {
    const r = parseMermaidFlowchart(`${header}\n  A[one] --> B[two]\n`)
    expect(r.error).toBeNull()
    expect(r.nodes).toHaveLength(2)
    expect(r.edges).toHaveLength(1)
    expect(r.direction).toBe(direction)
  })

  test('a statement on the header line is parsed, not discarded', () => {
    // `graph LR; A --> B` is ordinary in the older style. Dropping the tail
    // would lose a node with no error at all, which is U18's failure again in
    // miniature.
    const r = parseMermaidFlowchart('graph LR; A[one] --> B[two]\n')
    expect(r.error).toBeNull()
    expect(r.nodes).toHaveLength(2)
    expect(r.edges).toHaveLength(1)
  })

  test('something that is not a flowchart is still refused, in words', () => {
    const r = parseMermaidFlowchart('A --> B\n')
    expect(r.error).toContain('flowchart')
    expect(r.error).not.toMatch(JS_ERROR)
  })

  test('a keyword that merely starts with one is not a header', () => {
    expect(parseMermaidFlowchart('flowchartish TD\n A-->B\n').error).not.toBeNull()
  })
})

describe('U19 · edges that touch a subgraph', () => {
  const DIAGRAM = `flowchart TD
  daemon[dev-host daemon] --> hub
  subgraph hub [Weave server]
    registry[host registry]
    api[REST + MCP]
  end
  registry --> api
`

  test('an edge to a subgraph lays out instead of throwing', () => {
    const r = parseMermaidFlowchart(DIAGRAM)
    expect(r.error).toBeNull()
    expect(r.nodes).toHaveLength(4)
    expect(r.edges).toHaveLength(2)
    for (const n of r.nodes) {
      expect(Number.isFinite(n.position.x)).toBe(true)
      expect(Number.isFinite(n.position.y)).toBe(true)
    }
  })

  test('the edge still points at the subgraph, not at a member of it', () => {
    // The substitution is a layout device. Rewriting the diagram to dodge a
    // limitation of the layout engine would change what the author drew.
    const r = parseMermaidFlowchart(DIAGRAM)
    expect(r.edges.find((e) => e.target === 'hub')?.source).toBe('daemon')
  })

  test.each([
    ['out of a subgraph', 'flowchart TD\n subgraph hub\n  a\n end\n hub --> out\n'],
    ['between two subgraphs', 'flowchart TD\n subgraph one\n  a\n end\n subgraph two\n  b\n end\n one --> two\n'],
    ['into an empty subgraph', 'flowchart TD\n subgraph hub\n end\n x --> hub\n'],
    ['from a member into its own parent', 'flowchart TD\n subgraph hub\n  a\n end\n a --> hub\n'],
    ['into a nested subgraph', 'flowchart TD\n subgraph outer\n  subgraph inner\n   a\n  end\n end\n x --> outer\n'],
  ])('%s', (_name, src) => {
    expect(parseMermaidFlowchart(src).error).toBeNull()
  })

  test('dagre is not silently falling back to the grid', () => {
    // **The assertion that makes the others mean anything.** The layout catches
    // a dagre failure and arranges the nodes in a grid so the diagram still
    // opens — which also means a broken layout looks exactly like a working
    // one from the outside. Three of the cases above passed with the fix
    // reverted until this check was added.
    const warned = mock(() => {})
    const real = console.error
    console.error = warned
    try {
      parseMermaidFlowchart(DIAGRAM)
    } finally {
      console.error = real
    }
    expect(warned).not.toHaveBeenCalled()
  })
})

describe('U19 · what a failure says', () => {
  test('a layout crash is explained, not pasted', async () => {
    // The catch branch is unreachable now that the cluster edges work, so it is
    // reached on purpose. Without this the property is asserted by reading the
    // code, and the code is what was wrong.
    mock.module('../lib/layout', () => ({
      applyDagreLayout: () => {
        throw new TypeError('Cannot set properties of undefined (setting \'rank\')')
      },
    }))
    const { parseMermaidFlowchart: parse } = await import('../lib/parser')
    const r = parse('flowchart TD\n A --> B\n')

    expect(r.error).not.toBeNull()
    expect(r.error).not.toMatch(JS_ERROR)
    expect(r.error).toContain('source is unchanged')
  })
})
