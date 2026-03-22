"""Test pre-open model accuracy across E3 vs E4 label variants.

Runs walk-forward XGBoost on each target label set and reports accuracy + margin.
"""

import sys
import numpy as np
import pandas as pd
import psycopg2
from pathlib import Path

from src.preopen_features import PREOPEN_FEATURE_COLS

LABEL_MAP = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}
LABEL_MAP_INV = {0: "Trend-Down", 1: "Range", 2: "Trend-Up"}
MIN_TRAIN = 126
RETRAIN_EVERY = 63


def get_conn():
    return psycopg2.connect(host="localhost", user="me", password="algotrix", dbname="atdb")


def load_feature_matrix():
    """Load pre-computed feature matrix CSV."""
    csv_path = Path(__file__).resolve().parent.parent / "data" / "preopen_feature_matrix.csv"
    print(f"Loading feature matrix from {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"  {len(df)} rows, {len(df.columns)} columns")
    return df


def load_labels():
    """Load all label variants from regime_ground_truth."""
    conn = get_conn()
    df = pd.read_sql("""
        SELECT date, coincident_label as label_e3,
               label_e4_strict, label_e4_moderate, label_e4_loose
        FROM regime_ground_truth
        ORDER BY date
    """, conn)
    conn.close()
    return df


def walk_forward_xgb(features_df, target_series):
    """Run walk-forward XGBoost and return accuracy metrics."""
    from xgboost import XGBClassifier

    valid_mask = target_series.notna()
    X = features_df[valid_mask].values
    y = target_series[valid_mask].values.astype(int)
    n = len(y)

    if n < MIN_TRAIN + 10:
        return None

    preds = np.full(n, np.nan)
    probs = np.full((n, 3), np.nan)
    model = None
    last_train_end = -1

    for i in range(MIN_TRAIN, n):
        if model is None or (i - last_train_end) >= RETRAIN_EVERY:
            X_train = X[:i]
            y_train = y[:i]
            model = XGBClassifier(
                max_depth=4, n_estimators=200, learning_rate=0.05,
                subsample=0.8, use_label_encoder=False,
                eval_metric="mlogloss", objective="multi:softprob",
                num_class=3, verbosity=0, random_state=42,
            )
            model.fit(X_train, y_train)
            last_train_end = i

        preds[i] = model.predict(X[i:i+1])[0]
        probs[i] = model.predict_proba(X[i:i+1])[0]

    # Evaluate on test portion only (after first training window)
    test_mask = ~np.isnan(preds)
    y_test = y[test_mask]
    p_test = preds[test_mask].astype(int)

    accuracy = (y_test == p_test).mean()

    # Majority class baseline
    from collections import Counter
    majority = Counter(y_test).most_common(1)[0][0]
    baseline = (y_test == majority).mean()

    # Confidence analysis (>= 70%)
    probs_test = probs[test_mask]
    max_prob = probs_test.max(axis=1)
    high_conf_mask = max_prob >= 0.70
    high_conf_pct = high_conf_mask.mean() * 100
    if high_conf_mask.sum() > 0:
        high_conf_acc = (y_test[high_conf_mask] == p_test[high_conf_mask]).mean()
    else:
        high_conf_acc = 0.0

    return {
        "accuracy": accuracy,
        "baseline": baseline,
        "margin": accuracy - baseline,
        "n_test": int(test_mask.sum()),
        "high_conf_acc": high_conf_acc,
        "high_conf_pct": high_conf_pct,
    }


def main():
    print("=" * 70)
    print("  E3 vs E4 — Pre-Open XGBoost Walk-Forward Comparison")
    print("=" * 70)

    features = load_feature_matrix()
    labels = load_labels()

    # Merge
    features["date"] = pd.to_datetime(features["date"]).dt.date
    labels["date"] = pd.to_datetime(labels["date"]).dt.date
    df = labels.merge(features, on="date", how="inner")
    print(f"\nMerged: {len(df)} days with features + labels\n")

    feat_cols = [c for c in PREOPEN_FEATURE_COLS if c in df.columns]
    X = df[feat_cols].copy()

    # Fill NaN features with 0 (same as existing model)
    X = X.fillna(0)

    variants = {
        "E3": "label_e3",
        "E4-strict": "label_e4_strict",
        "E4-moderate": "label_e4_moderate",
        "E4-loose": "label_e4_loose",
    }

    print(f"Features: {len(feat_cols)} columns")
    print(f"Walk-forward: min_train={MIN_TRAIN}, retrain_every={RETRAIN_EVERY}\n")

    results = {}
    for name, col in variants.items():
        print(f"Running {name}...")
        target = df[col].map(LABEL_MAP)
        res = walk_forward_xgb(X, target)
        if res:
            results[name] = res
            print(f"  Accuracy: {res['accuracy']:.1%} | Baseline: {res['baseline']:.1%} | "
                  f"Margin: {res['margin']:+.1%} | N={res['n_test']}")
            print(f"  High-conf (>=70%): {res['high_conf_acc']:.1%} on {res['high_conf_pct']:.1f}% of days")
        else:
            print(f"  SKIPPED (insufficient data)")
        print()

    # Summary table
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"{'Variant':<16} {'Accuracy':>10} {'Baseline':>10} {'Margin':>10} {'HiConf Acc':>12} {'HiConf %':>10} {'N':>6}")
    print("-" * 76)
    for name, res in results.items():
        print(f"{name:<16} {res['accuracy']:>9.1%} {res['baseline']:>9.1%} {res['margin']:>+9.1%} "
              f"{res['high_conf_acc']:>11.1%} {res['high_conf_pct']:>9.1f}% {res['n_test']:>5}")


if __name__ == "__main__":
    main()
