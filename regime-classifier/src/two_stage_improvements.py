"""Three Stage 1 improvements to the two-stage regime predictor.

Improvement 1: Ensemble Stage 1 (XGB + LightGBM + LogReg soft vote)
Improvement 2: Stage-specific feature selection (top-K features)
Improvement 3: Gap routing (separate models per GIFT gap bucket)

All variants compared through the same two-stage pipeline.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import psycopg2
from pathlib import Path
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from src.preopen_features import PREOPEN_FEATURE_COLS

# --- Config ---
MIN_TRAIN = 126
RETRAIN_EVERY = 63
V1_FEATURES = PREOPEN_FEATURE_COLS[:27]
STAGE1_THRESHOLD = 0.55
STAGE2_THRESHOLD = 0.60

XGB_PARAMS = dict(
    max_depth=4, n_estimators=200, learning_rate=0.05,
    subsample=0.8, use_label_encoder=False, verbosity=0, random_state=42,
)

LGBM_PARAMS = dict(
    max_depth=4, n_estimators=200, learning_rate=0.05,
    subsample=0.8, num_leaves=31, verbose=-1, random_state=42,
)

E3_LABEL_MAP = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}


# =============================================================================
# Data loading
# =============================================================================

def load_data():
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


def get_feature_matrix(df, feat_cols=None):
    if feat_cols is None:
        feat_cols = [c for c in V1_FEATURES if c in df.columns]
    return df[feat_cols].fillna(0).values, feat_cols


# =============================================================================
# Stage 2 (shared across all variants — always single XGB)
# =============================================================================

def run_stage2(X, y_3class, s1_pred_class, s1_conf, stage1_threshold):
    """Run Stage 2 on days where Stage 1 predicts Trend with confidence."""
    n = len(y_3class)
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)
    y_s2 = np.where(y_3class == 2, 1, 0).astype(int)
    trend_mask_full = y_s1 == 1

    final_pred = np.full(n, "", dtype=object)
    s2_pred = np.full(n, np.nan)
    s2_conf = np.full(n, np.nan)

    model_s2 = None
    last_train_s2 = -1

    for i in range(MIN_TRAIN, n):
        # Retrain Stage 2
        if model_s2 is None or (i - last_train_s2) >= RETRAIN_EVERY:
            trend_train = trend_mask_full[:i]
            if trend_train.sum() >= 20:
                model_s2 = XGBClassifier(
                    **XGB_PARAMS, objective="binary:logistic", eval_metric="logloss",
                )
                model_s2.fit(X[:i][trend_train], y_s2[:i][trend_train])
                last_train_s2 = i

        # Check Stage 1 output
        if np.isnan(s1_conf[i]):
            continue

        if s1_conf[i] < stage1_threshold:
            final_pred[i] = "Uncertain"
            continue

        if s1_pred_class[i] == 0:  # Range
            final_pred[i] = "Range"
            continue

        # Stage 2 prediction
        if model_s2 is None:
            final_pred[i] = "Trend-Unknown"
            continue

        s2_prob = model_s2.predict_proba(X[i:i+1])[0]
        s2_class = int(s2_prob[1] >= 0.5)
        s2_confidence = s2_prob[s2_class]
        s2_pred[i] = s2_class
        s2_conf[i] = s2_confidence

        if s2_confidence < STAGE2_THRESHOLD:
            final_pred[i] = "Trend-Unknown"
        elif s2_class == 1:
            final_pred[i] = "Trend-Up"
        else:
            final_pred[i] = "Trend-Down"

    return final_pred, s2_pred, s2_conf


# =============================================================================
# Variant 0: Baseline single XGB Stage 1
# =============================================================================

def walk_forward_stage1_baseline(X, y_s1):
    n = len(y_s1)
    s1_pred = np.full(n, np.nan)
    s1_conf = np.full(n, np.nan)
    model = None
    last_train = -1

    for i in range(MIN_TRAIN, n):
        if model is None or (i - last_train) >= RETRAIN_EVERY:
            model = XGBClassifier(
                **XGB_PARAMS, objective="binary:logistic", eval_metric="logloss",
            )
            model.fit(X[:i], y_s1[:i])
            last_train = i

        prob = model.predict_proba(X[i:i+1])[0]
        cls = int(prob[1] >= 0.5)
        s1_pred[i] = cls
        s1_conf[i] = prob[cls]

    return s1_pred, s1_conf


# =============================================================================
# Variant 1: Ensemble Stage 1 (XGB + LightGBM + LogReg)
# =============================================================================

def walk_forward_stage1_ensemble(X, y_s1):
    n = len(y_s1)
    s1_pred = np.full(n, np.nan)
    s1_conf = np.full(n, np.nan)

    model_xgb = None
    model_lgbm = None
    model_lr = None
    scaler = None
    last_train = -1

    for i in range(MIN_TRAIN, n):
        if model_xgb is None or (i - last_train) >= RETRAIN_EVERY:
            X_train, y_train = X[:i], y_s1[:i]

            model_xgb = XGBClassifier(
                **XGB_PARAMS, objective="binary:logistic", eval_metric="logloss",
            )
            model_xgb.fit(X_train, y_train)

            model_lgbm = LGBMClassifier(**LGBM_PARAMS)
            model_lgbm.fit(X_train, y_train)

            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X_train)
            model_lr = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
            model_lr.fit(X_scaled, y_train)

            last_train = i

        xi = X[i:i+1]
        p_xgb = model_xgb.predict_proba(xi)[0]
        p_lgbm = model_lgbm.predict_proba(xi)[0]
        p_lr = model_lr.predict_proba(scaler.transform(xi))[0]

        # Soft vote: average probabilities
        avg_prob = (p_xgb + p_lgbm + p_lr) / 3.0
        cls = int(avg_prob[1] >= 0.5)
        s1_pred[i] = cls
        s1_conf[i] = avg_prob[cls]

    return s1_pred, s1_conf


# =============================================================================
# Variant 2: Feature selection
# =============================================================================

def get_top_k_features(X, y, feat_cols, k):
    """Train a full XGB model and return indices of top-K features by importance."""
    model = XGBClassifier(
        **XGB_PARAMS, objective="binary:logistic", eval_metric="logloss",
    )
    model.fit(X, y)
    importances = model.feature_importances_
    top_idx = np.argsort(importances)[::-1][:k]
    top_idx_sorted = np.sort(top_idx)  # keep original order
    top_names = [feat_cols[i] for i in top_idx_sorted]
    print(f"    Top-{k} features: {top_names}")
    return top_idx_sorted, top_names


def walk_forward_stage1_feature_selected(X_full, y_s1, feat_cols, k):
    """Walk-forward Stage 1 with feature selection."""
    # Get top-K from full training data (using first MIN_TRAIN as proxy)
    top_idx, top_names = get_top_k_features(X_full[:MIN_TRAIN], y_s1[:MIN_TRAIN], feat_cols, k)
    X = X_full[:, top_idx]
    return walk_forward_stage1_baseline(X, y_s1), top_idx


def walk_forward_stage2_feature_selected(X_full, y_3class, s1_pred_class, s1_conf, stage1_threshold, feat_cols, k):
    """Run Stage 2 with its own top-K features."""
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)
    y_s2 = np.where(y_3class == 2, 1, 0).astype(int)
    trend_mask = y_s1 == 1

    # Get top-K for Stage 2 from trend days in training window
    train_trend = trend_mask[:MIN_TRAIN]
    if train_trend.sum() >= 20:
        top_idx, top_names = get_top_k_features(
            X_full[:MIN_TRAIN][train_trend], y_s2[:MIN_TRAIN][train_trend], feat_cols, k
        )
        print(f"    Stage 2 top-{k}: {top_names}")
    else:
        top_idx = np.arange(min(k, X_full.shape[1]))

    X = X_full[:, top_idx]
    return run_stage2(X, y_3class, s1_pred_class, s1_conf, stage1_threshold)


# =============================================================================
# Variant 3: Gap routing
# =============================================================================

def classify_gap(gap_val):
    if np.isnan(gap_val):
        return "missing"
    if gap_val > 0.003:
        return "gap_up"
    if gap_val < -0.003:
        return "gap_down"
    return "flat"


def walk_forward_stage1_gap_routed(X, y_s1, gap_values):
    """Walk-forward Stage 1 with separate models per gap bucket."""
    n = len(y_s1)
    s1_pred = np.full(n, np.nan)
    s1_conf = np.full(n, np.nan)

    gap_buckets = np.array([classify_gap(g) for g in gap_values])

    # Models per bucket
    models = {}
    last_trains = {}

    # Also maintain a global fallback model
    global_model = None
    global_last_train = -1

    for i in range(MIN_TRAIN, n):
        # Retrain global model
        if global_model is None or (i - global_last_train) >= RETRAIN_EVERY:
            global_model = XGBClassifier(
                **XGB_PARAMS, objective="binary:logistic", eval_metric="logloss",
            )
            global_model.fit(X[:i], y_s1[:i])
            global_last_train = i

        bucket = gap_buckets[i]

        # Retrain bucket model if needed
        if bucket != "missing":
            should_retrain = (bucket not in models) or (i - last_trains.get(bucket, -1)) >= RETRAIN_EVERY
            if should_retrain:
                bucket_mask = gap_buckets[:i] == bucket
                if bucket_mask.sum() >= MIN_TRAIN:
                    m = XGBClassifier(
                        **XGB_PARAMS, objective="binary:logistic", eval_metric="logloss",
                    )
                    m.fit(X[:i][bucket_mask], y_s1[:i][bucket_mask])
                    models[bucket] = m
                    last_trains[bucket] = i

        # Predict
        if bucket != "missing" and bucket in models:
            prob = models[bucket].predict_proba(X[i:i+1])[0]
        else:
            prob = global_model.predict_proba(X[i:i+1])[0]

        cls = int(prob[1] >= 0.5)
        s1_pred[i] = cls
        s1_conf[i] = prob[cls]

    return s1_pred, s1_conf


# =============================================================================
# Variant 5: Ensemble + Gap-routed
# =============================================================================

def walk_forward_stage1_ensemble_gap_routed(X, y_s1, gap_values):
    """Ensemble (XGB+LGBM+LogReg) per gap bucket with global fallback."""
    n = len(y_s1)
    s1_pred = np.full(n, np.nan)
    s1_conf = np.full(n, np.nan)

    gap_buckets = np.array([classify_gap(g) for g in gap_values])

    # Per-bucket ensemble models
    bucket_models = {}  # bucket -> (xgb, lgbm, lr, scaler)
    last_trains = {}

    # Global ensemble fallback
    global_models = None
    global_last_train = -1

    def _train_ensemble(X_tr, y_tr):
        xgb = XGBClassifier(**XGB_PARAMS, objective="binary:logistic", eval_metric="logloss")
        xgb.fit(X_tr, y_tr)
        lgbm = LGBMClassifier(**LGBM_PARAMS)
        lgbm.fit(X_tr, y_tr)
        sc = StandardScaler()
        X_sc = sc.fit_transform(X_tr)
        lr = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
        lr.fit(X_sc, y_tr)
        return (xgb, lgbm, lr, sc)

    def _predict_ensemble(models_tuple, xi):
        xgb, lgbm, lr, sc = models_tuple
        p1 = xgb.predict_proba(xi)[0]
        p2 = lgbm.predict_proba(xi)[0]
        p3 = lr.predict_proba(sc.transform(xi))[0]
        return (p1 + p2 + p3) / 3.0

    for i in range(MIN_TRAIN, n):
        # Retrain global ensemble
        if global_models is None or (i - global_last_train) >= RETRAIN_EVERY:
            global_models = _train_ensemble(X[:i], y_s1[:i])
            global_last_train = i

        bucket = gap_buckets[i]

        # Retrain bucket ensemble
        if bucket != "missing":
            should_retrain = (bucket not in bucket_models) or (i - last_trains.get(bucket, -1)) >= RETRAIN_EVERY
            if should_retrain:
                bucket_mask = gap_buckets[:i] == bucket
                if bucket_mask.sum() >= MIN_TRAIN:
                    bucket_models[bucket] = _train_ensemble(X[:i][bucket_mask], y_s1[:i][bucket_mask])
                    last_trains[bucket] = i

        # Predict
        xi = X[i:i+1]
        if bucket != "missing" and bucket in bucket_models:
            avg_prob = _predict_ensemble(bucket_models[bucket], xi)
        else:
            avg_prob = _predict_ensemble(global_models, xi)

        cls = int(avg_prob[1] >= 0.5)
        s1_pred[i] = cls
        s1_conf[i] = avg_prob[cls]

    return s1_pred, s1_conf


# =============================================================================
# Evaluation
# =============================================================================

def evaluate_variant(name, y_3class, s1_pred, s1_conf, s2_pred, s2_conf, final_pred, nifty_returns):
    """Compute all metrics for a variant."""
    test_mask = np.arange(len(y_3class)) >= MIN_TRAIN

    # Stage 1 accuracy
    y_s1_true = np.where(y_3class == 1, 0, 1)
    s1_valid = test_mask & ~np.isnan(s1_pred)
    s1_acc = (y_s1_true[s1_valid] == s1_pred[s1_valid].astype(int)).mean() if s1_valid.sum() > 0 else 0

    # Stage 2 accuracy
    y_s2_true = np.where(y_3class == 2, 1, 0)
    s2_valid = test_mask & ~np.isnan(s2_pred) & (y_s1_true == 1)
    s2_acc = (y_s2_true[s2_valid] == s2_pred[s2_valid].astype(int)).mean() if s2_valid.sum() > 0 else 0

    # Final predictions
    test_preds = final_pred[test_mask]
    test_y = y_3class[test_mask]
    test_nr = nifty_returns[test_mask]

    # Map to 3-class
    mapping = {"Trend-Up": 2, "Trend-Down": 0, "Range": 1}
    committed_mask = np.array([p in mapping for p in test_preds])
    committed_pct = committed_mask.mean() * 100
    abstention_rate = 100 - committed_pct

    if committed_mask.sum() > 0:
        committed_y = test_y[committed_mask]
        committed_p = np.array([mapping[p] for p in test_preds[committed_mask]])
        committed_acc = (committed_y == committed_p).mean()
    else:
        committed_acc = 0

    # Return separation
    return_sep = {}
    for cls in ["Trend-Up", "Trend-Down", "Range", "Trend-Unknown", "Uncertain"]:
        cls_mask = test_preds == cls
        if cls_mask.sum() > 0:
            return_sep[cls] = {
                "mean_return": test_nr[cls_mask].mean() * 100,
                "count": int(cls_mask.sum()),
            }

    return {
        "name": name,
        "s1_acc": s1_acc,
        "s1_n": int(s1_valid.sum()),
        "s2_acc": s2_acc,
        "s2_n": int(s2_valid.sum()),
        "committed_acc": committed_acc,
        "committed_pct": committed_pct,
        "abstention_rate": abstention_rate,
        "return_sep": return_sep,
    }


def print_comparison(results):
    """Print comparison table."""
    print("\n" + "=" * 100)
    print("  COMPARISON: ALL VARIANTS")
    print("=" * 100)

    header = f"  {'Variant':<30} {'S1 Acc':>8} {'S2 Acc':>8} {'Commit%':>9} {'CommitAcc':>10} {'Abstain%':>10}"
    print(header)
    print("  " + "-" * 96)

    for r in results:
        print(f"  {r['name']:<30} {r['s1_acc']:>7.1%} {r['s2_acc']:>7.1%} "
              f"{r['committed_pct']:>8.1f}% {r['committed_acc']:>9.1%} {r['abstention_rate']:>9.1f}%")

    # Return separation detail
    print("\n" + "-" * 100)
    print("  RETURN SEPARATION (mean nifty_return % per predicted class)")
    print("-" * 100)

    for r in results:
        print(f"\n  {r['name']}:")
        for cls, stats in r["return_sep"].items():
            print(f"    {cls:<14} mean={stats['mean_return']:+.3f}%  n={stats['count']}")

    # Delta vs baseline
    if len(results) >= 2:
        baseline = results[0]
        print("\n" + "-" * 100)
        print("  DELTA vs BASELINE")
        print("-" * 100)
        print(f"  {'Variant':<30} {'ΔS1 Acc':>10} {'ΔCommitAcc':>12} {'ΔCommit%':>10}")
        print("  " + "-" * 65)
        for r in results[1:]:
            ds1 = r["s1_acc"] - baseline["s1_acc"]
            dca = r["committed_acc"] - baseline["committed_acc"]
            dcp = r["committed_pct"] - baseline["committed_pct"]
            print(f"  {r['name']:<30} {ds1:>+9.1%} {dca:>+11.1%} {dcp:>+9.1f}%")


# =============================================================================
# Main
# =============================================================================

def main():
    print("=" * 100)
    print("  Two-Stage Regime Predictor — Stage 1 Improvements")
    print("=" * 100)

    df = load_data()
    X_full, feat_cols = get_feature_matrix(df)
    y_3class = df["coincident_label"].map(E3_LABEL_MAP).values.astype(int)
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)  # Range=0, Trend=1
    nifty_returns = df["nifty_return"].fillna(0).values

    # Gap values for routing
    gap_col = "gift_overnight_gap_pct"
    gap_values = df[gap_col].values if gap_col in df.columns else np.full(len(df), np.nan)

    print(f"\nFeatures: {len(feat_cols)}")
    print(f"Walk-forward: min_train={MIN_TRAIN}, retrain_every={RETRAIN_EVERY}")
    print(f"Stage 1 threshold: {STAGE1_THRESHOLD}, Stage 2 threshold: {STAGE2_THRESHOLD}")

    results = []

    # --- Variant 0: Baseline ---
    print("\n" + "-" * 100)
    print("  [0] Baseline: Single XGB Stage 1")
    print("-" * 100)
    s1_pred, s1_conf = walk_forward_stage1_baseline(X_full, y_s1)
    final_pred, s2_pred, s2_conf = run_stage2(X_full, y_3class, s1_pred, s1_conf, STAGE1_THRESHOLD)
    r = evaluate_variant("Baseline (XGB)", y_3class, s1_pred, s1_conf, s2_pred, s2_conf, final_pred, nifty_returns)
    results.append(r)
    print(f"  Stage 1 acc: {r['s1_acc']:.1%} | Stage 2 acc: {r['s2_acc']:.1%} | "
          f"Committed: {r['committed_acc']:.1%} ({r['committed_pct']:.1f}%)")

    # --- Variant 1: Ensemble Stage 1 ---
    print("\n" + "-" * 100)
    print("  [1] Ensemble Stage 1 (XGB + LightGBM + LogReg)")
    print("-" * 100)
    s1_pred, s1_conf = walk_forward_stage1_ensemble(X_full, y_s1)
    final_pred, s2_pred, s2_conf = run_stage2(X_full, y_3class, s1_pred, s1_conf, STAGE1_THRESHOLD)
    r = evaluate_variant("Ensemble (soft vote)", y_3class, s1_pred, s1_conf, s2_pred, s2_conf, final_pred, nifty_returns)
    results.append(r)
    print(f"  Stage 1 acc: {r['s1_acc']:.1%} | Stage 2 acc: {r['s2_acc']:.1%} | "
          f"Committed: {r['committed_acc']:.1%} ({r['committed_pct']:.1f}%)")

    # --- Variant 2a: Feature-selected top-15 ---
    print("\n" + "-" * 100)
    print("  [2a] Feature-selected Stage 1 (top-15)")
    print("-" * 100)
    (s1_pred, s1_conf), s1_top_idx = walk_forward_stage1_feature_selected(X_full, y_s1, feat_cols, k=15)
    final_pred, s2_pred, s2_conf = walk_forward_stage2_feature_selected(
        X_full, y_3class, s1_pred, s1_conf, STAGE1_THRESHOLD, feat_cols, k=15
    )
    r = evaluate_variant("Feature-sel (top-15)", y_3class, s1_pred, s1_conf, s2_pred, s2_conf, final_pred, nifty_returns)
    results.append(r)
    print(f"  Stage 1 acc: {r['s1_acc']:.1%} | Stage 2 acc: {r['s2_acc']:.1%} | "
          f"Committed: {r['committed_acc']:.1%} ({r['committed_pct']:.1f}%)")

    # --- Variant 2b: Feature-selected top-20 ---
    print("\n" + "-" * 100)
    print("  [2b] Feature-selected Stage 1 (top-20)")
    print("-" * 100)
    (s1_pred, s1_conf), s1_top_idx = walk_forward_stage1_feature_selected(X_full, y_s1, feat_cols, k=20)
    final_pred, s2_pred, s2_conf = walk_forward_stage2_feature_selected(
        X_full, y_3class, s1_pred, s1_conf, STAGE1_THRESHOLD, feat_cols, k=20
    )
    r = evaluate_variant("Feature-sel (top-20)", y_3class, s1_pred, s1_conf, s2_pred, s2_conf, final_pred, nifty_returns)
    results.append(r)
    print(f"  Stage 1 acc: {r['s1_acc']:.1%} | Stage 2 acc: {r['s2_acc']:.1%} | "
          f"Committed: {r['committed_acc']:.1%} ({r['committed_pct']:.1f}%)")

    # --- Variant 3: Gap-routed ---
    print("\n" + "-" * 100)
    print("  [3] Gap-routed Stage 1")
    print("-" * 100)
    gap_dist = pd.Series([classify_gap(g) for g in gap_values]).value_counts()
    print(f"  Gap distribution: {dict(gap_dist)}")
    s1_pred, s1_conf = walk_forward_stage1_gap_routed(X_full, y_s1, gap_values)
    final_pred, s2_pred, s2_conf = run_stage2(X_full, y_3class, s1_pred, s1_conf, STAGE1_THRESHOLD)
    r = evaluate_variant("Gap-routed", y_3class, s1_pred, s1_conf, s2_pred, s2_conf, final_pred, nifty_returns)
    results.append(r)
    print(f"  Stage 1 acc: {r['s1_acc']:.1%} | Stage 2 acc: {r['s2_acc']:.1%} | "
          f"Committed: {r['committed_acc']:.1%} ({r['committed_pct']:.1f}%)")

    # --- Variant 4: Ensemble + Gap-routed ---
    print("\n" + "-" * 100)
    print("  [4] Ensemble + Gap-routed Stage 1")
    print("-" * 100)
    s1_pred, s1_conf = walk_forward_stage1_ensemble_gap_routed(X_full, y_s1, gap_values)
    final_pred, s2_pred, s2_conf = run_stage2(X_full, y_3class, s1_pred, s1_conf, STAGE1_THRESHOLD)
    r = evaluate_variant("Ensemble + Gap-routed", y_3class, s1_pred, s1_conf, s2_pred, s2_conf, final_pred, nifty_returns)
    results.append(r)
    print(f"  Stage 1 acc: {r['s1_acc']:.1%} | Stage 2 acc: {r['s2_acc']:.1%} | "
          f"Committed: {r['committed_acc']:.1%} ({r['committed_pct']:.1f}%)")

    # --- Comparison ---
    print_comparison(results)
    print()


if __name__ == "__main__":
    main()
