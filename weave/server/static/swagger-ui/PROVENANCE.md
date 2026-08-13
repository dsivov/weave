# Vendored Swagger UI assets

**Do not edit these files.** They are third-party build output, vendored so that `/docs` works on an
installation that cannot reach a CDN — an air-gapped or NAT-bound deployment, which is the shape
Weave is built for (A15). Approved by dsivov 2026-08-13; see **D-042** in `docs/DECISIONS.md`.

| | |
|---|---|
| Package | `swagger-ui-dist` |
| Version | **5.32.13** — pinned, not a floating major |
| Source | `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.13/<file>` |
| Licence | Apache-2.0 — `LICENSE`, plus `swagger-ui-bundle.js.LICENSE.txt` for the bundle's own dependencies |
| Vendored | 2026-08-13 |

## Checksums

Verify before trusting a copy that arrived some other way:

```
5f3be5d9cf40cdd60dca0dafeaf8743fd858d1b3bb717bbdaebf7201303f63d7  swagger-ui-bundle.js
9e617d9ac0afb0e430c11a17366de8624db7ce34c99ebd297443f0048ce30899  swagger-ui.css
3ed612f41e050ca5e7000cad6f1cbe7e7da39f65fca99c02e99e6591056e5837  favicon-32x32.png
```

## Refreshing

```bash
V=<new version>
for f in swagger-ui-bundle.js swagger-ui.css favicon-32x32.png \
         swagger-ui-bundle.js.LICENSE.txt LICENSE; do
  curl -sS -o weave/server/static/swagger-ui/$f \
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@$V/$f"
done
```

Then update the version and checksums above, and run
**`tests/test_docs_assets_are_actually_served.py`** — it builds the app, reads every URL `/docs`
names, and fetches each one, so a missing *or truncated* file fails rather than silently serving a
blank page. **That is the failure this directory exists to prevent** (U9): the page named
`/static/swagger-ui/*` while nothing was mounted there, so `/docs` returned 200 with a 404 stylesheet
and a 404 script.

Its sibling `tests/test_docs_page_names_assets_that_exist.py` checks the *source* — that the page and
the mount share one condition. Both matter and neither substitutes for the other: the structural one
cannot see a half-copied file, and the behavioural one cannot see a condition drifting apart. **An
earlier draft of this file credited the structural test with fetching URLs. It does not, and the
sentence was wrong the moment it was written** — which is the same reach-versus-claim mistake U9 was
made of, so it is corrected here rather than quietly deleted.
