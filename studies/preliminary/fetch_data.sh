#!/usr/bin/env bash
# Downloads the EvoCode-Bench task definitions and published per-round results into data/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$ROOT/data/evocodebench"
mkdir -p "$DEST"
cd "$DEST"

# Published v2 results (per model, per round, failing requirements).
if [ ! -d results-repo ]; then
  git clone -q --depth 1 https://github.com/UniPat-AI/EvoCodeBench.git results-repo
fi

# Task definitions: instructions, cumulative tests and change types for each round.
if [ ! -d evocodebench_wotraj ]; then
  curl -sL -o wotraj.tar.zst \
    https://huggingface.co/datasets/UnipatAI/EvoCodeBench/resolve/main/archives/evocodebench_wotraj.tar.zst
  tar --zstd -xf wotraj.tar.zst
  rm wotraj.tar.zst
fi
echo "EvoCode-Bench data in $DEST"
