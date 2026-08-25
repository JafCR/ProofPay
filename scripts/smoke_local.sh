#!/usr/bin/env bash
# CI-style local verification, no API key needed (SPEC §7): runs the whole loop
# twice against a real local marketplace with the deterministic stub judge —
# honest must end RELEASED, fraud must end DISPUTED with the stake slashed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

DELAY_SECONDS="${DELAY_SECONDS:-5}" TIMEOUT=150 "$HERE/demo_happy.sh"
DELAY_SECONDS="${DELAY_SECONDS:-5}" TIMEOUT=150 "$HERE/demo_fraud.sh"

echo
echo "SMOKE OK — happy path RELEASED, fraud path DISPUTED with stake slashed."
