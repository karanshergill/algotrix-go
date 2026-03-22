"""Two-stage regime predictor with abstention.

Stage 1: Trend vs Range (binary) — if confidence < threshold → ABSTAIN
Stage 2: Trend-Up vs Trend-Down (binary, conditional on Stage 1) — if confidence < 0.60 → Trend-Unknown

Final classes: Trend-Up, Trend-Down, Trend-Unknown, Range, Uncertain

Comparison against baseline single-stage 3-class XGBoost.
"""

import numpy as np
import pandas as pd
import psycopg2
from pathlib import Path
from collections import Counter
from xgboost import XGBClassifier

from src.preopen_features import PREOPEN_FEATURE_COLS

# --- Config ---
MIN_TRAIN = 126
RETRAIN_EVERY = 63
V1_FEATURES = PREOPEN_FEATURE_COLS[:27]

XGB_PARAMS = dict(
    max_depth=4, n_estimators=200, learning_rate=0.05,
    subsample=0.8, use_label_encoder=False, verbosity=0, random_state=42,
)

E3_LABEL_MAP = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}
E3_LABEL_INV = {0: "Trend-Down", 1: "Range", 2: "Trend-Up"}

STAGE1_THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70]
STAGE2_THRESHOLD = 0.60


def load_data():
    """Load features from CSV and labels from DB, merge on date."""
    csv_path = Path(__file__).resolve().parent.parent / "data" / "preopen_feature_matrix.csv"
    features = pd.read_csv(csv_path, parse_dates=["date"])
    features["date"] = features["date"].dt.date

    conn = psycopg2.connect(host="localhost", user="me", password="algotrix", dbname="atdb")
    try:
        labels = pd.read_sql(
            "SELECT date, coincident_label, nifty_return FROM regime_ground_truth ORDER BY date",
            conn,
        )
    finally:
        conn.close()

    labels["date"] = pd.to_datetime(labels["date"]).dt.date
    df = labels.merge(features, on="date", how="inner", suffixes=("", "_feat")).sort_values("date").reset_index(drop=True)
    df = df[df["coincident_label"].isin(["Trend-Up", "Range", "Trend-Down"])].reset_index(drop=True)

    print(f"Loaded {len(df)} days with features + labels")
    dist = df["coincident_label"].value_counts()
    for lbl, cnt in dist.items():
        print(f"  {lbl:<12} {cnt:>5} ({cnt/len(df)*100:.1f}%)")
    return df


def get_feature_matrix(df):
    """Extract v1 feature columns, fill NaN with 0."""
    feat_cols = [c for c in V1_FEATURES if c in df.columns]
    return df[feat_cols].fillna(0).values, feat_cols


# =============================================================================
# Baseline: single-stage 3-class XGBoost
# =============================================================================

def walk_forward_baseline(X, y_3class):
    """Walk-forward 3-class XGBoost. Returns predictions and probabilities."""
    n = len(y_3class)
    preds = np.full(n, np.nan)
    probs = np.full((n, 3), np.nan)
    model = None
    last_train = -1

    for i in range(MIN_TRAIN, n):
        if model is None or (i - last_train) >= RETRAIN_EVERY:
            model = XGBClassifier(
                **XGB_PARAMS, objective="multi:softprob",
                eval_metric="mlogloss", num_class=3,
            )
            model.fit(X[:i], y_3class[:i])
            last_train = i

        preds[i] = model.predict(X[i:i+1])[0]
        probs[i] = model.predict_proba(X[i:i+1])[0]

    return preds, probs


# =============================================================================
# Two-stage model
# =============================================================================

