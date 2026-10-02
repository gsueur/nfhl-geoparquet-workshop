#!/usr/bin/env bash
# Remove one state from the local data tree and the control database.
# Derived outputs (duckdb, gold_*) are NOT touched: rebuild them with load / gold-analytic.
set -euo pipefail
cd "$(dirname "$0")/.."
st="${1:?usage: drop_state.sh XX}"
for stage in bronze silver silver_subdivided; do
  rm -rf "data/$stage/state=$st"
done
uv run python - "$st" <<'PY'
import sys, duckdb
st = sys.argv[1]
con = duckdb.connect("data/control/fema_control.duckdb")
n = con.execute("SELECT count(*) FROM import_log WHERE state = ?", [st]).fetchone()[0]
con.execute("DELETE FROM import_log WHERE state = ?", [st])
print(f"{st}: {n} import_log rows removed, files under data/{{bronze,silver,silver_subdivided}}/state={st} deleted")
PY
