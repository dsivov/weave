/**
 * The ontology canvas, against data the preset does not contain (CR-001 §4b).
 *
 * **This fixture exists because a preset-derived test proves nothing about
 * either case it needs to cover.** The shipped preset has **0 of 23** link types
 * carrying properties and **0 of 23** using the ANY wildcard — so a test built
 * from it renders 37 well-behaved edges, passes, and says nothing about the save
 * that drops link properties or the wildcard that produces 324 edges.
 *
 * That is the same defect twice: *a property of the data, mistaken for a
 * property of the code, because the only data anyone tested against did not
 * exercise it.* W13's shape, and W5's before it. One fixture closes both, and it
 * costs a line each.
 *
 * Run with `bun test`. **These have never been executed** — bun is not installed
 * in the container this was written in, and after D-036 nothing runs them
 * automatically either. They are written to be right, not verified to be.
 */

import { describe, expect, test } from 'bun:test'

import { expandOntology } from '../OntologyCanvas'
import type { OntologyDoc } from '@/api/weave'

/** Four types, and every case the preset lacks. */
const FIXTURE: OntologyDoc = {
  name: 'fixture',
  object_types: [
    { name: 'Task', description: 'a unit of work', properties: [{ name: 'title', kind: 'string' }] },
    { name: 'Review', description: '', properties: [] },
    { name: 'Insight', description: '', properties: [] },
    { name: 'Person', description: '', properties: [] }
  ],
  link_types: [
    // 1 · a plain one-to-one pair — the well-behaved majority
    { name: 'reviewed_in', source_types: ['Task'], target_types: ['Review'], cardinality: '1:N' },
    // 2 · fans out: one link type, four concrete edges
    {
      name: 'specified_by',
      source_types: ['Task', 'Review'],
      target_types: ['Insight', 'Person'],
      cardinality: 'N:M'
    },
    // 3 · LINK PROPERTIES — 0 of 23 preset links have any
    {
      name: 'yielded',
      source_types: ['Review'], target_types: ['Insight'], cardinality: '1:N',
      properties: [{ name: 'confidence', kind: 'percent' }, { name: 'at', kind: 'date' }]
    },
    // 4 · THE ANY WILDCARD — 0 of 23 preset links use one. Empty lists mean
    //     "any type", so this alone is 4 × 4 = 16 edges here, and 18 × 18 = 324
    //     against the real preset.
    { name: 'mentions', source_types: [], target_types: [], cardinality: 'N:M' }
  ]
}

describe('expandOntology', () => {
  test('one LinkType becomes N edges, and the count is the fan-out', () => {
    const { nodes, edges } = expandOntology(FIXTURE)
    expect(nodes).toHaveLength(4)
    // 1 + 4 + 1 + 16 = 22 edges from 4 link types. The arithmetic is the point:
    // a canvas that drew 4 would be lying about how they attach.
    expect(edges).toHaveLength(22)
  })

  test('a wildcard link connects every type to every type, including itself', () => {
    const { edges } = expandOntology(FIXTURE)
    const wildcard = edges.filter((e) => e.data?.linkType === 'mentions')
    expect(wildcard).toHaveLength(16)
    expect(wildcard.every((e) => e.data?.wildcard)).toBe(true)
    // Self-links are real: a Task may mention a Task.
    expect(wildcard.some((e) => e.source === e.target)).toBe(true)
  })

  test('a wildcard is marked as one, so the inspector can say so', () => {
    const { edges } = expandOntology(FIXTURE)
    const named = edges.filter((e) => e.data?.linkType === 'reviewed_in')
    expect(named.every((e) => e.data?.wildcard)).toBe(false)
  })

  test('every edge of a shared link type knows its siblings', () => {
    const { edges } = expandOntology(FIXTURE)
    const shared = edges.filter((e) => e.data?.linkType === 'specified_by')
    expect(shared).toHaveLength(4)
    // Each one names the other three — this is what the inspector heads itself
    // with, and it is why "edit one, change many" is visible rather than a
    // surprise.
    for (const edge of shared) expect(edge.data?.siblings).toHaveLength(3)
  })

  test('a link type drawn once has no siblings and claims none', () => {
    const { edges } = expandOntology(FIXTURE)
    const single = edges.find((e) => e.data?.linkType === 'reviewed_in')
    expect(single?.data?.siblings).toHaveLength(0)
  })

  test('cardinality survives onto every concrete edge', () => {
    const { edges } = expandOntology(FIXTURE)
    for (const e of edges.filter((x) => x.data?.linkType === 'yielded')) {
      expect(e.data?.cardinality).toBe('1:N')
    }
  })

  test('link-type properties are not lost — the fixture the preset cannot be', () => {
    // `expandOntology` does not carry properties onto edges (the inspector reads
    // them from the document), so this asserts the document round-trips
    // untouched: nothing in the expansion may mutate or drop them.
    const before = JSON.stringify(FIXTURE)
    expandOntology(FIXTURE)
    expect(JSON.stringify(FIXTURE)).toBe(before)

    const yielded = FIXTURE.link_types.find((l) => l.name === 'yielded')
    expect(yielded?.properties).toHaveLength(2)
    expect(yielded?.properties?.map((p) => p.kind)).toEqual(['percent', 'date'])
  })

  test('edge ids are unique, or the canvas silently drops edges', () => {
    const { edges } = expandOntology(FIXTURE)
    expect(new Set(edges.map((e) => e.id)).size).toBe(edges.length)
  })

  test('an empty ontology produces an empty canvas rather than throwing', () => {
    const { nodes, edges } = expandOntology({ name: 'empty', object_types: [], link_types: [] })
    expect(nodes).toHaveLength(0)
    expect(edges).toHaveLength(0)
  })

  test('a wildcard against no types produces nothing, not a crash', () => {
    // The degenerate case: ANY over an empty type list. Worth pinning because
    // the expansion multiplies, and multiplying by zero is the one input that
    // makes a fan-out disappear rather than explode.
    const { edges } = expandOntology({
      name: 'x', object_types: [],
      link_types: [{ name: 'mentions', source_types: [], target_types: [], cardinality: 'N:M' }]
    })
    expect(edges).toHaveLength(0)
  })
})
