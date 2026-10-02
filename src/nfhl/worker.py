"""One county, one stage, in a child process: the unit of work behind `--jobs`.

Counties are independent (one input file, one output file), so a stage runs
them in a process pool. Only the parent touches the control database.
"""

from __future__ import annotations

import os
import time


class StageError(RuntimeError):
    """A stage failure as text: exceptions from httpx, tenacity or DuckDB carry
    objects (locks, requests, connections) that cannot cross a process boundary."""


def run(stage: str, state: str, county: str, url: str, fema_update_date, kw: dict) -> dict:
    t0 = time.perf_counter()
    try:
        res = _run(stage, state, county, url, fema_update_date, kw)
    except Exception as e:  # noqa: BLE001
        raise StageError(
            f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
        ) from None
    # The county's own time, measured here: with --jobs the parent only sees queue + work.
    res["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    return res


def _run(stage: str, state: str, county: str, url: str, fema_update_date, kw: dict) -> dict:
    # Share the cores between the workers instead of oversubscribing them.
    os.environ.setdefault("NFHL_THREADS", str(kw.pop("_threads", 0) or 0))
    if stage == "bronze":
        from .ingest import ingest_county

        return ingest_county(state, county, url, fema_update_date)
    if stage == "silver":
        from .normalize import normalize_county

        return normalize_county(state, county)
    if stage == "subdivided":
        from .stages import subdivide_county

        return subdivide_county(state, county, kw["max_vertices"], kw.get("engine", "duckdb"))
    raise ValueError(f"no worker for stage {stage!r}")
