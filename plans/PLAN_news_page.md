# PLAN: News & Corporate Events Page

**Status:** REVISED
**Author:** Gxozt (refined by Coder, revised 2026-03-25 post-review)
**Date:** 2026-03-24 (refined 2026-03-25, revised 2026-03-25)
**Location:** AlgoTrix Dashboard (`/news`)

## Goal

A single dashboard page that surfaces all corporate events from our 5 NSE data sources, designed for a trader who needs to know: **what happened today that matters, what's coming up, and who's buying/selling.**

## Data Sources (already collected, all in atdb)

| Source | Table | Rows | Key Columns | Update Freq |
|--------|-------|------|-------------|-------------|
| Announcements | `nse_announcements` | ~8,700 | `symbol, category, description, announcement_dt (TIMESTAMP), attachment_url, is_market_moving, raw_json` | 2min market, 10min after |
| Block Deals | `nse_block_deals` | ~24 | `symbol, series, session, traded_volume, traded_value, price, deal_date (DATE), raw_json` | 2min market |
| Board Meetings | `nse_board_meetings` | ~370 | `symbol, meeting_date (DATE), purpose, description, raw_json` | 10min after |
| Corporate Actions | `nse_corporate_actions` | ~290 | `symbol, subject, ex_date (DATE), record_date (DATE), raw_json` | 10min after |
| Insider Trading | `nse_insider_trading` | **2.99M** | `symbol, acquirer_name, acquisition_mode, shares_acquired, value, transaction_date (DATE), raw_json` | 2min market |

**Important schema notes:**
- All tables use `symbol TEXT` (not ISIN). Joining to `symbols` table is by `trading_symbol`.
- All tables have `id SERIAL PRIMARY KEY`, `raw_json JSONB`, `created_at TIMESTAMP DEFAULT NOW()`.
- Only `nse_announcements.announcement_dt` is a TIMESTAMP with time-of-day. All other date columns are DATE-only.
- Insider trading has no explicit buy/sell column — must derive from `acquisition_mode` (e.g., "Market Purchase" = buy, "Market Sale" = sell). Check `raw_json->>'transactionType'` for authoritative buy/sell classification.

## Page Layout

### Tab Structure
Three tabs: **Feed** (default) | **Upcoming** | **Insider Activity**

### Top Bar (shared across all tabs)
- **Date picker** — `<Input type="date">` defaulting to today **in IST** (see Timezone section below)
- **Symbol filter** — text input with debounce (not autocomplete in V1 — keep it simple). **Important:** convert empty string `""` to `undefined` before passing to hooks (same footgun as signals `screenerFilter || undefined`).
- **Source chips** — filter badges: All | Announcements | Block Deals (visible on Feed tab only)
- **Market-moving toggle** — filter `is_market_moving = true` (Feed tab, announcements only)

### Tab 1: Today's Feed (default, full-width)

Unified chronological stream of announcements + block deals for the selected date.

**Critical decision: Exclude insider trading and date-only sources from the unified feed.**
- Announcements have `announcement_dt` (TIMESTAMP) — can sort by time ✓
- Block deals have `deal_date` (DATE only) — show at top of date group, no time
- Board meetings / corporate actions — DATE only, no intraday relevance → belong in Upcoming tab
- Insider trading — 2.99M rows, date-only, aggregated view is more useful → own tab

Each row shows:
- **Time** — `HH:MM` for announcements, "—" for block deals (no time component)
- **Symbol** — font-medium text, not a badge (simpler, matches signals pattern)
- **Source** — `<Badge variant="outline">` with source-specific color class
- **Title** — announcement `description`, or block deal formatted as "Block: {traded_volume} @ ₹{price}" (**formatted in frontend** using `Intl.NumberFormat('en-IN')`, not in SQL)
- **Market-moving indicator** — red left border + `<Badge>` for `is_market_moving = true`
- **Attachment** — clickable link icon if `attachment_url` is present

Sorted by `announcement_dt DESC` (announcements) then `deal_date DESC` (block deals at bottom of date).

**Announcement category styling** (via `category` column):
- Red left border: Outcome of Board Meeting, Acquisitions, Credit Rating, Spurt in Volume, SEBI Takeover
- Amber left border: Press Release, Allotment of Securities, News Verification, Bagging of Orders
- No accent: Trading Window, ESOP, Copy of Newspaper, General Updates

**Expandable detail:** Click row to expand and show full `raw_json` in a `<pre>` block. **raw_json is NOT included in feed responses** — fetched on-demand via `GET /api/news/:source/:id` detail endpoint (see below).

**Pagination:** "Load more" button at bottom, not infinite scroll. Default 50 rows, load 50 more on click. **Uses `useInfiniteQuery`** with `getNextPageParam` (see Hooks section).

### Tab 2: Upcoming Events

Future board meetings + corporate actions, sorted by date.

