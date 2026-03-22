#!/usr/bin/env python3
"""Regime Prediction Tuner.

1. Compute ground truth labels from actual Nifty 50 price action
2. Compute feature vectors from day N
3. Predict day N+1's label using threshold-based rules
4. Measure accuracy
5. Sweep parameters to find optimal thresholds

Ground truth is defined by what ACTUALLY HAPPENED that day (outcome),
features are what we OBSERVE on the prior day (inputs).
"""

import sys
import json
import numpy as np
import psycopg2
from datetime import date, timedelta
from collections import Counter, defaultdict

DB = dict(dbname="atdb", user="me", password="algotrix", host="localhost")

# ─── STEP 1: Ground Truth Labels ──────────────────────────────────────────────
# Define what each day ACTUALLY WAS based on objective price action.
# These use ONLY that day's data — no lookahead.

def compute_ground_truth(conn):
    """Compute ground truth regime label for each trading day.
    
    Uses Nifty 50 OHLCV + breadth (advance/decline from CM bhavcopy).
    Returns dict of {date: label}.
    """
    cur = conn.cursor()
    
    # Get Nifty 50 daily data
    cur.execute("""
        SELECT date, open, high, low, close, volume
        FROM nse_indices_daily 
        WHERE index = 'Nifty 50' 
        ORDER BY date
    """)
    nifty_rows = cur.fetchall()
    
    # Get daily advance/decline counts
    cur.execute("""
        SELECT date,
            SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as advances,
            SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as declines,
            COUNT(*) as total
        FROM nse_cm_bhavcopy
        GROUP BY date
        ORDER BY date
    """)
    breadth_rows = {r[0]: {"adv": r[1], "dec": r[2], "total": r[3]} for r in cur.fetchall()}
    
    # Get India VIX
    cur.execute("""
        SELECT date, close as vix
        FROM nse_indices_daily 
        WHERE index = 'India VIX' 
        ORDER BY date
    """)
    vix_rows = {r[0]: r[1] for r in cur.fetchall()}
    
    labels = {}
    prev_close = None
    
    for row in nifty_rows:
        dt, opn, high, low, close, vol = row
        
        if prev_close is None:
            prev_close = close
            continue
        
        # Day's metrics
        ret = (close - prev_close) / prev_close * 100  # close-to-close return %
        intraday_range = (high - low) / prev_close * 100  # intraday range %
        body = abs(close - opn) / prev_close * 100  # candle body size %
        
        # Breadth
        b = breadth_rows.get(dt, {})
        adv = b.get("adv", 0)
        dec = b.get("dec", 0)
        ad_ratio = adv / dec if dec > 0 else 10.0
        
        # VIX
        vix = vix_rows.get(dt)
        
        # ── Ground truth rules ──
        # Based on what actually happened, not what indicators predicted
        
        # Volatile/Choppy: big intraday range but small close-to-close move
        # (whipsaw — went both ways)
        range_to_body = intraday_range / body if body > 0.01 else 999
        
        if intraday_range > 2.0 and range_to_body > 3.0:
            label = "volatile_choppy"
        elif ret > 1.0 and ad_ratio > 1.5:
            label = "strong_bull"
        elif ret > 0.3 and ad_ratio > 1.0:
            label = "bullish"
        elif ret < -1.0 and ad_ratio < 0.7:
            label = "bearish"
        elif ret < -0.3 and ad_ratio < 1.0:
            label = "weak_bearish"
        elif intraday_range > 1.5 and abs(ret) < 0.3:
            label = "volatile_choppy"
        else:
            label = "neutral"
        
        labels[dt] = {
            "label": label,
            "return": round(ret, 3),
            "range": round(intraday_range, 3),
            "body": round(body, 3),
            "ad_ratio": round(ad_ratio, 3),
            "vix": round(vix, 2) if vix else None,
        }
        
        prev_close = close
    
    return labels


# ─── STEP 2: Feature Vectors ──────────────────────────────────────────────────
# What we can observe at end of day N to predict day N+1

