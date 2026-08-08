"""The persistence port.

* :mod:`.record` — ``RecordStore``: typed records with atomic replace and
  corrupt-file tolerance. **The one persistence port** — users, hosts, workers,
  tasks and project layouts are all written against it, and no module builds a
  database client outside its own adapter (A4, D-020).
* :mod:`.locks` — keyed locks and shared process state, including the
  workspace-keyed claim lock the collision guarantee rests on.
"""
