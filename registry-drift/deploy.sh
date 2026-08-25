#!/usr/bin/env bash
# Deploy the registry-drift service to Cloud Run. WRITES ONLY — you run it (with
# Jaf's approval); nothing here executes on its own. Every gcloud command is echoed
# before it runs so you can see exactly what will happen.
#
# Prereqs (once): scripts/wire_cloud.sh has enabled the APIs and created the
# Artifact Registry repo. Auth as jaf@pactaprotocol.org with the project set.
set -euo pipefail

PROJECT="${PROJECT:-optimal-signer-506615-d5}"
REGION="${REGION:-us-central1}"
REPO="${AR_REPO:-proofpay}"
SERVICE="${SERVICE:-registry-drift}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}:latest"

# The revoke control token. Prefer a Secret Manager secret in a real environment;
# for the demo an env var is enough. NEVER commit a real value.
REVOKE_TOKEN="${REVOKE_TOKEN:-}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run() { echo "+ $*"; "$@"; }

echo "== Deploying ${SERVICE} to Cloud Run =="
echo "   project=${PROJECT} region=${REGION} image=${IMAGE}"

run gcloud builds submit "${HERE}" \
  --project "${PROJECT}" \
  --tag "${IMAGE}"

env_args=()
if [ -n "${REVOKE_TOKEN}" ]; then
  env_args+=(--set-env-vars "REVOKE_TOKEN=${REVOKE_TOKEN}")
else
  echo "WARNING: REVOKE_TOKEN is unset — the revoke control will be OPEN in cloud."
  echo "         Set REVOKE_TOKEN=... before deploy, or wire a Secret Manager secret:"
  echo "         --set-secrets REVOKE_TOKEN=proofpay-revoke-token:latest"
fi

run gcloud run deploy "${SERVICE}" \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --image "${IMAGE}" \
  --platform managed \
  --allow-unauthenticated \
  --min-instances 1 \
  --port 8080 \
  "${env_args[@]}"

URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" --format 'value(status.url)')"
echo "== ${SERVICE} deployed: ${URL} =="
echo "Point the marketplace at it with:  REGISTRY_URL=${URL}"