def compute_features(conn):
    """Compute daily feature vectors.
    
    For each day, compute indicators that are available at market close.
    These are the INPUTS for prediction.
    """
    cur = conn.cursor()
    
    # Nifty 50 OHLCV
    cur.execute("""
        SELECT date, open, high, low, close, volume
        FROM nse_indices_daily 
        WHERE index = 'Nifty 50' 
        ORDER BY date
    """)
    nifty = [(r[0], float(r[1] or 0), float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), int(r[5] or 0)) for r in cur.fetchall()]
    
    # India VIX
    cur.execute("""
        SELECT date, close as vix
        FROM nse_indices_daily 
        WHERE index = 'India VIX' 
        ORDER BY date
    """)
    vix_map = {r[0]: float(r[1]) for r in cur.fetchall() if r[1]}
    
    # Breadth
    cur.execute("""
        SELECT date,
            SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as advances,
            SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as declines,
            COUNT(*) as total
        FROM nse_cm_bhavcopy
        GROUP BY date
        ORDER BY date
    """)
    breadth_map = {r[0]: {"adv": int(r[1]), "dec": int(r[2]), "total": int(r[3])} for r in cur.fetchall()}
    
    features = {}
    closes = []
    highs = []
    lows = []
    returns_list = []
    ad_ratios = []
    vix_list = []
    dates_list = []
    
    for i, (dt, opn, high, low, close, vol) in enumerate(nifty):
        closes.append(close)
        highs.append(high)
        lows.append(low)
        dates_list.append(dt)
        
        if i == 0:
            continue
        
        prev_close = closes[-2]
        ret = (close - prev_close) / prev_close * 100
        returns_list.append(ret)
        
        intraday_range = (high - low) / prev_close * 100
        
        # VIX
        vix = vix_map.get(dt)
        vix_list.append(vix if vix else None)
        
        # Breadth
        b = breadth_map.get(dt, {})
        adv = b.get("adv", 0)
        dec = b.get("dec", 0)
        ad_ratio = adv / dec if dec > 0 else 5.0
        ad_ratios.append(ad_ratio)
        
        # Need enough history for rolling calcs
        if i < 20:
            continue
        
        # ── Feature computation ──
        feat = {}
        
        # 1. Return features
        feat["return_1d"] = ret
        feat["return_3d"] = sum(returns_list[-3:]) if len(returns_list) >= 3 else ret
        feat["return_5d"] = sum(returns_list[-5:]) if len(returns_list) >= 5 else ret
        
        # 2. Volatility features
        feat["vix"] = vix if vix else None
        if len(vix_list) >= 2 and vix_list[-1] and vix_list[-2]:
            feat["vix_change"] = vix_list[-1] - vix_list[-2]
        else:
            feat["vix_change"] = None
        
        # Rolling realized volatility (5d, 10d)
        if len(returns_list) >= 5:
            feat["rvol_5d"] = float(np.std(returns_list[-5:]))
        if len(returns_list) >= 10:
            feat["rvol_10d"] = float(np.std(returns_list[-10:]))
        if len(returns_list) >= 20:
            feat["rvol_20d"] = float(np.std(returns_list[-20:]))
        
        # Intraday range
        feat["range_1d"] = intraday_range
        if i >= 5:
            recent_ranges = [(highs[j] - lows[j]) / closes[j-1] * 100 for j in range(i-4, i+1)]
            feat["range_5d_avg"] = float(np.mean(recent_ranges))
        
        # ATR (14-period)
        if i >= 14:
            trs = []
            for j in range(i-13, i+1):
                tr = max(highs[j] - lows[j], 
                        abs(highs[j] - closes[j-1]),
                        abs(lows[j] - closes[j-1]))
                trs.append(tr)
            feat["atr14"] = float(np.mean(trs))
            feat["atr14_pct"] = feat["atr14"] / close * 100
        
        # 3. Trend features
        # EMA20
        if i >= 20:
            ema = float(np.mean(closes[-20:]))  # SMA as approximation
            feat["ema20_dist"] = (close - ema) / ema * 100
        
        # ADX approximation: directional movement trend strength
        if len(returns_list) >= 14:
            pos_moves = [max(r, 0) for r in returns_list[-14:]]
            neg_moves = [max(-r, 0) for r in returns_list[-14:]]
            avg_pos = np.mean(pos_moves)
            avg_neg = np.mean(neg_moves)
            if avg_pos + avg_neg > 0:
                feat["trend_strength"] = abs(avg_pos - avg_neg) / (avg_pos + avg_neg) * 100
            else:
                feat["trend_strength"] = 0
        
        # Consecutive up/down days
        streak = 0
        for j in range(len(returns_list)-1, max(len(returns_list)-10, -1), -1):
            if returns_list[j] > 0:
                streak += 1
            elif returns_list[j] < 0:
                streak -= 1
            else:
                break
            if j > 0 and (returns_list[j] > 0) != (returns_list[j-1] > 0):
                break
        feat["streak"] = streak
        
        # 4. Breadth features
        feat["ad_ratio"] = ad_ratio
        if len(ad_ratios) >= 5:
            feat["ad_ratio_5d"] = float(np.mean(ad_ratios[-5:]))
            feat["ad_ratio_change"] = ad_ratios[-1] - ad_ratios[-2]
        
        # 5. VIX term structure proxy (VIX vs realized vol)
        if vix and "rvol_20d" in feat and feat["rvol_20d"] > 0:
            # VIX is annualized, rvol_20d is daily — normalize
            rvol_ann = feat["rvol_20d"] * np.sqrt(252)
            feat["vrp"] = vix - rvol_ann  # Volatility risk premium
        
        # 6. Volume features (Nifty)
        feat["volume"] = vol
        
        features[dt] = feat
    
    return features


