# Vendored: mermaid-visual-editor

`components/` and `lib/` are a vendored copy of
[mermaid-visual-editor](https://github.com/saketkattu/mermaid-visual-editor)
by Saket Kattuboina, MIT licensed — see `LICENSE`, which must stay with this
directory.

It is vendored rather than depended on because the upstream package publishes a
built Next.js site (`out/` + a `serve` binary), not importable React components.

## What was changed on the way in

- `'use client'` directives dropped — Vite has no server components.
- `@/components/…` and `@/lib/…` imports repointed at this directory. CG's own
  `@/components` and `@/lib` exist, so the original paths would have silently
  resolved to the wrong modules.
- `app/layout.tsx` and `app/page.tsx` dropped (Next-specific). The page is
  reimplemented as `../pages/Diagrams.tsx`, which adds what upstream has no
  concept of: loading from and saving to the workspace's shared, signed
  diagram set.
- `app/globals.css` → `editor.css`, with the four neumorphic custom properties
  bound to CG's theme tokens so the canvas follows light/dark.
- Unit tests dropped (`lib/*.test.ts`) — they are upstream's, and run under a
  toolchain this repo doesn't carry.

Keep edits here minimal and mechanical, so a future upstream refresh stays a
re-copy plus this same rewrite rather than a merge.
