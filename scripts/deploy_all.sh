#!/usr/bin/env bash
# Deploy every ProofPay service to Cloud Run, in dependency order, chaining each
# service's URL into the next. WRITES ONLY — you run it with Jaf's approval.
#
# Order and wiring:
#   1. registry-drift   -> its URL becomes the marketplace's REGISTRY_URL
#   2. pacta-marketplace-> its URL becomes the agent's and provider-bot's MARKETPLACE_URL
#   3. proofpay-agent   -> resolves the marketplace URL itself
#   4. proofpay-provider-> resolves the marketplace URL itself (Cloud Run Job)
#
# Prereq: run scripts/wire_cloud.sh once first (APIs, Artifact Registry, topic, SAs).
set -euo pipefail

PROJECT="${PROJECT:-optimal-signer-506615-d5}"
REGION="${REGION:-us-central1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT REGION

url_of() { # url_of <service>
  gcloud run services describe "$1" --project "${PROJECT}" --region "${REGION}" \
    --format 'value(status.url)' 2>/dev/null || true
}

echo "############################################################"
echo "# 1/4  registry-drift"
echo "############################################################"
"${ROOT}/registry-drift/deploy.sh"
REGISTRY_URL="$(url_of registry-drift)"
echo ">> registry-drift URL: ${REGISTRY_URL}"

echo "############################################################"
echo "# 2/4  pacta-marketplace  (REGISTRY_URL from step 1)"
echo "############################################################"
REGISTRY_URL="${REGISTRY_URL}" "${ROOT}/marketplace/deploy.sh"
MARKETPLACE_URL="$(url_of pacta-marketplace)"
echo ">> marketplace URL: ${MARKETPLACE_URL}"

echo "############################################################"
echo "# 3/4  proofpay-agent  (MARKETPLACE_URL from step 2)"
echo "############################################################"
MARKETPLACE_URL="${MARKETPLACE_URL}" "${ROOT}/agent/deploy.sh"
AGENT_URL="$(url_of proofpay-agent)"
echo ">> agent URL: ${AGENT_URL}"

echo "############################################################"
echo "# 4/4  proofpay-provider  (Cloud Run Job)"
echo "############################################################"
MARKETPLACE_URL="${MARKETPLACE_URL}" "${ROOT}/provider-bot/deploy.sh"

echo
echo "== All services deployed =="
echo "   registry-drift : ${REGISTRY_URL}"
echo "   marketplace    : ${MARKETPLACE_URL}"
echo "   agent          : ${AGENT_URL}"
echo "Next: finish the event wiring (Pub/Sub push + Scheduler) with scripts/wire_cloud.sh,"
echo "then run the cloud demos:  AGENT_URL=${AGENT_URL} MARKET_URL=${MARKETPLACE_URL} make demo-happy"