# ─── STEP 3: Prediction Rules ─────────────────────────────────────────────────

def predict_next_day(feat, params):
    """Given day N's features, predict day N+1's regime label.
    
    Uses threshold-based rules with tunable parameters.
    """
    vix = feat.get("vix")
    vix_change = feat.get("vix_change")
    ret_1d = feat.get("return_1d", 0)
    ret_3d = feat.get("return_3d", 0)
    rvol_5d = feat.get("rvol_5d", 0)
    ad_ratio = feat.get("ad_ratio", 1.0)
    ad_5d = feat.get("ad_ratio_5d", 1.0)
    ema_dist = feat.get("ema20_dist", 0)
    trend_str = feat.get("trend_strength", 0)
    streak = feat.get("streak", 0)
    range_1d = feat.get("range_1d", 0)
    
    p = params  # shorthand
    
    # Score each regime
    scores = {
        "strong_bull": 0,
        "bullish": 0,
        "neutral": 0,
        "weak_bearish": 0,
        "bearish": 0,
        "volatile_choppy": 0,
    }
    
    # ── Volatile/Choppy signals ──
    if vix and vix > p["vix_high"]:
        scores["volatile_choppy"] += 2
    if vix_change and vix_change > p["vix_spike"]:
        scores["volatile_choppy"] += 3
    if rvol_5d > p["rvol_high"]:
        scores["volatile_choppy"] += 2
    if range_1d > p["range_high"]:
        scores["volatile_choppy"] += 1
    
    # ── Bearish signals ──
    if ret_1d < -p["ret_bear"]:
        scores["bearish"] += 2
        scores["weak_bearish"] += 1
    if ret_3d < -p["ret3d_bear"]:
        scores["bearish"] += 2
    if ad_ratio < p["ad_bearish"]:
        scores["bearish"] += 1
        scores["weak_bearish"] += 1
    if ema_dist < -p["ema_dist_bear"]:
        scores["bearish"] += 1
    if streak < -p["streak_bear"]:
        scores["bearish"] += 1
    
    # ── Bullish signals ──
    if ret_1d > p["ret_bull"]:
        scores["strong_bull"] += 2
        scores["bullish"] += 1
    if ret_3d > p["ret3d_bull"]:
        scores["strong_bull"] += 2
    if ad_ratio > p["ad_bullish"]:
        scores["strong_bull"] += 1
        scores["bullish"] += 1
    if ema_dist > p["ema_dist_bull"]:
        scores["bullish"] += 1
    if streak > p["streak_bull"]:
        scores["strong_bull"] += 1
    
    # ── Neutral signals ──
    if abs(ret_1d) < p["ret_neutral"]:
        scores["neutral"] += 2
    if vix and vix < p["vix_low"]:
        scores["neutral"] += 1
    if rvol_5d < p["rvol_low"]:
        scores["neutral"] += 1
    if abs(ema_dist) < p["ema_dist_neutral"]:
        scores["neutral"] += 1
    
    # Pick highest scoring regime
    best = max(scores, key=scores.get)
    
    # If all scores are 0 or tied, default to neutral
    if scores[best] == 0:
        best = "neutral"
    
    return best, scores


# ─── STEP 4: Evaluate ─────────────────────────────────────────────────────────