**Board Meetings** section:
- Query: `meeting_date >= (CURRENT_DATE AT TIME ZONE 'Asia/Kolkata')::date` with optional symbol filter, limit 100
- Show: symbol, meeting_date, purpose
- Purpose highlighting: "Financial Results" → red badge, "Dividend" → emerald badge, other → default

**Corporate Actions** section:
- Query: `ex_date >= (CURRENT_DATE AT TIME ZONE 'Asia/Kolkata')::date` with optional symbol filter, limit 100
- Show: symbol, subject, ex_date, record_date
- Subject parsing: highlight "Dividend", "Bonus", "Split", "Rights" with distinct badges

Grouped by week (This Week / Next Week / Later). Use `date-fns` `startOfWeek` / `isSameWeek` with `{ weekStartsOn: 1 }` (Monday) since this is for Indian markets.

### Tab 3: Insider Activity

**Aggregated view (default):**
- Top 20 symbols by net insider buying value in selected period
- Top 20 symbols by net insider selling value
- Period toggle: 7d / 30d / 90d (chips, like screener filter in signals). **Server validates `days` is one of `[7, 30, 90]`; rejects other values with 400.**
- Table columns: Rank, Symbol, Net Value (₹), # Transactions, Top Acquirer
- **Value formatting is frontend-only** — use `new Intl.NumberFormat('en-IN').format(value)` in the component, not SQL `format()`

**Per-symbol drill-down:** When a symbol is clicked or entered in the symbol filter:
- Table of individual transactions: acquirer_name, acquisition_mode, shares_acquired, value, transaction_date
- Sorted by transaction_date DESC, limit 100 with "load more" (uses `useInfiniteQuery`)

**No chart in V1** — bar chart deferred to V2. Table-first approach is simpler and matches the rest of the dashboard.

### Block Deals (inline in Feed tab, not a separate tab)

Only 24 rows total — doesn't warrant its own tab. Block deals appear inline in the feed, styled with an amber source badge. If the user filters to "Block Deals" source chip, they see only block deals.

## Timezone Handling (IST)

NSE data uses IST dates. All date logic must account for this:

1. **Default date computation:** Server computes "today" in IST, not UTC:
   ```ts
   const todayIST = new Date().toLocaleString('en-CA', { timeZone: 'Asia/Kolkata' }).slice(0, 10)
   ```
2. **Upcoming queries:** Use `AT TIME ZONE` for `CURRENT_DATE` comparisons:
   ```sql
   WHERE meeting_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
   ```
3. **Announcement timestamp queries:** Range query using IST date boundaries (see Feed query below).

## API Routes (`server/routes/news.ts`)

Follow the Hono pattern from `server/routes/signals.ts`. Use parameterized queries with `$1, $2` placeholders. **Note:** signals route lets errors propagate to Hono's default handler (no try-catch). This plan follows the same pattern for consistency. Error handling is Hono's responsibility.

**Parameter index handling:** Use a dynamic `idx` counter for building parameterized queries, same as signals route. Do NOT hardcode `$2`, `$3` etc. in conditional branches:
```ts
const params: any[] = []
let idx = 1
params.push(dateVal); const dateIdx = idx++
if (symbol) { params.push(symbol); symbolIdx = idx++ }
// ... build query string using symbolIdx
```

### Route Registration

In `server/index.ts`:
```ts
import news from './routes/news'
// ...
app.route('/api/news', news)
```

### GET `/` — Feed

Announcements + block deals for a date. **Does NOT include insider trading.**

```
Query params:
  date:    YYYY-MM-DD (default: today in IST)
  symbol:  string (optional)
  source:  announcements|block_deals (optional — only these two in feed)
  market_moving: "true" (optional, filters announcements only)
  limit:   number (default: 50, max: 200)
  offset:  number (default: 0)
```

**Validation:** `date` must match `YYYY-MM-DD` format (regex check), reject with 400 otherwise.

**Implementation — UNION ALL approach:**

Block deals are ~24 rows/day. Rather than two separate queries with broken pagination, use a single `UNION ALL` query so Postgres handles sorting and pagination correctly:

```sql
SELECT * FROM (
  SELECT id, symbol, 'announcement' AS source,
         announcement_dt AS timestamp, description AS title,
         category, is_market_moving, attachment_url,
         NULL::numeric AS traded_volume, NULL::numeric AS price, NULL::numeric AS traded_value
  FROM nse_announcements
  WHERE announcement_dt >= $1::date
    AND announcement_dt < $1::date + interval '1 day'
    ${symbol ? `AND symbol = $${symbolIdx}` : ''}
    ${marketMoving ? 'AND is_market_moving = true' : ''}

  UNION ALL

  SELECT id, symbol, 'block_deal' AS source,
         deal_date::timestamp AS timestamp, NULL AS title,
         NULL AS category, false AS is_market_moving, NULL AS attachment_url,
         traded_volume, price, traded_value
  FROM nse_block_deals
  WHERE deal_date = $1::date
    ${symbol ? `AND symbol = $${symbolIdx}` : ''}
) combined
ORDER BY timestamp DESC
LIMIT $${limitIdx} + 1 OFFSET $${offsetIdx}
```

