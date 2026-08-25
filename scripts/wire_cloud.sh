#!/usr/bin/env bash
# One-time (idempotent) Google Cloud wiring for ProofPay Phase B. WRITES ONLY — you
# run it with Jaf's approval; every gcloud command is echoed before it runs. Safe to
# re-run: each step checks for existence first, so re-running only fills in gaps.
#
# What it sets up:
#   - Enables the APIs ProofPay uses (Run, Cloud Build, Pub/Sub, Scheduler, Firestore,
#     Vertex AI, Artifact Registry, IAM).
#   - An Artifact Registry docker repo ("proofpay") for the images.
#   - A Firestore database in Native mode (the agent's mission store).
#   - Three least-privilege service accounts:
#       proofpay-agent    -> Vertex (aiplatform.user) + Firestore (datastore.user)
#       proofpay-provider -> Pub/Sub publisher (delivery events)
#       proofpay-invoker  -> run.invoker on the agent (used by the push sub + Scheduler OIDC)
#   - The Pub/Sub topic "proofpay-delivery".
#   - The delivery PUSH subscription (OIDC) to AGENT_URL/events/delivery.
#   - A Cloud Scheduler job hitting AGENT_URL/sweep every 10 minutes (OIDC).
#
# The subscription and Scheduler steps need the agent URL, so run this AFTER
# `make deploy-agent` (or re-run it then — the earlier steps are no-ops the 2nd time).
set -euo pipefail

PROJECT="${PROJECT:-optimal-signer-506615-d5}"
REGION="${REGION:-us-central1}"
REPO="${AR_REPO:-proofpay}"
FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-nam5}"   # US multi-region; override for EU etc.
TOPIC="${PUBSUB_TOPIC:-proofpay-delivery}"
PUSH_SUB="${PUSH_SUB:-proofpay-delivery-push}"
SCHED_JOB="${SCHED_JOB:-proofpay-sweep}"

AGENT_SA="proofpay-agent@${PROJECT}.iam.gserviceaccount.com"
PROVIDER_SA="proofpay-provider@${PROJECT}.iam.gserviceaccount.com"
INVOKER_SA="proofpay-invoker@${PROJECT}.iam.gserviceaccount.com"

run() { echo "+ $*"; "$@"; }
exists() { "$@" >/dev/null 2>&1; }

echo "== ProofPay cloud wiring =="
echo "   project=${PROJECT} region=${REGION}"
run gcloud config set project "${PROJECT}"

# --- 1. APIs ------------------------------------------------------------------ #
run gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  pubsub.googleapis.com \
  cloudscheduler.googleapis.com \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  --project "${PROJECT}"

# --- 2. Artifact Registry docker repo ----------------------------------------- #
if exists gcloud artifacts repositories describe "${REPO}" --location "${REGION}" --project "${PROJECT}"; then
  echo "   artifact repo '${REPO}' already exists"
else
  run gcloud artifacts repositories create "${REPO}" \
    --repository-format docker --location "${REGION}" --project "${PROJECT}" \
    --description "ProofPay service images"
fi

# --- 3. Firestore (Native) ---------------------------------------------------- #
if exists gcloud firestore databases describe --database '(default)' --project "${PROJECT}"; then
  echo "   Firestore (default) database already exists"
else
  run gcloud firestore databases create \
    --database '(default)' --location "${FIRESTORE_LOCATION}" \
    --type firestore-native --project "${PROJECT}"
fi

# --- 4. Service accounts ------------------------------------------------------ #
ensure_sa() { # ensure_sa <account-id> <display>
  local email="$1@${PROJECT}.iam.gserviceaccount.com"
  if exists gcloud iam service-accounts describe "${email}" --project "${PROJECT}"; then
    echo "   SA ${email} already exists"
  else
    run gcloud iam service-accounts create "$1" --display-name "$2" --project "${PROJECT}"
  fi
}
ensure_sa proofpay-agent    "ProofPay agent (Vertex + Firestore)"
ensure_sa proofpay-provider "ProofPay provider-bot (Pub/Sub publisher)"
ensure_sa proofpay-invoker  "ProofPay push/scheduler invoker"

