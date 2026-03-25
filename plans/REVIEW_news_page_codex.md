# Code Review: PLAN_news_page.md

**Reviewer:** Codex
**Date:** 2026-03-25
**Verdict:** Needs revision before implementation. Several real bugs, one migration collision, and a query design that will fall over on the insider table.

---

## 1. Architecture Concerns

### 1.1 Migration file collision — `003` is already taken
`engine/db/migrations/003_is_tradeable.sql` already exists. The plan specifies `003_news_indexes.sql`. This will either be skipped or collide depending on the migration runner. **Must be `004_news_indexes.sql`.**

### 1.2 Signals route does NOT use try-catch — plan invents a non-existent pattern
The plan says: *"Wrap each handler in try-catch returning `c.json({ error }, 500)`."* But `server/routes/signals.ts` — the pattern the plan explicitly claims to follow — has **zero try-catch blocks**. It lets errors propagate to Hono's default handler. Some other routes (regime, watchlist, backtest) do use try-catch, but the reference pattern (signals) doesn't. The plan should pick one and be explicit about why.

### 1.3 Response shape diverges from signals pattern
Signals returns a flat array: `c.json(result.rows)`. The news feed returns a wrapped object: `{ items, total, has_more }`. This is arguably better, but it's **not** the "signals pattern" the plan keeps referencing. The plan should acknowledge this is a deliberate departure, or all existing routes will eventually need migration to the wrapped format. Consistency matters more than being right on one endpoint.

### 1.4 `total` count requires a second query — plan doesn't show it
The response shape includes `"total": 45`, but neither the announcements query nor the block deals query includes a `COUNT(*)`. To return `total` and `has_more` you need either:
- A separate `COUNT(*)` query (doubles DB round-trips)
- `SQL_CALC_FOUND_ROWS` (MySQL, not Postgres)
- Fetch `limit + 1` rows and derive `has_more` from the extra row

The plan hand-waves this. The fetch-one-extra pattern is the correct approach for Postgres — document it.

### 1.5 Two queries merged in JS with separate LIMIT/OFFSET is broken pagination
The feed endpoint runs two queries (announcements + block deals), each with their own `LIMIT $N OFFSET $M`, then merges and re-sorts in JS. This means:
- **Page 1:** 50 announcements + 50 block deals → sorted → truncated to 50 → user sees 50
- **Page 2 (offset=50):** Both queries skip 50 rows independently → you skip 50 announcements AND 50 block deals, but the user only consumed ~48 announcements and ~2 block deals from page 1

**The offset is applied per-source, not per-merged-result.** Pagination will skip or duplicate rows. Either use a `UNION ALL` in SQL (preferred — let Postgres sort and paginate), or fetch all block deals (only ~24 rows for any date) and only paginate announcements.

---

## 2. Missing Edge Cases

### 2.1 No input validation on `days` parameter
The insider endpoint accepts `days: 7|30|90` but the plan shows no validation. A caller can pass `days=3650` and trigger a full table scan on 2.99M rows. Must whitelist to `[7, 30, 90]` and reject anything else with 400.

### 2.2 No input validation on `date` parameter
`date` is passed directly into the SQL query. While parameterized queries prevent injection, an invalid date string (e.g., `date=hello`) will cause a Postgres cast error that bubbles up as an unhandled 500. Validate format server-side.

### 2.3 No `limit` cap on `/upcoming`
Feed has `max: 200`. Upcoming has no max — a client can request `limit=100000`. The default is 100 but there's no cap mentioned.

### 2.4 Empty symbol filter sends empty string, not undefined
The signals page uses `screenerFilter || undefined` to convert empty string to undefined. The plan's symbol filter is described as a text input with debounce, but the hook signatures use `symbol?: string`. If the frontend passes `""` instead of `undefined`, the query will filter for `symbol = ''` and return nothing. The plan should note this footgun explicitly since it bit the signals page too.

### 2.5 Timezone ambiguity on "today"
The server uses `new Date().toISOString().slice(0, 10)` for "today" (signals pattern). This is UTC. If the server runs in UTC and a user hits the API at 22:00 IST (16:30 UTC), "today" on the server is still the correct IST date. But at 00:30 IST (19:00 UTC previous day), the server's "today" is yesterday in IST. NSE data uses IST dates. The default date should be computed in IST, not UTC. The existing signals route has this same bug — doesn't mean the new route should inherit it.

