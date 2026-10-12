#!/usr/bin/env bash
# Downloads the SlopCodeBench problems (specs, tests, reference solutions) into data/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$ROOT/data"
if [ ! -d "$ROOT/data/scb-problems" ]; then
  git clone -q --depth 1 https://github.com/gabeorlanski/scb-problems.git "$ROOT/data/scb-problems"
fi
echo "SlopCodeBench problems in $ROOT/data/scb-problems"
