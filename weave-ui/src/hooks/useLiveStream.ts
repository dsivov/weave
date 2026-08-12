/**
 * useLiveStream — subscribe to `GET /live/stream` (SSE) for the current workspace.
 *
 * **Why fetch and not `EventSource`.** `EventSource` cannot send request headers,
 * and this surface authenticates with a bearer token and selects its tenant with
 * `WEAVE-WORKSPACE`. The alternatives were putting the token in the query string
 * — where it lands in access logs, proxy logs and browser history — or reading
 * the stream with `fetch` and a `ReadableStream`. The second is more code and no
 * secrets in URLs, so it is the one used here.
 *
 * **Reconnects, because a dropped stream is silent.** A closed connection looks
 * exactly like a quiet system: the board simply stops changing and nobody is
 * told. So the hook reconnects with capped backoff and reports `connected`, and
 * the board shows that state rather than implying freshness it does not have.
 *
 * This replaces polling (R32). Polling is not merely wasteful here — a 4-second
 * poll means two people can act on the same task for 4 seconds each believing it
 * is unclaimed.
 */

import { useEffect, useRef, useState } from 'react'
import { useSettingsStore } from '@/stores/settings'

export type LiveEvent = {
  type: string
  payload: Record<string, unknown>
  workspace: string
  source?: string
}

const BACKOFF_START_MS = 500
const BACKOFF_MAX_MS = 10_000

/** Parse one SSE block (`id:` / `event:` / `data:` lines) into an event. */
function parseFrame(block: string): LiveEvent | null {
  let data = ''
  for (const line of block.split('\n')) {
    if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return null
  try {
    return JSON.parse(data) as LiveEvent
  } catch {
    // A malformed frame must not kill the stream — one bad message would
    // otherwise end live updates for the session.
    return null
  }
}

export function useLiveStream(onEvent: (event: LiveEvent) => void) {
  const workspace = useSettingsStore.use.workspace()
  const [connected, setConnected] = useState(false)
  const [lastEventAt, setLastEventAt] = useState<number | null>(null)

  // Held in a ref so a re-render with a new callback does not tear down and
  // re-establish the connection — reconnecting on every render would be a
  // subtler kind of polling.
  const handler = useRef(onEvent)
  useEffect(() => { handler.current = onEvent }, [onEvent])

  useEffect(() => {
    const abort = new AbortController()
    let backoff = BACKOFF_START_MS
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const connect = async () => {
      if (stopped) return
      try {
        const token = localStorage.getItem('WEAVE-API-TOKEN')
        const headers: Record<string, string> = { Accept: 'text/event-stream' }
        if (token) headers['Authorization'] = `Bearer ${token}`
        if (workspace) headers['WEAVE-WORKSPACE'] = workspace

        const response = await fetch('/live/stream', {
          headers,
          signal: abort.signal
        })
        if (!response.ok || !response.body) {
          throw new Error(`live stream: HTTP ${response.status}`)
        }

        setConnected(true)
        backoff = BACKOFF_START_MS

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })

          // Frames are separated by a blank line; anything after the last one
          // is a partial frame and stays in the buffer.
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() ?? ''
          for (const block of blocks) {
            if (block.startsWith(':')) continue      // keep-alive comment
            const event = parseFrame(block)
            if (event) {
              setLastEventAt(Date.now())
              handler.current(event)
            }
          }
        }
        throw new Error('live stream closed')
      } catch {
        // The error is deliberately not inspected: every failure here — network
        // drop, server restart, a closed stream — has the same answer, which is
        // to reconnect with backoff. Binding it and ignoring it said the
        // opposite, that something was meant to be done with it.
        if (stopped || abort.signal.aborted) return
        setConnected(false)
        timer = setTimeout(connect, backoff)
        backoff = Math.min(backoff * 2, BACKOFF_MAX_MS)
      }
    }

    void connect()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
      abort.abort()
      setConnected(false)
    }
  }, [workspace])

  return { connected, lastEventAt }
}
