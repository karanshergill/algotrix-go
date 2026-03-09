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