**Notes:**
- `announcement_dt::date = $1` replaced with **range query** (`>= date AND < date + 1 day`) to allow index usage on `idx_ann_dt`.
- Block deal raw numeric columns (`traded_volume`, `price`, `traded_value`) returned as numbers — **frontend formats** the title string with `Intl.NumberFormat('en-IN')`.
- Uses `LIMIT + 1` pattern (fetch one extra row) to derive `has_more` without a separate COUNT query. If `limit + 1` rows are returned, `has_more = true` and the extra row is stripped before responding.

**Response shape:**
```json
{
  "items": [
    {
      "id": 123,
      "source": "announcement",
      "symbol": "RELIANCE",
      "timestamp": "2026-03-24T14:30:00",
      "title": "Outcome of Board Meeting...",
      "category": "Board Meeting",
      "is_market_moving": true,
      "attachment_url": "https://nsearchives.nseindia.com/corporate/...",
      "traded_volume": null,
      "price": null,
      "traded_value": null
    }
  ],
  "has_more": true
}
```

**Deliberate departure from signals pattern:** Signals returns a flat array `c.json(result.rows)`. The feed returns a wrapped object `{ items, has_more }` because pagination metadata is necessary. This is intentional — flat arrays don't support pagination state.

### GET `/api/news/:source/:id` — Detail (raw_json)

Returns the full `raw_json` for a single item. Used by the expandable row detail view.

```
Path params:
  source:  announcements|block_deals|board_meetings|corporate_actions|insider_trading
  id:      number
```

**Implementation:**
```ts
const TABLE_MAP: Record<string, string> = {
  announcements: 'nse_announcements',
  block_deals: 'nse_block_deals',
  board_meetings: 'nse_board_meetings',
  corporate_actions: 'nse_corporate_actions',
  insider_trading: 'nse_insider_trading',
}

const table = TABLE_MAP[source]
if (!table) return c.json({ error: 'Invalid source' }, 400)

const result = await pool.query(`SELECT raw_json FROM ${table} WHERE id = $1`, [id])
// Note: table name is from a whitelist constant, not user input — safe from injection
```

**Response:**
```json
{ "raw_json": { ... } }
```

### GET `/upcoming` — Future Events

Board meetings + corporate actions with future dates.

```
Query params:
  symbol:  string (optional)
  limit:   number (default: 100, max: 200)
```

**Implementation:** Two queries with `Promise.all()`:
```sql
-- Board meetings
SELECT id, symbol, 'board_meeting' AS source, meeting_date, purpose, description
FROM nse_board_meetings
WHERE meeting_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
${symbol ? 'AND symbol = $1' : ''}
ORDER BY meeting_date ASC
LIMIT $N

-- Corporate actions
SELECT id, symbol, 'corporate_action' AS source, ex_date, record_date, subject
FROM nse_corporate_actions
WHERE ex_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
${symbol ? 'AND symbol = $1' : ''}
ORDER BY ex_date ASC
LIMIT $N
```

**Response:** `{ meetings: [...], actions: [...] }` — keep separate, frontend groups by week.

### GET `/insider-activity` — Aggregated Insider Data

**This is the critical performance endpoint.** The 2.99M-row table requires careful query design.

```
Query params:
  days:    7|30|90 (default: 7) — MUST be one of these three values, reject others with 400
  symbol:  string (optional — if provided, returns individual transactions)
  limit:   number (default: 20, max: 100)
  offset:  number (default: 0)
```

**Validation:**
```ts
const ALLOWED_DAYS = [7, 30, 90]
const days = parseInt(c.req.query('days') || '7')
if (!ALLOWED_DAYS.includes(days)) {
  return c.json({ error: 'days must be 7, 30, or 90' }, 400)
}
```

**Aggregated mode** (no symbol param):

**V1 uses exact CASE WHEN matches** instead of ILIKE with leading wildcards. ILIKE `'%purchase%'` prevents efficient index-only scans on the covering index. The known `acquisition_mode` values are finite and enumerable:

