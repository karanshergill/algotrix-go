# Changelog

## 2026-03-09

### Directory Rename: `database/` → `db/`

**What changed:**
- `database/` → `db/`
- `database/connections/` → `db/conns/`
- `database/operations/` → `db/ops/`

**Why:**
Shorter, consistent naming. `db/conns/`, `db/ops/` is cleaner than `database/connections/`, `database/operations/`.

**Files updated:**
- `main.go` — import paths (`db/conns`, `db/ops`), package references (`conns.`, `ops.`), config path (`db/conns/db.yaml`)
- `db/conns/*.go` — package declaration `connections` → `conns`
- `db/ops/*.go` — package declaration `operations` → `ops`

**Structure after rename:**
```
db/
  conns/
    conn.go
    db.yaml
    postgres.go
    quest.go
  ops/
    ohlcv_1d.go
    ohlcv_1m.go
    ohlcv_5s.go
    scrips.go
```

**Verified:** `go build ./...` compiles clean.

### Go file renames in `db/conns/` and `db/ops/`

**What changed:**
- `db/conns/conn.go` → `db/conns/go_conn.go`
- `db/conns/postgres.go` → `db/conns/go_postgres.go`
- `db/conns/quest.go` → `db/conns/go_quest.go`
- `db/ops/ohlcv_5s.go` → `db/ops/write_ohlcv_5s.go`
- `db/ops/ohlcv_1m.go` → `db/ops/write_ohlcv_1m.go`
- `db/ops/ohlcv_1d.go` → `db/ops/write_ohlcv_1d.go`
- `db/ops/scrips.go` → `db/ops/write_scrips.go`

**Why:**
- `go_` prefix on connection files to distinguish from Python `py_conn.py` (coming next)
- `write_` prefix on ops files to clearly describe the action (all are write operations)
- Consistent naming: `fetch_*` for reads, `write_*` for writes

**Verified:** `go build ./...` compiles clean.

### Volume Profile plugin — full rewrite

**What changed:**
- Deleted monolithic `baselines/volume_profile.py`
- Created `baselines/volume_profile/` package with split modules:
  - `plugin.py` — VolumeProfilePlugin class
  - `buckets.py` — price bucket construction
  - `allocate.py` — range-overlap volume allocation with tick_size normalization
  - `poc.py` — Point of Control detection
  - `value_area.py` — Value Area (VAH/VAL) computation
  - `hvn_lvn.py` — percentile-based HVN/LVN detection
  - `output.py` — output row builder
- Created shared modules:
  - `baselines/shared/filters.py` — MAD-based outlier filtering
  - `db/fetch_ohlcv.py` — OHLCV data fetcher (to be rewritten with pandas + SQL)
  - `db/tick_size.py` — config-driven tick band lookup
- Updated `baselines/baseline_config.yaml`:
  - Added top-level `tick_bands` (NSE price-dependent, effective April 15, 2025)
  - Replaced `hvn_threshold`/`lvn_threshold` with `hvn_percentile`/`lvn_percentile`
  - Added `outlier_mad_k`
- Updated `baselines/baseline_runner.py` — discovers both .py files and package directories

**Why:**
- Old code assigned all volume to close price (delta function) — biased POC, distorted HVN/LVN
- New code uses range-overlap allocation proportional to candle high-low range
- Percentile-based HVN/LVN is robust to skewed distributions (old mean-based was fragile)
- MAD outlier filtering prevents bad prints from distorting profiles
- Split into independent functions for testability and reuse
- Full documentation at `docs/baselines/volume-profile.md`

**Structure:**
```
db/
  __init__.py
  fetch_ohlcv.py
  tick_size.py
  conns/
    db.yaml
    go_conn.go
    go_postgres.go
    go_quest.go
  ops/
    write_ohlcv_5s.go
    write_ohlcv_1m.go
    write_ohlcv_1d.go
    write_scrips.go

baselines/
  shared/
    filters.py
  volume_profile/
    plugin.py
    buckets.py
    allocate.py
    poc.py
    value_area.py
    hvn_lvn.py
    output.py
```

### Python DB layer — SQL connections replacing HTTP/ILP

**What changed:**
- Created `db/conns/py_conn.py` — SQLAlchemy engine factory for QuestDB (port 8812) and PostgreSQL (port 5432), reads from `db/conns/db.yaml`
- Rewrote `db/fetch_ohlcv.py` — pandas + SQLAlchemy via QuestDB Postgres wire protocol (was urllib HTTP API)
- Created `db/fetch_isins.py` — ISIN fetcher via SQL (replaces `utils/query.py` `get_isins` for new plugins)
- Created `db/write_baseline.py` — SQL-based writer via `pd.to_sql` (replaces ILP TCP writer)
- Updated `baselines/volume_profile/plugin.py` — uses new DB layer, zero HTTP/ILP dependencies

**Why:**
Proper SQL connections instead of HTTP API and raw TCP ILP. Single connection config source (`db/conns/db.yaml`). Pandas for efficient data handling.

### Go OHLCV writers — consolidated and dynamic

**What changed:**
- Deleted `db/ops/write_ohlcv_5s.go`, `write_ohlcv_1m.go`, `write_ohlcv_1d.go`
- Created single `db/ops/write_ohlcv.go` — `WriteOHLCV(ctx, sender, table, candles)` takes table name as parameter
- Updated `main.go` — all call sites pass table name explicitly

**Why:**
Three identical functions differing only by hardcoded table name → one function with dynamic table. No hardcoded table names in db layer.

### Polars migration — replacing pandas

**What changed:**
- `db/fetch_ohlcv.py` — Polars DataFrame for groupby and numpy conversion (was pandas)
- `db/fetch_isins.py` — psycopg2 cursor with list extraction (ConnectorX incompatible with QuestDB)
- `db/write_baseline.py` — raw SQL INSERT with ns→µs timestamp conversion (pd.to_sql incompatible with QuestDB)
- `db/conns/py_conn.py` — added `get_questdb_conn_string()` and `get_postgres_conn_string()` for future ConnectorX use
- Installed: polars 1.38.1, connectorx 0.4.5, pyarrow 23.0.1

**Why:**
Polars is 1.3-1.8x faster than pandas for groupby/numpy conversion. Lower memory footprint. Better API.

**Limitations discovered:**
- ConnectorX doesn't work with QuestDB (QuestDB doesn't support `BINARY` protocol option)
- `pd.to_sql` doesn't work with QuestDB (pg_catalog `ANY(ARRAY)` queries unsupported)
- QuestDB Postgres wire expects microsecond timestamps, not nanoseconds
- Reads use psycopg2 cursor → Polars DataFrame (workaround for ConnectorX)
