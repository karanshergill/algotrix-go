"""Test E3 labels with enriched feature set (original 27 + D3-D6 raw values).

Compares:
  A) E3 labels + original 27 features (baseline)
  B) E3 labels + 27 features + 4 new dimensions as features
  C) E3 labels + 27 features + 4 new dimensions + prev-day versions

This answers: do volume, dispersion, concentration, sector participation
have predictive power when used as model features?
"""

import numpy as np
import pandas as pd
import psycopg2
from pathlib import Path
from collections import Counter

from src.preopen_features import PREOPEN_FEATURE_COLS

LABEL_MAP = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}
MIN_TRAIN = 126
RETRAIN_EVERY = 63


def get_conn():
    return psycopg2.connect(host="localhost", user="me", password="algotrix", dbname="atdb")


def load_data():
    """Load feature matrix + E4 raw dimension values."""
    # Original features
    csv_path = Path(__file__).resolve().parent.parent / "data" / "preopen_feature_matrix.csv"
    features = pd.read_csv(csv_path)
    features["date"] = pd.to_datetime(features["date"]).dt.date
    print(f"Feature matrix: {len(features)} rows, {len(features.columns)} cols")

    # E4 dimension data (raw values + E3 labels)
    conn = get_conn()
    gt = pd.read_sql("""
        SELECT date, coincident_label,
               d3_raw, d4_raw, d5_raw, d6_raw
        FROM regime_ground_truth
        ORDER BY date
    """, conn)
    conn.close()
    gt["date"] = pd.to_datetime(gt["date"]).dt.date
    print(f"Ground truth: {len(gt)} rows")

    # Merge
    df = features.merge(gt, on="date", how="inner")
    print(f"Merged: {len(df)} rows")
    return df


def add_prev_day_dimensions(df):
    """Add previous-day D3-D6 values as features (available before open)."""
    df = df.sort_values("date").reset_index(drop=True)
    for col in ["d3_raw", "d4_raw", "d5_raw", "d6_raw"]:
        df[f"prev_{col}"] = df[col].shift(1)
    return df


def walk_forward_xgb(X, y):
    """Run walk-forward XGBoost."""
    from xgboost import XGBClassifier

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

    # High confidence
    probs_test = probs[test_mask]
    max_prob = probs_test.max(axis=1)
    high_conf = max_prob >= 0.70
    high_conf_pct = high_conf.mean() * 100
    high_conf_acc = (y_test[high_conf] == p_test[high_conf]).mean() if high_conf.sum() > 0 else 0

    # Per-class accuracy
    from sklearn.metrics import classification_report
    report = classification_report(y_test, p_test, target_names=["Trend-Down", "Range", "Trend-Up"], output_dict=True)

    # Feature importance (last trained model)
    importances = model.feature_importances_

    return {
        "accuracy": accuracy,
        "baseline": baseline,
        "margin": accuracy - baseline,
        "n_test": int(test_mask.sum()),
        "high_conf_acc": high_conf_acc,
        "high_conf_pct": high_conf_pct,
        "report": report,
        "importances": importances,
    }


def main():
    print("=" * 70)
    print("  E3 + Enriched Features — Walk-Forward Comparison")
    print("=" * 70)

    df = load_data()
    df = add_prev_day_dimensions(df)

    # Target: E3 labels
    target = df["coincident_label"].map(LABEL_MAP)
    valid = target.notna()
    y = target[valid].values.astype(int)

    # Feature sets
    orig_cols = [c for c in PREOPEN_FEATURE_COLS if c in df.columns]

    # NEW: same-day D3-D6 raw values are NOT available pre-open (they're realized EOD)
    # But PREVIOUS DAY values ARE available pre-open
    prev_dim_cols = ["prev_d3_raw", "prev_d4_raw", "prev_d5_raw", "prev_d6_raw"]

    experiments = {
        "A) E3 + 27 original features": orig_cols,
        "B) E3 + 27 + prev-day D3-D6": orig_cols + prev_dim_cols,
    }

    print(f"\nTarget: E3 labels (coincident_label)")
    print(f"Walk-forward: min_train={MIN_TRAIN}, retrain_every={RETRAIN_EVERY}\n")

    results = {}
    for name, cols in experiments.items():
        print(f"\n{'='*70}")
        print(f"  {name}")
        print(f"  Features: {len(cols)}")
        print(f"{'='*70}")

        X = df.loc[valid, cols].fillna(0).values
        res = walk_forward_xgb(X, y)
        if res:
            results[name] = res
            print(f"\n  Accuracy:  {res['accuracy']:.1%}")
            print(f"  Baseline:  {res['baseline']:.1%}")
            print(f"  Margin:    {res['margin']:+.1%}")
            print(f"  N test:    {res['n_test']}")
            print(f"  HiConf:    {res['high_conf_acc']:.1%} on {res['high_conf_pct']:.1f}% of days")

            # Per-class F1
            print(f"\n  Per-class F1:")
            for cls in ["Trend-Down", "Range", "Trend-Up"]:
                f1 = res['report'][cls]['f1-score']
                prec = res['report'][cls]['precision']
                rec = res['report'][cls]['recall']
                print(f"    {cls:<12}: F1={f1:.3f}  Prec={prec:.3f}  Recall={rec:.3f}")

            # Top 10 features by importance
            imp = res["importances"]
            feat_imp = sorted(zip(cols, imp), key=lambda x: -x[1])[:15]
            print(f"\n  Top 15 features by importance:")
            for fname, fimp in feat_imp:
                bar = "█" * int(fimp * 100)
                print(f"    {fname:<40} {fimp:.4f} {bar}")

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY COMPARISON")
    print(f"{'='*70}")
    print(f"{'Experiment':<40} {'Acc':>7} {'Base':>7} {'Margin':>8} {'HiConf':>8} {'HC%':>6}")
    print("-" * 70)
    for name, res in results.items():
        print(f"{name:<40} {res['accuracy']:>6.1%} {res['baseline']:>6.1%} {res['margin']:>+7.1%} "
              f"{res['high_conf_acc']:>7.1%} {res['high_conf_pct']:>5.1f}%")

    # Delta
    if len(results) == 2:
        keys = list(results.keys())
        a, b = results[keys[0]], results[keys[1]]
        print(f"\n  Delta (B - A):")
        print(f"    Accuracy:  {b['accuracy'] - a['accuracy']:+.2%}")
        print(f"    Margin:    {b['margin'] - a['margin']:+.2%}")
        print(f"    HiConf:    {b['high_conf_acc'] - a['high_conf_acc']:+.2%}")


if __name__ == "__main__":
    main()