```sql
SELECT symbol,
       SUM(CASE WHEN acquisition_mode IN (
         'Market Purchase', 'Off Market - Loss of Securities',
         'Purchase', 'Allotment', 'ESOP', 'Inter-se Transfer - Acquisition'
       ) THEN value ELSE 0 END) AS buy_value,
       SUM(CASE WHEN acquisition_mode IN (
         'Market Sale', 'Sale', 'Off Market - Invocation of Pledge',
         'Disposal', 'Inter-se Transfer - Disposal'
       ) THEN value ELSE 0 END) AS sell_value,
       SUM(CASE WHEN acquisition_mode IN (
         'Market Purchase', 'Off Market - Loss of Securities',
         'Purchase', 'Allotment', 'ESOP', 'Inter-se Transfer - Acquisition'
       ) THEN value ELSE -value END) AS net_value,
       COUNT(*) AS txn_count
FROM nse_insider_trading
WHERE transaction_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date - $1::int
  AND value IS NOT NULL AND value > 0
GROUP BY symbol
ORDER BY ABS(net_value) DESC
LIMIT $2
```

**V2 plan (documented, not implemented):** Pre-compute a `transaction_type` column (`BUY`/`SELL`) during collection. Add it to the covering index. This moves classification cost from query-time to write-time and enables simpler queries. Alternatively, a materialized view refreshed on schedule.

**If an unknown `acquisition_mode` is encountered at runtime**, it falls into the ELSE branch (treated as 0 for buy/sell, -value for net). This is conservative — unknown modes don't inflate either side. Log unknown modes for review.

Split response into buyers (net_value > 0) and sellers (net_value < 0) in JS.

**Drill-down mode** (symbol provided):
```sql
SELECT id, acquirer_name, acquisition_mode, shares_acquired, value, transaction_date
FROM nse_insider_trading
WHERE symbol = $1
  AND transaction_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date - $2::int
ORDER BY transaction_date DESC, value DESC
LIMIT $3 + 1 OFFSET $4
```

Uses `LIMIT + 1` pattern for `has_more` derivation.

**Response (aggregated):**
```json
{
  "top_buyers": [
    { "symbol": "RELIANCE", "net_value": 150000000, "buy_value": 200000000, "sell_value": 50000000, "txn_count": 12 }
  ],
  "top_sellers": [
    { "symbol": "INFY", "net_value": -80000000, "buy_value": 10000000, "sell_value": 90000000, "txn_count": 8 }
  ],
  "period_days": 7
}
```

**Response (drill-down):**
```json
{
  "transactions": [...],
  "has_more": true
}
```

### GET `/summary` — Counts for Summary Cards

```
Query params:
  date: YYYY-MM-DD (default: today in IST)
```

**Implementation:** Use `Promise.all()` to run all COUNT queries in parallel (explicitly — not sequential). Alternatively, a single CTE query:

```sql
WITH ann AS (
  SELECT COUNT(*)::int AS total,
         COUNT(*) FILTER (WHERE is_market_moving)::int AS market_moving
  FROM nse_announcements
  WHERE announcement_dt >= $1::date
    AND announcement_dt < $1::date + interval '1 day'
),
blocks AS (
  SELECT COUNT(*)::int AS total FROM nse_block_deals WHERE deal_date = $1::date
),
meetings AS (
  SELECT COUNT(*)::int AS total FROM nse_board_meetings
  WHERE meeting_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
),
actions AS (
  SELECT COUNT(*)::int AS total FROM nse_corporate_actions
  WHERE ex_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
)
SELECT ann.total AS announcements, ann.market_moving,
       blocks.total AS block_deals,
       meetings.total AS upcoming_meetings,
       actions.total AS upcoming_actions
FROM ann, blocks, meetings, actions
```

**Note:** Uses range query for `announcement_dt` (not `::date` cast) to match the feed query and use indexes. Uses `AT TIME ZONE` for upcoming date comparisons.

**Response:**
```json
{ "announcements": 45, "market_moving": 3, "block_deals": 1, "upcoming_meetings": 5, "upcoming_actions": 2 }
```

## Frontend Structure

```
dashboard/src/features/news/
├── index.ts                    # Barrel export: export { NewsPage } from './news-page'
├── news-page.tsx               # Main page component with tabs (mirrors signals-page.tsx)
├── types.ts                    # TypeScript interfaces
├── use-news.ts                 # React Query hooks (mirrors use-signals.ts)
└── components/
    ├── news-feed.tsx           # Feed tab: table of announcements + block deals
    ├── news-card.tsx           # Expandable row component
    ├── upcoming-events.tsx     # Upcoming tab: meetings + actions grouped by week
    └── insider-activity.tsx    # Insider tab: aggregated table + drill-down

dashboard/src/routes/_authenticated/news/
└── index.tsx                   # Route: createFileRoute('/_authenticated/news/')({ component: NewsPage })
```

**Structural note:** Signals has a flat structure (no `components/` subdirectory). News uses `components/` because it has 3 tabs with distinct sub-components. This is an intentional divergence — 4 sub-components warrant a directory.

**Dropped from original plan:**
- `news-filters.tsx` — filters live directly in `news-page.tsx` (signals pattern: filters are in the page, not a separate component)
- `news-summary-cards.tsx` — summary cards are part of `news-page.tsx` header area
- `block-deals-table.tsx` — block deals are inline in the feed, no separate component
- `hooks/` directory — single `use-news.ts` file at feature root (signals pattern)

