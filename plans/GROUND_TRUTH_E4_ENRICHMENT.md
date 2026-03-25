# Ground Truth E4 — Multi-Dimensional Label Enrichment

**Status:** DRAFT — awaiting approval
**Date:** 2026-03-23
**Author:** Gxozt
**Reviewed by:** Codex, Gemx
**Prerequisite:** E3 percentile-based labels (DONE, promoted 2026-03-23)

---

## Problem Statement

E3 labels use only **3 inputs** (return %, CIR, breadth ratio) to classify 1,538 trading days. This creates systematic mislabeling:

- **Narrow-participation pumps** → mislabeled Trend-Up (one heavyweight drags index while rest is flat)
- **High-volume rotational days** → mislabeled Range (broad activity but index goes nowhere)
- **Low-conviction drifts** → labeled as trend when volume says "nobody cares"
- **High-dispersion chaos** → lumped with clean trends (stock returns all over the place)

The model ceiling is capped by label quality. 50.1% accuracy with +12% margin over baseline — but the labels themselves are wrong on ~15-20% of days.

## Design Principle: Label Purity

**Labels answer ONE question: "What kind of day actually happened in the cash market?"**

Labels use ONLY day-realized cash market data. No predictors, no derivatives, no overnight data.

| Belongs in Labels (day-realized) | Belongs in Features (pre-open / predictive) |
|----------------------------------|---------------------------------------------|
| Nifty return, range, CIR | GIFT Nifty overnight gap/range/OI |
| Market breadth (adv/dec) | FII/DII net flows |
| Market volume / turnover | VIX level and change |
| Cross-sectional dispersion | F&O OI, PCR, max pain |
| Turnover concentration | S&P 500, USD/INR overnight |
| Sector participation | Prior-day regime |
| | Calendar / expiry context |

This separation prevents "predictor leakage" into target definition — a flaw in the original E4 draft that mixed FII/DII and VIX into labels.

---

## Available Data for Labels (All 1,538+ trading days)

| Source | Key Columns | Coverage |
|--------|-------------|----------|
| `nse_indices_daily` (Nifty 50) | OHLC → return, range, CIR | 1,539 days |
| `nse_cm_bhavcopy` | volume, traded_value, num_trades per stock | 1,539 days, 2.77M rows |
| `nse_indices_daily` (12 sector indices) | Sector returns | 1,539 days each |

Everything we need is in two tables with complete coverage.

---

## E4: 6-Dimension Labeling

Keep E3's 3-class taxonomy: **Trend-Up / Range / Trend-Down**

Require multi-dimensional confirmation from day-realized cash market data.

### Dimension 1: Price Direction (from E3)
**Source:** `nse_indices_daily` (Nifty 50)

```
return_pct = (nifty_close / prev_nifty_close) - 1
cir = (nifty_close - nifty_low) / (nifty_high - nifty_low)
```

- Uses rolling 252-day P33/P67 percentiles (same as E3)
- Sets the **candidate direction**: Trend-Up, Range, or Trend-Down

### Dimension 2: Market Breadth (from E3)
**Source:** `nse_cm_bhavcopy`

```
advances = count(stocks where close > prev_close)
declines = count(stocks where close < prev_close)
breadth_ratio = advances / (advances + declines)
```

- Uses rolling 252-day P33/P67 percentiles (same as E3)
- Confirms whether broad market agrees with index direction

### Dimension 3: Volume Conviction — NEW
**Source:** `nse_cm_bhavcopy` (traded_value)

```
market_turnover = SUM(traded_value) across all stocks for the day
turnover_20d_avg = rolling 20-day average of market_turnover
volume_ratio = market_turnover / turnover_20d_avg
```

Scoring:
- `volume_ratio >= 1.20` → **HIGH** conviction (+1 confirms trend)
- `volume_ratio <= 0.80` → **LOW** conviction (-1 contradicts trend)
- `0.80 < volume_ratio < 1.20` → **NEUTRAL** (0)

