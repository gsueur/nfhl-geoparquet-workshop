"""CLI: `nfhl <stage> [--state MA] [--county Barnstable]`.

Every stage reads its worklist from import_log, processes, and writes back.
Without --state, --county or --all, a live state defaults to its live counties
(config/workshop.yaml). --force reruns rows regardless of their status.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

import typer
from rich import print
from rich.table import Table

from . import catalog as cat
from . import config, control, normalize, stages

app = typer.Typer(no_args_is_help=True, add_completion=False)

ALL = typer.Option(False, "--all", help="Every county of the state, not only the live ones")
FORCE = typer.Option(False, "--force", help="Rerun regardless of import_log status")
JOBS = typer.Option(1, "--jobs", "-j", help="Counties processed in parallel (processes)")
ALL_STATES = typer.Option(False, "--all-states", help="Every county of every state in import_log")


def _scope(
    state: str | None,
    county: list[str] | None,
    all_counties: bool,
    all_states: bool = False,
) -> tuple[str | None, list[str] | None]:
    """Which counties a stage touches.

    - `--all-states`: every county of every state the catalog registered
    - `--county X` (repeatable): exactly those
    - `--all`: every county of the state (needs `--state`)
    - a state with a default county list (config/workshop.yaml): that list
    - anything else: refuse. Attendees pick their own state, but nobody
      downloads Texas by accident. `nfhl counties --state XX` lists what there is.
    """
    if all_states:
        return None, None
    if county:
        return state, list(county)
    if all_counties and state:
        return state, None
    ws = config.load("workshop")["states"]
    if state and ws.get(state, {}).get("role") == "live":
        return state, ws[state].get("live_counties")
    if all_counties:
        print("[yellow]--all needs --state XX. The whole country is --all-states.[/yellow]")
        raise typer.Exit(1)
    where = f"--state {state}" if state else "no --state"
    print(
        f"[yellow]{where}: no default county list. Pick counties with --county (repeatable), "
        f"--all for the whole state, or --all-states. `nfhl counties --state {state or 'XX'}` lists them.[/yellow]"
    )
    raise typer.Exit(1)


def _run_stage(
    stage: str,
    state,
    county,
    all_counties=False,
    force=False,
    jobs=1,
    all_states=False,
    loaded_only=False,
    **kw,
):
    """Read the worklist, process each county, write its result back.

    The control database is opened only for the read and for each write, never
    for the whole run: DuckDB gives one process the file at a time, and
    `nfhl status` in a second terminal must work while a stage runs.
    With --jobs N the counties run in N processes (nfhl.worker); results come
    back to this process, which is the only one writing the control database.
    """
    from . import worker

    state, counties = _scope(state, county, all_counties, all_states)
    with control.control_db() as con:
        rows = control.to_process(con, stage, state, None, force=force, loaded_only=loaded_only)
    if counties:
        rows = [r for r in rows if r[1] in counties]
        missing = sorted(set(counties) - {r[1] for r in rows})
        if missing and not force:
            print(
                f"  [yellow]already past this stage, nothing to do: {', '.join(missing)} "
                f"(--force redoes them)[/yellow]"
            )
    jobs = max(1, min(jobs, len(rows) or 1))
    print(
        f"[bold]{stage}[/bold]: {len(rows)} counties to process"
        + (f", {jobs} jobs" if jobs > 1 else "")
    )

    def done(st: str, cty: str, t0: float, res: dict | None, err: Exception | None) -> None:
        ms = int((time.perf_counter() - t0) * 1000)
        if err is None:
            ms = res.pop("elapsed_ms", ms)  # the county's own time, not its wait in the queue
            with control.control_db() as con:
                control.mark(con, st, cty, **res)
            print(f"  [green]ok[/green] {st} {cty} -> {res.get('status')} ({ms} ms)")
        else:
            # worker.StageError already carries "ExceptionType: message"
            msg = str(err) if isinstance(err, worker.StageError) else f"{type(err).__name__}: {err}"
            with control.control_db() as con:
                control.fail(con, st, cty, msg)
            print(f"  [red]failed[/red] {st} {cty}: {msg}")

    if jobs == 1:
        for st, cty, fdate, url in rows:
            t0 = time.perf_counter()
            try:
                res = worker.run(stage, st, cty, url, fdate, dict(kw))
            except Exception as e:  # noqa: BLE001
                done(st, cty, t0, None, e)
            else:
                done(st, cty, t0, res, None)
        return

    from concurrent.futures import ProcessPoolExecutor, as_completed

    kw["_threads"] = max(1, (os.cpu_count() or 2) // jobs)
    t_all = time.perf_counter()
    with ProcessPoolExecutor(jobs) as pool:
        futures = {
            pool.submit(worker.run, stage, st, cty, url, fdate, dict(kw)): (
                st,
                cty,
                time.perf_counter(),
            )
            for st, cty, fdate, url in rows
        }
        for fut in as_completed(futures):
            st, cty, t0 = futures[fut]
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001
                done(st, cty, t0, None, e)
            else:
                done(st, cty, t0, res, None)
    print(f"  {len(rows)} counties in {time.perf_counter() - t_all:.1f} s wall, {jobs} jobs")


@app.command()
def check():
    """Verify DuckDB, its extensions and the Python deps, one row per component.

    Every component is tried on its own, so a failure shows as a red row with
    the reason instead of a traceback, and the exit code is 1.
    """
    import duckdb

    t0 = time.perf_counter()
    t = Table("component", "version")
    failed: list[str] = []

    def row(name: str, fn):
        try:
            t.add_row(name, str(fn()))
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            t.add_row(name, f"[red]failed: {type(e).__name__}: {str(e).splitlines()[0][:80]}[/red]")

    con = duckdb.connect()
    row("duckdb", lambda: duckdb.__version__)
    for ext, source in (("spatial", ""), ("httpfs", ""), ("h3", " FROM community")):

        def load(ext=ext, source=source):
            con.execute(f"INSTALL {ext}{source}; LOAD {ext};")
            return con.execute(
                "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = ?", [ext]
            ).fetchone()[0]

        row(f"  {ext}", load)

    def pyogrio_version():
        import pyogrio

        return f"{pyogrio.__version__} / {pyogrio.__gdal_version_string__}"

    def shapely_version():
        import shapely

        return shapely.__version__

    row("pyogrio / GDAL", pyogrio_version)
    row("shapely", shapely_version)

    def gpio_path():
        exe = shutil.which("gpio")
        if not exe:
            raise FileNotFoundError("not on PATH, run uv sync")
        return os.path.relpath(exe)

    row("gpio (geoparquet-io)", gpio_path)
    print(t)
    if failed:
        print(f"[red]failed[/red]: {', '.join(failed)} ({time.perf_counter() - t0:.1f} s)")
        raise typer.Exit(1)
    print(f"[green]ok[/green] in {time.perf_counter() - t0:.1f} s")


@app.command()
def catalog(state: str | None = None, county: str | None = None):
    """Scrape the FEMA portal and register (state, county, date, url) in import_log."""
    _catalog(state, county)
    if state:
        # Read back from the control database, not from the page: the list you pick from
        _print_counties(state)


def _catalog(state: str | None, county: str | None = None, update_only: bool = False) -> None:
    cfg = config.load("workshop")["fema"]
    ds = cat.list_datasets(state, county, county_wide_only=False)
    wide = [d for d in ds if d["county_wide"]]
    keep = wide if cfg.get("county_wide_only", True) else ds
    with control.control_db() as con:
        n = control.register_catalog(con, keep, update_only=update_only)
    mb = sum(d["zip_size_mb"] or 0 for d in keep)
    if update_only:
        print(
            f"[green]{n['updated']} cataloged counties checked[/green] against {len(keep)} on the portal, "
            f"{n['newer']} with a newer FEMA delivery; nothing added"
        )
    else:
        print(
            f"[green]{len(keep)} datasets registered[/green] ({mb:.0f} MB zipped, {n['inserted']} new rows), "
            f"{len(ds) - len(wide)} community-level DFIRMs {'skipped' if keep is wide else 'included'}"
        )


@app.command()
def download(
    state: str | None = None,
    county: list[str] | None = None,
    all_counties: bool = ALL,
    force: bool = FORCE,
    all_states: bool = ALL_STATES,
):
    """Optional pre-fetch: the FEMA ZIPs of the counties in scope into data/downloads.

    Ingest does the same fetch on its own when a ZIP is missing; this command
    exists to fill the cache the day before, on a good connection. The worklist
    and the URLs come from the control database (counties at status new, every
    county with --force); a ZIP is fetched only when its dated name is not there.
    """
    from .ingest import cache_path, fetch_zip

    state, counties = _scope(state, county, all_counties, all_states)
    with control.control_db() as con:
        rows = control.to_process(con, "bronze", state, None, force=force)
    if counties:
        rows = [r for r in rows if r[1] in counties]
    print(f"[bold]download[/bold]: {len(rows)} counties")
    for st, cty, _fdate, url in rows:
        t0 = time.perf_counter()
        path, source = fetch_zip(url)
        ms = int((time.perf_counter() - t0) * 1000)
        mb = os.path.getsize(path) / 1e6
        print(
            f"  [green]{source}[/green] {st} {cty} {os.path.basename(cache_path(url))} {mb:.0f} MB ({ms} ms)"
        )


@app.command()
def ingest(
    state: str | None = None,
    county: list[str] | None = None,
    all_counties: bool = ALL,
    force: bool = FORCE,
    jobs: int = JOBS,
    all_states: bool = ALL_STATES,
):
    """FEMA ZIP (data/downloads first, fetched otherwise) -> memory -> Arrow -> bronze GeoParquet."""
    _run_stage("bronze", state, county, all_counties, force, jobs, all_states)


@app.command(name="normalize")
def normalize_(
    state: str | None = None,
    county: list[str] | None = None,
    all_counties: bool = ALL,
    force: bool = FORCE,
    show_sql: bool = False,
    jobs: int = JOBS,
    all_states: bool = ALL_STATES,
):
    """bronze -> silver via config/mapping.yaml."""
    if show_sql:
        print(normalize.show_sql())
        return
    _run_stage("silver", state, county, all_counties, force, jobs, all_states)


@app.command()
def subdivide(
    state: str | None = None,
    county: list[str] | None = None,
    all_counties: bool = ALL,
    force: bool = FORCE,
    max_vertices: int = 100,
    jobs: int = JOBS,
    all_states: bool = ALL_STATES,
    engine: str = typer.Option("duckdb", help="duckdb (ST_Subdivide) or python (bbox bisection)"),
):
    """silver -> silver_subdivided: DuckDB ST_Subdivide, cap N vertices per piece."""
    _run_stage(
        "subdivided",
        state,
        county,
        all_counties,
        force,
        jobs,
        all_states,
        max_vertices=max_vertices,
        engine=engine,
    )


@app.command()
def load():
    """Land silver_subdivided in a .duckdb file with an RTree index and validate."""
    print(stages.load_duckdb())


@app.command(name="gold-analytic")
def gold_analytic():
    """silver_subdivided -> gold_analytic (one file per H3 r5 cell, Hilbert order, bbox column)."""
    print(stages.gold_analytic())


@app.command()
def bench(stage: str = "all", state: str = "MA"):
    """Run the benchmarks (subdivide | rtree | gold | map | all) and record them."""
    from . import bench as b

    fns = {
        "subdivide": lambda: b.bench_subdivide(state),
        "rtree": b.bench_rtree,
        "gold": b.bench_gold,
        "map": b.bench_map,
    }
    for name, fn in fns.items():
        if stage not in ("all", name):
            continue
        print(f"[bold]bench {name}[/bold]")
        try:
            rows = fn()
        except FileNotFoundError as e:
            print(f"  [yellow]skipped[/yellow]: {e}")
            continue
        # The control database is opened only to record, so it stays readable
        # from another terminal during a long run.
        with control.control_db() as con:
            for row in rows:
                control.record_benchmark(con, **row)
                print("  ", {k: v for k, v in row.items() if v is not None})


@app.command()
def benchmarks():
    """Print the benchmarks table, the wrap-up slide."""
    with control.control_db() as con:
        cols = [
            "run_at",
            "stage",
            "name",
            "layout",
            "variant",
            "scope",
            "rows",
            "files",
            "bytes",
            "elapsed_ms",
            "notes",
        ]
        t = Table(*cols)
        for r in con.execute(
            f"SELECT {', '.join(cols)} FROM benchmarks ORDER BY run_at"
        ).fetchall():
            t.add_row(
                *["" if x is None else str(x)[:19] if i == 0 else str(x) for i, x in enumerate(r)]
            )
        print(t)


@app.command()
def verify(path: str | None = None):
    """Side check with geoparquet-io (gpio): metadata and best practices of one output file."""
    path = path or config.path("silver", "state=MA", "county=Barnstable.parquet")
    gpio = shutil.which("gpio")
    if not gpio:
        raise typer.Exit("gpio not found: uv sync")
    for args in (["inspect", "meta", path, "--geo"], ["check", "all", path]):
        print(f"[bold]$ gpio {' '.join(args)}[/bold]")
        subprocess.run([gpio, *args], check=False)


@app.command()
def update(state: str | None = None, gold: bool = False, jobs: int = JOBS):
    """Production mode: refresh what was loaded before, only where FEMA has a newer delivery.

    The control database is the memory. The catalog is fetched (one page for
    the whole country) and only the counties already in the control database
    are refreshed: nothing is inserted, a county goes back to `new` only when
    FEMA's date is newer than the one stored, and only counties that went
    through bronze at least once are processed again. Cataloged but never
    loaded counties are reported, never started: `nfhl ingest --state XX --all`.
    --gold rebuilds the derived layouts afterwards (full rebuilds).
    """
    _catalog(state, update_only=True)
    with control.control_db() as con:
        n = control.reconcile(con)
        if n:
            print(f"[yellow]{n} counties downgraded, their files were missing[/yellow]")
        skipped = control.never_loaded(con, state)
    if skipped:
        print(
            "  registered but never loaded, left alone: "
            + ", ".join(f"{st} {k}" for st, k in skipped)
            + " (`nfhl ingest --state XX --all` starts them)"
        )
    everywhere = state is None
    for stage in ("bronze", "silver", "subdivided"):
        _run_stage(
            stage,
            state,
            None,
            not everywhere,
            False,
            jobs,
            everywhere,
            loaded_only=True,
            **({"max_vertices": 100, "engine": "duckdb"} if stage == "subdivided" else {}),
        )
    if gold:
        load()
        gold_analytic()
    status()


@app.command()
def prune(state: str | None = None):
    """Drop catalog rows that never went through bronze: what was listed, never loaded."""
    with control.control_db() as con:
        q = "DELETE FROM import_log WHERE bronze_at IS NULL"
        params: list = []
        if state:
            q += " AND state = ?"
            params.append(state)
        n = con.execute(q + " RETURNING state").fetchall()
    by_state: dict[str, int] = {}
    for (st,) in n:
        by_state[st] = by_state.get(st, 0) + 1
    print(
        f"[green]{len(n)} never-loaded rows removed[/green]"
        + (": " + ", ".join(f"{k} {v}" for k, v in sorted(by_state.items())) if n else "")
    )


@app.command()
def counties(state: str | None = None):
    """List the counties import_log knows for a state: FEMA date, ZIP size and current status."""
    _print_counties(state)


def _print_counties(state: str | None) -> None:
    """The control database read back: one row per county, sorted by ZIP size."""
    with control.control_db() as con:
        q = "SELECT state, county, fema_update_date, zip_size_mb, status FROM import_log"
        params: list = []
        if state:
            q += " WHERE state = ?"
            params.append(state)
        rows = con.execute(q + " ORDER BY state, zip_size_mb", params).fetchall()
    if not rows:
        print(
            f"nothing registered{' for ' + state if state else ''}: run `nfhl catalog --state XX` first"
        )
        raise typer.Exit(1)
    t = Table("state", "county", "fema date", "zip MB", "status")
    for r in rows:
        t.add_row(
            *["" if x is None else (f"{x:.0f}" if isinstance(x, float) else str(x)) for x in r]
        )
    print(t)
    mb = sum(r[3] or 0 for r in rows)
    print(
        f"{len(rows)} counties, {mb:.0f} MB zipped. Pick 2 or 3 under 50 MB: "
        "`--state XX --county A --county B` on every later command"
    )


@app.command()
def status():
    """import_log summarised per state and status, plus the last errors."""
    with control.control_db() as con:
        t = Table(
            "state",
            "status",
            "counties",
            "zip MB",
            "bronze",
            "silver",
            "subdivided",
            "ratio",
            "errors",
        )
        for r in control.status_table(con):
            t.add_row(*["" if x is None else str(x) for x in r])
        print(t)
        errors = con.execute(
            "SELECT state, county, status, error_at, error FROM import_log WHERE error IS NOT NULL ORDER BY error_at"
        ).fetchall()
        if errors:
            e = Table("state", "county", "status", "at", "error")
            for r in errors:
                e.add_row(*[str(x)[:80] for x in r])
            print(e)


if __name__ == "__main__":
    app()
