"""Comprehensive improvement sweep on the Ensemble + Gap-Routed two-stage model.

Tests: hyperparameter tuning, walk-forward windows, stacking meta-learner,
cherry-picked 29 features, calibrated probabilities, threshold grid search.
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
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import StratifiedKFold

from src.preopen_features import PREOPEN_FEATURE_COLS

# --- Defaults (current best) ---
V1_FEATURES = PREOPEN_FEATURE_COLS[:27]
CHERRY_PICK_EXTRAS = ["prev_smallcap_vs_nifty", "prev_nifty_fut_oi_change_pct"]
E3_LABEL_MAP = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}

XGB_CONFIGS = {
    "A (current)": dict(
        max_depth=4, n_estimators=200, learning_rate=0.05,
        subsample=0.8, use_label_encoder=False, verbosity=0, random_state=42,
    ),
    "B (deeper)": dict(
        max_depth=6, n_estimators=300, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.8,
        use_label_encoder=False, verbosity=0, random_state=42,
    ),
    "C (shallow)": dict(
        max_depth=3, n_estimators=400, learning_rate=0.02,
        subsample=0.7, colsample_bytree=0.7, min_child_weight=5,
        use_label_encoder=False, verbosity=0, random_state=42,
    ),
}

LGBM_BASE = dict(
    max_depth=4, n_estimators=200, learning_rate=0.05,
    subsample=0.8, num_leaves=31, verbose=-1, random_state=42,
)

WINDOW_CONFIGS = {
    "A (126/63)": (126, 63),
    "B (252/63)": (252, 63),
    "C (126/21)": (126, 21),
    "D (252/21)": (252, 21),
}

S1_THRESHOLDS = [0.50, 0.52, 0.55, 0.57, 0.60, 0.65, 0.70]
S2_THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70]


# =============================================================================
# Data loading
# =============================================================================

def load_data():
    base = Path(__file__).resolve().parent.parent / "data"
    v1 = pd.read_csv(base / "preopen_feature_matrix.csv", parse_dates=["date"])
    v1["date"] = v1["date"].dt.date

    v2 = pd.read_csv(base / "preopen_feature_matrix_v2.csv", parse_dates=["date"])
    v2["date"] = v2["date"].dt.date

    conn = psycopg2.connect(host="localhost", user="me", password="algotrix", dbname="atdb")
    try:
        labels = pd.read_sql(
            "SELECT date, coincident_label, nifty_return FROM regime_ground_truth ORDER BY date",
            conn,
        )
    finally:
        conn.close()

    labels["date"] = pd.to_datetime(labels["date"]).dt.date

    # Merge v1 features with labels
    df = labels.merge(v1, on="date", how="inner", suffixes=("", "_v1")).sort_values("date").reset_index(drop=True)
    df = df[df["coincident_label"].isin(["Trend-Up", "Range", "Trend-Down"])].reset_index(drop=True)

    # Merge v2 extras for cherry-pick
    v2_extras = v2[["date"] + CHERRY_PICK_EXTRAS].copy()
    df = df.merge(v2_extras, on="date", how="left", suffixes=("", "_v2"))

    print(f"Loaded {len(df)} days")
    dist = df["coincident_label"].value_counts()
    for lbl, cnt in dist.items():
        print(f"  {lbl:<12} {cnt:>5} ({cnt/len(df)*100:.1f}%)")
    return df


def get_features(df, feat_cols):
    return df[feat_cols].fillna(0).values


# =============================================================================
# Gap classification
# =============================================================================

def classify_gap(gap_val):
    if np.isnan(gap_val):
        return "missing"
    if gap_val > 0.003:
        return "gap_up"
    if gap_val < -0.003:
        return "gap_down"
    return "flat"


# =============================================================================
# Core: Ensemble + Gap-Routed Stage 1 (parameterized)
# =============================================================================

def walk_forward_ensemble_gap_routed(X, y_s1, gap_values, xgb_params, min_train, retrain_every, calibrate=False):
    """Ensemble (XGB+LGBM+LogReg) per gap bucket with global fallback."""
    n = len(y_s1)
    s1_pred = np.full(n, np.nan)
    s1_conf = np.full(n, np.nan)

    gap_buckets = np.array([classify_gap(g) for g in gap_values])

    # Match LGBM depth/estimators to XGB config
    lgbm_params = LGBM_BASE.copy()
    lgbm_params["max_depth"] = xgb_params.get("max_depth", 4)
    lgbm_params["n_estimators"] = xgb_params.get("n_estimators", 200)
    lgbm_params["learning_rate"] = xgb_params.get("learning_rate", 0.05)

    bucket_models = {}
    last_trains = {}
    global_models = None
    global_last_train = -1

    def _train_ensemble(X_tr, y_tr):
        xgb = XGBClassifier(**xgb_params, objective="binary:logistic", eval_metric="logloss")
        xgb.fit(X_tr, y_tr)
        lgbm = LGBMClassifier(**lgbm_params)
        lgbm.fit(X_tr, y_tr)
        sc = StandardScaler()
        X_sc = sc.fit_transform(X_tr)
        lr = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
        lr.fit(X_sc, y_tr)

        if calibrate and len(X_tr) >= 50:
            # Wrap ensemble as a calibrated classifier via Platt scaling
            # We calibrate the XGB as representative (main model)
            cal = CalibratedClassifierCV(xgb, method="sigmoid", cv=min(5, max(2, int(y_tr.sum()))))
            try:
                cal.fit(X_tr, y_tr)
            except Exception:
                cal = None
            return (xgb, lgbm, lr, sc, cal)
        return (xgb, lgbm, lr, sc, None)

    def _predict_ensemble(models_tuple, xi):
        xgb, lgbm, lr, sc, cal = models_tuple
        p1 = xgb.predict_proba(xi)[0]
        p2 = lgbm.predict_proba(xi)[0]
        p3 = lr.predict_proba(sc.transform(xi))[0]
        avg = (p1 + p2 + p3) / 3.0

        if cal is not None:
            # Use calibrated XGB probability blended with ensemble
            p_cal = cal.predict_proba(xi)[0]
            avg = (avg + p_cal) / 2.0

        return avg

    for i in range(min_train, n):
        if global_models is None or (i - global_last_train) >= retrain_every:
            global_models = _train_ensemble(X[:i], y_s1[:i])
            global_last_train = i

        bucket = gap_buckets[i]
        if bucket != "missing":
            should_retrain = (bucket not in bucket_models) or (i - last_trains.get(bucket, -1)) >= retrain_every
            if should_retrain:
                bucket_mask = gap_buckets[:i] == bucket
                if bucket_mask.sum() >= min_train:
                    bucket_models[bucket] = _train_ensemble(X[:i][bucket_mask], y_s1[:i][bucket_mask])
                    last_trains[bucket] = i

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
# Stacking meta-learner Stage 1
# =============================================================================

def walk_forward_stacking(X, y_s1, gap_values, xgb_params, min_train, retrain_every):
    """Stacking: XGB + LGBM + LogReg base models, LogReg meta-learner on OOF predictions."""
    n = len(y_s1)
    s1_pred = np.full(n, np.nan)
    s1_conf = np.full(n, np.nan)

    gap_buckets = np.array([classify_gap(g) for g in gap_values])

    lgbm_params = LGBM_BASE.copy()
    lgbm_params["max_depth"] = xgb_params.get("max_depth", 4)
    lgbm_params["n_estimators"] = xgb_params.get("n_estimators", 200)
    lgbm_params["learning_rate"] = xgb_params.get("learning_rate", 0.05)

    base_models = None
    meta_model = None
    meta_scaler = None
    last_train = -1

    for i in range(min_train, n):
        if base_models is None or (i - last_train) >= retrain_every:
            X_tr, y_tr = X[:i], y_s1[:i]

            # Train base models on full training data
            xgb = XGBClassifier(**xgb_params, objective="binary:logistic", eval_metric="logloss")
            xgb.fit(X_tr, y_tr)
            lgbm = LGBMClassifier(**lgbm_params)
            lgbm.fit(X_tr, y_tr)
            sc = StandardScaler()
            X_sc = sc.fit_transform(X_tr)
            lr = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
            lr.fit(X_sc, y_tr)

            # Get OOF predictions for meta-learner
            n_splits = min(5, max(2, int(y_tr.sum()), int((1-y_tr).sum())))
            try:
                skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
                oof_preds = np.zeros((len(X_tr), 3))  # 3 base models

                for fold_train, fold_val in skf.split(X_tr, y_tr):
                    # XGB
                    m = XGBClassifier(**xgb_params, objective="binary:logistic", eval_metric="logloss")
                    m.fit(X_tr[fold_train], y_tr[fold_train])
                    oof_preds[fold_val, 0] = m.predict_proba(X_tr[fold_val])[:, 1]

                    # LGBM
                    m = LGBMClassifier(**lgbm_params)
                    m.fit(X_tr[fold_train], y_tr[fold_train])
                    oof_preds[fold_val, 1] = m.predict_proba(X_tr[fold_val])[:, 1]

                    # LogReg
                    s = StandardScaler()
                    X_f_sc = s.fit_transform(X_tr[fold_train])
                    m = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
                    m.fit(X_f_sc, y_tr[fold_train])
                    oof_preds[fold_val, 2] = m.predict_proba(s.transform(X_tr[fold_val]))[:, 1]

                # Train meta-learner
                meta_scaler = StandardScaler()
                oof_scaled = meta_scaler.fit_transform(oof_preds)
                meta_model = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
                meta_model.fit(oof_scaled, y_tr)
            except Exception:
                meta_model = None

            base_models = (xgb, lgbm, lr, sc)
            last_train = i

        # Predict
        xi = X[i:i+1]
        xgb, lgbm, lr, sc = base_models
        p_xgb = xgb.predict_proba(xi)[0, 1]
        p_lgbm = lgbm.predict_proba(xi)[0, 1]
        p_lr = lr.predict_proba(sc.transform(xi))[0, 1]

        if meta_model is not None:
            meta_input = meta_scaler.transform([[p_xgb, p_lgbm, p_lr]])
            prob_trend = meta_model.predict_proba(meta_input)[0, 1]
        else:
            prob_trend = (p_xgb + p_lgbm + p_lr) / 3.0

        prob = np.array([1 - prob_trend, prob_trend])
        cls = int(prob[1] >= 0.5)
        s1_pred[i] = cls
        s1_conf[i] = prob[cls]

    return s1_pred, s1_conf


# =============================================================================
# Stage 2 (parameterized)
# =============================================================================

def run_stage2(X, y_3class, s1_pred_class, s1_conf, s1_threshold, s2_threshold,
               xgb_params, min_train, retrain_every):
    n = len(y_3class)
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)
    y_s2 = np.where(y_3class == 2, 1, 0).astype(int)
    trend_mask_full = y_s1 == 1

    final_pred = np.full(n, "", dtype=object)
    s2_pred = np.full(n, np.nan)
    s2_conf = np.full(n, np.nan)

    model_s2 = None
    last_train_s2 = -1

    for i in range(min_train, n):
        if model_s2 is None or (i - last_train_s2) >= retrain_every:
            trend_train = trend_mask_full[:i]
            if trend_train.sum() >= 20:
                model_s2 = XGBClassifier(
                    **xgb_params, objective="binary:logistic", eval_metric="logloss",
                )
                model_s2.fit(X[:i][trend_train], y_s2[:i][trend_train])
                last_train_s2 = i

        if np.isnan(s1_conf[i]):
            continue

        if s1_conf[i] < s1_threshold:
            final_pred[i] = "Uncertain"
            continue

        if s1_pred_class[i] == 0:
            final_pred[i] = "Range"
            continue

        if model_s2 is None:
            final_pred[i] = "Trend-Unknown"
            continue

        s2_prob = model_s2.predict_proba(X[i:i+1])[0]
        s2_class = int(s2_prob[1] >= 0.5)
        s2_confidence = s2_prob[s2_class]
        s2_pred[i] = s2_class
        s2_conf[i] = s2_confidence

        if s2_confidence < s2_threshold:
            final_pred[i] = "Trend-Unknown"
        elif s2_class == 1:
            final_pred[i] = "Trend-Up"
        else:
            final_pred[i] = "Trend-Down"

    return final_pred, s2_pred, s2_conf


# =============================================================================
# Evaluation
# =============================================================================

def evaluate(name, y_3class, s1_pred, s1_conf, s2_pred, s2_conf, final_pred,
             nifty_returns, min_train):
    test_mask = np.arange(len(y_3class)) >= min_train
    y_s1_true = np.where(y_3class == 1, 0, 1)
    s1_valid = test_mask & ~np.isnan(s1_pred)
    s1_acc = (y_s1_true[s1_valid] == s1_pred[s1_valid].astype(int)).mean() if s1_valid.sum() > 0 else 0

    y_s2_true = np.where(y_3class == 2, 1, 0)
    s2_valid = test_mask & ~np.isnan(s2_pred) & (y_s1_true == 1)
    s2_acc = (y_s2_true[s2_valid] == s2_pred[s2_valid].astype(int)).mean() if s2_valid.sum() > 0 else 0

    test_preds = final_pred[test_mask]
    test_y = y_3class[test_mask]
    test_nr = nifty_returns[test_mask]

    mapping = {"Trend-Up": 2, "Trend-Down": 0, "Range": 1}
    committed_mask = np.array([p in mapping for p in test_preds])
    committed_pct = committed_mask.mean() * 100

    if committed_mask.sum() > 0:
        committed_y = test_y[committed_mask]
        committed_p = np.array([mapping[p] for p in test_preds[committed_mask]])
        committed_acc = (committed_y == committed_p).mean()
    else:
        committed_acc = 0

    # Return separation
    return_sep = {}
    for cls in ["Trend-Up", "Trend-Down", "Range"]:
        cls_mask = test_preds == cls
        if cls_mask.sum() > 0:
            return_sep[cls] = test_nr[cls_mask].mean() * 100

    up_mean = return_sep.get("Trend-Up", 0)
    down_mean = return_sep.get("Trend-Down", 0)
    spread = up_mean - down_mean

    return {
        "name": name,
        "s1_acc": s1_acc,
        "s2_acc": s2_acc,
        "committed_acc": committed_acc,
        "committed_pct": committed_pct,
        "up_mean": up_mean,
        "down_mean": down_mean,
        "spread": spread,
        "return_sep": return_sep,
    }


def print_results_table(results, title):
    print(f"\n{'=' * 120}")
    print(f"  {title}")
    print(f"{'=' * 120}")
    hdr = (f"  {'Variant':<45} {'S1 Acc':>7} {'S2 Acc':>7} {'CommAcc':>8} {'Comm%':>7}"
           f"  {'Up%':>7} {'Down%':>7} {'Spread':>7}")
    print(hdr)
    print("  " + "-" * 115)
    for r in results:
        print(f"  {r['name']:<45} {r['s1_acc']:>6.1%} {r['s2_acc']:>6.1%} "
              f"{r['committed_acc']:>7.1%} {r['committed_pct']:>6.1f}%"
              f"  {r['up_mean']:>+6.3f} {r['down_mean']:>+6.3f} {r['spread']:>6.3f}")


# =============================================================================
# Full pipeline: Stage 1 -> Stage 2 -> evaluate
# =============================================================================

def run_pipeline(name, X, y_3class, gap_values, nifty_returns,
                 xgb_params, min_train, retrain_every,
                 s1_threshold=0.55, s2_threshold=0.60,
                 stage1_fn="ensemble_gap", calibrate=False):
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)

    if stage1_fn == "ensemble_gap":
        s1_pred, s1_conf = walk_forward_ensemble_gap_routed(
            X, y_s1, gap_values, xgb_params, min_train, retrain_every, calibrate=calibrate)
    elif stage1_fn == "stacking":
        s1_pred, s1_conf = walk_forward_stacking(
            X, y_s1, gap_values, xgb_params, min_train, retrain_every)
    else:
        raise ValueError(f"Unknown stage1_fn: {stage1_fn}")

    final_pred, s2_pred, s2_conf = run_stage2(
        X, y_3class, s1_pred, s1_conf, s1_threshold, s2_threshold,
        xgb_params, min_train, retrain_every)

    return evaluate(name, y_3class, s1_pred, s1_conf, s2_pred, s2_conf,
                    final_pred, nifty_returns, min_train), s1_pred, s1_conf


# =============================================================================
# Main
# =============================================================================

def main():
    print("=" * 120)
    print("  COMPREHENSIVE IMPROVEMENT SWEEP")
    print("  Base: Ensemble + Gap-Routed Two-Stage Model")
    print("=" * 120)

    df = load_data()
    y_3class = df["coincident_label"].map(E3_LABEL_MAP).values.astype(int)
    nifty_returns = df["nifty_return"].fillna(0).values
    gap_col = "gift_overnight_gap_pct"
    gap_values = df[gap_col].values if gap_col in df.columns else np.full(len(df), np.nan)

    # Feature sets
    v1_cols = [c for c in V1_FEATURES if c in df.columns]
    cherry_cols = v1_cols + [c for c in CHERRY_PICK_EXTRAS if c in df.columns]

    X_v1 = get_features(df, v1_cols)
    X_29 = get_features(df, cherry_cols)

    all_results = []

    # =========================================================================
    # 1. HYPERPARAMETER TUNING
    # =========================================================================
    print("\n\n" + "#" * 120)
    print("  SECTION 1: HYPERPARAMETER TUNING (3 XGB configs)")
    print("#" * 120)

    hp_results = []
    for cfg_name, xgb_p in XGB_CONFIGS.items():
        name = f"HP {cfg_name}"
        print(f"\n  Running: {name} ...")
        r, _, _ = run_pipeline(name, X_v1, y_3class, gap_values, nifty_returns,
                               xgb_p, min_train=126, retrain_every=63)
        hp_results.append(r)
        print(f"    S1={r['s1_acc']:.1%} CommAcc={r['committed_acc']:.1%} Comm%={r['committed_pct']:.1f}%")

    print_results_table(hp_results, "HYPERPARAMETER TUNING RESULTS")
    all_results.extend(hp_results)

    # Pick best HP config by committed accuracy
    best_hp = max(hp_results, key=lambda r: r["committed_acc"])
    best_hp_name = best_hp["name"].replace("HP ", "")
    best_xgb = XGB_CONFIGS[best_hp_name]
    print(f"\n  >>> Best HP config: {best_hp_name} (CommAcc={best_hp['committed_acc']:.1%})")

    # =========================================================================
    # 2. WALK-FORWARD WINDOW TUNING
    # =========================================================================
    print("\n\n" + "#" * 120)
    print(f"  SECTION 2: WALK-FORWARD WINDOW TUNING (using HP {best_hp_name})")
    print("#" * 120)

    wf_results = []
    for wf_name, (mt, re) in WINDOW_CONFIGS.items():
        name = f"WF {wf_name}"
        print(f"\n  Running: {name} ...")
        r, _, _ = run_pipeline(name, X_v1, y_3class, gap_values, nifty_returns,
                               best_xgb, min_train=mt, retrain_every=re)
        wf_results.append(r)
        print(f"    S1={r['s1_acc']:.1%} CommAcc={r['committed_acc']:.1%} Comm%={r['committed_pct']:.1f}%")

    print_results_table(wf_results, "WALK-FORWARD WINDOW RESULTS")
    all_results.extend(wf_results)

    best_wf = max(wf_results, key=lambda r: r["committed_acc"])
    best_wf_name = best_wf["name"].replace("WF ", "")
    best_mt, best_re = WINDOW_CONFIGS[best_wf_name]
    print(f"\n  >>> Best WF config: {best_wf_name} (CommAcc={best_wf['committed_acc']:.1%})")

    # =========================================================================
    # 3. STACKING META-LEARNER
    # =========================================================================
    print("\n\n" + "#" * 120)
    print(f"  SECTION 3: STACKING META-LEARNER (HP {best_hp_name}, WF {best_wf_name})")
    print("#" * 120)

    print("\n  Running: Stacking ...")
    r_stack, _, _ = run_pipeline("Stacking meta-learner", X_v1, y_3class, gap_values,
                                 nifty_returns, best_xgb, best_mt, best_re,
                                 stage1_fn="stacking")
    print(f"    S1={r_stack['s1_acc']:.1%} CommAcc={r_stack['committed_acc']:.1%} Comm%={r_stack['committed_pct']:.1f}%")
    all_results.append(r_stack)

    # =========================================================================
    # 4. CHERRY-PICKED 29 FEATURES
    # =========================================================================
    print("\n\n" + "#" * 120)
    print(f"  SECTION 4: CHERRY-PICKED 29 FEATURES (HP {best_hp_name}, WF {best_wf_name})")
    print("#" * 120)

    print(f"\n  Features: {len(cherry_cols)} ({len(v1_cols)} base + {CHERRY_PICK_EXTRAS})")
    print("\n  Running: Cherry-pick 29 ...")
    r_29, _, _ = run_pipeline("Cherry-pick 29 features", X_29, y_3class, gap_values,
                              nifty_returns, best_xgb, best_mt, best_re)
    print(f"    S1={r_29['s1_acc']:.1%} CommAcc={r_29['committed_acc']:.1%} Comm%={r_29['committed_pct']:.1f}%")
    all_results.append(r_29)

    # =========================================================================
    # 5. CALIBRATED PROBABILITIES
    # =========================================================================
    print("\n\n" + "#" * 120)
    print(f"  SECTION 5: CALIBRATED PROBABILITIES (Platt scaling)")
    print("#" * 120)

    print("\n  Running: Calibrated (27 features) ...")
    r_cal27, _, _ = run_pipeline("Calibrated (27 feat)", X_v1, y_3class, gap_values,
                                 nifty_returns, best_xgb, best_mt, best_re,
                                 calibrate=True)
    print(f"    S1={r_cal27['s1_acc']:.1%} CommAcc={r_cal27['committed_acc']:.1%} Comm%={r_cal27['committed_pct']:.1f}%")
    all_results.append(r_cal27)

    print("\n  Running: Calibrated (29 features) ...")
    r_cal29, _, _ = run_pipeline("Calibrated (29 feat)", X_29, y_3class, gap_values,
                                 nifty_returns, best_xgb, best_mt, best_re,
                                 calibrate=True)
    print(f"    S1={r_cal29['s1_acc']:.1%} CommAcc={r_cal29['committed_acc']:.1%} Comm%={r_cal29['committed_pct']:.1f}%")
    all_results.append(r_cal29)

    # =========================================================================
    # Combined: best features + stacking + calibration
    # =========================================================================
    print("\n\n" + "#" * 120)
    print("  SECTION 5b: COMBINATIONS")
    print("#" * 120)

    print("\n  Running: Stacking + 29 feat ...")
    r_s29, _, _ = run_pipeline("Stacking + 29 feat", X_29, y_3class, gap_values,
                               nifty_returns, best_xgb, best_mt, best_re,
                               stage1_fn="stacking")
    print(f"    S1={r_s29['s1_acc']:.1%} CommAcc={r_s29['committed_acc']:.1%} Comm%={r_s29['committed_pct']:.1f}%")
    all_results.append(r_s29)

    print("\n  Running: Calibrated + 29 feat + stacking ...")
    # Can't directly combine stacking + calibration in the same way, so just note this
    # Instead, test calibrated ensemble + 29 feat as the combo
    # Already done above as r_cal29

    # =========================================================================
    # SUMMARY TABLE
    # =========================================================================
    print_results_table(all_results, "FULL SUMMARY — ALL VARIANTS")

    # =========================================================================
    # 6. THRESHOLD GRID SEARCH
    # =========================================================================
    # Find best variant overall
    best_variant = max(all_results, key=lambda r: r["committed_acc"])
    print(f"\n\n  >>> Best overall variant: {best_variant['name']} (CommAcc={best_variant['committed_acc']:.1%})")

    # Determine config for best variant
    # Identify which features/stage1 to use
    if "29" in best_variant["name"]:
        grid_X = X_29
    else:
        grid_X = X_v1

    if "Stacking" in best_variant["name"]:
        grid_s1fn = "stacking"
    else:
        grid_s1fn = "ensemble_gap"

    grid_calibrate = "Calibrat" in best_variant["name"]

    print("\n\n" + "#" * 120)
    print(f"  SECTION 6: THRESHOLD GRID SEARCH on '{best_variant['name']}'")
    print(f"  S1 thresholds: {S1_THRESHOLDS}")
    print(f"  S2 thresholds: {S2_THRESHOLDS}")
    print("#" * 120)

    # Pre-compute Stage 1 predictions once
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)
    if grid_s1fn == "ensemble_gap":
        s1_pred, s1_conf = walk_forward_ensemble_gap_routed(
            grid_X, y_s1, gap_values, best_xgb, best_mt, best_re, calibrate=grid_calibrate)
    else:
        s1_pred, s1_conf = walk_forward_stacking(
            grid_X, y_s1, gap_values, best_xgb, best_mt, best_re)

    grid_results = []
    for s1t in S1_THRESHOLDS:
        for s2t in S2_THRESHOLDS:
            final_pred, s2_pred, s2_conf = run_stage2(
                grid_X, y_3class, s1_pred, s1_conf, s1t, s2t,
                best_xgb, best_mt, best_re)
            r = evaluate(f"S1={s1t:.2f} S2={s2t:.2f}", y_3class, s1_pred, s1_conf,
                         s2_pred, s2_conf, final_pred, nifty_returns, best_mt)
            r["s1_threshold"] = s1t
            r["s2_threshold"] = s2t
            grid_results.append(r)

    # Print grid
    print(f"\n{'=' * 120}")
    print("  THRESHOLD GRID RESULTS")
    print(f"{'=' * 120}")
    print(f"  {'S1 Thr':>7} {'S2 Thr':>7} {'CommAcc':>8} {'Comm%':>7} {'Up%':>7} {'Down%':>7} {'Spread':>7}")
    print("  " + "-" * 60)
    for r in sorted(grid_results, key=lambda x: (-x["committed_acc"], -x["committed_pct"])):
        print(f"  {r['s1_threshold']:>7.2f} {r['s2_threshold']:>7.2f} "
              f"{r['committed_acc']:>7.1%} {r['committed_pct']:>6.1f}%"
              f"  {r['up_mean']:>+6.3f} {r['down_mean']:>+6.3f} {r['spread']:>6.3f}")

    # Pareto frontier: committed_acc vs committed_pct (coverage)
    print(f"\n{'=' * 120}")
    print("  PARETO FRONTIER (Committed Accuracy vs Coverage)")
    print(f"{'=' * 120}")

    # Sort by coverage ascending, find Pareto-optimal points
    sorted_grid = sorted(grid_results, key=lambda x: x["committed_pct"])
    pareto = []
    best_acc_so_far = -1
    for r in sorted(sorted_grid, key=lambda x: -x["committed_pct"]):
        # Pareto: no other point has both higher acc AND higher coverage
        if r["committed_acc"] > best_acc_so_far:
            pareto.append(r)
            best_acc_so_far = r["committed_acc"]

    pareto.sort(key=lambda x: x["committed_pct"])
    print(f"  {'S1 Thr':>7} {'S2 Thr':>7} {'CommAcc':>8} {'Comm%':>7} {'Up%':>7} {'Down%':>7} {'Spread':>7}")
    print("  " + "-" * 60)
    for r in pareto:
        print(f"  {r['s1_threshold']:>7.2f} {r['s2_threshold']:>7.2f} "
              f"{r['committed_acc']:>7.1%} {r['committed_pct']:>6.1f}%"
              f"  {r['up_mean']:>+6.3f} {r['down_mean']:>+6.3f} {r['spread']:>6.3f}")

    # Overall best
    print(f"\n{'=' * 120}")
    print("  RECOMMENDATIONS")
    print(f"{'=' * 120}")
    best_grid = max(grid_results, key=lambda r: r["committed_acc"])
    best_balanced = max(grid_results, key=lambda r: r["committed_acc"] * 0.7 + (r["committed_pct"]/100) * 0.3)
    print(f"  Highest accuracy:  S1={best_grid['s1_threshold']:.2f} S2={best_grid['s2_threshold']:.2f} "
          f"→ CommAcc={best_grid['committed_acc']:.1%}, Coverage={best_grid['committed_pct']:.1f}%")
    print(f"  Best balanced:     S1={best_balanced['s1_threshold']:.2f} S2={best_balanced['s2_threshold']:.2f} "
          f"→ CommAcc={best_balanced['committed_acc']:.1%}, Coverage={best_balanced['committed_pct']:.1f}%")
    print()


if __name__ == "__main__":
    main()
