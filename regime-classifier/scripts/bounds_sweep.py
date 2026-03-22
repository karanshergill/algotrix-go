#!/usr/bin/env python3
"""Sweep multiple scorer bound configurations and compare regime distributions.

Reads raw features from market_regime.features_snapshot, re-scores with each
bound config, classifies via Euclidean distance to profiles, and prints
distribution + quality metrics for each config.
"""

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import psycopg2

# ─── Bound configurations to test ─────────────────────────────────────────────

BOUND_CONFIGS = {
    "current": {
        "india_vix_close":       (10.0, 40.0, False),
        "nifty_atr_pctile_60d":  (0.0, 100.0, False),
        "nifty_bbw_pctile_60d":  (0.0, 100.0, False),
        "nifty_adx14":           (10.0, 50.0, False),
        "nifty_ema20_distance":  (-5.0, 5.0, False),
        "nifty_ema20_slope":     (-3.0, 3.0, False),
        "ad_ratio":              (0.3, 3.0, False),
        "ad_ratio_5d_avg":       (0.5, 2.5, False),
        "trin":                  (0.5, 2.0, True),
        "universe_pct_above_ema20": (20.0, 80.0, False),
        "nifty50_pct_above_ema20":  (20.0, 80.0, False),
        "pcr_oi":                (0.5, 1.5, True),
        "fut_basis_pct":         (-0.5, 0.5, False),
    },
    "tight_v1": {
        "india_vix_close":       (9.0, 25.0, False),
        "nifty_atr_pctile_60d":  (0.0, 100.0, False),
        "nifty_bbw_pctile_60d":  (0.0, 100.0, False),
        "nifty_adx14":           (10.0, 40.0, False),
        "nifty_ema20_distance":  (-4.0, 4.0, False),
        "nifty_ema20_slope":     (-2.0, 2.0, False),
        "ad_ratio":              (0.3, 3.0, False),
        "ad_ratio_5d_avg":       (0.5, 2.5, False),
        "trin":                  (0.5, 2.0, True),
        "universe_pct_above_ema20": (20.0, 80.0, False),
        "nifty50_pct_above_ema20":  (20.0, 80.0, False),
        "pcr_oi":                (0.5, 1.5, True),
        "fut_basis_pct":         (-0.1, 0.5, False),
    },
    "tight_v2": {
        "india_vix_close":       (9.0, 22.0, False),
        "nifty_atr_pctile_60d":  (0.0, 100.0, False),
        "nifty_bbw_pctile_60d":  (0.0, 100.0, False),
        "nifty_adx14":           (12.0, 38.0, False),
        "nifty_ema20_distance":  (-3.0, 3.0, False),
        "nifty_ema20_slope":     (-1.5, 1.5, False),
        "ad_ratio":              (0.2, 3.5, False),
        "ad_ratio_5d_avg":       (0.4, 2.5, False),
        "trin":                  (0.5, 2.0, True),
        "universe_pct_above_ema20": (15.0, 75.0, False),
        "nifty50_pct_above_ema20":  (15.0, 75.0, False),
        "pcr_oi":                (0.6, 1.4, True),
        "fut_basis_pct":         (-0.1, 0.6, False),
    },
    "data_driven": {
        # Bounds set to actual p5-p95 of our data
        "india_vix_close":       (9.0, 20.0, False),
        "nifty_atr_pctile_60d":  (0.0, 100.0, False),
        "nifty_bbw_pctile_60d":  (0.0, 100.0, False),
        "nifty_adx14":           (12.0, 35.0, False),
        "nifty_ema20_distance":  (-3.5, 2.5, False),
        "nifty_ema20_slope":     (-1.5, 1.2, False),
        "ad_ratio":              (0.3, 2.5, False),
        "ad_ratio_5d_avg":       (0.5, 2.0, False),
        "trin":                  (0.5, 1.8, True),
        "universe_pct_above_ema20": (25.0, 70.0, False),
        "nifty50_pct_above_ema20":  (20.0, 75.0, False),
        "pcr_oi":                (0.65, 1.35, True),
        "fut_basis_pct":         (0.0, 0.6, False),
    },
    "aggressive": {
        # Very tight bounds — maximize score spread
        "india_vix_close":       (9.5, 18.0, False),
        "nifty_atr_pctile_60d":  (5.0, 95.0, False),
        "nifty_bbw_pctile_60d":  (5.0, 95.0, False),
        "nifty_adx14":           (13.0, 32.0, False),
        "nifty_ema20_distance":  (-2.5, 2.0, False),
        "nifty_ema20_slope":     (-1.2, 1.0, False),
        "ad_ratio":              (0.4, 2.0, False),
        "ad_ratio_5d_avg":       (0.6, 1.8, False),
        "trin":                  (0.6, 1.5, True),
        "universe_pct_above_ema20": (30.0, 65.0, False),
        "nifty50_pct_above_ema20":  (25.0, 70.0, False),
        "pcr_oi":                (0.7, 1.3, True),
        "fut_basis_pct":         (0.05, 0.55, False),
    },
}

# Dimension composition (same for all configs)
DIMENSION_WEIGHTS = {
    "volatility": {
        "india_vix_close": 0.4,
        "nifty_atr_pctile_60d": 0.3,
        "nifty_bbw_pctile_60d": 0.3,
    },
    "trend": {
        "nifty_adx14": 0.4,
        "nifty_ema20_distance": 0.35,
        "nifty_ema20_slope": 0.25,
    },
    "participation": {
        "ad_ratio": 0.15,
        "ad_ratio_5d_avg": 0.15,
        "trin": 0.15,
        "universe_pct_above_ema20": 0.25,
        "nifty50_pct_above_ema20": 0.30,
    },
    "sentiment": {
        "pcr_oi": 0.6,
        "fut_basis_pct": 0.4,
    },
}

