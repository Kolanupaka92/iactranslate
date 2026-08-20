#!/usr/bin/env bash
# Build the docs site + investor overview into one static site and deploy it
# publicly to Vercel (project: iactranslate-docs → https://iactranslate-docs.vercel.app).
#
# Prereqs: `vercel` CLI logged in (`vercel login`), and Python deps (`pip install -e .[dev]`).
# The Vercel project has deployment protection (SSO) disabled so the pages are
# publicly viewable; if you recreate the project, disable it under
# Settings → Deployment Protection, or via the API (ssoProtection: null).
set -euo pipefail
cd "$(dirname "$0")/.."

# `python` is not on PATH on a stock macOS (only `python3`), and is absent
# outside an activated virtualenv — the script failed at its first line for
# exactly that reason. Prefer the repo venv, then python3, then python.
if [ -x "$(dirname "$0")/../.venv/bin/python" ]; then
  PY="$(cd "$(dirname "$0")/.." && pwd)/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi

BUILD="${1:-/tmp/iactranslate-site}"
ALIAS="${2:-iactranslate-docs.vercel.app}"
rm -rf "$BUILD"; mkdir -p "$BUILD"

"$PY" scripts/build_docs_site.py "$BUILD/index.html"
"$PY" scripts/build_deck_static.py "$BUILD/overview.html"

# Cross-link the two pages within the deployed site.
"$PY" - "$BUILD" <<'PY'
import sys, pathlib
b = pathlib.Path(sys.argv[1])
idx, ov = b / "index.html", b / "overview.html"
idx.write_text(idx.read_text().replace(
    '<a href="#doc-adr">ADRs</a></nav>',
    '<a href="#doc-adr">ADRs</a>'
    '<a href="/overview" style="margin-left:auto;color:var(--accent)">Investor overview →</a></nav>'))
ov.write_text(ov.read_text().replace(
    "https://claude.ai/code/artifact/ecc2a04b-904e-431b-9ab0-7a8856963c20", "/"))
PY
printf '{"cleanUrls": true}\n' > "$BUILD/vercel.json"

cd "$BUILD"
# $BUILD is normally under /tmp, which doesn't survive reboots — re-link to the
# real project every run instead of relying on a persisted .vercel/project.json,
# or a stale/missing link makes `vercel deploy` silently create a new project.
vercel link --yes --project=iactranslate-docs >/dev/null
DEPLOY_LOG="$(mktemp)"
vercel deploy --prod --yes | tee "$DEPLOY_LOG"
URL="$(grep -oE 'https://iactranslate-docs-[a-zA-Z0-9.-]*\.vercel\.app' "$DEPLOY_LOG" | tail -1)"
rm -f "$DEPLOY_LOG"
if [ -z "$URL" ]; then
  echo "Could not find the deployment URL in vercel's output — aborting alias step." >&2
  exit 1
fi
vercel alias set "$URL" "$ALIAS"
echo "Live: https://$ALIAS  (/ = docs, /overview = investor overview)"
