# AlgoTrix Go — Project Rules

## Architecture

| Component | Path | Stack |
|-----------|------|-------|
| **Engine** | `engine/` | Go 1.25, pgx/v5, Fyers SDK, protobuf, WebSocket |
| **Server** | `server/` | Hono (Node.js), pg pool |
| **Dashboard** | `dashboard/` | React + Vite + Tailwind CSS + TanStack Router + SWC |

Python is used where it's a better fit than Go — data analysis, ML, scripting, prototyping.

## Database

- **DB name:** `atdb`
- **Connection:** `postgres://me:algotrix@localhost:5432/atdb`
- **Config file:** `engine/db/conns/db.yaml`
- **Migrations:** `engine/db/migrations/`
- **CLI:** `PGPASSWORD=algotrix psql -h localhost -U me -d atdb`

### Key Tables
- `nse_cm_depth` — order book depth data
- `nse_cm_ticks` — tick-by-tick trade data
- `nse_cm_bhavcopy` — daily OHLCV data (DO NOT corrupt — see danger rules)

## Data Sources

**Market:** NSE (National Stock Exchange of India)

**Priority data sources (first priority):**
1. **NSE** — direct exchange data | Unofficial APIs: [stock-nse-india](https://github.com/hi-imcodeman/stock-nse-india), [NseIndiaApi (Python)](https://github.com/BennyThadikaran/NseIndiaApi)
2. **Fyers** — Go SDK: `github.com/FyersDev/fyers-go-sdk` | [API v3 Docs](https://myapi.fyers.in/docsv3) | [API Dashboard](https://myapi.fyers.in/dashboard/)
3. **Upstox** — [API Docs](https://upstox.com/developer/api-documentation/open-api/) | [Python SDK](https://github.com/upstox/upstox-python) | [SDKs (Python/Node/Java/.NET)](https://upstox.com/developer/api-documentation/sdk/)
4. **Dhan** — [DhanHQ API v2 Docs](https://dhanhq.co/docs/v2/) | [Python SDK](https://github.com/dhan-oss/DhanHQ-py) | [API Portal](https://api.dhan.co/)

Always prefer these four over any other data provider.

**API-first approach:** Prefer using REST APIs and WebSockets directly over broker SDKs. This gives us flexibility to switch between brokers, handle edge cases ourselves, and avoid SDK-specific lock-in. Only use an SDK if the raw API is undocumented or impractical.

**Market Depth levels by broker:**
| Broker | Depth Levels |
|--------|-------------|
| Dhan | 5 |
| Upstox | 30 |
| Fyers | 50 |

- **Feed types:** TBT (tick-by-tick) and DataSocket
- **Feed config:** `engine/feed/config.yaml`
- **Auth tokens:** `engine/token.json`

## Language Selection

| Use Case | Language | Why |
|----------|----------|-----|
| Real-time feed, order execution, low-latency | Go | Performance, concurrency |
| API server | TypeScript (Hono) | Fast dev, type safety |
| Dashboard | React + TypeScript | SPA, TanStack Router |
| Data analysis, backtesting research | Python | pandas, numpy, ML libs |
| Quick scripts, one-off data processing | Python | Faster to write |
| ML models, strategy research | Python | scikit-learn, pytorch ecosystem |

## Go Conventions

- Module: `github.com/karanshergill/algotrix-go`
- Internal packages: `engine/internal/auth`, `engine/internal/config`
- Models: `engine/models/` (ohlcv.go, scrip.go, symbols.go)
- DB driver: pgx/v5 (NOT lib/pq)
- Config format: YAML (`gopkg.in/yaml.v3`)
- Tests: `cd engine && go test ./...`
- Build: `cd engine && go build -o algotrix ./`

## Server Conventions

- Framework: **Hono** (NOT Express)
- Routes: `server/routes/` (auth, backtest, calendar, feed, indices, ohlcv, sectors, symbols, watchlist)
- DB pool: `server/db.ts`
- CORS: enabled for all `/api/*` routes

## Dashboard Conventions

- React + TypeScript + Vite + SWC
- Tailwind CSS via `@tailwindcss/vite`
- TanStack Router (file-based, auto code-splitting)
- State: `dashboard/src/stores/`
- Components: `dashboard/src/components/`
- Features: `dashboard/src/features/`

## Danger Rules

- **NEVER trust your internal knowledge for date, time, market holidays, or trading days.** Always run `date` for current date/time and `date -d "YYYY-MM-DD" +%A` to verify day of week. Verify market holidays against NSE calendar — do NOT guess. You have gotten this wrong multiple times.
- **NEVER state exchange parameters (tick sizes, lot sizes, margin rules, circuit limits) from memory.** Web search + verify against official source.
- **Before any time-sensitive operation** (fetching data, scheduling jobs, checking market hours): run `date` first. Session metadata can be stale after compaction.
- **NEVER use `timeout` with the backtest engine.** Table-swap pattern means SIGTERM corrupts `nse_cm_bhavcopy`. Use `--step` or smaller configs instead.
- **NEVER modify bhavcopy tables directly** — use migrations or the engine's own write paths.
- **NEVER hardcode API tokens** — read from `engine/token.json` or env vars.

## Process Manager

- PM2 via `ecosystem.config.cjs` in project root

---

## Documentation Index

**IMPORTANT: Prefer retrieval-led reasoning over pre-training-led reasoning for any framework-specific tasks. Read the relevant doc file BEFORE writing code.**

| Framework | Doc Path | Use When |
|-----------|----------|----------|
| Hono | `.docs/hono/basics.md` | Server routes, middleware, validation, context API |
| pgx/v5 | `.docs/pgx/reference.md` | Go database queries, transactions, batch, COPY, pool |
| TanStack Router | `.docs/tanstack-router/reference.md` | Dashboard routing, loaders, search params, navigation |

Read the specific doc file before implementing anything with that framework. These contain API references that may differ from what's in training data.
