#!/usr/bin/env bash
# make demo-fraud — the fraud run, end to end (SPEC §6, reframed per DECISIONS.md).
#
# Two layers of defense get exercised:
#   1. The provider first tries a forged registry reference. The protocol itself
#      rejects it at submission (HTTP 409) — watch the provider-bot log.
#   2. The provider falls back to real references and submits. Then the registry
#      record for step 2 is revoked at the source ("registry drift": the regulator
#      annuls the credential after submission). The delivery event fires, the agent
#      wakes, re-verifies EVERY reference itself, gets a 404 on the revoked one —
#      predicate P2 fails and the gate says DISPUTE. No payment moves.
#   3. The marketplace arbiter resolves the dispute as a refund, which slashes the
#      provider's stake (20% of the engagement price). Stake printed before/after.
#
# Env knobs: DELAY_SECONDS (default 10), TIMEOUT, REVOKED_REF.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_lib.sh"

DELAY_SECONDS="${DELAY_SECONDS:-10}"
TIMEOUT="${TIMEOUT:-$((DELAY_SECONDS + 180))}"
REVOKED_REF="${REVOKED_REF:-CR-RN-2026-104513}"
GOAL="Form a company in Costa Rica to purchase land in Guanacaste and operate a hotel. Budget \$6,000."

if [ "${KEEP_UP:-0}" != "1" ]; then trap cleanup_demo EXIT; fi
banner "ProofPay demo — fraud path (forged ref + registry drift, ${DELAY_SECONDS}s delay)"

if [ "$CLOUD" = "1" ]; then
  ts "cloud mode: AGENT_URL=$AGENT_URL  MARKET_URL=$MARKET_URL  REGISTRY_DRIFT_URL=$REGISTRY_DRIFT_URL"
  if [ -z "$REGISTRY_DRIFT_URL" ]; then
    ts "FAIL: cloud fraud demo needs REGISTRY_DRIFT_URL (the deployed registry-drift URL) and REVOKE_TOKEN"
    exit 1
  fi
  wait_url "$AGENT_URL/" 30
  start_bot fraud "$DELAY_SECONDS"
else
  "$ROOT/scripts/seed.sh"
  start_agent
  start_bot fraud "$DELAY_SECONDS"
fi

ts "Wake 1: creating the mission — the agent hires, signs and funds, then goes to sleep"
TRACE="$(create_mission "$GOAL" 6000)"
MISSION_ID="$(jsonget "$TRACE" mission.mission_id)"
ENGAGEMENT_ID="$(jsonget "$TRACE" mission.engagement_id)"
PROVIDER="$(jsonget "$TRACE" mission.provider_name)"
SMB_ID="$(jsonget "$(curl -fsS "$MARKET_URL/api/engagements/$ENGAGEMENT_ID")" smb.id)"
[ -n "$SMB_ID" ] || SMB_ID=1
ts "mission $MISSION_ID hired '$PROVIDER' (engagement #$ENGAGEMENT_ID, smb #$SMB_ID)"

ts "waiting for the provider to try the forged reference and then submit real work..."
WAITED=0
STATE=""
while [ "$WAITED" -lt "$TIMEOUT" ]; do
  STATE="$(jsonget "$(curl -fsS "$MARKET_URL/api/engagements/$ENGAGEMENT_ID")" state)"
  [ "$STATE" = "submitted" ] && break
  sleep 2
  WAITED=$((WAITED + 2))
done
if [ "$STATE" != "submitted" ]; then
  ts "FAIL: engagement never reached 'submitted' (state: $STATE)"
  exit 1
fi
ts "engagement submitted — first defense already fired at the protocol:"
grep -m1 "PROTOCOL BLOCKED FORGED REFERENCE" "$ROOT/.demo-bot.log" \
  && grep -m1 "409" "$ROOT/.demo-bot.log" | head -1 || true

STAKE_BEFORE="$(jsonget "$(curl -fsS "$MARKET_URL/api/smbs/$SMB_ID")" stake_cents)"

revoke_ref "$REVOKED_REF"

ts "firing the delivery event — Wake 2 begins, the agent re-verifies everything itself"
PAYLOAD_B64="$(printf '{"engagement_id": %s}' "$ENGAGEMENT_ID" | base64)"
curl -fsS -X POST "$AGENT_URL/events/delivery" \
  -H 'Content-Type: application/json' \
  -d "{\"message\":{\"data\":\"$PAYLOAD_B64\"}}" >/dev/null

FINAL="$(poll_mission "$MISSION_ID" DISPUTED "$TIMEOUT")" || true
TRACE="$(curl -fsS "$AGENT_URL/missions/$MISSION_ID")"
VERDICT="$(jsonget "$TRACE" wakes.-1.policy.verdict)"
FAILED="$(jsonget "$TRACE" wakes.-1.policy.failed_predicates)"
REASON="$(jsonget "$(curl -fsS "$MARKET_URL/api/engagements/$ENGAGEMENT_ID")" dispute_reason)"

ts "arbiter resolves the dispute: refund (slashes 20% of the price from the stake)"
curl -fsS -X POST "$MARKET_URL/api/engagements/$ENGAGEMENT_ID/resolve" \
  -H 'Content-Type: application/json' -d '{"ruling":"refund"}' >/dev/null
STAKE_AFTER="$(jsonget "$(curl -fsS "$MARKET_URL/api/smbs/$SMB_ID")" stake_cents)"

banner "RESULT"
ts "final mission status : $FINAL"
ts "policy gate verdict  : $VERDICT (failed predicates: $FAILED)"
ts "dispute reason       : $REASON"
ts "SLASHING EVIDENCE    : provider stake $STAKE_BEFORE -> $STAKE_AFTER cents"
ts "trace page           : $AGENT_URL/?mission=$MISSION_ID"

if [ "$FINAL" != "DISPUTED" ]; then
  ts "FAIL: expected DISPUTED, got $FINAL"
  exit 1
fi
if [ -z "$STAKE_AFTER" ] || [ -z "$STAKE_BEFORE" ] || [ "$STAKE_AFTER" -ge "$STAKE_BEFORE" ]; then
  ts "FAIL: stake was not slashed ($STAKE_BEFORE -> $STAKE_AFTER)"
  exit 1
fi
ts "OK: revoked reference caught by the agent's own re-verification; stake slashed"
if [ "${KEEP_UP:-0}" = "1" ]; then
  ts "KEEP_UP=1: marketplace and agent left running — open the trace page above. Stop with: make stop"
fi