**Why:** A +1% day on 60% of average volume is a drift, not a trend. A +0.5% day on 150% volume is real institutional commitment.

### Dimension 4: Cross-Sectional Dispersion — NEW
**Source:** `nse_cm_bhavcopy` (close, prev_close)

```
stock_returns = [(close - prev_close) / prev_close for each stock]
dispersion = std_dev(stock_returns)
dispersion_20d_avg = rolling 20-day average of dispersion
dispersion_ratio = dispersion / dispersion_20d_avg
```

Scoring:
- `dispersion_ratio <= 0.85` → **LOW** dispersion (+1 confirms clean trend — stocks moving together)
- `dispersion_ratio >= 1.30` → **HIGH** dispersion (-1 contradicts trend — chaotic, stocks all over the place)
- Between → **NEUTRAL** (0)

**Why:** Clean trend days have low dispersion — most stocks move in the same direction with similar magnitude. High dispersion means some stocks are up 3%, others down 2% — the index return is an average of chaos, not a coherent trend.

### Dimension 5: Turnover Concentration — NEW
**Source:** `nse_cm_bhavcopy` (traded_value)

```
stock_turnovers = traded_value per stock, sorted descending
top10_share = sum(top 10 stocks) / sum(all stocks)
top10_20d_avg = rolling 20-day average of top10_share
concentration_ratio = top10_share / top10_20d_avg
```

Scoring:
- `concentration_ratio >= 1.15` → **CONCENTRATED** (-1 contradicts trend — few heavyweights driving the move)
- `concentration_ratio <= 0.90` → **DISTRIBUTED** (+1 confirms trend — broad participation)
- Between → **NEUTRAL** (0)

**Why:** If Reliance + HDFC Bank + TCS account for 40% of total turnover (vs normal 25%), the index move is narrow. Three stocks rallying ≠ market trending.

### Dimension 6: Sector Participation — NEW
**Source:** `nse_indices_daily` (12 sector indices)

Core 12 sectors:
- Nifty Bank, IT, Pharma, Auto, Metal, FMCG
- Nifty Energy, Realty, Financial Services, Infrastructure, Media, PSU Bank

```
sector_returns[i] = (sector_close / sector_prev_close) - 1
nifty_direction = sign(nifty_return)
sectors_agreeing = count(sectors where sign(return) == nifty_direction)
sector_participation = sectors_agreeing / 12
```

Scoring:
- `sector_participation >= 0.75` → **BROAD** (+1 confirms trend — 9+ of 12 sectors agree)
- `sector_participation <= 0.42` → **NARROW** (-1 contradicts trend — 5 or fewer agree)
- Between → **MODERATE** (0)

**Why:** Index up because banking rallied while IT, Pharma, Metal fell? That's sector rotation, not a trend day for intraday trading.

---

## E4 Voting Logic

### Step 1: E3 proposes the candidate label
D1 (Price) + D2 (Breadth) use existing E3 percentile logic to propose: Trend-Up, Range, or Trend-Down.

### Step 2: D3–D6 vote to confirm or reject

Each new dimension gives a score: +1 (confirms), 0 (neutral), -1 (contradicts)

```
confirm_score = D3_score + D4_score + D5_score + D6_score
# Range: -4 to +4
```

### Step 3: Final classification

```
IF E3_label == "Trend-Up":
    IF confirm_score >= 2:  → "Trend-Up"    # Strong confirmation
    IF confirm_score >= 0:  → "Trend-Up"    # Weak but not contradicted (E4-loose)
    IF confirm_score < 0:   → "Range"       # Contradicted — demote to Range

IF E3_label == "Trend-Down":
    IF confirm_score >= 2:  → "Trend-Down"
    IF confirm_score >= 0:  → "Trend-Down"  # E4-loose
    IF confirm_score < 0:   → "Range"       # Contradicted — demote

IF E3_label == "Range":
    → "Range"  # Already no price signal, keep as Range
```