def walk_forward_two_stage(X, y_3class, stage1_threshold=0.55):
    """Walk-forward two-stage model.

    Stage 1: Trend(1) vs Range(0)  — binary
    Stage 2: Trend-Up(1) vs Trend-Down(0) — binary, only on trend days

    Returns per-day: final_pred (str), stage1_pred, stage1_conf, stage2_pred, stage2_conf
    """
    n = len(y_3class)

    # Derived targets
    # Stage 1: Trend (Trend-Up or Trend-Down) = 1, Range = 0
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)  # Range(1 in 3class)→0, else→1

    # Stage 2: Trend-Up(2 in 3class)→1, Trend-Down(0 in 3class)→0
    # Only defined for trend days
    y_s2 = np.where(y_3class == 2, 1, 0).astype(int)  # only meaningful where y_s1==1
    trend_mask_full = y_s1 == 1  # which days are actually trending

    # Output arrays
    final_pred = np.full(n, "", dtype=object)
    s1_pred = np.full(n, np.nan)
    s1_conf = np.full(n, np.nan)
    s2_pred = np.full(n, np.nan)
    s2_conf = np.full(n, np.nan)

    model_s1 = None
    model_s2 = None
    last_train_s1 = -1
    last_train_s2 = -1

    for i in range(MIN_TRAIN, n):
        # --- Retrain Stage 1 ---
        if model_s1 is None or (i - last_train_s1) >= RETRAIN_EVERY:
            model_s1 = XGBClassifier(
                **XGB_PARAMS, objective="binary:logistic", eval_metric="logloss",
            )
            model_s1.fit(X[:i], y_s1[:i])
            last_train_s1 = i

        # --- Retrain Stage 2 (only on trend days) ---
        if model_s2 is None or (i - last_train_s2) >= RETRAIN_EVERY:
            trend_train = trend_mask_full[:i]
            if trend_train.sum() >= 20:  # need enough trend days
                model_s2 = XGBClassifier(
                    **XGB_PARAMS, objective="binary:logistic", eval_metric="logloss",
                )
                model_s2.fit(X[:i][trend_train], y_s2[:i][trend_train])
                last_train_s2 = i

        # --- Predict Stage 1 ---
        s1_prob = model_s1.predict_proba(X[i:i+1])[0]  # [P(Range), P(Trend)]
        s1_class = int(s1_prob[1] >= 0.5)  # 1=Trend, 0=Range
        s1_confidence = s1_prob[s1_class]
        s1_pred[i] = s1_class
        s1_conf[i] = s1_confidence

        if s1_confidence < stage1_threshold:
            final_pred[i] = "Uncertain"
            continue

        if s1_class == 0:
            final_pred[i] = "Range"
            continue

        # --- Predict Stage 2 (only if Stage 1 says Trend with confidence) ---
        if model_s2 is None:
            final_pred[i] = "Trend-Unknown"
            continue

        s2_prob = model_s2.predict_proba(X[i:i+1])[0]  # [P(Down), P(Up)]
        s2_class = int(s2_prob[1] >= 0.5)  # 1=Up, 0=Down
        s2_confidence = s2_prob[s2_class]
        s2_pred[i] = s2_class
        s2_conf[i] = s2_confidence

        if s2_confidence < STAGE2_THRESHOLD:
            final_pred[i] = "Trend-Unknown"
        elif s2_class == 1:
            final_pred[i] = "Trend-Up"
        else:
            final_pred[i] = "Trend-Down"

    return final_pred, s1_pred, s1_conf, s2_pred, s2_conf


# =============================================================================
# Evaluation helpers
# =============================================================================

def map_twostage_to_3class(final_preds):
    """Map two-stage predictions to 3-class for comparison.
    Trend-Up→2, Trend-Down→0, Range→1, Uncertain/Trend-Unknown→-1 (wrong).
    """
    mapping = {"Trend-Up": 2, "Trend-Down": 0, "Range": 1,
               "Trend-Unknown": -1, "Uncertain": -1, "": np.nan}
    return np.array([mapping.get(p, np.nan) for p in final_preds])


def compute_metrics(y_true, y_pred_3class, final_preds, nifty_returns, label="Model"):
    """Compute all comparison metrics."""
    test_mask = ~np.isnan(y_pred_3class)
    if final_preds is not None:
        test_mask = test_mask & np.array([p != "" for p in final_preds])

    yt = y_true[test_mask].astype(int)
    yp = y_pred_3class[test_mask].astype(int)
    fp = final_preds[test_mask] if final_preds is not None else None
    nr = nifty_returns[test_mask]

    n_test = len(yt)

    # Overall accuracy (treating Uncertain/Trend-Unknown as wrong via -1 mapping)
    overall_acc = (yt == yp).mean()

    # Committed: exclude Uncertain and Trend-Unknown
    if fp is not None:
        committed_mask = np.array([(p not in ("Uncertain", "Trend-Unknown", "")) for p in fp])
    else:
        committed_mask = np.ones(n_test, dtype=bool)

    committed_pct = committed_mask.mean() * 100
    committed_acc = (yt[committed_mask] == yp[committed_mask]).mean() if committed_mask.sum() > 0 else 0

    # Return separation
    return_sep = {}
    if fp is not None:
        classes = ["Trend-Up", "Trend-Down", "Range", "Trend-Unknown", "Uncertain"]
    else:
        classes = ["Trend-Up", "Range", "Trend-Down"]

    for cls in classes:
        if fp is not None:
            cls_mask = fp == cls
        else:
            cls_id = E3_LABEL_MAP.get(cls)
            if cls_id is None:
                continue
            cls_mask = yp == cls_id
        if cls_mask.sum() > 0:
            return_sep[cls] = {
                "mean_return": nr[cls_mask].mean() * 100,
                "count": int(cls_mask.sum()),
            }

    return {
        "label": label,
        "n_test": n_test,
        "overall_acc": overall_acc,
        "committed_acc": committed_acc,
        "committed_pct": committed_pct,
        "return_sep": return_sep,
    }


