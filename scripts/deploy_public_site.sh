#!/usr/bin/env bash
# Build the docs site + investor overview into one static site and deploy it
# publicly to Vercel (project: iactranslate-docs → https://docs.iactranslate.com).
# Normally unnecessary: the project builds the same thing on every push to main
# (repo-root vercel.json). This is the manual fallback.
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
ALIAS="${2:-docs.iactranslate.com}"
rm -rf "$BUILD"; mkdir -p "$BUILD"

"$PY" scripts/build_public_site.py "$BUILD"

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
