# Phase 1: Data Layer — Regime Classifier

**Status:** ✅ Complete (March 21, 2026)
**Purpose:** Build the comprehensive market data foundation required for regime classification and prediction.

---

## What Is Phase 1?

Phase 1 is the **data infrastructure layer** of the Regime Classifier. Before we can score, classify, or predict market regimes, we need deep historical data across multiple dimensions. Phase 1 answers: *"What data do we have, where does it come from, and how far back does it go?"*

This phase delivers:
1. **7 normalized data tables** in PostgreSQL (atdb) covering 6+ years of market history
2. **6 Go feed handlers** in the daily pipeline for automated ongoing collection
3. **4 Python backfill scripts** for historical gap-filling
4. **A registry-based pipeline architecture** that makes adding new feeds trivial

---

## The Data We Collected

### Core Market Data (Jan 2020 → Mar 2026)

| Table | Rows | Date Range | Source | What It Contains |
|-------|------|------------|--------|------------------|
| `nse_cm_bhavcopy` | 2,769,990 | 2020-01-01 → 2026-03-19 | NSE Archives | Daily OHLCV for all NSE equities (~2,342 stocks × 1,538 trading days) |
| `nse_indices_daily` | 164,435 | 2020-01-01 → 2026-03-19 | NSE Archives | OHLCV + advances/declines/unchanged for all NSE indices |
| `nse_fo_bhavcopy` | 72,374,372 | 2020-01-01 → 2026-03-19 | NSE Archives | Full F&O bhavcopy — all contracts, OI, settlement prices |

### Institutional Flow Data (Jan 2020 → Mar 2026)

| Table | Rows | Date Range | Source | What It Contains |
|-------|------|------------|--------|------------------|
| `nse_fii_dii_participant` | 1,538 | 2020-01-01 → 2026-03-20 | NSE Archives | Participant-wise OI and volume (FII, DII, Client, Pro) for futures and options — index + stock |

### GIFT Nifty / NSE IX (Jul 2023 → Mar 2026)

| Table | Rows | Date Range | Source | What It Contains |
|-------|------|------------|--------|------------------|
| `nseix_settlement_prices` | 5,341,497 | 2023-07-14 → 2026-03-19 | NSE IX API | Daily settlement prices for all GIFT Nifty derivatives (futures, options, all expiries/strikes) |
| `nseix_combined_oi` | 5,259,824 | 2023-07-14 → 2026-03-19 | NSE IX API | Combined OI with delta for all GIFT derivatives |

### Global Cues (Jan 2020 → Mar 2026)

| Table | Rows | Date Range | Source | What It Contains |
|-------|------|------------|--------|------------------|
| `global_market_daily` | 4,688 | 2020-01-02 → 2026-03-20 | Yahoo Finance | S&P 500 (^GSPC), US Dollar Index (DX-Y.NYB), US 10Y Yield (^TNX) — OHLCV |

**Total: ~86 million rows across 7 tables, spanning 1,538 NSE trading days.**

---

## Why These Specific Data Sources?

The regime classifier scores the market across **5 dimensions**. Each data source maps to one or more:

| Dimension | What It Measures | Primary Data Sources |
|-----------|-----------------|---------------------|
| **Volatility** | Market fear/uncertainty | India VIX (from `nse_indices_daily`), Nifty intraday range |
| **Trend** | Directional strength | Nifty OHLCV, moving averages (from `nse_indices_daily`) |
| **Participation** | Market breadth & conviction | Advances/declines (from `nse_indices_daily`), delivery % (from `nse_cm_bhavcopy`) |
| **Sentiment** | Put-call ratio, skew | F&O OI and volumes (from `nse_fo_bhavcopy`), GIFT OI (from `nseix_combined_oi`) |
| **Institutional Flow** | Smart money positioning | FII/DII net positions (from `nse_fii_dii_participant`) |

**Leading indicators for prediction** (next-day regime forecast):
- FII/DII flow delta → institutional positioning shift
- GIFT Nifty overnight settlement → pre-market directional signal (from `nseix_settlement_prices`)
- Global cues (S&P 500, DXY, US 10Y) → overnight risk-on/off signal (from `global_market_daily`)
- VIX rate of change → fear acceleration
- Breadth momentum → regime shift early warning

---

## Pipeline Architecture

### Go Feed Handlers (Daily Automated Collection)

Located in `engine/data/nse/`:

| Handler | File | Feed | Notes |
|---------|------|------|-------|
| CM Bhavcopy | `bhavcopy.go` | NSE equity bhavcopy | ~2,342 stocks/day |
| Indices | `indices.go` | NSE index data | All indices + breadth stats |
| F&O Bhavcopy | `fo_bhavcopy.go` | NSE F&O bhavcopy | Full derivative chain |
| FII/DII Participant | `fii_dii_participant.go` | NSE participant OI | FII/DII/Client/Pro breakdown |
| NSE IX Settlement | `nseix_settlement.go` | GIFT Nifty settlement | Requires `Referer: nseix.com` header |
| NSE IX Combined OI | `nseix_combined_oi.go` | GIFT combined OI | Raw CSV (no header), variable-width |

