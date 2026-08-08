"""Knowledge — the retrieval index and the passes that keep it honest.

* :mod:`.dedup` — canonical entities and the resolver that merges duplicates.
* :mod:`.quality` — extraction quality filter and gate.
* :mod:`.community` — community detection and summarisation.
* :mod:`.connectivity` — rescue for orphaned regions of the graph.

What this package produces is **derived data**, rebuilt rather than authored. It
is not an artifact body: an artifact node references its source by
``repo · path · rev`` and never embeds a copy of it (A5).
"""