def evaluate_stage_accuracy(y_3class, s1_pred, s2_pred, test_start):
    """Compute Stage 1 and Stage 2 accuracy separately."""
    mask = np.arange(len(y_3class)) >= test_start

    # Stage 1: Trend vs Range
    y_s1_true = np.where(y_3class == 1, 0, 1)  # Range→0, Trend→1
    s1_valid = mask & ~np.isnan(s1_pred)
    s1_acc = (y_s1_true[s1_valid] == s1_pred[s1_valid].astype(int)).mean() if s1_valid.sum() > 0 else 0

    # Stage 2: Up vs Down (only on true trend days where Stage 2 ran)
    y_s2_true = np.where(y_3class == 2, 1, 0)  # Up→1, Down→0
    s2_valid = mask & ~np.isnan(s2_pred) & (y_s1_true == 1)  # true trend days where s2 predicted
    s2_acc = (y_s2_true[s2_valid] == s2_pred[s2_valid].astype(int)).mean() if s2_valid.sum() > 0 else 0

    return s1_acc, s1_valid.sum(), s2_acc, s2_valid.sum()


# =============================================================================
# Main
# =============================================================================

def main():
    print("=" * 78)
    print("  Two-Stage Regime Predictor with Abstention")
    print("=" * 78)

    df = load_data()
    X, feat_cols = get_feature_matrix(df)
    y_3class = df["coincident_label"].map(E3_LABEL_MAP).values.astype(int)
    nifty_returns = df["nifty_return"].fillna(0).values

    print(f"\nFeatures: {len(feat_cols)} (v1 set)")
    print(f"Walk-forward: min_train={MIN_TRAIN}, retrain_every={RETRAIN_EVERY}")
    print(f"XGBoost: max_depth={XGB_PARAMS['max_depth']}, n_estimators={XGB_PARAMS['n_estimators']}, "
          f"lr={XGB_PARAMS['learning_rate']}, subsample={XGB_PARAMS['subsample']}")

    # -------------------------------------------------------------------------
    # Baseline: 3-class XGBoost
    # -------------------------------------------------------------------------
    print("\n" + "-" * 78)
    print("  Running baseline 3-class XGBoost walk-forward...")
    print("-" * 78)

    baseline_preds, baseline_probs = walk_forward_baseline(X, y_3class)
    baseline_3class = baseline_preds.copy()

    test_mask_bl = ~np.isnan(baseline_preds)
    bl_yt = y_3class[test_mask_bl]
    bl_yp = baseline_preds[test_mask_bl].astype(int)
    bl_acc = (bl_yt == bl_yp).mean()
    majority = Counter(bl_yt).most_common(1)[0][0]
    bl_baseline = (bl_yt == majority).mean()

    # Map baseline to string labels for return sep
    bl_str_preds = np.array([E3_LABEL_INV.get(int(p), "") if not np.isnan(p) else "" for p in baseline_preds])

    bl_metrics = compute_metrics(y_3class, baseline_3class, bl_str_preds, nifty_returns, "Baseline 3-class")

    print(f"\n  Baseline 3-class accuracy: {bl_metrics['overall_acc']:.1%}")
    print(f"  Majority baseline:         {bl_baseline:.1%}")
    print(f"  Margin:                    {bl_metrics['overall_acc'] - bl_baseline:+.1%}")
    print(f"  Committed accuracy:         {bl_metrics['committed_acc']:.1%} (100% committed)")

    print(f"\n  Return separation (baseline):")
    for cls, stats in bl_metrics["return_sep"].items():
        print(f"    {cls:<14} mean={stats['mean_return']:+.3f}%  n={stats['count']}")

    # -------------------------------------------------------------------------
    # Two-stage model at default threshold (0.55)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 78)
    print("  Running two-stage model walk-forward (Stage 1 threshold=0.55)...")
    print("-" * 78)

    final_pred, s1_pred, s1_conf, s2_pred, s2_conf = walk_forward_two_stage(X, y_3class, stage1_threshold=0.55)
    ts_3class = map_twostage_to_3class(final_pred).astype(float)

    ts_metrics = compute_metrics(y_3class, ts_3class, final_pred, nifty_returns, "Two-stage (0.55)")

    # Stage accuracy
    s1_acc, s1_n, s2_acc, s2_n = evaluate_stage_accuracy(y_3class, s1_pred, s2_pred, MIN_TRAIN)

    # Abstention rate
    test_indices = np.arange(len(final_pred)) >= MIN_TRAIN
    test_preds = final_pred[test_indices]
    uncertain_pct = (test_preds == "Uncertain").mean() * 100
    trend_unknown_pct = (test_preds == "Trend-Unknown").mean() * 100
    abstention_rate = uncertain_pct + trend_unknown_pct

    print(f"\n  Overall accuracy (Uncertain/TrendUnknown=wrong): {ts_metrics['overall_acc']:.1%}")
    print(f"  Committed accuracy:                               {ts_metrics['committed_acc']:.1%}")
    print(f"  Committed %:                                      {ts_metrics['committed_pct']:.1f}%")
    print(f"\n  Stage 1 accuracy (Trend vs Range):  {s1_acc:.1%}  (n={s1_n})")
    print(f"  Stage 2 accuracy (Up vs Down):      {s2_acc:.1%}  (n={s2_n})")
    print(f"\n  Abstention rate:     {abstention_rate:.1f}%")
    print(f"    Uncertain:         {uncertain_pct:.1f}%")
    print(f"    Trend-Unknown:     {trend_unknown_pct:.1f}%")

    print(f"\n  Return separation (two-stage):")
    for cls, stats in ts_metrics["return_sep"].items():
        print(f"    {cls:<14} mean={stats['mean_return']:+.3f}%  n={stats['count']}")

    # -------------------------------------------------------------------------
    # Threshold sweep
    # -------------------------------------------------------------------------
    print("\n" + "-" * 78)
    print("  Stage 1 Confidence Threshold Sweep")
    print("-" * 78)
    print(f"\n  {'Threshold':>10} {'Committed%':>12} {'CommittedAcc':>14} {'OverallAcc':>12} {'Abstain%':>10}")
    print("  " + "-" * 60)

    for thresh in STAGE1_THRESHOLDS:
        fp, sp1, sc1, sp2, sc2 = walk_forward_two_stage(X, y_3class, stage1_threshold=thresh)
        tc = map_twostage_to_3class(fp).astype(float)
        m = compute_metrics(y_3class, tc, fp, nifty_returns, f"Two-stage ({thresh})")

        tp = fp[test_indices]
        abst = ((tp == "Uncertain").sum() + (tp == "Trend-Unknown").sum()) / len(tp) * 100

        print(f"  {thresh:>10.2f} {m['committed_pct']:>11.1f}% {m['committed_acc']:>13.1%} "
              f"{m['overall_acc']:>11.1%} {abst:>9.1f}%")

    # -------------------------------------------------------------------------
    # Head-to-head summary
    # -------------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("  HEAD-TO-HEAD SUMMARY")
    print("=" * 78)
    print(f"\n  {'Metric':<35} {'Baseline':>12} {'Two-Stage':>12}")
    print("  " + "-" * 60)
    print(f"  {'Overall accuracy':<35} {bl_metrics['overall_acc']:>11.1%} {ts_metrics['overall_acc']:>11.1%}")
    print(f"  {'Committed accuracy':<35} {bl_metrics['committed_acc']:>11.1%} {ts_metrics['committed_acc']:>11.1%}")
    print(f"  {'Committed %':<35} {bl_metrics['committed_pct']:>10.1f}% {ts_metrics['committed_pct']:>10.1f}%")
    print(f"  {'Stage 1 acc (Trend vs Range)':<35} {'—':>12} {s1_acc:>11.1%}")
    print(f"  {'Stage 2 acc (Up vs Down)':<35} {'—':>12} {s2_acc:>11.1%}")
    print(f"  {'Abstention rate':<35} {'0.0%':>12} {abstention_rate:>10.1f}%")

    delta_committed = ts_metrics["committed_acc"] - bl_metrics["committed_acc"]
    print(f"\n  Committed accuracy delta: {delta_committed:+.1%}")
    if delta_committed > 0:
        print(f"  → Two-stage gains {delta_committed:.1%} committed accuracy by abstaining on {abstention_rate:.1f}% of days")
    else:
        print(f"  → Baseline wins on committed accuracy")

    print()


if __name__ == "__main__":
    main()
