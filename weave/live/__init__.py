"""`weave.live` — the live surface: what is happening right now, to whom.

Two things, and they are different in kind:

- :mod:`weave.live.stream` — SSE. The **transport** by which events already
  published on the bus reach a browser. It answers no questions of its own; it
  is a third adapter over the handlers REST and MCP already share (A9), so a
  board cannot show something the other surfaces would deny.
- :mod:`weave.live.presence` — who is on a board and what they are editing.
  Short-lived state, deliberately not durable: presence that outlives the person
  is worse than no presence.

**A15 holds here and it is worth stating plainly.** SSE is the *client* holding a
connection open to the hub; the server never dials out. Nothing in this package
requires an inbound connection to a dev host or a worker, so remote fleets behind
NAT are unaffected.

**The tenant boundary is the thing to get right.** A stream is a long-lived
subscription to a bus carrying every workspace's events, so filtering is not a
nicety — it is the same boundary `/ask` and `/projects` enforce per request,
enforced once per connection instead. Every event is checked against both the
connection's workspace and the subscriber's membership before it is written.
"""