### 2.6 `CURRENT_DATE` in upcoming queries is server timezone-dependent
The upcoming queries use `WHERE meeting_date >= CURRENT_DATE`. Postgres `CURRENT_DATE` uses the session timezone. If the Postgres server timezone is UTC, this will be wrong for IST dates near midnight. Should use `CURRENT_DATE AT TIME ZONE 'Asia/Kolkata'` or pass the date explicitly.

---

## 3. Performance Risks

### 3.1 Insider aggregation query will be slow even with the covering index
The covering index `idx_insider_agg` is on `(transaction_date, symbol, acquisition_mode, value)` with a partial filter `WHERE value IS NOT NULL AND value > 0`. This helps, but the query still does:
- `ILIKE '%purchase%'` — **ILIKE with leading wildcard kills index usage on `acquisition_mode`**. The covering index includes `acquisition_mode` for index-only scans, but the `ILIKE '%purchase%'` still requires a sequential comparison on every matched row.
- `GROUP BY symbol` + `ORDER BY ABS(net_value) DESC` on potentially hundreds of thousands of rows (90-day window)

**Recommendation:** Pre-compute a `transaction_type` column (`BUY`/`SELL`) during collection and index on it. The ILIKE-based classification is a query-time cost that should be a write-time cost. Alternatively, create a materialized view refreshed on a schedule.

### 3.2 90-day window on 2.99M rows — how many rows is that?
The plan doesn't estimate how many rows fall in a 90-day window. If data collection started recently, 90 days might be most of the 2.99M rows. The `EXPLAIN ANALYZE` validation step is good, but should be run for **all three periods** (7d, 30d, 90d), not just 7d as shown.

### 3.3 `announcement_dt::date = $1` prevents index usage
The feed query casts `announcement_dt` to date: `WHERE announcement_dt::date = $1`. This applies a function to the column, which **prevents the `idx_ann_dt` index from being used** (the index is on the raw `announcement_dt` column, not the cast result). Should use a range query instead:
```sql
WHERE announcement_dt >= $1::date
  AND announcement_dt < ($1::date + interval '1 day')
```

### 3.4 Summary endpoint fires 5 sequential-looking COUNT queries
The plan shows 5 COUNT queries but doesn't explicitly say `Promise.all()` (it does for other endpoints). If run sequentially, this is 5 round-trips for a summary bar. Should be explicit about parallel execution, or better, combine into a single query with CTEs.

### 3.5 30-second polling is aggressive for a page with 4 endpoints
During market hours, `useNewsFeed` and `useNewsSummary` both poll at 30s. If the user has the Feed tab open, that's 2 requests every 30 seconds. If they switch to Insider tab, those queries stay mounted (React Query default). Over a trading session (6h 15m), that's ~1,500 requests just from this page. The signals page polls at 10s which is already aggressive — news data updating every 2min doesn't need 30s polling. 60s or 120s would be more appropriate and match the collector cadence.

---

## 4. Implementation Gaps

### 4.1 `isMarketOpen()` exists but `use-signals.ts` doesn't use it
The plan says: *"Use shared `market-hours.ts` instead of duplicating (signals has this bug)."* This is correct — `use-signals.ts` defines its own `isMarketHours()` inline instead of importing from `@/lib/market-hours.ts`. But the plan doesn't include a task to fix the signals bug. If someone implements the news page using the shared util, and later someone "aligns" the two, they might break signals. Add a task to fix signals too, or at minimum note the inconsistency.

### 4.2 Dynamic query building with string interpolation
The plan shows query building with template literals:
```ts
${symbol ? 'AND symbol = $2' : ''}
```
This works but the parameter index (`$2`) is hardcoded while being conditionally included. When multiple optional params are present (symbol + marketMoving), the indices shift. The signals route handles this correctly with a dynamic `idx` counter — the plan's pseudocode doesn't. A developer following the plan literally will have parameter index mismatches.