## Types (`types.ts`)

```ts
export type FeedSource = 'announcement' | 'block_deal' | 'board_meeting' | 'corporate_action' | 'insider_trading'

export type FeedItem = {
  id: number
  source: FeedSource
  symbol: string
  timestamp: string
  title: string | null
  category: string | null
  is_market_moving: boolean
  attachment_url: string | null
  traded_volume: number | null
  price: number | null
  traded_value: number | null
}

export type UpcomingMeeting = {
  id: number
  symbol: string
  meeting_date: string
  purpose: string
  description: string | null
}

export type UpcomingAction = {
  id: number
  symbol: string
  subject: string
  ex_date: string
  record_date: string | null
}

export type InsiderAggregate = {
  symbol: string
  net_value: number
  buy_value: number
  sell_value: number
  txn_count: number
}

export type InsiderTransaction = {
  id: number
  acquirer_name: string
  acquisition_mode: string
  shares_acquired: number
  value: number
  transaction_date: string
}

export type NewsSummary = {
  announcements: number
  market_moving: number
  block_deals: number
  upcoming_meetings: number
  upcoming_actions: number
}
```

## Hooks (`use-news.ts`)

Use `@tanstack/react-query` (already installed, v5). Follow `use-signals.ts` pattern for fetch functions; use `useInfiniteQuery` for paginated endpoints.

```ts
import { useQuery, useInfiniteQuery } from '@tanstack/react-query'
import { isMarketOpen } from '@/lib/market-hours'  // Use shared util, don't duplicate

// --- Fetch functions ---

async function fetchFeed(params: { date: string; source?: string; symbol?: string; marketMoving?: boolean; limit: number; offset: number }) {
  const sp = new URLSearchParams({ date: params.date, limit: String(params.limit), offset: String(params.offset) })
  if (params.source) sp.set('source', params.source)
  if (params.symbol) sp.set('symbol', params.symbol)
  if (params.marketMoving) sp.set('market_moving', 'true')
  const res = await fetch(`/api/news?${sp}`)
  if (!res.ok) throw new Error('Failed to fetch feed')
  return res.json() as Promise<{ items: FeedItem[]; has_more: boolean }>
}

async function fetchSummary(date: string) {
  const res = await fetch(`/api/news/summary?date=${date}`)
  if (!res.ok) throw new Error('Failed to fetch summary')
  return res.json() as Promise<NewsSummary>
}

async function fetchUpcoming(symbol?: string) {
  const sp = new URLSearchParams()
  if (symbol) sp.set('symbol', symbol)
  const res = await fetch(`/api/news/upcoming?${sp}`)
  if (!res.ok) throw new Error('Failed to fetch upcoming')
  return res.json() as Promise<{ meetings: UpcomingMeeting[]; actions: UpcomingAction[] }>
}

async function fetchInsider(params: { days: number; symbol?: string; limit: number; offset: number }) {
  const sp = new URLSearchParams({ days: String(params.days), limit: String(params.limit), offset: String(params.offset) })
  if (params.symbol) sp.set('symbol', params.symbol)
  const res = await fetch(`/api/news/insider-activity?${sp}`)
  if (!res.ok) throw new Error('Failed to fetch insider activity')
  return res.json()
}

async function fetchDetail(source: string, id: number) {
  const res = await fetch(`/api/news/${source}/${id}`)
  if (!res.ok) throw new Error('Failed to fetch detail')
  return res.json() as Promise<{ raw_json: Record<string, unknown> }>
}

// --- Hooks ---

const PAGE_SIZE = 50

export function useNewsFeed(date: string, source?: string, symbol?: string, marketMoving?: boolean) {
  return useInfiniteQuery({
    queryKey: ['news-feed', date, source, symbol, marketMoving],
    queryFn: ({ pageParam = 0 }) => fetchFeed({ date, source, symbol, marketMoving, limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, _allPages, lastPageParam) =>
      lastPage.has_more ? lastPageParam + PAGE_SIZE : undefined,
    refetchInterval: isMarketOpen() ? 120_000 : false,  // 120s — matches collector 2min cadence
  })
}

export function useNewsSummary(date: string) {
  return useQuery({
    queryKey: ['news-summary', date],
    queryFn: () => fetchSummary(date),
    refetchInterval: isMarketOpen() ? 120_000 : false,  // 120s — matches collector cadence
  })
}

export function useUpcomingEvents(symbol?: string) {
  return useQuery({
    queryKey: ['news-upcoming', symbol],
    queryFn: () => fetchUpcoming(symbol),
    // No auto-refresh — upcoming events don't change intraday
  })
}

export function useInsiderActivity(days: number, symbol?: string) {
  return useQuery({
    queryKey: ['news-insider', days, symbol],
    queryFn: () => fetchInsider({ days, symbol, limit: 20, offset: 0 }),
    // No auto-refresh — insider data updates are infrequent
  })
}

export function useNewsDetail(source: string, id: number) {
  return useQuery({
    queryKey: ['news-detail', source, id],
    queryFn: () => fetchDetail(source, id),
    enabled: false,  // Only fetch when user expands a row (call refetch() on click)
    staleTime: Infinity,  // raw_json doesn't change
  })
}
```

