# 0067. A per-request nonce CSP instead of a static one

**Status:** Accepted
**Date:** 2026-09-15

## Context

The console had a Content-Security-Policy set as a static header in
`next.config.ts`. A static header can only express what is the same on every
response, and the one directive that carries the security — `script-src` —
is not: Next.js hydrates through inline scripts, so a static policy either
allowed `'unsafe-inline'` (and therefore any injected script) or broke the
app. The shipped header chose the first. It stopped framing and mixed content
and did nothing against the attack a CSP exists to stop.

## Decision

**The policy is generated per request, with a nonce, in `web/proxy.ts`**
(Next.js 16's request proxy). Each response gets
`script-src 'self' 'nonce-<random>' 'strict-dynamic'`. Next.js reads the
nonce from the request header and stamps it onto its own script tags; a script
without the nonce does not run, and `'strict-dynamic'` lets the nonced
scripts load Next's chunks without listing hosts. `connect-src 'self'` holds
because every API call already goes through the same-origin `/api/v1`
rewrite; `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'` and
`form-action 'self'` close the remaining directives.

**Styles keep `'unsafe-inline'`, deliberately.** React sets `style`
*attributes* (the cost-bar widths), and a nonce can cover a `<style>` element
but never an attribute. The alternative is a hash per attribute value, which
changes with every number rendered. CSS injection is a far weaker class than
script injection, and every strict-CSP deployment that renders data-driven
styles makes this same trade. The comment in `proxy.ts` says so, so the next
reader does not "fix" it.

**The root layout is forced dynamic** (`await headers()` in `layout.tsx`).
The first deployment shipped with the landing page still statically
prerendered, and a prerendered page has no per-request nonce — 33 violations
in the browser console. A nonce CSP requires per-request rendering; that is
its price, and for a marketing page at this traffic it is not measurable.

## Consequences

- Verified on the live site: the nonce differs between two requests, exactly
  one `Content-Security-Policy` header is present, and the console reports no
  violations on the landing page, sign-in, or the console.
- Every page is now server-rendered per request. If a page is ever added that
  must be static, it has to opt out of the matcher and go without a nonce —
  and therefore without inline scripts.
- Third-party scripts have no path in. Adding one means listing it and
  arguing for it here, which is the correct amount of friction.
