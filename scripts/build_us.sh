#!/usr/bin/env bash
# Whole-country build into another data root (an external disk), without touching data/.
# Usage: bash scripts/build_us.sh /Volumes/T9/DATA/nfhl
# Idempotent: a rerun only processes what is missing or failed (import_log).
set -uo pipefail
cd "$(dirname "$0")/.."
export NFHL_DATA_ROOT="${1:?data root}"
JOBS="${JOBS:-4}"
mkdir -p "$NFHL_DATA_ROOT"
uv run nfhl catalog
uv run nfhl ingest --all-states --jobs "$JOBS"
uv run nfhl normalize --all-states --jobs "$JOBS"
uv run nfhl subdivide --all-states --max-vertices 100 --jobs "$JOBS"
uv run nfhl status