**No after-hours polling.** The original plan had 5-min after-hours refresh, but this data doesn't change after hours. React Query's stale-while-revalidate handles tab refocus already.

## Sidebar Entry

In `dashboard/src/components/layout/data/sidebar-data.ts`:

```ts
import { Newspaper } from 'lucide-react'
// ...
// After "Signals" entry in General nav group:
{
  title: 'News & Events',
  url: '/news',
  icon: Newspaper,
},
```

## UI States (per tab)

### Loading
- 5x `<Skeleton className="h-10 w-full rounded-lg" />` (signals pattern)

### Empty
- `<div className="flex items-center justify-center h-32 text-muted-foreground text-sm">No {X} for {date}</div>`

### Error
- React Query global error handler in `main.tsx` already handles 401/500
- Per-query: if `isError`, show inline error message with retry button: `"Failed to load feed. <button>Retry</button>"` using `refetch()`

## Design Notes

### Source Badge Colors (Badge variant="outline" + color class)
```ts
const SOURCE_COLORS: Record<string, string> = {
  announcement: 'text-violet-500 border-violet-500/30 bg-violet-500/10',
  block_deal:   'text-amber-500 border-amber-500/30 bg-amber-500/10',
}
```

### Market-Moving Row Styling
```
className="border-l-2 border-l-red-500 bg-red-500/5"
```

### Category Badge Colors (for announcement category)
```ts
const CATEGORY_SEVERITY: Record<string, 'red' | 'amber' | null> = {
  'Outcome of Board Meeting': 'red',
  'Acquisitions': 'red',
  'Credit Rating': 'red',
  // ... etc
  'Press Release': 'amber',
  'Allotment of Securities': 'amber',
  // ... etc
}
```

### Upcoming Tab: Purpose Badges
- "Financial Results" → `text-red-500 border-red-500/30 bg-red-500/10`
- "Dividend" → `text-emerald-500 border-emerald-500/30 bg-emerald-500/10`
- Other → `text-muted-foreground border-border`

### Insider Tab: Value Formatting
- **Format in frontend only** — server returns raw numbers, frontend formats with `new Intl.NumberFormat('en-IN').format(value)` and prepends ₹
- Positive net → emerald, negative net → red (same as signals percent_above pattern)

### Block Deal Title Formatting (frontend)
Server returns `traded_volume`, `price`, `traded_value` as raw numbers. Frontend constructs the display string:
```ts
const fmt = new Intl.NumberFormat('en-IN')
const title = `Block: ${fmt.format(item.traded_volume)} shares @ ₹${fmt.format(item.price)} (₹${fmt.format(item.traded_value)})`
```

## Indexes (migration file: `engine/db/migrations/004_news_indexes.sql`)

```sql
-- Announcements: feed query by date (range query), market-moving filter
CREATE INDEX IF NOT EXISTS idx_ann_dt ON nse_announcements(announcement_dt DESC);
CREATE INDEX IF NOT EXISTS idx_ann_symbol ON nse_announcements(symbol);
CREATE INDEX IF NOT EXISTS idx_ann_market_moving ON nse_announcements(is_market_moving)
  WHERE is_market_moving = true;

-- Block deals: feed query by date (small table, but consistent)
CREATE INDEX IF NOT EXISTS idx_block_deals_date ON nse_block_deals(deal_date DESC);

-- Board meetings: upcoming query
CREATE INDEX IF NOT EXISTS idx_board_meetings_date ON nse_board_meetings(meeting_date);

-- Corporate actions: upcoming query
CREATE INDEX IF NOT EXISTS idx_corp_actions_exdate ON nse_corporate_actions(ex_date);

-- Insider trading: THE CRITICAL INDEXES for 2.99M rows
-- Aggregation query: needs transaction_date for range scan + symbol for GROUP BY
CREATE INDEX IF NOT EXISTS idx_insider_txn_date ON nse_insider_trading(transaction_date DESC);
-- Drill-down query: symbol + date range
CREATE INDEX IF NOT EXISTS idx_insider_symbol_date ON nse_insider_trading(symbol, transaction_date DESC);
-- Covering index for aggregation: avoid heap fetches on the hot path
CREATE INDEX IF NOT EXISTS idx_insider_agg ON nse_insider_trading(transaction_date, symbol, acquisition_mode, value)
  WHERE value IS NOT NULL AND value > 0;
```

**After creating indexes, run:** `ANALYZE nse_insider_trading;` to update planner statistics.

