#!/usr/bin/env bash
# Precompute the three-state checkpoints. Run on a machine with bandwidth, not at the venue.
# --all bypasses the live county list of config/workshop.yaml (MA would otherwise stop at 3).
set -euo pipefail
cd "$(dirname "$0")/.."
for st in MA LA UT; do
  uv run nfhl catalog --state "$st"
  uv run nfhl download --state "$st" --all
  uv run nfhl ingest --state "$st" --all --jobs 4
  uv run nfhl normalize --state "$st" --all --jobs 4
  uv run nfhl subdivide --state "$st" --all --max-vertices 100 --jobs 4
done
uv run nfhl status
uv run nfhl load
uv run nfhl gold-analytic
uv run nfhl bench
uv run nfhl benchmarks
