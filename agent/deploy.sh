#!/usr/bin/env bash
# Deploy the ProofPay agent to Cloud Run. WRITES ONLY - you run it with Jaf's
# approval; every gcloud command is echoed before it runs.
#
# The image is built from the repo ROOT (it needs web/), with -f agent/Dockerfile,
# via a small Cloud Build config. Vertex AI is used through ADC (no API key on disk):
# the Cloud Run service account needs roles/aiplatform.user, and roles/datastore.user
# when STATE_BACKEND=firestore (scripts/wire_cloud.sh sets these up).
set -euo pipefail

PROJECT="${PROJECT:-optimal-signer-506615-d5}"
REGION="${REGION:-us-central1}"
REPO="${AR_REPO:-proofpay}"
SERVICE="${SERVICE:-proofpay-agent}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}:latest"

# Repo root = parent of this agent/ dir.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run() { echo "+ $*"; "$@"; }

# --- Resolve dependencies from already-deployed services ---------------------- #
MARKETPLACE_URL="${MARKETPLACE_URL:-}"
if [ -z "${MARKETPLACE_URL}" ]; then
  MARKETPLACE_URL="$(gcloud run services describe pacta-marketplace \
    --project "${PROJECT}" --region "${REGION}" --format 'value(status.url)' 2>/dev/null || true)"
fi
if [ -z "${MARKETPLACE_URL}" ]; then
  echo "ERROR: MARKETPLACE_URL is unset and pacta-marketplace is not deployed yet."
  echo "       Deploy the marketplace first (make deploy-marketplace) or pass MARKETPLACE_URL=..."
  exit 1
fi

GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-global}"
STATE_BACKEND="${STATE_BACKEND:-firestore}"
PUBSUB_TOPIC="${PUBSUB_TOPIC:-proofpay-delivery}"
DELIVERY_DEADLINE_SECONDS="${DELIVERY_DEADLINE_SECONDS:-86400}"
AGENT_SA="${AGENT_SA:-}"          # e.g. proofpay-agent@${PROJECT}.iam.gserviceaccount.com
DEMO_TOKEN="${DEMO_TOKEN:-}"       # prefer Secret Manager (see below) over a plain env var

echo "== Deploying ${SERVICE} to Cloud Run =="
echo "   project=${PROJECT} region=${REGION} image=${IMAGE}"
echo "   marketplace=${MARKETPLACE_URL} model=${GEMINI_MODEL} state=${STATE_BACKEND}"

# --- Build from the repo root with the agent Dockerfile ----------------------- #
CLOUDBUILD="$(mktemp)"
trap 'rm -f "${CLOUDBUILD}"' EXIT
cat >"${CLOUDBUILD}" <<YAML
steps:
  - name: gcr.io/cloud-builders/docker
    args: ['build', '-f', 'agent/Dockerfile', '-t', '${IMAGE}', '.']
images:
  - '${IMAGE}'
YAML
run gcloud builds submit "${ROOT}" \
  --project "${PROJECT}" \
  --config "${CLOUDBUILD}"

# --- Deploy ------------------------------------------------------------------- #
env_vars="GOOGLE_CLOUD_PROJECT=${PROJECT}"
env_vars="${env_vars},GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION}"
env_vars="${env_vars},GEMINI_MODEL=${GEMINI_MODEL}"
env_vars="${env_vars},JUDGE_STUB=0"
env_vars="${env_vars},MARKETPLACE_URL=${MARKETPLACE_URL}"
env_vars="${env_vars},STATE_BACKEND=${STATE_BACKEND}"
env_vars="${env_vars},PUBSUB_TOPIC=${PUBSUB_TOPIC}"
env_vars="${env_vars},DELIVERY_DEADLINE_SECONDS=${DELIVERY_DEADLINE_SECONDS}"

deploy_args=(
  --project "${PROJECT}"
  --region "${REGION}"
  --image "${IMAGE}"
  --platform managed
  --allow-unauthenticated
  --port 8080
  --set-env-vars "${env_vars}"
)
[ -n "${AGENT_SA}" ] && deploy_args+=(--service-account "${AGENT_SA}")
if [ -n "${DEMO_TOKEN}" ]; then
  deploy_args+=(--set-env-vars "DEMO_TOKEN=${DEMO_TOKEN}")
else
  echo "NOTE: DEMO_TOKEN unset - /missions will be open. To require a token, either"
  echo "      pass DEMO_TOKEN=... or wire a secret: --set-secrets DEMO_TOKEN=proofpay-demo-token:latest"
fi

run gcloud run deploy "${SERVICE}" "${deploy_args[@]}"

URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" --format 'value(status.url)')"
echo "== ${SERVICE} deployed: ${URL} =="
echo "Trace viewer:  ${URL}/   |   delivery push target:  ${URL}/events/delivery   |   sweep:  ${URL}/sweep"
