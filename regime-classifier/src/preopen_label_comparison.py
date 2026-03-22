"""Pre-Open Predictability Comparison: Set A vs Set D.

Uses existing feature matrix + walk-forward XGBoost to compare
how well pre-open features can predict each label set.
"""

import numpy as np
import pandas as pd
import psycopg2
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix, classification_report
from xgboost import XGBClassifier

DB_DSN = "host=localhost dbname=atdb user=me password=algotrix"

MIN_TRAIN_DAYS = 126
RETRAIN_INTERVAL = 63

FEATURE_COLS = [
    "gift_overnight_gap_pct",
    "gift_overnight_range_pct",
    "gift_overnight_oi_change_pct",
    "gift_overnight_volume_conviction",
    "gift_overnight_vol_delta",
    "prev_nifty_return",
    "prev_nifty_return_5d",
    "prev_nifty_return_20d",
    "prev_vix_close",
    "prev_vix_change_pct",
    "prev_ad_ratio",
    "prev_breadth_turnover_weighted",
    "prev_pcr_oi",
    "prev_max_pain_distance_pct",
    "prev_fii_net_idx_fut",
    "prev_fii_net_total",
    "prev_dii_net_total",
    "prev_fii_options_skew",
    "prev_index_divergence_500",
    "prev_index_divergence_midcap",
    "prev_coincident_regime",
    "sp500_overnight_return",
    "usdinr_overnight_change",
    "day_of_week",
    "days_to_monthly_expiry",
    "is_expiry_week",
    "prev_day_range_pct",
]


def load_data():
    """Load feature matrix and compute Set D labels."""
    # Load pre-open feature matrix
    fm = pd.read_csv(
        "/home/me/projects/algotrix-go/regime-classifier/data/preopen_feature_matrix.csv",
        parse_dates=["date"],
    )
    
    # Load label review results for Set D labels
    lr = pd.read_csv(
        "/home/me/projects/algotrix-go/regime-classifier/label_review_results.csv",
        parse_dates=["date"],
    )
    
    # Merge Set D labels
    fm = fm.merge(lr[["date", "label_d"]], on="date", how="left")
    
    return fm


def walk_forward(df, feature_cols, target_col, label_map, n_classes):
    """Walk-forward XGBoost training and prediction."""
    valid = df.dropna(subset=[target_col]).copy().reset_index(drop=True)
    valid["_target_num"] = valid[target_col].map(label_map)
    valid = valid.dropna(subset=["_target_num"]).reset_index(drop=True)
    
    results = []
    model = None
    last_train_end = -1
    
    for i in range(MIN_TRAIN_DAYS, len(valid)):
        if model is None or (i - last_train_end) >= RETRAIN_INTERVAL:
            train = valid.iloc[:i]
            X_train = train[feature_cols].fillna(0).values
            y_train = train["_target_num"].astype(int).values
            
            model = XGBClassifier(
                max_depth=4, n_estimators=200, learning_rate=0.05,
                subsample=0.8, use_label_encoder=False,
                eval_metric="mlogloss",
                objective="multi:softprob",
                num_class=n_classes,
                verbosity=0, random_state=42,
            )
            model.fit(X_train, y_train)
            last_train_end = i
        
        row = valid.iloc[i]
        X_test = pd.DataFrame([row[feature_cols].fillna(0)]).values
        pred = model.predict(X_test)[0]
        proba = model.predict_proba(X_test)[0]
        
        results.append({
            "date": row["date"],
            "actual": int(row["_target_num"]),
            "predicted": int(pred),
            "confidence": float(proba.max()),
            "nifty_return": row.get("nifty_return"),
        })
    
    return pd.DataFrame(results), model


