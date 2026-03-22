# Ground Truth Calibration Sprint — E1/E2/E3 Bake-off

## Goal
Compare 3 labelling variants on the same 1,538-day universe (2020-01-02 → 2026-03-20).
Determine which produces the cleanest, most tradeable regime labels.

## Data Sources (ALL from raw tables, NOT from `regime_ground_truth`)

```sql
-- Nifty 50 OHLCV
SELECT date, open, high, low, close, volume, turnover
FROM nse_indices_daily WHERE index = 'Nifty 50'

-- VIX
SELECT date, open, high, low, close FROM nse_vix_daily

-- Breadth (advances/declines from bhavcopy)
SELECT date,
  SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END)::float /
  NULLIF(SUM(CASE WHEN close != prev_close THEN 1 ELSE 0 END), 0) as breadth_ratio
FROM nse_cm_bhavcopy GROUP BY date
```

DB: `host=localhost dbname=atdb user=me password=algotrix`

## Derived Features (compute from raw)

```python
# Return
return_pct = (close[D] / close[D-1]) - 1

# CIR (Close-In-Range) — FROZEN FORMULA
CIR = (close - low) / (high - low)
# Guard: if high == low → CIR = 0.5

# Day Range
range_pct = (high - low) / close * 100

# Range Ratio (energy proxy)
avg_range_20d = rolling_mean(high - low, 20)
range_ratio = (high - low) / avg_range_20d
# Guard: if avg_range_20d == 0 → range_ratio = 1.0

# Volume Z-Score (normalized across time)
vol_median_20d = rolling_median(volume, 20)
vol_zscore = (volume - vol_median_20d) / rolling_std(volume, 20)
# Guard: if std == 0 → vol_zscore = 0.0

# VIX (keep level and change separate)
vix_level = vix_close
vix_change_pct = (vix_close[D] / vix_close[D-1] - 1) * 100
```

## Variant Definitions

### E1 — Current Fixed-Threshold Set E
Existing `label_set_e()` from `preopen_set_e.py`:
- **Trend-Up:** return > 0.3% AND CIR > 0.60 AND breadth > 0.50
- **Trend-Down:** return < -0.3% AND CIR < 0.40 AND breadth < 0.50
- Weaker: return > 0.2% AND CIR > 0.50 → Trend-Up (same for down)
- **Range:** everything else

### E2 — Set E + Range/Energy Confirmation
Same as E1 BUT trend labels require energy confirmation:
- **Trend-Up:** E1 Trend-Up conditions AND (range_ratio > 0.8 OR vol_zscore > -0.5)
  - i.e., not a drift-on-no-volume day
- **Trend-Down:** E1 Trend-Down conditions AND (range_ratio > 0.8 OR vol_zscore > -0.5)
- **Range:** everything else
- Intent: "Trend" should mean the market actually moved with conviction

### E3 — Percentile-Based Thresholds
Replace fixed thresholds with rolling 252-day (1-year) percentiles:
- **return_threshold:** P33 and P67 of abs(return) over trailing 252 days
  - Trend-Up: return > P67 of positive returns
  - Trend-Down: return < -P67 of negative returns (by magnitude)
- **CIR thresholds:** P33 and P67 of CIR over trailing 252 days
- **breadth thresholds:** P33 and P67 of breadth over trailing 252 days
- For days < 252 history, use expanding window
- Apply same logic as E1 but with dynamic thresholds

## Output Requirements

### Per-Day CSV
Save `regime-classifier/data/calibration_labels.csv`:
```
date, return_pct, cir, breadth_ratio, range_pct, range_ratio, vol_zscore, vix_level, vix_change_pct, label_e1, label_e2, label_e3
```

### Comparison Report (print to stdout)
For each variant (E1, E2, E3):

1. **Class distribution** — count and % for each label
2. **Baselines** (compute all four):
   - Majority class accuracy
   - Persistence (predict yesterday's label)
   - Gap-sign rule (if pre-open gap > 0 → Trend-Up, etc.)
   - Previous-day regime
3. **Transition matrix** — how often does each label follow each label?
4. **Economic validation:**
   - Mean return by label (should be: Trend-Up > 0, Trend-Down < 0, Range ≈ 0)
   - Mean range_pct by label (Trend days should have wider range than Range days)
   - Mean volume by label
5. **Label agreement** — % where E1==E2, E1==E3, E2==E3
6. **Spot-check** — print 10 random days where E1 and E2 disagree, showing all metrics

### Pre-Open Model Test (optional, run if time permits)
Walk-forward XGBoost (same as preopen_set_e.py) for each variant.
Report accuracy, F1-macro, margin over baseline, confidence analysis.

## Implementation Notes
- Single Python script: `regime-classifier/src/calibrate_labels.py`
- Use `psycopg2` for DB access
- Use `pandas` + `numpy` for computation
- Print all results to stdout (we'll review in terminal)
- Save per-day CSV for further analysis
- DO NOT modify any existing files — this is a new standalone script