**Performance validation before shipping — run for ALL three periods:**
```sql
-- 7-day window
EXPLAIN ANALYZE SELECT symbol, SUM(value), COUNT(*)
FROM nse_insider_trading
WHERE transaction_date >= CURRENT_DATE - 7
  AND value IS NOT NULL AND value > 0
GROUP BY symbol ORDER BY SUM(value) DESC LIMIT 20;

-- 30-day window
EXPLAIN ANALYZE SELECT symbol, SUM(value), COUNT(*)
FROM nse_insider_trading
WHERE transaction_date >= CURRENT_DATE - 30
  AND value IS NOT NULL AND value > 0
GROUP BY symbol ORDER BY SUM(value) DESC LIMIT 20;

-- 90-day window
EXPLAIN ANALYZE SELECT symbol, SUM(value), COUNT(*)
FROM nse_insider_trading
WHERE transaction_date >= CURRENT_DATE - 90
  AND value IS NOT NULL AND value > 0
GROUP BY symbol ORDER BY SUM(value) DESC LIMIT 20;

-- All must show Index Scan on idx_insider_agg, execution < 500ms (7d/30d) / < 2s (90d)
```

## Implementation Order

1. **Migration** — Create `004_news_indexes.sql`, run it, verify with `EXPLAIN ANALYZE` for all three periods
2. **Server route** — `server/routes/news.ts` with all 5 endpoints (feed, upcoming, insider-activity, summary, detail), register in `server/index.ts`
3. **Types + hooks** — `types.ts` and `use-news.ts`
4. **Feed tab** — `news-page.tsx` + `news-feed.tsx` + `news-card.tsx` (get the primary view working first)
5. **Summary cards** — Wire up `/summary` counts in the page header
6. **Upcoming tab** — `upcoming-events.tsx`
7. **Insider tab** — `insider-activity.tsx`
8. **Sidebar + route** — Register in sidebar-data.ts and create route file

## Issues Fixed from Draft

1. **React Query, not "native fetch + useEffect"** — The codebase uses `@tanstack/react-query` v5. The draft's hook comment said "SWR/React Query" which was half-right, but the project context was wrong about it not being installed.
2. **Insider trading excluded from unified feed** — The 2.99M-row table should NOT be in the `/feed` UNION. It has no time-of-day component and is only useful in aggregate. Own tab.
3. **Board meetings + corporate actions excluded from feed** — They have DATE-only columns, no intraday relevance. Moved to Upcoming tab.
4. **Block deals inline, not separate tab** — Only 24 rows. Doesn't justify a tab.
5. **Actual column names added** — Draft used generic names. Refined plan references real schema columns.
6. **Covering index for insider aggregation** — A basic `(transaction_date)` index isn't enough for the GROUP BY + SUM aggregation on 2.99M rows. Added a covering index.
7. **Buy/sell classification clarified** — No explicit buy/sell column exists. Must derive from `acquisition_mode` text via ILIKE.
8. **No after-hours polling** — News data doesn't change after hours. Removed the 5-min after-hours refresh.
9. **`isMarketOpen()` reuse** — Use shared `market-hours.ts` instead of duplicating (signals has this bug).
10. **File structure simplified** — Dropped unnecessary separate components (filters, summary cards, block deals table) to match the signals feature's flatter structure.
11. **Route + sidebar registration steps added** — Draft was missing where to register `app.route()` and sidebar nav entry.
12. **Migration file path specified** — `003_news_indexes.sql` instead of loose SQL.
13. **Summary endpoint excludes insider daily count** — COUNT(*) on 2.99M rows filtered by date is expensive and not meaningful. Removed.
14. **Pagination via "Load more" button** — Draft mentioned offset but not the UI pattern. Clarified.
15. **`EXPLAIN ANALYZE` validation step** — Added explicit performance gate before shipping.

## Not in V1 (future)

- Real-time WebSocket push for new announcements
- Watchlist-aware highlighting (badge if symbol is in active watchlist)
- Sentiment analysis on announcement text
- Price impact correlation (announcement time → price movement)
- Notification/alert on market-moving announcements
- PDF content extraction from attachment URLs
- Bar chart for insider activity (table-first in V1)
- Symbol autocomplete (plain text input in V1)
- Pre-computed `transaction_type` column for insider trading (V2 — eliminates CASE WHEN classification at query time)
- Materialized view for insider aggregation (V2 alternative to pre-computed column)

---

## Changelog (post-review revision, 2026-03-25)

Changes addressing review at `plans/REVIEW_news_page_codex.md`:

### BLOCKERS fixed
1. **Migration 003 collision → renamed to 004** — `003_is_tradeable.sql` already exists. All references updated to `004_news_indexes.sql`. (Review 1.1)
2. **Broken pagination → UNION ALL** — Replaced two-query-merge-in-JS approach with a single `UNION ALL` SQL query. Postgres handles sorting and `LIMIT/OFFSET` correctly across both sources. Block deals (~24/day) are trivially small in the union. (Review 1.5)

