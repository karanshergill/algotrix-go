# Promote E3 as Canonical Ground Truth

## What to do

### 1. Update `src/ground_truth.py`

Replace the existing `compute_coincident_truth()` function with E3 percentile-based logic.

The new function signature:
```python
def compute_coincident_truth(date, nifty_open, nifty_high, nifty_low, nifty_close, 
                              prev_nifty_close, breadth_ratio, vix_close, prev_vix_close,
                              rolling_stats: dict) -> str:
```

Where `rolling_stats` contains pre-computed trailing 252-day percentiles:
- `ret_p33`, `ret_p67` — percentiles of abs(return)
- `cir_p33`, `cir_p67` — percentiles of CIR
- `breadth_p33`, `breadth_p67` — percentiles of breadth

**E3 Logic (from calibrate_labels.py):**
```python
# Compute derived values
return_pct = (nifty_close / prev_nifty_close) - 1
day_range = nifty_high - nifty_low
cir = 0.5 if day_range == 0 else (nifty_close - nifty_low) / day_range

# Strong trend
if return_pct > ret_p67 and cir > cir_p67 and (breadth_ratio is None or breadth_ratio > breadth_p67):
    return "Trend-Up"
if return_pct < -ret_p67 and cir < cir_p33 and (breadth_ratio is None or breadth_ratio < breadth_p33):
    return "Trend-Down"

# Weaker directional
cir_mid = (cir_p33 + cir_p67) / 2
if return_pct > ret_p33 and cir > cir_mid:
    return "Trend-Up"
if return_pct < -ret_p33 and cir < cir_mid:
    return "Trend-Down"

return "Range"
```

**Return labels:** "Trend-Up", "Range", "Trend-Down" (replacing old "Bullish", "Neutral", "Bearish")

Also update `compute_predictive_truth()` to use the same label names:
- "Bullish" → "Trend-Up"
- "Neutral" → "Range"  
- "Bearish" → "Trend-Down"

Add a helper function `compute_rolling_stats(df, window=252)` that takes a DataFrame of historical data and returns the rolling percentile stats needed for labelling.

### 2. Create `src/backfill_e3.py`

Standalone script to:
1. Load all data from raw tables (same as calibrate_labels.py)
2. Compute E3 labels for all 1,538 days
3. Update `regime_ground_truth` table:
   - ALTER TABLE to add new columns if needed: `cir REAL`, `range_pct REAL`
   - UPDATE `coincident_label` with E3 labels for all rows
   - UPDATE `predictive_label` with new label names

**DB connection:** `host=localhost dbname=atdb user=me password=algotrix`

**IMPORTANT:** 
- Backup the old labels first: `SELECT * FROM regime_ground_truth` → save to `data/regime_gt_backup_set_a.csv`
- Then update in-place
- Print summary: old vs new label distribution, number of changes

### 3. Update `src/preopen_set_e.py` → rename to `src/preopen_model.py`

- Update `label_set_e()` to use E3 logic (percentile-based)
- Update label maps: `{"Trend-Down": 0, "Range": 1, "Trend-Up": 2}`
- Keep walk-forward evaluation logic the same
- Update comparison to be E3 vs old Set A

### 4. Do NOT touch
- `calibrate_labels.py` — keep as-is for reference
- `data/calibration_labels.csv` — keep as-is
- DB table schema beyond adding cir/range_pct columns

## Verification
After backfill, run:
```sql
SELECT coincident_label, COUNT(*), ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER() * 100, 1) 
FROM regime_ground_truth GROUP BY coincident_label ORDER BY COUNT(*) DESC;
```
Expected: Trend-Up ~32%, Range ~42%, Trend-Down ~26%
