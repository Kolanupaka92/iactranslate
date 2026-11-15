#!/usr/bin/env bash
# Deploy the API to Cloud Run from the image CI already built, scanned and signed.
#
#   scripts/deploy_api.sh [COMMIT]      # default: the current HEAD, which must be pushed
#
# Nothing is rebuilt here. CI publishes ghcr.io/<owner>/iactranslate:<commit> only
# after every job passes (tests, Trivy, Bandit, pip-audit), so production runs
# byte-for-byte what was tested. Cloud Run cannot pull from GHCR, so Cloud Build
# copies that image into the project's Artifact Registry first.
#
# Only the image changes. `gcloud run deploy --image` keeps the service's
# environment, secrets, VPC egress and service account exactly as they are.
#
# Prereqs: gcloud signed in (`gcloud auth login`) with access to the project.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${IACTRANSLATE_GCP_PROJECT:-project-c736b433-1b47-40c0-a2c}"
REGION="${IACTRANSLATE_GCP_REGION:-us-central1}"
SERVICE="${IACTRANSLATE_SERVICE:-iactranslate-api}"
SOURCE_REPO="ghcr.io/kolanupaka92/iactranslate"
TARGET_REPO="${REGION}-docker.pkg.dev/${PROJECT}/iactranslate/api"
GCLOUD="$(command -v gcloud || echo "$HOME/.local/bin/gcloud")"

SHA="${1:-$(git rev-parse HEAD)}"
if ! git branch -r --contains "$SHA" 2>/dev/null | grep -q "origin/main"; then
  echo "Commit $SHA is not on origin/main; CI only publishes images for main." >&2
  exit 1
fi

# CI publishes the image as its last job, so it exists only if every job passed.
TOKEN="$(curl -fsS "https://ghcr.io/token?scope=repository:${SOURCE_REPO#ghcr.io/}:pull" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
STATUS="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json" \
  "https://${SOURCE_REPO/ghcr.io/ghcr.io/v2}/manifests/${SHA}")"
if [ "$STATUS" != "200" ]; then
  echo "No CI-published image for $SHA (HTTP $STATUS). Wait for CI on main to pass." >&2
  exit 1
fi

echo "==> Copying ${SOURCE_REPO}:${SHA} into Artifact Registry"
CONFIG="$(mktemp)"
trap 'rm -f "$CONFIG"' EXIT
cat > "$CONFIG" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: [pull, "${SOURCE_REPO}:${SHA}"]
  - name: gcr.io/cloud-builders/docker
    args: [tag, "${SOURCE_REPO}:${SHA}", "${TARGET_REPO}:${SHA}"]
  - name: gcr.io/cloud-builders/docker
    args: [tag, "${SOURCE_REPO}:${SHA}", "${TARGET_REPO}:latest"]
images: ["${TARGET_REPO}:${SHA}", "${TARGET_REPO}:latest"]
EOF
"$GCLOUD" builds submit --no-source --config "$CONFIG" --project "$PROJECT" --region "$REGION" --quiet

PREVIOUS="$("$GCLOUD" run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format='value(status.latestReadyRevisionName)')"
echo "==> Deploying ${TARGET_REPO}:${SHA} to ${SERVICE} (currently ${PREVIOUS})"
"$GCLOUD" run deploy "$SERVICE" --image "${TARGET_REPO}:${SHA}" \
  --project "$PROJECT" --region "$REGION" --quiet

URL="$("$GCLOUD" run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format='value(status.url)')"
echo "==> Verifying ${URL}"
for _ in $(seq 1 10); do
  if curl -fsS "${URL}/health" >/dev/null; then
    "$GCLOUD" run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
      --format='value(status.latestReadyRevisionName,status.traffic[0].percent)'
    echo "Live: ${URL} at ${SHA}"
    exit 0
  fi
  sleep 3
done
echo "Health check failed after deploy; roll back with:" >&2
echo "  gcloud run services update-traffic ${SERVICE} --to-revisions=${PREVIOUS}=100 --region ${REGION} --project ${PROJECT}" >&2
exit 1