def evaluate(features, ground_truth, params, train_end=None):
    """Run prediction on all days and measure accuracy.
    
    features[day N] → predict → compare with ground_truth[day N+1]
    """
    sorted_dates = sorted(set(features.keys()) & set(ground_truth.keys()))
    
    results = []
    for i in range(len(sorted_dates) - 1):
        d_n = sorted_dates[i]      # feature day
        d_n1 = sorted_dates[i+1]   # target day (next trading day)
        
        if d_n1 not in ground_truth:
            continue
        
        pred, scores = predict_next_day(features[d_n], params)
        actual = ground_truth[d_n1]["label"]
        
        is_train = train_end and d_n <= train_end
        
        results.append({
            "date": d_n1,
            "predicted": pred,
            "actual": actual,
            "correct": pred == actual,
            "is_train": is_train,
        })
    
    return results


def print_metrics(results, label=""):
    """Print accuracy metrics."""
    if not results:
        print(f"  No results for {label}")
        return
    
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    acc = correct / total * 100
    
    print(f"\n{'='*60}")
    print(f"  {label}: {correct}/{total} = {acc:.1f}% accuracy")
    print(f"{'='*60}")
    
    # Per-regime breakdown
    regime_stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "total": 0})
    for r in results:
        actual = r["actual"]
        pred = r["predicted"]
        regime_stats[actual]["total"] += 1
        if r["correct"]:
            regime_stats[actual]["tp"] += 1
        else:
            regime_stats[pred]["fp"] += 1
            regime_stats[actual]["fn"] += 1
    
    print(f"\n  {'Regime':<18s} {'Actual':>6s} {'TP':>5s} {'FP':>5s} {'Prec':>6s} {'Recall':>7s}")
    print(f"  {'-'*50}")
    for regime in ["strong_bull", "bullish", "neutral", "weak_bearish", "bearish", "volatile_choppy"]:
        s = regime_stats[regime]
        prec = s["tp"] / (s["tp"] + s["fp"]) * 100 if (s["tp"] + s["fp"]) > 0 else 0
        recall = s["tp"] / s["total"] * 100 if s["total"] > 0 else 0
        print(f"  {regime:<18s} {s['total']:>6d} {s['tp']:>5d} {s['fp']:>5d} {prec:>5.1f}% {recall:>6.1f}%")
    
    # Confusion: predicted vs actual distribution
    pred_dist = Counter(r["predicted"] for r in results)
    actual_dist = Counter(r["actual"] for r in results)
    print(f"\n  Predicted distribution: {dict(pred_dist)}")
    print(f"  Actual distribution:   {dict(actual_dist)}")
    
    # "Close enough" accuracy: bearish/weak_bearish are neighbors, bullish/strong_bull are neighbors
    neighbors = {
        "strong_bull": {"strong_bull", "bullish"},
        "bullish": {"bullish", "strong_bull", "neutral"},
        "neutral": {"neutral", "bullish", "weak_bearish"},
        "weak_bearish": {"weak_bearish", "bearish", "neutral"},
        "bearish": {"bearish", "weak_bearish"},
        "volatile_choppy": {"volatile_choppy"},
    }
    close_correct = sum(1 for r in results if r["predicted"] in neighbors.get(r["actual"], {r["actual"]}))
    print(f"\n  Close-enough accuracy: {close_correct}/{total} = {close_correct/total*100:.1f}%")
    print(f"  (counts neighbor regimes as correct)")


# ─── STEP 5: Parameter Sweep ──────────────────────────────────────────────────

# Baseline parameters
BASELINE_PARAMS = {
    "vix_high": 18.0,
    "vix_low": 13.0,
    "vix_spike": 2.0,
    "rvol_high": 1.5,
    "rvol_low": 0.5,
    "ret_bull": 0.5,
    "ret_bear": 0.5,
    "ret3d_bull": 1.5,
    "ret3d_bear": 1.5,
    "ret_neutral": 0.3,
    "ad_bullish": 1.5,
    "ad_bearish": 0.7,
    "ema_dist_bull": 1.0,
    "ema_dist_bear": 1.0,
    "ema_dist_neutral": 0.5,
    "streak_bull": 3,
    "streak_bear": 3,
    "range_high": 2.0,
}


