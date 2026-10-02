set dotenv-load

# Verify the environment in under 10 seconds (DuckDB + spatial + httpfs + h3, Python deps, gpio)
check:
    uv run nfhl check

test:
    uv run pytest -q

lint:
    uv run ruff check src tests && uv run ruff format --check src tests

# Stage-by-stage, defaults to the live workshop counties (config/workshop.yaml)
catalog state="MA":
    uv run nfhl catalog --state {{state}}

ingest state="MA" county="":
    uv run nfhl ingest --state {{state}} {{ if county != "" { "--county " + county } else { "" } }}

normalize state="MA":
    uv run nfhl normalize --state {{state}}

subdivide state="MA" n="100":
    uv run nfhl subdivide --state {{state}} --max-vertices {{n}}

load:
    uv run nfhl load

gold:
    uv run nfhl gold-analytic

bench stage="all":
    uv run nfhl bench --stage {{stage}}

benchmarks:
    uv run nfhl benchmarks

status:
    uv run nfhl status

# geoparquet-io side check of one output file
verify path="":
    uv run nfhl verify {{ if path != "" { "--path " + path } else { "" } }}

# Serve the repo locally with CORS + Range: http://localhost:8000/web/index.html?base=http://localhost:8000/data
serve port="8000":
    uv run python scripts/serve_local.py {{port}}

# Full run for the live MA counties
all: catalog ingest normalize subdivide load gold bench status