# Profile centroids (same for all — we evaluate how well each bounds config
# spreads data relative to these profiles)
PROFILES = {
    "strong_bull":     np.array([25.0, 80.0, 80.0, 70.0]),
    "breakout_setup":  np.array([35.0, 50.0, 60.0, 55.0]),
    "volatile_choppy": np.array([80.0, 25.0, 35.0, 40.0]),
    "bearish":         np.array([60.0, 70.0, 30.0, 25.0]),
    "neutral":         np.array([45.0, 45.0, 50.0, 50.0]),
}

# Also test data-derived profiles (will compute from each config's output)


def normalize(value, min_val, max_val, invert):
    if value is None or np.isnan(value):
        return 50.0
    clamped = np.clip(value, min_val, max_val)
    score = (clamped - min_val) / (max_val - min_val) * 100
    return 100 - score if invert else score


def score_features(features, bounds):
    scores = []
    for dim_name in ["volatility", "trend", "participation", "sentiment"]:
        weights = DIMENSION_WEIGHTS[dim_name]
        wsum = 0.0
        wtot = 0.0
        for ind, w in weights.items():
            val = features.get(ind)
            if val is None:
                continue
            b = bounds[ind]
            wsum += normalize(val, *b) * w
            wtot += w
        scores.append(wsum / wtot if wtot > 0 else 50.0)
    return np.array(scores)


def classify(dim_scores, profiles):
    dists = {}
    for label, centroid in profiles.items():
        dists[label] = np.linalg.norm(dim_scores - centroid)
    nearest = min(dists, key=dists.get)
    # Confidence: inverse distance normalized
    min_d = dists[nearest]
    second_d = sorted(dists.values())[1]
    conf = 1.0 - (min_d / (min_d + second_d)) if (min_d + second_d) > 0 else 0.5
    return nearest, conf, dists


def main():
    conn = psycopg2.connect(dbname="atdb", user="me", password="algotrix", host="localhost")
    cur = conn.cursor()
    cur.execute("SELECT date, features_snapshot FROM market_regime WHERE features_snapshot IS NOT NULL ORDER BY date")
    rows = cur.fetchall()
    conn.close()

    print(f"Loaded {len(rows)} days with features\n")

    for config_name, bounds in BOUND_CONFIGS.items():
        print(f"{'='*70}")
        print(f"CONFIG: {config_name}")
        print(f"{'='*70}")

        all_scores = []
        labels = []
        confs = []
        dates_labels = []

        for dt, feat_json in rows:
            feat = feat_json if isinstance(feat_json, dict) else json.loads(feat_json)
            ds = score_features(feat, bounds)
            all_scores.append(ds)
            lbl, conf, _ = classify(ds, PROFILES)
            labels.append(lbl)
            confs.append(conf)
            dates_labels.append((dt, lbl, ds))

        all_scores = np.array(all_scores)

        # Distribution
        from collections import Counter
        dist = Counter(labels)
        total = len(labels)
        print(f"\nRegime distribution ({total} days):")
        for regime in ["strong_bull", "breakout_setup", "neutral", "volatile_choppy", "bearish"]:
            cnt = dist.get(regime, 0)
            pct = cnt / total * 100
            bar = "█" * int(pct / 2)
            print(f"  {regime:18s} {cnt:4d} ({pct:5.1f}%) {bar}")

        # Score spread
        print(f"\nDimension score stats:")
        dim_names = ["Volatility", "Trend", "Participation", "Sentiment"]
        for i, dn in enumerate(dim_names):
            col = all_scores[:, i]
            print(f"  {dn:14s}  min={col.min():5.1f}  p25={np.percentile(col,25):5.1f}  "
                  f"med={np.median(col):5.1f}  p75={np.percentile(col,75):5.1f}  max={col.max():5.1f}  "
                  f"std={col.std():5.1f}")

        # Confidence stats
        confs = np.array(confs)
        print(f"\nConfidence: mean={confs.mean():.3f}  min={confs.min():.3f}  max={confs.max():.3f}")

        # Quality: dominance ratio (highest regime % / second highest %)
        sorted_dist = sorted(dist.values(), reverse=True)
        if len(sorted_dist) >= 2 and sorted_dist[1] > 0:
            dom_ratio = sorted_dist[0] / sorted_dist[1]
        else:
            dom_ratio = float('inf')
        
        # Entropy (higher = more balanced)
        probs = np.array([dist.get(r, 0) / total for r in PROFILES])
        probs = probs[probs > 0]
        entropy = -np.sum(probs * np.log2(probs))
        max_entropy = np.log2(len(PROFILES))

        print(f"\nQuality metrics:")
        print(f"  Dominance ratio: {dom_ratio:.2f}x (lower = more balanced, ideal ~2-3x)")
        print(f"  Entropy: {entropy:.3f} / {max_entropy:.3f} ({entropy/max_entropy*100:.0f}% of max)")
        print(f"  # regimes used: {len(dist)} / {len(PROFILES)}")

        # Check known dates
        known_events = {
            "2026-03-04": "sell-off start (should be volatile/bearish)",
            "2026-03-05": "sell-off (should be volatile/bearish)",
            "2026-03-06": "sell-off (should be volatile/bearish)",
        }
        print(f"\nKnown event sanity check:")
        for dt, lbl, ds in dates_labels:
            ds_str = str(dt)
            if ds_str in known_events:
                print(f"  {ds_str}: {lbl:18s} scores=[{ds[0]:.0f},{ds[1]:.0f},{ds[2]:.0f},{ds[3]:.0f}]  expected: {known_events[ds_str]}")

        print()


if __name__ == "__main__":
    main()
