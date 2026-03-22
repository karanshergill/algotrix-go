# v2 Regime Model — Evaluation Report

**Generated:** 2026-03-22

## 1. Feature Matrix Summary

- **Total dates:** 1529
- **Total columns:** 64
- **Feature columns:** 55

**Coincident truth distribution:**
- Neutral: 719 (47.0%)
- Bullish: 418 (27.3%)
- Bearish: 392 (25.6%)

**Predictive truth distribution:**
- Bullish: 588 (38.5%)
- Neutral: 471 (30.8%)
- Bearish: 469 (30.7%)

**Feature null rates (top 10):**
- overnight_oi_change_pct_z20: 100.0%
- overnight_oi_change_pct: 100.0%
- overnight_vol_delta: 100.0%
- overnight_vol_delta_z20: 100.0%
- fii_net_idx_fut_p60: 100.0%
- overnight_vs_session_vol: 100.0%
- client_vs_fii_divergence_z60: 100.0%
- overnight_vs_session_vol_z20: 100.0%
- volume_concentration_p60: 100.0%
- leadership_concentration_p60: 100.0%

## 2a. Coincident Model Results

**Test dates:** 1277

### Baselines

| Baseline | Accuracy | Description |
|----------|----------|-------------|
| return_breadth | 98.7% | 2-of-3 vote: return sign + breadth ratio |
| return_sign | 82.4% | Return > 0.3% → Bullish, < -0.3% → Bearish |
| majority_class | 47.0% | Always predict 'Neutral' (most frequent) |
| persistence | 39.1% | Today = yesterday's label |
| random_uniform | 33.3% | Random guess (uniform 3-class) |

### Model Comparison

| Model | Accuracy | F1 (macro) | Bearish F1 | Neutral F1 | Bullish F1 |
|-------|----------|------------|------------|------------|------------|
| lightgbm_expanding | 80.2% | 0.805 | 0.844 | 0.791 | 0.782 |
| lightgbm_rolling | 80.0% | 0.804 | 0.840 | 0.788 | 0.785 |
| logreg_expanding | 48.6% | 0.490 | 0.563 | 0.399 | 0.508 |
| logreg_rolling | 49.5% | 0.500 | 0.584 | 0.417 | 0.501 |
| xgboost_expanding | 81.1% | 0.814 | 0.849 | 0.802 | 0.792 |
| xgboost_rolling | 80.2% | 0.805 | 0.837 | 0.791 | 0.788 |

### Best Model: xgboost_expanding

- **Accuracy:** 81.1%
- **F1 (macro):** 0.814
- **Test dates:** 1277

**Confusion Matrix:**
```
              Pred Bear  Pred Neut  Pred Bull
     Bearish        276         49          0
     Neutral         48        486         81
     Bullish          1         62        274
```

**Per-class accuracy:**
- Bearish: 84.9%
- Neutral: 79.0%
- Bullish: 81.3%

**Return separation by predicted class:**
| Predicted | Mean Return | Median Return | Count |
|-----------|-------------|---------------|-------|
| Bearish | -0.9338% | -0.8101% | 325 |
| Neutral | 0.0553% | 0.0306% | 597 |
| Bullish | 0.9117% | 0.7768% | 355 |

**Accuracy by year:**
| Year | Accuracy | N |
|------|----------|---|
| 2021 | 78.4% | 236 |
| 2022 | 79.8% | 248 |
| 2023 | 77.6% | 245 |
| 2024 | 82.1% | 246 |
| 2025 | 85.9% | 248 |
| 2026 | 88.9% | 54 |

### Feature Importance (SHAP)


## 2b. Predictive Model Results

**Test dates:** 1276

### Baselines

| Baseline | Accuracy | Description |
|----------|----------|-------------|
| majority_class | 38.5% | Always predict 'Bullish' (most frequent) |
| transition_matrix | 38.5% | Most likely next regime given current regime |
| persistence | 35.0% | Tomorrow = today's coincident label |
| random_uniform | 33.3% | Random guess (uniform 3-class) |

### Model Comparison

| Model | Accuracy | F1 (macro) | Bearish F1 | Neutral F1 | Bullish F1 |
|-------|----------|------------|------------|------------|------------|
| lightgbm_expanding | 35.1% | 0.343 | 0.303 | 0.301 | 0.425 |
| lightgbm_rolling | 36.4% | 0.357 | 0.314 | 0.324 | 0.432 |
| logreg_expanding | 33.9% | 0.337 | 0.364 | 0.366 | 0.280 |
| logreg_rolling | 34.2% | 0.342 | 0.360 | 0.339 | 0.327 |
| xgboost_expanding | 35.3% | 0.347 | 0.317 | 0.315 | 0.410 |
| xgboost_rolling | 35.6% | 0.348 | 0.325 | 0.299 | 0.419 |

### Best Model: lightgbm_rolling

- **Accuracy:** 36.4%
- **F1 (macro):** 0.357
- **Test dates:** 1276

**Confusion Matrix:**
```
              Pred Bear  Pred Neut  Pred Bull
     Bearish        121         95        173
     Neutral        136        123        160
     Bullish        125        122        221
```

**Per-class accuracy:**
- Bearish: 31.1%
- Neutral: 29.4%
- Bullish: 47.2%

**Return separation by predicted class:**
| Predicted | Mean Return | Median Return | Count |
|-----------|-------------|---------------|-------|
| Bearish | -0.0389% | -0.0027% | 382 |
| Neutral | 0.1047% | 0.1565% | 340 |
| Bullish | 0.0577% | 0.0271% | 554 |

**Accuracy by year:**
| Year | Accuracy | N |
|------|----------|---|
| 2021 | 39.8% | 236 |
| 2022 | 36.7% | 248 |
| 2023 | 31.0% | 245 |
| 2024 | 37.8% | 246 |
| 2025 | 35.1% | 248 |
| 2026 | 45.3% | 53 |

### Feature Importance (SHAP)


## 3. Decision Gate Assessment

- PASS: Coincident best (xgboost_expanding) = 81.1% > Phase 1 (52.3%) + 10pp
-   Return+Breadth baseline: 98.7%
- CHECK: Predictive best (lightgbm_rolling) = 36.4% vs random (33.3%), persistence (35.0%), Phase 1 (36.9%)

**Overall: REVIEW NEEDED** — some models may not clear all thresholds. Check return separation.