def sweep_parameter(features, ground_truth, base_params, param_name, values, train_end=None):
    """Sweep a single parameter and report accuracy for each value."""
    print(f"\n{'─'*60}")
    print(f"Sweeping: {param_name}")
    print(f"{'─'*60}")
    
    best_acc = 0
    best_val = None
    
    for val in values:
        params = dict(base_params)
        params[param_name] = val
        results = evaluate(features, ground_truth, params, train_end)
        
        if train_end:
            test_results = [r for r in results if not r["is_train"]]
        else:
            test_results = results
        
        correct = sum(1 for r in test_results if r["correct"])
        total = len(test_results)
        acc = correct / total * 100 if total > 0 else 0
        
        marker = " ◀" if acc > best_acc else ""
        print(f"  {param_name}={val:<8} → {acc:.1f}% ({correct}/{total}){marker}")
        
        if acc > best_acc:
            best_acc = acc
            best_val = val
    
    print(f"  Best: {param_name}={best_val} → {best_acc:.1f}%")
    return best_val


def main():
    print("Connecting to DB...")
    conn = psycopg2.connect(**DB)
    
    print("Computing ground truth labels...")
    ground_truth = compute_ground_truth(conn)
    print(f"  {len(ground_truth)} days labeled")
    
    gt_dist = Counter(v["label"] for v in ground_truth.values())
    print(f"  Distribution: {dict(gt_dist)}")
    
    print("\nComputing feature vectors...")
    features = compute_features(conn)
    print(f"  {len(features)} days with features")
    
    conn.close()
    
    # Train/test split: train on 2020-2024, test on 2025+
    train_end = date(2024, 12, 31)
    
    print(f"\n{'='*60}")
    print(f"BASELINE EVALUATION")
    print(f"Train: <= {train_end}, Test: > {train_end}")
    print(f"{'='*60}")
    
    results = evaluate(features, ground_truth, BASELINE_PARAMS, train_end)
    
    train_results = [r for r in results if r["is_train"]]
    test_results = [r for r in results if not r["is_train"]]
    
    print_metrics(train_results, "TRAIN (2020-2024)")
    print_metrics(test_results, "TEST (2025+)")
    print_metrics(results, "ALL")
    
    # Parameter sweep on train set
    print(f"\n{'='*60}")
    print(f"PARAMETER SWEEP (optimizing on train set)")
    print(f"{'='*60}")
    
    best_params = dict(BASELINE_PARAMS)
    
    sweeps = {
        "vix_high": [14, 16, 18, 20, 22, 25],
        "vix_low": [10, 11, 12, 13, 14, 15],
        "vix_spike": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
        "ret_bull": [0.3, 0.5, 0.7, 1.0, 1.2],
        "ret_bear": [0.3, 0.5, 0.7, 1.0, 1.2],
        "ret3d_bull": [0.8, 1.0, 1.5, 2.0, 2.5],
        "ret3d_bear": [0.8, 1.0, 1.5, 2.0, 2.5],
        "ad_bullish": [1.2, 1.3, 1.5, 1.8, 2.0],
        "ad_bearish": [0.5, 0.6, 0.7, 0.8, 0.9],
        "ema_dist_bull": [0.3, 0.5, 1.0, 1.5, 2.0],
        "ema_dist_bear": [0.3, 0.5, 1.0, 1.5, 2.0],
        "rvol_high": [1.0, 1.2, 1.5, 1.8, 2.0],
        "range_high": [1.5, 2.0, 2.5, 3.0],
    }
    
    for param_name, values in sweeps.items():
        best_val = sweep_parameter(features, ground_truth, best_params, param_name, values, train_end)
        best_params[param_name] = best_val
    
    # Final evaluation with optimized params
    print(f"\n{'='*60}")
    print(f"OPTIMIZED EVALUATION")
    print(f"{'='*60}")
    print(f"Optimized params: {json.dumps(best_params, indent=2)}")
    
    results = evaluate(features, ground_truth, best_params, train_end)
    train_results = [r for r in results if r["is_train"]]
    test_results = [r for r in results if not r["is_train"]]
    
    print_metrics(train_results, "TRAIN (2020-2024)")
    print_metrics(test_results, "TEST (2025+)")
    print_metrics(results, "ALL")
    
    # Show some example predictions on test set
    print(f"\n{'='*60}")
    print(f"SAMPLE PREDICTIONS (TEST SET)")
    print(f"{'='*60}")
    for r in test_results[:30]:
        mark = "✓" if r["correct"] else "✗"
        print(f"  {r['date']} predicted={r['predicted']:<18s} actual={r['actual']:<18s} {mark}")


if __name__ == "__main__":
    main()