grant() { # grant <sa-email> <role>
  run gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member "serviceAccount:$1" --role "$2" --condition None >/dev/null
}
grant "${AGENT_SA}"     roles/aiplatform.user
grant "${AGENT_SA}"     roles/datastore.user
grant "${PROVIDER_SA}"  roles/pubsub.publisher

# --- 5. Pub/Sub topic --------------------------------------------------------- #
if exists gcloud pubsub topics describe "${TOPIC}" --project "${PROJECT}"; then
  echo "   topic '${TOPIC}' already exists"
else
  run gcloud pubsub topics create "${TOPIC}" --project "${PROJECT}"
fi

# --- 6. Steps that need the deployed agent URL -------------------------------- #
AGENT_URL="${AGENT_URL:-$(gcloud run services describe proofpay-agent \
  --project "${PROJECT}" --region "${REGION}" --format 'value(status.url)' 2>/dev/null || true)}"
if [ -z "${AGENT_URL}" ]; then
  echo
  echo "!! proofpay-agent is not deployed yet, so the push subscription and the"
  echo "   Cloud Scheduler job were NOT created. Deploy the agent (make deploy-agent),"
  echo "   then re-run this script — the steps above are idempotent no-ops."
  exit 0
fi
echo "   agent URL: ${AGENT_URL}"

# invoker SA must be allowed to invoke the agent service (for OIDC-authenticated calls).
grant_invoker() {
  run gcloud run services add-iam-policy-binding proofpay-agent \
    --project "${PROJECT}" --region "${REGION}" \
    --member "serviceAccount:${INVOKER_SA}" --role roles/run.invoker >/dev/null
}
grant_invoker

# Pub/Sub needs permission to mint OIDC tokens as the invoker SA for push auth.
run gcloud iam service-accounts add-iam-policy-binding "${INVOKER_SA}" \
  --project "${PROJECT}" \
  --member "serviceAccount:service-$(gcloud projects describe "${PROJECT}" --format 'value(projectNumber)')@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role roles/iam.serviceAccountTokenCreator >/dev/null

# --- 7. Delivery PUSH subscription (OIDC) ------------------------------------- #
if exists gcloud pubsub subscriptions describe "${PUSH_SUB}" --project "${PROJECT}"; then
  echo "   subscription '${PUSH_SUB}' already exists (delete to reconfigure)"
else
  run gcloud pubsub subscriptions create "${PUSH_SUB}" \
    --project "${PROJECT}" \
    --topic "${TOPIC}" \
    --push-endpoint "${AGENT_URL}/events/delivery" \
    --push-auth-service-account "${INVOKER_SA}" \
    --ack-deadline 30 \
    --min-retry-delay 10s --max-retry-delay 300s
fi

# --- 8. Cloud Scheduler → /sweep every 10 minutes (OIDC) ---------------------- #
if exists gcloud scheduler jobs describe "${SCHED_JOB}" --location "${REGION}" --project "${PROJECT}"; then
  echo "   scheduler job '${SCHED_JOB}' already exists"
else
  run gcloud scheduler jobs create http "${SCHED_JOB}" \
    --project "${PROJECT}" --location "${REGION}" \
    --schedule "*/10 * * * *" \
    --uri "${AGENT_URL}/sweep" --http-method POST \
    --oidc-service-account-email "${INVOKER_SA}" \
    --oidc-token-audience "${AGENT_URL}"
fi

echo
echo "== Cloud wiring complete =="
echo "   topic=${TOPIC}  push_sub=${PUSH_SUB} -> ${AGENT_URL}/events/delivery"
echo "   scheduler=${SCHED_JOB} (*/10m) -> ${AGENT_URL}/sweep"
echo "Deploy the provider-bot with BOT_SA=${PROVIDER_SA} and AGENT_SA=${AGENT_SA} on the agent."