### Calibration Variants

| Variant | Confirmation Threshold | Expected Effect |
|---------|----------------------|-----------------|
| **E4-strict** | confirm_score >= 3 | Fewest trend days, highest quality labels |
| **E4-moderate** | confirm_score >= 2 | Balanced |
| **E4-loose** | confirm_score >= 0 | Most trend days, lower bar |

All three will be computed and compared.

---

## Implementation Plan

### Step 1: Build dimension extractors
**File:** `regime-classifier/src/ground_truth_e4.py`

Functions:
- `compute_volume_conviction(date, cm_bhavcopy_df)` → score (-1/0/+1), raw ratio
- `compute_dispersion(date, cm_bhavcopy_df)` → score (-1/0/+1), raw ratio
- `compute_turnover_concentration(date, cm_bhavcopy_df)` → score (-1/0/+1), raw ratio
- `compute_sector_participation(date, indices_df)` → score (-1/0/+1), raw ratio, sectors agreeing
- `compute_e4_label(date, e3_label, cm_df, indices_df, threshold)` → final label + all scores

### Step 2: Backfill all 1,538 days
**File:** `regime-classifier/src/backfill_e4.py`

- Compute all 4 new dimensions for every trading day
- Store E4 labels in `regime_ground_truth` table (new columns: `label_e4_strict`, `label_e4_moderate`, `label_e4_loose`, `d3_volume_score`, `d4_dispersion_score`, `d5_concentration_score`, `d6_sector_score`)
- Export to `regime-classifier/data/e4_labels.csv`

### Step 3: Comparison analysis
**File:** `regime-classifier/src/compare_e3_e4.py`

Analysis:
1. **Distribution comparison:** E3 vs E4 (strict/moderate/loose) class balance
2. **Flipped days:** Which days changed label and why (dimension breakdown)
3. **Economic separation:** Mean return by label for E3 vs each E4 variant
4. **Spot-check 30 flipped days:** Output for manual review against actual price action
5. **Pre-open model rerun:** XGBoost accuracy + margin over baseline on each E4 variant
6. **Dimension correlation:** Do D3–D6 scores correlate with each other? (avoid redundancy)

### Step 4: Decision
- If E4 shows better economic separation AND model accuracy improves → promote
- If mixed → tune thresholds or dimension weights
- If worse → diagnose which dimensions hurt

---

## Success Criteria

1. **Better economic separation:** Mean return spread between Trend-Up and Trend-Down > E3's 2.15%
2. **Cleaner Range bucket:** Range days should have mean return near zero (E3 is +0.01% — good)
3. **Model accuracy improvement:** Pre-open XGBoost on E4 labels > E3's 50.1%
4. **Distribution balance:** No class < 20% or > 45% of days
5. **Spot-check validation:** At least 25/30 randomly sampled flipped days should feel "correctly relabeled"
6. **Dimension independence:** No two dimensions should have correlation > 0.7

---

## Files

| File | Purpose |
|------|---------|
| `regime-classifier/src/ground_truth_e4.py` | Dimension extractors + E4 labeler |
| `regime-classifier/src/backfill_e4.py` | Backfill script for all 1,538 days |
| `regime-classifier/src/compare_e3_e4.py` | Head-to-head comparison analysis |
| `regime-classifier/data/e4_labels.csv` | Per-day E4 labels + all dimension scores |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Dimension redundancy (volume + concentration measure similar things) | Check pairwise correlation; drop if > 0.7 |
| Threshold sensitivity (20d window, scoring cutoffs) | Test 10d/20d/60d windows; calibrate cutoffs on rolling basis |
| Over-filtering trends (too many demoted to Range) | E4-loose variant as fallback; check class balance |
| Sector index gaps or stale data | Verify all 12 sectors have data for all 1,539 days before backfill |
| Top-10 concentration unstable across years (universe grew from 1,548 to 2,437 stocks) | Use relative concentration (ratio vs 20d avg) not absolute share |
