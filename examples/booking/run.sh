#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
EX="$REPO/examples/booking"
APP="$REPO/data/booking"
companion() { PYTHONPATH="$REPO" python3 -m companion "$@"; }

rm -rf "$APP" && mkdir -p "$APP" && cd "$APP"
cp "$EX/app/booking.py" booking.py

companion init booking.py
companion synthesize
companion tell "no two bookings in the same room may overlap, and if a request is refused nothing should change"
companion check

# adds a `move` without the overlap check.
companion tell "let people move a booking to another time"
cp "$EX/turn2/booking.py" booking.py
companion check || true