All handlers use the **registry pattern** (`registry.go`):
- `FeedConfig` struct defines URL template, parser, table, date format, headers
- `fetchWithRetry` handles retries with custom headers
- Pipeline health endpoint exposes status of all 6 feeds
- Adding a new feed = one `FeedConfig` entry + one parser function

### Python Backfill Scripts

Located in `regime-classifier/scripts/`:

| Script | What It Backfills | Date Range |
|--------|-------------------|------------|
| `backfill_nse_history.py` | CM, Indices, F&O bhavcopy | Jan 2020 → Aug 2025 (old + new NSE formats) |
| `backfill_fii_dii.py` | FII/DII participant OI | Jan 2020 → Mar 2026 |
| `backfill_nseix.py` | NSE IX settlement + OI | Jul 2023 → Mar 2026 (via Go binary) |
| `backfill_global_cues.py` | S&P 500, DXY, US 10Y | Jan 2020 → present (yfinance) |

### Database

- **Database:** `atdb` on localhost PostgreSQL
- **Migrations:** `regime-classifier/migrations/003_regime_tables.sql`, `004_layer1_new_feeds.sql`
- **Credentials:** `PGPASSWORD=algotrix`, user `me`

---

## Prototype Results (Before Phase 2)

Two scoring prototypes were tested against this data to validate the approach:

### Market Score v1 (7 coincident indicators)
- 1,286 scored days
- Regime distribution: Bullish 33%, Neutral 29%, Bearish 38%
- **Predictive power: near zero** (Pearson r = -0.008)
- Coincident/lagging indicators describe the regime but don't predict it
- Mean-reversion signal found: most bearish quintile had highest next-day return (+0.186%)

### Market Score v2 (+ 6 leading indicators)
- Added: overnight gap, volume surge, breadth momentum, VIX rate of change, score momentum, mean-reversion contrarian
- Pearson r improved to +0.027, bullish hit rate ~59%
- Still not statistically significant (p=0.13) but directionally correct
- **Key insight:** leading indicators help, but FII/DII flows and GIFT Nifty (not yet integrated) are expected to add the most predictive value

---

## What Phase 1 Does NOT Include

- ❌ Feature computation (that's Phase 2)
- ❌ Regime scoring/labeling engine (Phase 2)
- ❌ Prediction model (Phase 3)
- ❌ Integration with the watchlist builder (Phase 4)
- ❌ VIX intraday data (only daily via indices)
- ❌ NSE IX bhavcopy (API returns 403 — not available)
- ❌ NSE IX volatility data (API returns 403 — not available)

---

## NSE IX API Availability (Verified Mar 21, 2026)

| Endpoint | Status | Used? |
|----------|--------|-------|
| `G_T_DSP_INDEX` (settlement prices) | ✅ Available | Yes |
| `G_T_UL_INDEX` (underlying index prices) | ✅ Available | No (redundant with NSE indices) |
| `G_T_UL_PRICE` (underlying stock prices) | ✅ Available | No (redundant with CM bhavcopy) |
| `G_COMBINED_OI` (open interest) | ✅ Available | Yes |
| `G_T_Bhavcopy_FO` (F&O bhavcopy) | ❌ 403 | — |
| `G_T1_Bhavcopy_FO` (Session 2 bhavcopy) | ❌ 403 | — |
| `G_T_VOLT` (volatility) | ❌ 403 | — |

---

## Key Decisions Made During Phase 1

1. **6+ years of history, not 6 months.** The original classifier plan had 137 days. We backfilled to 1,538 trading days (Jan 2020 → Mar 2026) to enable proper tuning and validation.

2. **Scoring over clustering.** Originally planned HMM/GMM clustering. Pivoted to continuous 0-100 scoring with threshold-based labels (Bullish/Neutral/Bearish). Simpler, more interpretable, tunable.

3. **3 regimes, not 5.** Started with 5 subdivisions (strong_bull, bull, neutral, bear, strong_bear). Simplified to 3 (Bullish, Neutral, Bearish) — the builder only needs to know direction + confidence.

4. **Prediction requires leading indicators.** v1 prototype proved coincident indicators have zero predictive power. FII/DII flows, GIFT Nifty overnight, and global cues are the key leading signals.

5. **Go handlers for daily, Python for backfill.** Go pipeline handles automated daily collection (fast, integrated). Python scripts handle one-time historical backfills (flexible, quick to write).

6. **NSE IX data starts Jul 2023.** GIFT Nifty derivatives launched mid-2023. We have 655 trading days of IX data vs 1,538 for everything else. The scorer must handle this gracefully (missing data → skip dimension or use fallback).

---

## Next: Phase 2

With the data layer complete, Phase 2 builds the **feature computation and scoring engine**:
- Compute the 5 dimension scores from raw data
- Derive regime labels from composite scores
- Add leading indicator features for prediction
- Backtest regime labels against actual market outcomes
- Tune thresholds empirically against ground truth

*This document will be updated if Phase 1 scope changes or new data sources are added.*

---

*Written: March 22, 2026 | Author: Gxozt*