def evaluate(preds, label_map_inv, set_name):
    """Evaluate and print results."""
    print(f"\n{'='*70}")
    print(f"  PRE-OPEN PREDICTABILITY: {set_name}")
    print(f"{'='*70}")
    
    y_true = preds["actual"].values
    y_pred = preds["predicted"].values
    
    acc = accuracy_score(y_true, y_pred)
    f1 = f1_score(y_true, y_pred, average="macro", zero_division=0)
    
    print(f"\n📊 Overall: Accuracy={acc:.4f}, F1-macro={f1:.4f}, N={len(preds)}")
    
    # Per-class metrics
    print(f"\n📈 Per-Class Performance:")
    labels = sorted(label_map_inv.keys())
    label_names = [label_map_inv[l] for l in labels]
    print(classification_report(y_true, y_pred, labels=labels, target_names=label_names, zero_division=0))
    
    # Confusion matrix
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    print("Confusion Matrix (rows=actual, cols=predicted):")
    header = "          " + "  ".join(f"{label_map_inv[l]:>12s}" for l in labels)
    print(header)
    for i, label_code in enumerate(labels):
        row = "  ".join(f"{int(v):>12d}" for v in cm[i])
        print(f"  {label_map_inv[label_code]:>8s}  {row}")
    
    # Return separation by predicted class
    print(f"\n💰 Return Separation (by predicted class):")
    for code in labels:
        mask = y_pred == code
        if mask.sum() > 0:
            mean_ret = preds.loc[mask, "nifty_return"].mean() * 100
            count = mask.sum()
            print(f"  {label_map_inv[code]:>15s}: avg return {mean_ret:+.3f}%, n={count}")
    
    # Confidence analysis
    if "confidence" in preds.columns:
        print(f"\n🎯 Confidence Analysis:")
        for threshold in [0.4, 0.5, 0.6, 0.7]:
            high_conf = preds[preds["confidence"] >= threshold]
            if len(high_conf) > 0:
                hc_acc = accuracy_score(high_conf["actual"], high_conf["predicted"])
                print(f"  Confidence >= {threshold:.0%}: Accuracy={hc_acc:.4f}, N={len(high_conf)} ({len(high_conf)/len(preds)*100:.1f}%)")
    
    # Baselines
    print(f"\n📏 Baselines:")
    # Always most-common
    most_common = pd.Series(y_true).mode()[0]
    always_common = accuracy_score(y_true, np.full_like(y_true, most_common))
    print(f"  Always '{label_map_inv[most_common]}': {always_common:.4f}")
    
    # Persistence
    prev_actual = np.roll(y_true, 1)
    prev_actual[0] = most_common
    persist_acc = accuracy_score(y_true, prev_actual)
    print(f"  Persistence (yesterday's label): {persist_acc:.4f}")
    
    print(f"\n  Model beats 'Always {label_map_inv[most_common]}' by: {acc - always_common:+.4f}")
    print(f"  Model beats persistence by: {acc - persist_acc:+.4f}")
    
    return {
        "accuracy": acc,
        "f1_macro": f1,
        "n_predictions": len(preds),
        "baseline_most_common": always_common,
        "baseline_persistence": persist_acc,
    }


def main():
    print("Loading data...")
    df = load_data()
    print(f"Feature matrix: {len(df)} rows")
    print(f"Set D labels available: {df['label_d'].notna().sum()}")
    
    # Verify feature columns exist
    avail_features = [c for c in FEATURE_COLS if c in df.columns]
    print(f"Features available: {len(avail_features)}/{len(FEATURE_COLS)}")
    
    # --- Set A: Bull/Neutral/Bear ---
    label_map_a = {"Bearish": 0, "Neutral": 1, "Bullish": 2}
    label_map_a_inv = {0: "Bearish", 1: "Neutral", 2: "Bullish"}
    
    print("\n" + "="*70)
    print("  TRAINING SET A (Bull/Neutral/Bear) — 3-class XGBoost")
    print("="*70)
    preds_a, model_a = walk_forward(df, avail_features, "coincident_truth", label_map_a, 3)
    results_a = evaluate(preds_a, label_map_a_inv, "Set A — Bull/Neutral/Bear")
    
    # --- Set D: Trend-Up/Trend-Down/Range/Expansion ---
    label_map_d = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2, "Expansion": 3}
    label_map_d_inv = {0: "Trend-Down", 1: "Range", 2: "Trend-Up", 3: "Expansion"}
    
    print("\n" + "="*70)
    print("  TRAINING SET D (Trend-Up/Trend-Down/Range/Expansion) — 4-class XGBoost")
    print("="*70)
    preds_d, model_d = walk_forward(df, avail_features, "label_d", label_map_d, 4)
    results_d = evaluate(preds_d, label_map_d_inv, "Set D — Trend-Up/Trend-Down/Range/Expansion")
    
    # --- Head-to-head comparison ---
    print("\n" + "="*70)
    print("  HEAD-TO-HEAD: Set A vs Set D")
    print("="*70)
    print(f"\n{'Metric':<35s} {'Set A':>10s} {'Set D':>10s} {'Delta':>10s}")
    print(f"{'-'*35} {'-'*10} {'-'*10} {'-'*10}")
    
    for metric in ["accuracy", "f1_macro", "baseline_most_common", "baseline_persistence"]:
        va = results_a[metric]
        vd = results_d[metric]
        delta = vd - va
        print(f"  {metric:<33s} {va:>10.4f} {vd:>10.4f} {delta:>+10.4f}")
    
    margin_a = results_a["accuracy"] - results_a["baseline_most_common"]
    margin_d = results_d["accuracy"] - results_d["baseline_most_common"]
    print(f"\n  {'Margin over baseline':<33s} {margin_a:>+10.4f} {margin_d:>+10.4f}")
    
    # Feature importance from final models
    print(f"\n📊 Top 10 Feature Importance (Set D final model):")
    if hasattr(model_d, 'feature_importances_'):
        imp = sorted(zip(avail_features, model_d.feature_importances_), key=lambda x: -x[1])
        for feat, score in imp[:10]:
            bar = "█" * int(score * 100)
            print(f"  {feat:40s} {score:.4f} {bar}")


if __name__ == "__main__":
    main()