### HIGH SEVERITY fixed
3. **`announcement_dt::date` cast → range query** — Replaced `WHERE announcement_dt::date = $1` with `WHERE announcement_dt >= $1::date AND announcement_dt < $1::date + interval '1 day'` in both the feed query and summary CTE. This allows the `idx_ann_dt` index to be used. (Review 3.3)
4. **No validation on `days` param → whitelist** — Added explicit validation: `days` must be one of `[7, 30, 90]`, reject with 400. Code example provided. (Review 2.1)
5. **ILIKE leading wildcard → exact CASE WHEN matches** — Replaced `ILIKE '%purchase%'` etc. with `acquisition_mode IN ('Market Purchase', 'Market Sale', ...)` using exact string matches. Documented known values. Unknown modes fall to conservative default (0 for buy/sell). V2 plan for pre-computed `transaction_type` column documented in "Not in V1" section. (Review 3.1)
6. **Load more → useInfiniteQuery** — Replaced `useQuery` with `useInfiniteQuery` for the feed hook. Added `getNextPageParam` using `has_more` from the `LIMIT+1` pattern. Also applies to insider drill-down. (Review 4.4)

### MEDIUM fixed
7. **raw_json not in response → detail endpoint** — Added `GET /api/news/:source/:id` detail endpoint. Returns `raw_json` on demand. Uses table name whitelist (not user input interpolation). Added `useNewsDetail` hook with `enabled: false` + `staleTime: Infinity`. (Review 4.5)
8. **total count → LIMIT+1 pattern** — Removed `"total"` from response shape. Uses `LIMIT + 1` to fetch one extra row; `has_more = true` if extra row exists, strip before responding. Applied to feed and insider drill-down. (Review 1.4)

### Additional fixes from review
9. **IST timezone** — Default date computed in IST (`toLocaleString` with `Asia/Kolkata` timezone). Upcoming queries use `(NOW() AT TIME ZONE 'Asia/Kolkata')::date` instead of bare `CURRENT_DATE`. Added dedicated "Timezone Handling" section. (Review 2.5, 2.6)
10. **Summary endpoint → CTE** — Replaced 5 separate COUNT queries with a single CTE query. Also uses range query for announcement_dt and `AT TIME ZONE` for upcoming dates. (Review 3.4)
11. **Polling 30s → 120s** — Changed `refetchInterval` from 30_000 to 120_000 (120s) to match the collector's 2-minute cadence. Applied to both `useNewsFeed` and `useNewsSummary`. (Review 3.5)
12. **Parameter index shifting → dynamic idx counter** — Added explicit guidance to use a dynamic `idx` counter for parameterized query building (same as signals route). Includes code example. (Review 4.2)
13. **FeedSource type widened** — Renamed `NewsSource` to `FeedSource` and added all 5 source values: `'announcement' | 'block_deal' | 'board_meeting' | 'corporate_action' | 'insider_trading'`. (Review 5.2)
14. **Format currency in frontend, not SQL** — Removed `format()` from block deals SQL query. Server returns raw `traded_volume`, `price`, `traded_value` numbers. Frontend formats with `Intl.NumberFormat('en-IN')`. Added code example. Updated `FeedItem` type to include nullable numeric fields. (Review 5.4)
15. **Fetch functions documented** — Added full `fetchFeed`, `fetchSummary`, `fetchUpcoming`, `fetchInsider`, `fetchDetail` implementations in the hooks section. (Review 4.3)
16. **Date validation** — Added `YYYY-MM-DD` regex validation for `date` param, reject with 400. (Review 2.2)
17. **Limit cap on /upcoming** — Added `max: 200` to upcoming endpoint. (Review 2.3)
18. **Empty symbol filter footgun** — Noted in Top Bar section: convert `""` to `undefined` before passing to hooks. (Review 2.4)
19. **EXPLAIN ANALYZE for all periods** — Expanded performance validation to run for 7d, 30d, AND 90d windows (was only 7d). Added separate timing expectations. (Review 3.2)
20. **Error handling pattern clarified** — Removed "try-catch" reference. Now explicitly states: follow signals pattern, let errors propagate to Hono's default handler. (Review 1.2)
21. **Response shape departure acknowledged** — Added note that `{ items, has_more }` wrapper is an intentional departure from signals' flat array pattern, with rationale. (Review 1.3)
22. **components/ directory divergence acknowledged** — Added structural note explaining why news uses `components/` subdirectory while signals doesn't. (Review 5.6)
23. **Week grouping specified** — Added `date-fns` `startOfWeek` / `isSameWeek` with `{ weekStartsOn: 1 }` (Monday) for upcoming tab week grouping. (Review 4.6)
