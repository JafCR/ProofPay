#!/usr/bin/env bash
# seed.sh — reset the Pacta marketplace to its seed state (docs/CONTRACTS.md §7).
#
# Pacta has no reset endpoint: it re-seeds automatically on an empty DB. So resetting means
# stopping the marketplace, deleting ONLY the SQLite database files, and starting it again.
# `data/` is gitignored runtime state, so this never touches tracked files — and this script
# deletes ONLY the three exact pacta.db files, never anything else in data/ (the custodial
# `platform-key` and any other file are left untouched).
#
# Works whether or not the marketplace is already running: it frees the port, deletes the DB,
# relaunches `PORT=<port> npm run start:pacta` in the background, and waits for health.
#
# Env overrides:
#   PACTA_DIR          path to the Pacta.Protocol clone (default: ../Pacta.Protocol next to ProofPay)
#   MARKETPLACE_PORT   port to run on (default: 3220)
#   SEED_NO_RESTART=1  only stop + delete the DB; do not relaunch (prints instructions instead)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROOFPAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PACTA_DIR="${PACTA_DIR:-$(cd "$PROOFPAY_DIR/.." && pwd)/Pacta.Protocol}"
PORT="${MARKETPLACE_PORT:-3220}"

log() { echo "$(date +%H:%M:%S) [seed] $*"; }

if [[ ! -d "$PACTA_DIR" ]]; then
  log "ERROR: Pacta clone not found at: $PACTA_DIR"
  log "Set PACTA_DIR to the Pacta.Protocol clone path and retry."
  exit 1
fi

DATA_DIR="$PACTA_DIR/data"

# 1. Stop anything listening on the marketplace port.
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
  if [[ -n "$PIDS" ]]; then
    log "stopping process(es) on port $PORT: $PIDS"
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      lsof -ti "tcp:$PORT" >/dev/null 2>&1 || break
      sleep 0.3
    done
    PIDS="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
    if [[ -n "$PIDS" ]]; then
      log "force-stopping: $PIDS"
      # shellcheck disable=SC2086
      kill -9 $PIDS 2>/dev/null || true
      sleep 0.5
    fi
  else
    log "nothing running on port $PORT"
  fi
else
  log "WARNING: lsof not available; cannot stop a running marketplace on port $PORT."
fi

# 2. Delete ONLY the exact SQLite files — never anything else in data/.
DELETED=0
for f in pacta.db pacta.db-shm pacta.db-wal; do
  target="$DATA_DIR/$f"
  if [[ -f "$target" ]]; then
    rm -f "$target"
    log "deleted $target"
    DELETED=1
  fi
done
[[ "$DELETED" -eq 0 ]] && log "no pacta.db files present (already reset)"

# 3. Relaunch (unless asked not to) and wait for health.
if [[ "${SEED_NO_RESTART:-0}" == "1" ]]; then
  log "SEED_NO_RESTART=1 — not relaunching."
  log "Start it yourself with:  (cd \"$PACTA_DIR\" && PORT=$PORT npm run start:pacta)"
  exit 0
fi

LOG_FILE="${TMPDIR:-/tmp}/pacta-marketplace-$PORT.log"
log "starting marketplace: PORT=$PORT npm run start:pacta  (logs → $LOG_FILE)"
( cd "$PACTA_DIR" && PORT="$PORT" npm run start:pacta >"$LOG_FILE" 2>&1 & )

log "waiting for health on http://localhost:$PORT/api/health ..."
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
    HEALTH="$(curl -fsS "http://localhost:$PORT/api/health" 2>/dev/null || true)"
    log "marketplace healthy: $HEALTH"
    log "seed reset complete."
    exit 0
  fi
  sleep 0.5
done

log "ERROR: marketplace did not become healthy within 30s. Last log lines:"
tail -n 20 "$LOG_FILE" 2>/dev/null || true
exit 1
