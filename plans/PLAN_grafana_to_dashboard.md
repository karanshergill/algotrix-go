# Plan: Migrate Grafana to AlgoTrix Dashboard

**Goal:** Remove Grafana. Rebuild all panels as pages in the React + shadcn dashboard with naming and architecture that supports multiple markets (NSE today, global/crypto/forex later).

**Database:** `atdb` (PostgreSQL/TimescaleDB)

**Stack:** React, TanStack Router, TanStack Query, TanStack Table, Recharts, shadcn/ui, Tailwind, Hono

---

## Design Principles

### Naming Convention

All routes, API endpoints, features, and components use **market-agnostic names**. Market-specific logic is handled via query params, config, or sub-routes — not baked into names.

- `/news` not `/nse-news`
- `/api/news/announcements?market=nse` not `/api/nse/announcements`
- `features/news/` not `features/nse-news/`
- Table names stay as-is (`nse_announcements`) — that's the data layer. The UI layer abstracts over it.

### Multi-Market Ready

- API endpoints accept `?market=nse` param (default: `nse` for now)
- Types/interfaces use generic names: `Announcement`, `InsiderTrade`, `BlockDeal` — not `NseAnnouncement`
- News page renders a unified feed — source/market shown as a badge per item
- When a new market is added: new collector writes to new tables, server adds a query for that market, UI stays the same
- Future sub-routes: `/markets` (overview all), `/markets/nse`, `/markets/global`, `/markets/crypto`

---

## What Grafana Currently Shows (22 panels)

**Market Overview:** Sector performance, top industries, breadth, gainers, losers
**System:** Engine status, pool size, watchlist, OHLCV date, signals count, ticks, heartbeat
**Signals:** Signal log, signals over time, by screener, breakout stocks, threshold distribution
**Live Indicators:** Volume spikes, buy/sell pressure, early momentum
**Charts:** Live candlestick, historical OHLCV

---

## What Already Exists

| Page | Overlap |
|------|---------|
| `/` (Home) | Partial — has overview, pipeline health, regime card, symbol universe |
| `/live-feed` | Partial — ticker card, depth panel, symbol search |
| `/industry-and-sector-pulse` | Partial — sector table, group chain card |
| `/watchlist`, `/backtests`, `/ohlcv` | No overlap |

---

## Phase 1: API Endpoints

### 1.1 `server/routes/news.ts` (NEW)

The central news/events API. Market-agnostic interface, market-specific queries behind the scenes.

```
GET /api/news/feed?market=nse&days=7&category=all
  → Unified news feed: announcements, insider trades, block deals merged into
    a single chronological stream with type badges.
  → Returns: { items: [{ type, market, symbol, headline, timestamp, metadata }] }

GET /api/news/announcements?market=nse&days=7&symbol=&market_moving=
  → Corporate announcements, filterable

GET /api/news/insider-trades?market=nse&days=7&symbol=
  → Insider trading activity

GET /api/news/block-deals?market=nse&days=7
  → Block/bulk deal trades

GET /api/news/events?market=nse&type=all
  → Upcoming events: board meetings + corporate actions (ex-dates, dividends, splits)
  → type=meetings|actions|all

GET /api/news/summary?market=nse
  → Quick stats: announcements today, insider trades this week, upcoming events count
  → Used by home page stat cards
```

### 1.2 `server/routes/markets.ts` (NEW)

Market overview data. Works per-market via param.

```
GET /api/markets/breadth?market=nse
  → { advances, declines, unchanged, total }

GET /api/markets/movers?market=nse&direction=gainers|losers|active&limit=10
  → Top movers by % change or volume

GET /api/markets/sectors?market=nse
  → Sector-level aggregates: avg change, leader, laggard

GET /api/markets/industries?market=nse&limit=15
  → Industry-level aggregates
```

### 1.3 `server/routes/live.ts` (NEW)

Real-time indicator screens. Feed-dependent.

```
GET /api/live/scans?type=volume-spikes|momentum|pressure&market=nse
  → Pre-built scans with configurable thresholds via query params
  → volume-spikes: min_spike, min_volume
  → momentum: min_spike, min_change, min_buy_ratio
  → pressure: top buy/sell imbalance

GET /api/live/snapshot?isin=INE002A01018
  → Single stock: latest tick + depth + VWAP + indicators
```

### 1.4 Update `server/routes/feed.ts`

```
GET /api/feed/stats
  → { ticks_today, latest_tick, symbols_active, feed_age_seconds }
```

---

## Phase 2: Dashboard Pages

