#!/usr/bin/env bash
# Shared helpers for the ProofPay demo scripts. Meant to be sourced, not run.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACTA_DIR="${PACTA_DIR:-$ROOT/../Pacta.Protocol}"
MARKET_PORT="${MARKET_PORT:-3220}"
AGENT_PORT="${AGENT_PORT:-8080}"
# URLs default to the local ports, but either can be overridden by env to point at
# already-deployed Cloud Run services (cloud demo mode).
MARKET_URL="${MARKET_URL:-http://localhost:$MARKET_PORT}"
AGENT_URL="${AGENT_URL:-http://localhost:$AGENT_PORT}"

# Cloud demo mode: set CLOUD=1 with AGENT_URL, MARKET_URL (and, for the fraud demo,
# REGISTRY_DRIFT_URL + REVOKE_TOKEN) pointing at the deployed services. In cloud mode
# the demo scripts do not start or reset local processes.
CLOUD="${CLOUD:-0}"
REGISTRY_DRIFT_URL="${REGISTRY_DRIFT_URL:-}"
REVOKE_TOKEN="${REVOKE_TOKEN:-}"

ts() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"; }

banner() {
  printf '\n============================================================\n'
  printf '%s\n' "$*"
  printf '============================================================\n'
}

# jsonget '<json>' 'dotted.path' - walks dicts by key and lists by index
# (negative indices work: wakes.-1 is the last wake).
jsonget() {
  printf '%s' "$1" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for k in sys.argv[1].split("."):
    if isinstance(d, list):
        d = d[int(k)]
    elif isinstance(d, dict):
        d = d.get(k)
    else:
        d = None
        break
print("" if d is None else d)
' "$2"
}

wait_url() { # wait_url <url> <timeout-seconds>
  local url="$1" limit="${2:-30}" waited=0
  until curl -fsS "$url" >/dev/null 2>&1; do
    waited=$((waited + 1))
    if [ "$waited" -ge "$limit" ]; then
      ts "TIMEOUT waiting for $url"
      return 1
    fi
    sleep 1
  done
}

kill_port() { lsof -ti tcp:"$1" 2>/dev/null | xargs kill 2>/dev/null || true; }

start_agent() {
  kill_port "$AGENT_PORT"
  (
    cd "$ROOT/agent"
    JUDGE_STUB="${JUDGE_STUB:-1}" \
    MARKETPLACE_URL="$MARKET_URL" \
    DELIVERY_DEADLINE_SECONDS="${DELIVERY_DEADLINE_SECONDS:-86400}" \
    DEMO_TOKEN="${DEMO_TOKEN:-}" \
      .venv/bin/python -m uvicorn proofpay.main:create_app --factory \
      --host 127.0.0.1 --port "$AGENT_PORT" \
      >"$ROOT/.demo-agent.log" 2>&1 &
    echo $! >"$ROOT/.demo-agent.pid"
  )
  wait_url "$AGENT_URL/" 30
  ts "agent listening on $AGENT_URL (stub judge: JUDGE_STUB=${JUDGE_STUB:-1})"
}

start_bot() { # start_bot <mode> <delay-seconds>
  (
    cd "$ROOT"
    MODE="$1" DELAY_SECONDS="$2" \
    MARKETPLACE_URL="$MARKET_URL" \
    DELIVERY_WEBHOOK_URL="$AGENT_URL/events/delivery" \
    POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-2}" \
      node provider-bot/src/bot.js >"$ROOT/.demo-bot.log" 2>&1 &
    echo $! >"$ROOT/.demo-bot.pid"
  )
  ts "provider-bot running (MODE=$1, DELAY_SECONDS=$2)"
}

cleanup_demo() {
  for pidfile in "$ROOT/.demo-bot.pid" "$ROOT/.demo-agent.pid"; do
    if [ -f "$pidfile" ]; then
      kill "$(cat "$pidfile")" 2>/dev/null || true
      rm -f "$pidfile"
    fi
  done
  kill_port "$AGENT_PORT"
  kill_port "$MARKET_PORT"
}

create_mission() { # create_mission <goal> <budget-usd> - prints the trace JSON
  curl -fsS -X POST "$AGENT_URL/missions" \
    -H 'Content-Type: application/json' \
    ${DEMO_TOKEN:+-H "X-Demo-Token: $DEMO_TOKEN"} \
    -d "$(printf '{"goal": %s, "budget_usd": %s}' "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" "$2")"
}

# revoke_ref <ref> - simulate registry drift (annul a credential after submission).
# Cloud: POST to the registry-drift service (token-guarded). Local: delete the row from
# Pacta's runtime SQLite (runtime data only - no Pacta code touched, DECISIONS.md).
revoke_ref() {
  local ref="$1"
  if [ -n "$REGISTRY_DRIFT_URL" ]; then
    curl -fsS -X POST "$REGISTRY_DRIFT_URL/revoke/$ref" \
      ${REVOKE_TOKEN:+-H "X-Revoke-Token: $REVOKE_TOKEN"} >/dev/null
    ts "registry drift: revoked $ref via registry-drift service ($REGISTRY_DRIFT_URL)"
  else
    sqlite3 "$PACTA_DIR/data/pacta.db" "DELETE FROM registry_records WHERE ref='$ref';"
    ts "registry drift: revoked $ref via local runtime SQLite (no Pacta code touched)"
  fi
}

poll_mission() { # poll_mission <id> <wanted-status> <timeout-s> - prints final status
  local id="$1" want="$2" limit="$3" waited=0 status=""
  while [ "$waited" -lt "$limit" ]; do
    status="$(jsonget "$(curl -fsS "$AGENT_URL/missions/$id")" mission.status)"
    if [ "$status" = "$want" ]; then
      echo "$status"
      return 0
    fi
    case "$status" in
      RELEASED|DISPUTED) echo "$status"; return 1 ;;
    esac
    sleep 3
    waited=$((waited + 3))
  done
  echo "${status:-TIMEOUT}"
  return 1
}
