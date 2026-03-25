# PLAN: Universe Explorer Dashboard Page

**Status:** draft
**Created:** 2026-03-23
**Author:** Gxozt (with input from Codex + Gemx)
**Assignee:** Coder

---

## Overview

A new interactive page on the AlgoTrix dashboard that lets Ricky explore, filter, and visualize the NSE stock universe in real-time. Visual-first design — charts that are unique, informative, and beautiful.

## Architecture

### Data Flow
```
[atdb PostgreSQL] → [Hono API: GET /api/universe/metrics] → [Frontend: client-side filtering]
```

- **One API call on page load** — returns all ~2,400 stocks with their metrics
- **All filtering, scoring, tier assignment happens in-browser** (instant, zero latency)
- Modern JS can re-filter 2,400 items in <5ms per slider change

### API Endpoint

**`GET /api/universe/metrics`**

Returns:
```json
{
  "asOf": "2026-03-20",
  "tradingDays": 20,
  "stocks": [
    {
      "isin": "INE002A01018",
      "symbol": "RELIANCE",
      "lastPrice": 2498.50,
      "avgVolume20d": 5200000,
      "avgTurnover20d": 1300000000,
      "tradedDays": 20,
      "series": "EQ",
      "sector": "Energy",
      "marketCap": 1690000,
      "indexMemberships": ["NIFTY_50", "NIFTY_100", "NIFTY_200", "NIFTY_500"],
      "isFnO": true,
      "isSuspended": false,
      "isASM": false,
      "isGSM": false
    }
  ]
}
```

SQL for the endpoint:
```sql
WITH recent_dates AS (
  SELECT DISTINCT date FROM nse_cm_bhavcopy ORDER BY date DESC LIMIT 20
),
last_day AS (
  SELECT MAX(date) as dt FROM recent_dates
)
SELECT
  lp.isin,
  lp.close AS last_price,
  a.avg_vol,
  a.avg_turnover,
  a.days_traded
FROM (
  SELECT isin, close FROM nse_cm_bhavcopy WHERE date = (SELECT dt FROM last_day)
) lp
JOIN (
  SELECT isin, AVG(volume) avg_vol, AVG(traded_value) avg_turnover, COUNT(DISTINCT date) days_traded
  FROM nse_cm_bhavcopy WHERE date IN (SELECT date FROM recent_dates)
  GROUP BY isin
) a ON lp.isin = a.isin
```

Note: series, sector, indexMemberships, isFnO, isASM, isGSM — we may not have all of these in nse_cm_bhavcopy yet. For V1, use what's available (price, volume, turnover, traded days). Add the rest as we build the metadata cache table.

---

## Page Layout: Universe Explorer

### Design Philosophy
- **Dark theme** (matches existing dashboard)
- **Visual-first** — charts dominate, controls are clean and minimal
- **Interactive** — every slider change updates all charts simultaneously
- **Unique charts** — not the standard boring pie/bar combos

### Layout Structure

