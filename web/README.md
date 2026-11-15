# IaCTranslate — web

Next.js app with two surfaces:

| Route | What it is | Rendering |
|---|---|---|
| `/` | Public landing page | Static, no client JS beyond Next's runtime |
| `/console` | The product workspace — sign in, upload, assess, compare, generate | Client component, talks to the FastAPI service |

## Running it

The console needs the API. Start both:

```bash
npm run dev --prefix web
```

```bash
.venv/bin/uvicorn iactranslate.api.main:app --port 8000
```

The API must allow the web origin — `IACTRANSLATE_CORS_ORIGINS=http://localhost:3000`.
Both are configured in `.claude/launch.json` at the repo root.

## Conventions

**Content lives in `lib/site.ts`, not in the markup.** Every figure on the landing
page is either measured in this repository or carries a published source, and it
is kept in one module so it cannot drift. The investor deck grew stale numbers
(403 tests, 32 ADRs) precisely because they were written inline.

**Never state a cost or a saving without its assumptions.** The cost comparison
renders its caveat at full size directly beneath the chart — not as a footnote and
not behind a tooltip. Making assumptions visible is the product's central claim;
a landing page that hides its own would contradict it on first contact.

**Colour comes from tokens in `app/globals.css`**, defined for light and dark.
Contrast is measured, not eyeballed: the light accent is emerald-700 rather than
the console's 600 because the landing page sets small text in it, and 600
measures 3.61:1 against the surface — below AA.

**The landing page has no client state.** If a change to it needs `"use client"`,
that is worth questioning first.

## Checks

```bash
npm run lint --prefix web && npm run build --prefix web
```

Both run in CI on every push.
