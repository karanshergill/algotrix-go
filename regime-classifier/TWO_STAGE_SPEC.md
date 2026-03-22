# Two-Stage Pre-Open Predictor + Feature Engineering v2

## Architecture

### Stage 1: Trending vs Range (Binary)
- Labels: "Trending" (Trend-Up + Trend-Down merged) vs "Range"
- Same walk-forward XGBoost setup
- Expected: easier binary task → higher accuracy

### Stage 2: Up vs Down (Binary, conditional)
- Only runs when Stage 1 predicts "Trending"
- Labels: "Trend-Up" vs "Trend-Down" (Range days excluded from training)
- Separate XGBoost model

### Combined output
- Stage 1 = Range → predict "Range"
- Stage 1 = Trending + Stage 2 = Up → predict "Trend-Up"
- Stage 1 = Trending + Stage 2 = Down → predict "Trend-Down"
- Confidence = min(stage1_conf, stage2_conf) for trending predictions
- Confidence = stage1_conf for Range predictions

## New Features (v2) — compute from existing DB data

### Compression/Tension (rolling range dynamics)
```python
# Range compression: current range vs 20-day avg
range_compression = range_pct / rolling_mean(range_pct, 20)
# Low values (<0.5) = compression = potential breakout
# High values (>1.5) = expansion = trend continuation or exhaustion

# Bollinger Band width (proxy via rolling std of returns)
bb_width_20d = rolling_std(nifty_return, 20) * 2
# Narrow = compression, wide = volatile

# Range contraction streak: consecutive days with range < 20d avg
range_contraction_streak = count of consecutive days where range_pct < rolling_mean(range_pct, 20)
```

### VIX Regime
```python
# VIX percentile rank (where does today's VIX sit in trailing 252 days)
vix_percentile = rolling_percentile_rank(vix_close, 252)
# Low (<25th) = complacency, high (>75th) = fear

# VIX mean reversion signal
vix_zscore_20d = (vix_close - rolling_mean(vix_close, 20)) / rolling_std(vix_close, 20)
```

### Momentum Divergence
```python
# Nifty 5d return vs Nifty 20d return direction mismatch
momentum_divergence = sign(return_5d) != sign(return_20d)  # 1 if divergent, 0 if aligned

# Rate of change acceleration
roc_5d = nifty_return_5d
roc_20d = nifty_return_20d
momentum_acceleration = roc_5d - (roc_20d / 4)  # 5d pace vs 20d daily pace
```

### Volume Profile
```python
# Volume trend (5d avg vs 20d avg)
volume_trend = rolling_mean(volume, 5) / rolling_mean(volume, 20)
# >1 = increasing participation, <1 = fading

# Up-volume ratio (breadth-weighted volume signal)
# Already have breadth, but add volume-confirmed breadth
vol_breadth_confirmation = breadth_ratio * volume_trend
```

### Calendar Enhanced
```python
# Monday effect (markets tend to follow Friday's trend or gap)
is_monday = day_of_week == 0

# Month-end effect (rebalancing)
days_to_month_end = business_days_remaining_in_month

# Week of month (1-5)
week_of_month = (day_of_month - 1) // 7 + 1
```

## Data Sources
All features computed from existing DB tables:
- `nse_indices_daily` (Nifty 50): OHLCV
- `nse_vix_daily`: VIX OHLC
- `nse_cm_bhavcopy`: breadth
- `regime_ground_truth`: E3 labels (target)
- Pre-open feature matrix CSV for GIFT/global features

DB: `host=localhost dbname=atdb user=me password=algotrix`

## Implementation

### File: `src/two_stage_model.py`

1. Load data (same sources as calibrate_labels.py + preopen feature matrix)
2. Compute v2 features
3. Merge with existing 27 pre-open features from CSV
4. Run walk-forward for:
   - Stage 1 (Trending vs Range)
   - Stage 2 (Up vs Down, on trending subset)
   - Combined 3-class output
5. Compare against single-stage E3 model
6. Report: accuracy, F1, margin over baseline, confidence analysis, return separation

### File: `src/predict_tomorrow.py`

1. Load all historical data + today's (March 20) EOD
2. Compute all features for tomorrow (March 23)
3. Train on full history up to March 20
4. Predict March 23's regime
5. Output: predicted regime, confidence, stage1/stage2 breakdown

## Critical Constraints
- DO NOT modify existing files (ground_truth.py, calibrate_labels.py, etc.)
- New standalone scripts only
- Use `python3 -u` for unbuffered output
- Print all results to stdout
