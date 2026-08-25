#!/usr/bin/env bash
# Deploy the Pacta marketplace (unmodified) to Cloud Run. WRITES ONLY - you run it
# with Jaf's approval; every gcloud command is echoed before it runs.
#
# min-instances=1 is REQUIRED for the demo: this marketplace keeps engagement state
# in-process (SQLite on the instance's local disk), so scaling to zero or to a second
# instance would lose/split state mid-mission. Document this in the README.
#
# REGISTRY_URL (optional): point the marketplace's pluggable registry at the
# registry-drift service to enable the cloud fraud demo. Unset = Pacta's built-in
# `local` seeded registry (happy path works either way).
set -euo pipefail

PROJECT="${PROJECT:-optimal-signer-506615-d5}"
REGION="${REGION:-us-central1}"
REPO="${AR_REPO:-proofpay}"
SERVICE="${SERVICE:-pacta-marketplace}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}:latest"
# Pin the exact Pacta revision this repo was built against (the local clone HEAD).
PACTA_COMMIT="${PACTA_COMMIT:-2ce2abe1d2dadeafcf16eaad847a39cfcf8b91a7}"
REGISTRY_URL="${REGISTRY_URL:-}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run() { echo "+ $*"; "$@"; }

echo "== Deploying ${SERVICE} (Pacta @ ${PACTA_COMMIT}) to Cloud Run =="
echo "   project=${PROJECT} region=${REGION} image=${IMAGE}"

# Pass the pinned commit as a build arg (the Dockerfile clones + checks it out).
run gcloud builds submit "${HERE}" \
  --project "${PROJECT}" \
  --tag "${IMAGE}"
# Note: to override PACTA_COMMIT at build time use a cloudbuild config; the default
# in the Dockerfile matches PACTA_COMMIT above, so the pinned build is reproducible.

env_args=(--set-env-vars "PACTA=1")
if [ -n "${REGISTRY_URL}" ]; then
  # Setting REGISTRY_URL makes Pacta select its `http` registry adapter automatically.
  env_args+=(--set-env-vars "REGISTRY_URL=${REGISTRY_URL}")
  echo "   registry: http adapter → ${REGISTRY_URL}"
else
  echo "   registry: built-in local (seeded)"
fi

run gcloud run deploy "${SERVICE}" \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --image "${IMAGE}" \
  --platform managed \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --port 8080 \
  "${env_args[@]}"

URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" --format 'value(status.url)')"
echo "== ${SERVICE} deployed: ${URL} =="
echo "Point the agent at it with:  MARKETPLACE_URL=${URL}"