```
┌──────────────────────────────────────────────────────────────┐
│  HEADER: "Universe Explorer"          [date] [Preset ▼]     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  FILTER BAR (horizontal, compact)                            │
│  Price [●━━━━━━━━] Vol [━━━●━━━━━] Turnover [━━━━●━━━]     │
│  Min Days [━━━━━━●] Series [EQ ✓] [BE ✗]  [Apply Preset ▼] │
│                                                              │
├────────────────────────────┬─────────────────────────────────┤
│                            │                                 │
│  SANKEY DIAGRAM            │  BUBBLE CHART                   │
│  (filter flow)             │  (turnover vs volume vs mcap)   │
│                            │                                 │
│  Raw ━━━━━━━━━━━━━━━━━━►  │     ○    ●                      │
│       ┗━━ Price ━━━━━━━►  │  ●    ○     ◉                   │
│            ┗━━ Vol ━━━►   │       ◉  ○                      │
│                 ┗━━ TO ►  │  color = tier (D5/D30/D50)      │
│                            │  size = market cap              │
│                            │  x = turnover, y = volume       │
│  (40% width)               │  (60% width)                    │
├────────────────────────────┴─────────────────────────────────┤
│                                                              │
│  SECTOR HEATMAP                                              │
│  (treemap — box size = stock count, color intensity = avg    │
│   turnover, grouped by sector)                               │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  DEPTH ALLOCATION STRIP                                      │
│  ┌──┬────────────────────┬──────────────────────────────┐   │
│  │D50│    D30 (250)       │        D5 (604)              │   │
│  │ 5 │                    │                              │   │
│  └──┴────────────────────┴──────────────────────────────┘   │
│  (horizontal segmented bar with stock count labels)          │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  SUMMARY STAT CARDS (horizontal row)                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ Universe  │ │ Eligible  │ │ D30 Fill  │ │ D50 Fill │       │
│  │   2,432   │ │    859    │ │  250/250  │ │   5/5    │       │
│  │  total    │ │  35.3%    │ │   100%    │ │  100%    │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  STOCK TABLE (TanStack Table)                                │
│  Symbol | Price | Avg Vol | Turnover | Tier | Status         │
│  RELIANCE | 2,498 | 5.2M | 130Cr | D30 | Pass              │
│  TCS | 3,890 | 1.8M | 70Cr | D30 | Pass                    │
│  [Search] [Export CSV] [Show: All/Pass/Fail]                 │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## Chart Specifications

### 1. Sankey Diagram (Filter Flow)
**Library:** d3-sankey (custom React wrapper)

Shows stocks flowing from raw universe through each filter stage:
- Left: Raw universe (2,432)
- Each filter is a node that splits the flow into pass/fail
- Failed stocks flow into a "rejected" stream (grayed out)
- Surviving stocks flow to the next filter
- Final output splits into depth tiers (D5 / D30 / D50)

Colors:
- Pass flow: gradient from blue to green
- Fail flow: muted red/gray
- Tier colors: D50 = red, D30 = amber, D5 = blue

Interactive: hover a flow to highlight which stocks are in it.

### 2. Bubble Chart (Multi-Dimensional Scatter)
**Library:** Recharts ScatterChart with custom bubble renderer

- **X-axis:** Avg daily turnover (log scale)
- **Y-axis:** Avg daily volume (log scale)
- **Bubble size:** Market cap (or last price as proxy)
- **Bubble color:** Depth tier assignment
  - D50: red glow
  - D30: amber
  - D5: blue
  - Failed: gray, 30% opacity
- **Hover:** Tooltip with symbol, all metrics, pass/fail reasons
- **Click:** Highlights stock in table below, scrolls to it

Log scale is important — turnover ranges from 1Cr to 500Cr+.

### 3. Sector Treemap
**Library:** Recharts Treemap or d3-treemap

- Each box = one sector
- Box size = number of eligible stocks in that sector
- Color intensity = average turnover of sector (darker = higher value)
- Click sector = filter table to that sector
- Show stock count label inside each box
- Hover: sector name, stock count, avg turnover, avg volume

### 4. Depth Allocation Strip
- Single horizontal bar, segmented into 3 tiers
- D50 (red, thin) | D30 (amber, medium) | D5 (blue, wide)
- Labels inside each segment: stock count + broker name
- Proportional to stock count

### 5. Summary Stat Cards (shadcn/ui Card)
4 cards in a row:
- **Total Universe** — raw count before filters
- **Eligible** — count after all filters, with % of total
- **D30 Fill** — how many D30 slots used out of 250
- **D50 Fill** — how many D50 slots used out of 5

Color coding: green under capacity, amber near full, red over.

### 6. Stock Table (TanStack Table + shadcn/ui)
Columns: Symbol, Last Price, Avg Volume, Avg Turnover, Traded Days, Sector, Depth Tier, Status
Features: Search, sort, filter by pass/fail/tier/sector, export CSV, click to expand details.

---

## Filter Controls (shadcn/ui)

```
Price Range:     [50 ----●-----------● 5000]  (dual-thumb slider)
Avg Volume:      [0 ---------●--------- 10M]  (log-scale slider)
Avg Turnover:    [0 -----------●------- 500Cr] (log-scale slider)
Min Traded Days: [0 --------------●--- 20]     (linear slider)
Series:          [EQ ✓] [BE ✗] [BZ ✗] [SM ✗]  (toggle chips)
```

### Presets
- **Conservative:** Price 100-3000, Vol 500K+, TO 10Cr+, 19/20 days
- **Balanced:** Price 50-5000, Vol 100K+, TO 5Cr+, 18/20 days
- **Broad:** Price 20-10000, Vol 25K+, TO 1Cr+, 15/20 days
- **D30 Target:** Tuned to produce ~250 stocks

### Behavior
- 200ms debounce on slider changes
- All charts update simultaneously
- Filter state persisted in URL params

---

## Files to Create/Modify

### New Files
```
dashboard/src/pages/UniverseExplorer.tsx
dashboard/src/components/universe/FilterBar.tsx
dashboard/src/components/universe/SankeyFlow.tsx
dashboard/src/components/universe/BubbleChart.tsx
dashboard/src/components/universe/SectorTreemap.tsx
dashboard/src/components/universe/DepthStrip.tsx
dashboard/src/components/universe/StatCards.tsx
dashboard/src/components/universe/StockTable.tsx
dashboard/src/components/universe/useUniverseData.ts
dashboard/src/components/universe/universeFilters.ts
dashboard/src/components/universe/types.ts
server/src/routes/universe.ts
```

### Modified Files
```
dashboard/src/App.tsx (add route)
dashboard/src/components/layout/Sidebar.tsx (add nav item)
```

### Dependencies
```bash
npm install recharts d3-sankey @types/d3-sankey d3-scale @tanstack/react-table
# Check package.json first — some may already exist
```

---

## V1 Scope
- [ ] API endpoint /api/universe/metrics
- [ ] Filter bar with sliders + series toggles + presets
- [ ] Sankey diagram
- [ ] Bubble chart
- [ ] Sector treemap
- [ ] Depth allocation strip
- [ ] Summary stat cards
- [ ] Stock table with search, sort, filter, export
- [ ] Client-side filtering
- [ ] Dark theme

## V2 Scope (Later)
- [ ] Additional metadata (sector, index, F&O, ASM/GSM)
- [ ] Manual overrides (force include/exclude, pin to tier)
- [ ] Saved custom presets
- [ ] Compare mode (two configs side-by-side)
- [ ] Historical universe snapshots
- [ ] Progressive depth promotion controls

---

## Notes
- Dashboard: port 5180, API: port 3001
- PM2 ecosystem: /home/me/projects/algotrix-go/ecosystem.config.cjs
- DB: PGPASSWORD=algotrix psql -h localhost -U me -d atdb
- Dashboard source: /home/me/projects/algotrix-go/dashboard/
- Server source: /home/me/projects/algotrix-go/server/