### 2.1 New: News & Events (`/news`)

**Route:** `routes/_authenticated/news/index.tsx`
**Feature:** `features/news/`

This is the primary new page — everything Grafana doesn't have a good equivalent for.

**Layout:** Tabs — Feed | Insider Trading | Block Deals | Events

#### Tab: Feed
- **`news-feed.tsx`** — Chronological stream of all news types
  - Each item: timestamp, market badge, symbol, type badge (announcement/insider/block), headline
  - Market-moving items highlighted
  - Filter bar: market, symbol, type, date range
  - Auto-refresh every 60s

#### Tab: Insider Trading
- **`insider-trades-table.tsx`** — TanStack Table
  - Columns: date, symbol, acquirer, mode, shares, value
  - Sortable, filterable by symbol
  - Summary row: total value this week

#### Tab: Block Deals
- **`block-deals-table.tsx`** — TanStack Table
  - Columns: date, symbol, volume, value, price
  - Highlights large deals

#### Tab: Events
- **`events-calendar.tsx`** — Upcoming board meetings + corporate actions
  - Two sections: This Week | Next Week
  - Badge: results, dividend, AGM, split, bonus
  - Calendar or list view toggle

**Hooks:**
- `use-news-feed.ts` → `GET /api/news/feed`
- `use-insider-trades.ts` → `GET /api/news/insider-trades`
- `use-block-deals.ts` → `GET /api/news/block-deals`
- `use-events.ts` → `GET /api/news/events`

### 2.2 New: Market Overview (`/markets`)

**Route:** `routes/_authenticated/markets/index.tsx`
**Feature:** `features/markets/`

**Layout:** Top stats row + charts + tables

**Components:**
- **`breadth-card.tsx`** — Advances / Declines / Unchanged as stat cards with Recharts donut
- **`sector-bars.tsx`** — Horizontal bar chart of sector % change (green/red)
- **`industry-bars.tsx`** — Top 15 industries by % change
- **`movers-table.tsx`** — Tabs: Gainers | Losers | Most Active
  - TanStack Table: symbol, LTP, % change, volume
  - Color-coded % change

**Hooks:**
- `use-breadth.ts` → `GET /api/markets/breadth`
- `use-movers.ts` → `GET /api/markets/movers`
- `use-sectors.ts` → `GET /api/markets/sectors`
- All with `refetchInterval: 30_000` during market hours

### 2.3 Enhance Home Page (`/`)

**Feature:** `features/dashboard/` (existing)

Add components:
- **`news-ticker.tsx`** — Horizontal scrolling bar of latest market-moving announcements
- **`markets-snapshot.tsx`** — Compact breadth + top 3 gainers/losers inline (extensible per market)
- **`system-health.tsx`** — Row of stat cards: Feed status, symbols active, latest OHLCV, ticks today, news today

These replace the current placeholder overview components with real data.

### 2.4 Enhance Live Feed (`/live-feed`)

**Feature:** `features/live-feed/` (existing)

Add tabs or sections:
- **`scan-results.tsx`** — Volume spikes, momentum candidates, buy/sell pressure
  - Configurable thresholds via sliders
  - Auto-refresh every 10s
- Integrates with existing WebSocket infrastructure for real-time updates

### 2.5 Enhance Sector Pulse (`/industry-and-sector-pulse`)

Add alongside existing table:
- **Sector bar chart** (Recharts) for visual ranking
- **Color-coded relative strength** badges

---

## Phase 3: Navigation

Update sidebar:

```
Home (/)
Market (/markets)                          — NEW
News (/news)                              — NEW
Live Feed (/live-feed)
Watchlist (/watchlist)
Backtests (/backtests)
Sectors (/industry-and-sector-pulse)
OHLCV (/ohlcv)
```

Icons: Home, BarChart3, Newspaper, Radio, Star, FlaskConical, PieChart, Database (from lucide-react)

---

## Phase 4: Cleanup

1. `docker stop grafana && docker rm grafana`
2. Remove `/home/me/grafana/` directory
3. Remove Grafana volumes: `docker volume rm grafana_grafana-data`
4. Remove `/home/me/projects/algotrix-v2/grafana/` dashboard JSON

---

## Types (market-agnostic)

