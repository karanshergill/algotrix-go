# Pre-Open Session Predictor — Evaluation Report

Generated: 2026-03-22 21:49

**Dataset:** 1529 trading days
**Date range:** 2020-01-15 to 2026-03-20
**Features:** 27
**Walk-forward:** min 126 days train, retrain every 63 days

## Target Distribution

- **Neutral:** 719 (47.0%)
- **Bullish:** 418 (27.3%)
- **Bearish:** 392 (25.6%)

## Baselines

| Baseline | 3-Class Accuracy |
|----------|-----------------|
| Always Neutral | 0.4840 |
| Persistence | 0.3927 |
| GIFT direction | 0.4604 |
| Previous regime | 0.3927 |
| GIFT + persistence | 0.4861 |
| Persistence (up/down) | n/a (up/down: 0.5638) |
| GIFT (up/down) | n/a (up/down: 0.6151) |
| Always Range | n/a (trend: 0.4961) |

## Model Comparison

| Model | 3-Class Acc | 3-Class F1 | Binary Up/Down | Binary Trend/Range |
|-------|------------|------------|----------------|-------------------|
| xgboost | 0.5709 | 0.5452 | 0.6978 | 0.6450 |
| lightgbm | 0.5517 | 0.5260 | 0.6900 | 0.6429 |
| logreg | 0.4626 | 0.3598 | 0.6636 | 0.5944 |

### XGBOOST — 3-Class Details

**Confusion Matrix** (rows=actual, cols=predicted):

|  | Bearish | Neutral | Bullish |
|--|---------|---------|---------|
| **Bearish** | 178 | 131 | 39 |
| **Neutral** | 98 | 464 | 117 |
| **Bullish** | 41 | 176 | 159 |

- Bearish accuracy: 0.5115
- Neutral accuracy: 0.6834
- Bullish accuracy: 0.4229

**Return Separation:**

| Predicted | Mean Return | Count |
|-----------|-------------|-------|
| Bullish | 0.489% | 315 |
| Neutral | 0.109% | 771 |
| Bearish | -0.499% | 317 |

**Conditional Accuracy:**

| Slice | Accuracy | Count |
|-------|----------|-------|
| Friday | 0.5719 | 278 |
| Monday | 0.5321 | 280 |
| Thursday | 0.6206 | 282 |
| Tuesday | 0.5951 | 284 |
| Wednesday | 0.5341 | 279 |
| conf_gt_50 | 0.5951 | 1173 |
| conf_gt_60 | 0.6227 | 880 |
| conf_gt_70 | 0.6656 | 607 |
| expiry_week | 0.5556 | 414 |
| flat_gap | 0.5084 | 891 |
| gap_down | 0.8203 | 128 |
| gap_up | 0.6328 | 384 |
| high_vol | 0.4810 | 289 |
| low_vol | 0.6308 | 715 |
| non_expiry_week | 0.5774 | 989 |

### LIGHTGBM — 3-Class Details

**Confusion Matrix** (rows=actual, cols=predicted):

|  | Bearish | Neutral | Bullish |
|--|---------|---------|---------|
| **Bearish** | 174 | 130 | 44 |
| **Neutral** | 102 | 448 | 129 |
| **Bullish** | 44 | 180 | 152 |

- Bearish accuracy: 0.5000
- Neutral accuracy: 0.6598
- Bullish accuracy: 0.4043

**Return Separation:**

| Predicted | Mean Return | Count |
|-----------|-------------|-------|
| Bullish | 0.431% | 325 |
| Neutral | 0.118% | 758 |
| Bearish | -0.468% | 320 |

**Conditional Accuracy:**

| Slice | Accuracy | Count |
|-------|----------|-------|
| Friday | 0.5576 | 278 |
| Monday | 0.5143 | 280 |
| Thursday | 0.5922 | 282 |
| Tuesday | 0.5634 | 284 |
| Wednesday | 0.5305 | 279 |
| conf_gt_50 | 0.5714 | 1323 |
| conf_gt_60 | 0.5909 | 1117 |
| conf_gt_70 | 0.6136 | 929 |
| expiry_week | 0.5193 | 414 |
| flat_gap | 0.5062 | 891 |
| gap_down | 0.7578 | 128 |
| gap_up | 0.5885 | 384 |
| high_vol | 0.4567 | 289 |
| low_vol | 0.5930 | 715 |
| non_expiry_week | 0.5652 | 989 |

### LOGREG — 3-Class Details

**Confusion Matrix** (rows=actual, cols=predicted):

|  | Bearish | Neutral | Bullish |
|--|---------|---------|---------|
| **Bearish** | 96 | 230 | 22 |
| **Neutral** | 111 | 512 | 56 |
| **Bullish** | 77 | 258 | 41 |

- Bearish accuracy: 0.2759
- Neutral accuracy: 0.7541
- Bullish accuracy: 0.1090

**Return Separation:**

| Predicted | Mean Return | Count |
|-----------|-------------|-------|
| Bullish | 0.244% | 119 |
| Neutral | 0.067% | 1000 |
| Bearish | -0.058% | 284 |

**Conditional Accuracy:**

| Slice | Accuracy | Count |
|-------|----------|-------|
| Friday | 0.4173 | 278 |
| Monday | 0.4536 | 280 |
| Thursday | 0.4645 | 282 |
| Tuesday | 0.5070 | 284 |
| Wednesday | 0.4695 | 279 |
| conf_gt_50 | 0.5064 | 703 |
| conf_gt_60 | 0.5017 | 287 |
| conf_gt_70 | 0.5000 | 108 |
| expiry_week | 0.4541 | 414 |
| flat_gap | 0.4714 | 891 |
| gap_down | 0.3125 | 128 |
| gap_up | 0.4922 | 384 |
| high_vol | 0.3737 | 289 |
| low_vol | 0.5343 | 715 |
| non_expiry_week | 0.4661 | 989 |

## SHAP Feature Importance (XGBoost 3-Class)

| Rank | Feature | Mean SHAP |
|------|---------|-----------|
| 1 | sp500_overnight_return | 0.2122 |
| 2 | prev_day_range_pct | 0.1175 |
| 3 | prev_dii_net_total | 0.1171 |
| 4 | gift_overnight_gap_pct | 0.1158 |
| 5 | prev_pcr_oi | 0.1064 |
| 6 | days_to_monthly_expiry | 0.0987 |
| 7 | prev_fii_options_skew | 0.0966 |
| 8 | prev_vix_close | 0.0782 |
| 9 | prev_nifty_return_20d | 0.0767 |
| 10 | prev_vix_change_pct | 0.0753 |

## Decision Gate

- Best model: **xgboost** (0.5709)
- Best baseline: 0.4861
- Beats all baselines: **YES** (margin: +0.0848)
- Return separation (Bull > Neutral > Bear): **YES** (0.489% > 0.109% > -0.499%)
- High-confidence (>60%) accuracy > overall: **YES** (0.6227 vs 0.5709)
