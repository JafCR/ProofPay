#!/usr/bin/env bash
# Deploy the provider-bot to Cloud Run as a JOB. WRITES ONLY - you run it with Jaf's
# approval; every gcloud command is echoed before it runs.
#
# Why a Job, not a Service: the bot is a worker (it polls the marketplace and then
# publishes a delivery event); it does not serve HTTP, so it cannot be a Cloud Run
# service (those must listen on $PORT). You trigger it per demo run with
# `gcloud run jobs execute proofpay-provider --wait` (or the Makefile target).
#
# The bot's poll loop runs until the job's task timeout; within that window it picks
# up the funded engagement, does the work, and publishes to Pub/Sub. Set MODE and
# DELAY_SECONDS per demo. (Alternatively, run the bot locally against the cloud URLs
# - it needs no cloud residency; this Job just makes it fully in-cloud.)
set -euo pipefail

PROJECT="${PROJECT:-optimal-signer-506615-d5}"
REGION="${REGION:-us-central1}"
REPO="${AR_REPO:-proofpay}"
JOB="${JOB:-proofpay-provider}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${JOB}:latest"

MODE="${MODE:-honest}"
DELAY_SECONDS="${DELAY_SECONDS:-90}"
SMB_ID="${SMB_ID:-1}"
PUBSUB_TOPIC="${PUBSUB_TOPIC:-proofpay-delivery}"
TASK_TIMEOUT="${TASK_TIMEOUT:-900}"     # seconds; covers the work delay + a margin
BOT_SA="${BOT_SA:-}"                     # SA with roles/pubsub.publisher (wire_cloud.sh)

# The marketplace URL the bot polls (resolved from the deployed service if unset).
MARKETPLACE_URL="${MARKETPLACE_URL:-}"
if [ -z "${MARKETPLACE_URL}" ]; then
  MARKETPLACE_URL="$(gcloud run services describe pacta-marketplace \
    --project "${PROJECT}" --region "${REGION}" --format 'value(status.url)' 2>/dev/null || true)"
fi
if [ -z "${MARKETPLACE_URL}" ]; then
  echo "ERROR: MARKETPLACE_URL unset and pacta-marketplace not deployed. Deploy it first."
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run() { echo "+ $*"; "$@"; }

echo "== Deploying ${JOB} (Cloud Run Job) =="
echo "   project=${PROJECT} region=${REGION} image=${IMAGE} mode=${MODE} marketplace=${MARKETPLACE_URL}"

run gcloud builds submit "${HERE}" \
  --project "${PROJECT}" \
  --tag "${IMAGE}"

env_vars="MODE=${MODE},DELAY_SECONDS=${DELAY_SECONDS},SMB_ID=${SMB_ID}"
env_vars="${env_vars},MARKETPLACE_URL=${MARKETPLACE_URL},PUBSUB_TOPIC=${PUBSUB_TOPIC}"

job_args=(
  --project "${PROJECT}"
  --region "${REGION}"
  --image "${IMAGE}"
  --task-timeout "${TASK_TIMEOUT}"
  --max-retries 0
  --set-env-vars "${env_vars}"
)
[ -n "${BOT_SA}" ] && job_args+=(--service-account "${BOT_SA}")

# `jobs deploy` creates or updates the job (idempotent).
run gcloud run jobs deploy "${JOB}" "${job_args[@]}"

echo "== ${JOB} deployed. Trigger a run for a demo with: =="
echo "   gcloud run jobs execute ${JOB} --project ${PROJECT} --region ${REGION} --wait"
