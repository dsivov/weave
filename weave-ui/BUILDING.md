# Building the UI

`bun run build` is the supported command (`bunx --bun vite build`). Everything below is for the case
where **`bun` is not installed** — which is true of the project's dev containers, and was true for the
whole of P6 and P7.

## Never run a bare `vite build` in this repository

`vite.config.ts` sets `outDir: '../weave/server/webui'` with **`emptyOutDir: true`**. That path is
**gitignored** and is what a running server serves, so a bare build silently replaces someone's live
UI and `git status` says nothing. It has happened twice.

**Always pass an explicit destination:**

```bash
vite build --outDir /tmp/weave-ui-build --emptyOutDir
```

Then check `weave/server/webui/index.html`'s mtime is unchanged — verify it rather than intend to.

## Building without bun

Vite runs under Node here, but three things bun does implicitly must be supplied. Put these two files
**outside the repository** (they are environment, not source) and point `NODE_OPTIONS` at the first.

```js
// register.mjs — NODE_OPTIONS="--import=/abs/path/register.mjs"
import { register } from 'node:module'
register('./hook.mjs', import.meta.url)
// `--import` alone only *runs* a file; loader hooks must go through register().
```

```js
// hook.mjs
import { pathToFileURL } from 'node:url'

// 1 · `@/…` path alias. bun reads tsconfig paths; node does not.
export async function resolve(spec, ctx, next) {
  if (spec.startsWith('@/')) {
    const base = '/abs/path/to/weave-ui/src/' + spec.slice(2)
    for (const ext of ['.ts', '.tsx', '/index.ts', '.js']) {
      try { return await next(pathToFileURL(base + ext).href, ctx) } catch { /* try next */ }
    }
  }
  return next(spec, ctx)
}

// 2 · `import.meta.env`. bun defines it; node does not. Vite bundles the config to
//     node_modules/.vite-temp/*.mjs and loads *that*, so the shim goes on the temp
//     file rather than on vite.config.ts.
export async function load(url, ctx, next) {
  if (url.includes('/.vite-temp/')) {
    const r = await next(url, ctx)
    return { ...r, source: 'import.meta.env ??= {};\n' + r.source.toString() }
  }
  return next(url, ctx)
}
```

```bash
NODE_OPTIONS="--import=/abs/path/register.mjs" \
  ./node_modules/.bin/vite build --outDir /tmp/weave-ui-build --emptyOutDir
```

**There was a fourth workaround — transpiling `.ts`/`.tsx` — and it is gone for good.** It existed
because `constants.ts` imported a type as a value, pulling `Button.tsx` into the runtime graph of every
consumer. `import type` fixed it at source. **If that hook is ever needed again, something has grown a
value import of a type** — fix the import rather than restoring the workaround.

## What this proves and what it does not

A Node build proves **the sources compile and bundle**. It does not prove `bunx --bun vite build`
produces the same output, and it runs no tests. **`bun test` has no substitute here** — after
[D-036](../docs/DECISIONS.md) nothing runs it automatically either, so it is a by-hand step at a gate.

## Two tool notes worth keeping

- **`eslint -f json` does not emit JSON.** `@stylistic/eslint-plugin-js` prints a deprecation banner to
  **stdout** first, and the banner also begins with `[`. Slice from the first `[{"filePath"`.
- **`eslint --fix-dry-run -f json`** returns residual counts *and* the rewritten text without touching a
  file. The whole W13 measurement was taken that way, with a clean tree throughout.
