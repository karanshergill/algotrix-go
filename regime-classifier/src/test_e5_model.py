"""Walk-forward XGBoost comparison: E3 vs E5 labels.

Uses v1 feature matrix (27 features) with same walk-forward params as E4 tests.
"""

import numpy as np
import pandas as pd
from pathlib import Path
from collections import Counter

from src.preopen_features import PREOPEN_FEATURE_COLS

LABEL_MAP = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}
LABEL_MAP_INV = {0: "Trend-Down", 1: "Range", 2: "Trend-Up"}
MIN_TRAIN = 126
RETRAIN_EVERY = 63

# v1 feature set (first 27)
V1_FEATURES = PREOPEN_FEATURE_COLS[:27]


def load_feature_matrix():
    csv_path = Path(__file__).resolve().parent.parent / "data" / "preopen_feature_matrix.csv"
    print(f"Loading feature matrix from {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"  {len(df)} rows, {len(df.columns)} columns")
    return df


def load_e5_labels():
    csv_path = Path(__file__).resolve().parent.parent / "data" / "e5_labels.csv"
    print(f"Loading E5 labels from {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"  {len(df)} rows")
    return df


def walk_forward_xgb(X_all, y_all):
    """Run walk-forward XGBoost, return metrics."""
    from xgboost import XGBClassifier

    valid_mask = ~np.isnan(y_all)
    X = X_all[valid_mask]
    y = y_all[valid_mask].astype(int)
    n = len(y)

    if n < MIN_TRAIN + 10:
        return None

    preds = np.full(n, np.nan)
    probs = np.full((n, 3), np.nan)
    model = None
    last_train_end = -1

    for i in range(MIN_TRAIN, n):
        if model is None or (i - last_train_end) >= RETRAIN_EVERY:
            model = XGBClassifier(
                max_depth=4, n_estimators=200, learning_rate=0.05,
                subsample=0.8, use_label_encoder=False,
                eval_metric="mlogloss", objective="multi:softprob",
                num_class=3, verbosity=0, random_state=42,
            )
            model.fit(X[:i], y[:i])
            last_train_end = i

        preds[i] = model.predict(X[i:i+1])[0]
        probs[i] = model.predict_proba(X[i:i+1])[0]

    test_mask = ~np.isnan(preds)
    y_test = y[test_mask]
    p_test = preds[test_mask].astype(int)

    accuracy = (y_test == p_test).mean()
    majority = Counter(y_test).most_common(1)[0][0]
    baseline = (y_test == majority).mean()

    probs_test = probs[test_mask]
    max_prob = probs_test.max(axis=1)
    high_conf_mask = max_prob >= 0.70
    high_conf_pct = high_conf_mask.mean() * 100
    high_conf_acc = 0.0
    if high_conf_mask.sum() > 0:
        high_conf_acc = (y_test[high_conf_mask] == p_test[high_conf_mask]).mean()

    # Per-class accuracy
    per_class = {}
    for cls_id, cls_name in LABEL_MAP_INV.items():
        cls_mask = y_test == cls_id
        if cls_mask.sum() > 0:
            per_class[cls_name] = {
                "n": int(cls_mask.sum()),
                "accuracy": (p_test[cls_mask] == cls_id).mean(),
            }

    return {
        "accuracy": accuracy,
        "baseline": baseline,
        "margin": accuracy - baseline,
        "n_test": int(test_mask.sum()),
        "high_conf_acc": high_conf_acc,
        "high_conf_pct": high_conf_pct,
        "per_class": per_class,
    }


def main():
    print("=" * 70)
    print("  E3 vs E5 — Pre-Open XGBoost Walk-Forward Comparison")
    print("=" * 70)

    features = load_feature_matrix()
    e5_labels = load_e5_labels()

    features["date"] = pd.to_datetime(features["date"]).dt.date
    e5_labels["date"] = pd.to_datetime(e5_labels["date"]).dt.date

    df = e5_labels.merge(features, on="date", how="inner")
    print(f"\nMerged: {len(df)} days with features + labels")

    # Use v1 features (27)
    feat_cols = [c for c in V1_FEATURES if c in df.columns]
    X = df[feat_cols].fillna(0).values
    print(f"Features: {len(feat_cols)} columns (v1 set)")
    print(f"Walk-forward: min_train={MIN_TRAIN}, retrain_every={RETRAIN_EVERY}")

    # Demotion stats
    demoted = df["was_demoted"].sum()
    print(f"\nE5 demotions: {demoted} days ({demoted/len(df)*100:.1f}%)")

    print(f"\n--- E3 Distribution (in test set) ---")
    e3_dist = df["label_e3"].value_counts()
    for label, count in e3_dist.items():
        print(f"  {label:<12} {count:>5} ({count/len(df)*100:.1f}%)")

    print(f"\n--- E5 Distribution (in test set) ---")
    e5_dist = df["label_e5"].value_counts()
    for label, count in e5_dist.items():
        print(f"  {label:<12} {count:>5} ({count/len(df)*100:.1f}%)")

    # Run walk-forward for both
    variants = {"E3": "label_e3", "E5": "label_e5"}
    results = {}

    for name, col in variants.items():
        print(f"\nRunning {name}...")
        target = df[col].map(LABEL_MAP).values.astype(float)
        res = walk_forward_xgb(X, target)
        if res:
            results[name] = res
            print(f"  Accuracy: {res['accuracy']:.1%} | Baseline: {res['baseline']:.1%} | "
                  f"Margin: {res['margin']:+.1%} | N={res['n_test']}")
            print(f"  High-conf (>=70%): {res['high_conf_acc']:.1%} on {res['high_conf_pct']:.1f}% of days")
            for cls_name, cls_stats in res["per_class"].items():
                print(f"  {cls_name}: {cls_stats['accuracy']:.1%} ({cls_stats['n']} days)")
        else:
            print(f"  SKIPPED (insufficient data)")

    # Summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"{'Variant':<8} {'Accuracy':>10} {'Baseline':>10} {'Margin':>10} {'HiConf Acc':>12} {'HiConf %':>10} {'N':>6}")
    print("-" * 68)
    for name, res in results.items():
        print(f"{name:<8} {res['accuracy']:>9.1%} {res['baseline']:>9.1%} {res['margin']:>+9.1%} "
              f"{res['high_conf_acc']:>11.1%} {res['high_conf_pct']:>9.1f}% {res['n_test']:>5}")

    # Delta
    if "E3" in results and "E5" in results:
        delta_acc = results["E5"]["accuracy"] - results["E3"]["accuracy"]
        delta_margin = results["E5"]["margin"] - results["E3"]["margin"]
        delta_hc = results["E5"]["high_conf_acc"] - results["E3"]["high_conf_acc"]
        print(f"\n  E5 vs E3 delta: accuracy {delta_acc:+.1%}, margin {delta_margin:+.1%}, hi-conf {delta_hc:+.1%}")


if __name__ == "__main__":
    main()
