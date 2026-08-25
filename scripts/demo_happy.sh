#!/usr/bin/env bash
# make demo-happy - the honest run, end to end (SPEC §6).
#
# What you watch happen:
#   1. The marketplace is reset to its seed state.
#   2. Wake 1: the agent searches offers, picks a provider, signs the contract and
#      funds escrow - then the request ends. The agent is asleep.
#   3. The provider "works" for DELAY_SECONDS, completes every step with a real
#      registry reference, submits, and fires the delivery event.
#   4. Wake 2: the event wakes the agent, it re-verifies every reference against
#      the registry itself, the policy gate says RELEASE, payment moves, the
#      provider gets rated.
#
# Env knobs: DELAY_SECONDS (default 90), TIMEOUT, AGENT_PORT, MARKET_PORT.
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/_lib.sh"

DELAY_SECONDS="${DELAY_SECONDS:-90}"
TIMEOUT="${TIMEOUT:-$((DELAY_SECONDS + 180))}"
GOAL="Form a company in Costa Rica to purchase land in Guanacaste and operate a hotel. Budget \$6,000."

if [ "${KEEP_UP:-0}" != "1" ]; then trap cleanup_demo EXIT; fi
banner "ProofPay demo - happy path (honest provider, ${DELAY_SECONDS}s work delay)"

if [ "$CLOUD" = "1" ]; then
  ts "cloud mode: AGENT_URL=$AGENT_URL  MARKET_URL=$MARKET_URL (not starting local services)"
  wait_url "$AGENT_URL/" 30
  # The provider-bot runs locally against the cloud marketplace + agent (it needs no
  # cloud residency); delivery is an HTTP POST to the cloud agent.
  start_bot honest "$DELAY_SECONDS"
else
  "$ROOT/scripts/seed.sh"
  start_agent
  start_bot honest "$DELAY_SECONDS"
fi

ts "Wake 1: creating the mission - the agent hires, signs and funds, then goes to sleep"
TRACE="$(create_mission "$GOAL" 6000)"
MISSION_ID="$(jsonget "$TRACE" mission.mission_id)"
ENGAGEMENT_ID="$(jsonget "$TRACE" mission.engagement_id)"
PROVIDER="$(jsonget "$TRACE" mission.provider_name)"
ts "mission $MISSION_ID hired '$PROVIDER' (engagement #$ENGAGEMENT_ID) - status: $(jsonget "$TRACE" mission.status)"
ts "the agent process is idle now; the mission lives only in the persisted trace"

ts "waiting for the provider to deliver and the delivery event to wake the agent..."
FINAL="$(poll_mission "$MISSION_ID" RELEASED "$TIMEOUT")" || true

TRACE="$(curl -fsS "$AGENT_URL/missions/$MISSION_ID")"
W1_END="$(jsonget "$TRACE" wakes.0.finished_at)"
W2_START="$(jsonget "$TRACE" wakes.-1.started_at)"
VERDICT="$(jsonget "$TRACE" wakes.-1.policy.verdict)"

banner "RESULT"
ts "final mission status : $FINAL"
ts "policy gate verdict  : $VERDICT"
ts "Wake 1 ended         : $W1_END"
ts "Wake 2 started       : $W2_START   <- the gap in between is the agent asleep"
ts "trace page           : $AGENT_URL/?mission=$MISSION_ID"

if [ "$FINAL" != "RELEASED" ]; then
  ts "FAIL: expected RELEASED, got $FINAL"
  exit 1
fi
ts "OK: honest delivery ended in RELEASED"
if [ "${KEEP_UP:-0}" = "1" ]; then
  ts "KEEP_UP=1: marketplace and agent left running - open the trace page above. Stop with: make stop"
fi