```typescript
// types/news.ts
interface NewsItem {
  id: number
  type: 'announcement' | 'insider_trade' | 'block_deal' | 'board_meeting' | 'corporate_action'
  market: string        // 'nse' | 'bse' | 'nasdaq' | 'global'
  symbol: string
  headline: string
  timestamp: string
  isMarketMoving?: boolean
  metadata: Record<string, unknown>
}

interface InsiderTrade {
  id: number
  market: string
  symbol: string
  acquirerName: string
  mode: string
  sharesAcquired: number
  value: number
  transactionDate: string
}

interface BlockDeal {
  id: number
  market: string
  symbol: string
  volume: number
  value: number
  price: number
  dealDate: string
}

interface MarketEvent {
  id: number
  market: string
  symbol: string
  eventType: 'board_meeting' | 'dividend' | 'split' | 'bonus' | 'agm' | 'rights'
  date: string
  description: string
}

// types/markets.ts
interface MarketBreadth {
  market: string
  advances: number
  declines: number
  unchanged: number
  timestamp: string
}

interface Mover {
  symbol: string
  isin: string
  ltp: number
  changePct: number
  volume: number
  sector: string
}

interface SectorPerformance {
  sector: string
  avgChangePct: number
  stockCount: number
  leader: string
  laggard: string
}
```

---

## File Structure (new files only)

```
server/routes/
├── news.ts                    # NEW — all news/events endpoints
├── markets.ts                 # NEW — breadth, movers, sectors
├── live.ts                    # NEW — scans, snapshots
└── feed.ts                    # UPDATED — add /stats

dashboard/src/
├── features/
│   ├── news/                  # NEW
│   │   ├── index.ts
│   │   ├── news-page.tsx
│   │   ├── news-feed.tsx
│   │   ├── insider-trades-table.tsx
│   │   ├── block-deals-table.tsx
│   │   ├── events-calendar.tsx
│   │   ├── types.ts
│   │   ├── use-news-feed.ts
│   │   ├── use-insider-trades.ts
│   │   ├── use-block-deals.ts
│   │   └── use-events.ts
│   ├── markets/               # NEW
│   │   ├── index.ts
│   │   ├── markets-page.tsx
│   │   ├── breadth-card.tsx
│   │   ├── sector-bars.tsx
│   │   ├── industry-bars.tsx
│   │   ├── movers-table.tsx
│   │   ├── types.ts
│   │   ├── use-breadth.ts
│   │   ├── use-movers.ts
│   │   └── use-sectors.ts
│   ├── dashboard/             # UPDATED
│   │   ├── components/
│   │   │   ├── news-ticker.tsx        # NEW
│   │   │   ├── markets-snapshot.tsx    # NEW
│   │   │   └── system-health.tsx       # NEW
│   └── live-feed/             # UPDATED
│       └── scan-results.tsx           # NEW
├── routes/_authenticated/
│   ├── news/index.tsx         # NEW
│   └── markets/index.tsx      # NEW
└── types/
    ├── news.ts                # NEW
    └── markets.ts             # NEW
```

---

## Data Layer (current → future)

| Data | Current Table (NSE) | Future Pattern |
|------|-------------------|----------------|
| Announcements | `nse_announcements` | Add `market` column, or new table per market: `bse_announcements`, `global_news` |
| Insider trading | `nse_insider_trading` | Same pattern — table per market or unified with `market` column |
| Block deals | `nse_block_deals` | Same |
| Board meetings | `nse_board_meetings` | Same |
| Corporate actions | `nse_corporate_actions` | Same |
| Ticks | `nse_cm_ticks` | Separate table per exchange (different schemas) |
| Symbols | `symbols` | Add `exchange` column (already implied by `fy_symbol` prefix `NSE:`) |

The server routes abstract this — the API always returns the same shape regardless of which table it queries. When a new market is added, only the server route query changes, not the dashboard.

---

## Priority Order

1. **News page** (Phase 1.1 + 2.1) — highest value, most unique, no Grafana equivalent in existing dashboard
2. **Markets overview** (Phase 1.2 + 2.2) — replaces 5 Grafana panels
3. **Home enhancements** (Phase 2.3) — news ticker + markets snapshot + system health
4. **Live scans** (Phase 1.3 + 2.4) — depends on feed running
5. **Navigation** (Phase 3) — do alongside each page
6. **Grafana cleanup** (Phase 4) — after all pages verified

---

## Estimated Scope

| Phase | New Files | Effort |
|-------|-----------|--------|
| API routes (news, markets, live) | 3 server files | Medium |
| News page (feature + route) | ~12 files | Large |
| Markets page (feature + route) | ~10 files | Medium |
| Home enhancements | 3 components | Small |
| Live feed enhancements | 1-2 components | Small |
| Nav + cleanup | Config changes | Trivial |