### 4.3 No `fetchFeed`, `fetchSummary`, `fetchUpcoming`, `fetchInsider` implementations
`use-news.ts` shows the hooks but references `fetchFeed()`, `fetchSummary()`, etc. without showing their implementations. The signals hook file (`use-signals.ts`) includes these fetch functions. A developer will need to write them, and they need to handle query param serialization, but the plan doesn't specify the URL construction. Minor but will cause a "wait, how do I…" moment.

### 4.4 "Load more" pagination state not addressed
The plan says "Load more button, 50 rows, load 50 more on click" but doesn't address how this works with React Query. React Query's `useQuery` replaces data on refetch. For append-style pagination you need either:
- `useInfiniteQuery` (the correct approach for "load more")
- Manual state accumulation outside React Query

The plan should specify `useInfiniteQuery` with `getNextPageParam`.

### 4.5 Expandable row detail — no endpoint for `raw_json`
The feed response shape doesn't include `raw_json`. The plan says clicking a row shows `raw_json` in a `<pre>` block. Either:
- Include `raw_json` in the feed response (bloats every row with potentially large JSON)
- Add a `GET /api/news/:id` detail endpoint (not in the plan)

Neither option is addressed.

### 4.6 Week grouping logic for Upcoming tab is non-trivial
"Grouped by week (This Week / Next Week / Later)" — the plan doesn't specify how to determine week boundaries. ISO weeks? Monday start? Sunday start? IST-based? This is a UI detail that will cause bikeshedding if not specified. Should use `date-fns` `startOfWeek` / `isSameWeek` with `{ weekStartsOn: 1 }` (Monday) since this is for Indian markets.

---

## 5. Nitpicks

### 5.1 Route naming: `/api/news` vs source naming
The plan uses `/api/news` but 3 of 5 data sources aren't "news" — they're corporate events (board meetings, corporate actions, insider trading). The route name is fine for the UI label but slightly misleading as an API namespace. `/api/corporate-events` would be more accurate, but this is bikeshed-level.

### 5.2 `NewsSource` type is too narrow
```ts
export type NewsSource = 'announcement' | 'block_deal'
```
The upcoming and insider endpoints return items with source `'board_meeting'`, `'corporate_action'`, and insider data. These aren't covered by `NewsSource`. Either widen the type or rename it to `FeedSource`.

### 5.3 Inconsistent source naming: underscore vs hyphen
API query param: `source=block_deals` (plural, underscore). Response field: `source: "block_deal"` (singular, underscore). Query key: `'news-feed'` (hyphen). Pick one convention.

### 5.4 `format()` in SQL is Postgres-specific and locale-dependent
```sql
format('Block: %s shares @ ₹%s (₹%s)', traded_volume, price, traded_value)
```
This constructs display strings in SQL. Formatting (₹ symbol, number formatting) should be a frontend concern. Return the raw numbers and format in the UI with `Intl.NumberFormat('en-IN')`.

### 5.5 Plan references `Skeleton` count of 5 but signals uses `Array.from({ length: 5 })`
Not a bug, but the plan describes skeletons in prose while the actual pattern uses `Array.from`. Minor — just follow the signals code, not the plan description.

### 5.6 Missing `components/` directory in signals — plan adds one for news
Signals has a flat structure (4 files, no `components/` subdirectory). The news plan introduces `components/` with 4 sub-components. This is a structural divergence from the "signals pattern" the plan keeps citing. For 3 tabs, it's probably justified, but should be called out as intentional.

---

## Summary of Blockers (must fix before implementation)

| # | Issue | Severity |
|---|-------|----------|
| 1.1 | Migration `003` collision | **Blocker** — will fail or overwrite |
| 1.5 | Broken pagination with dual-query merge | **Blocker** — will produce wrong results |
| 3.3 | `::date` cast prevents index usage | **High** — feed query will seq-scan |
| 2.1 | No validation on `days` param | **High** — full table scan possible |
| 3.1 | ILIKE with leading wildcard on 2.99M rows | **High** — slow aggregation |
| 4.4 | "Load more" incompatible with `useQuery` | **High** — needs `useInfiniteQuery` |
| 4.5 | `raw_json` not in response but needed for expand | **Medium** — feature won't work |
| 1.4 | `total` count not computed anywhere | **Medium** — response shape is a lie |
