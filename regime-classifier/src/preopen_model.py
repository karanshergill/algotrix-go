"""Pre-Open Model — walk-forward training, evaluation, SHAP, report generation.

Models: XGBoost, LightGBM, LogisticRegression
Targets: 3-class (Bullish/Neutral/Bearish), binary up/down, binary trending/range
Protocol: expanding window, min 126 days, retrain every 63 days
"""

import logging
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "preopen"
REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"

LABEL_MAP_3CLASS = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}
LABEL_MAP_3CLASS_INV = {0: "Trend-Down", 1: "Range", 2: "Trend-Up"}

MIN_TRAIN_DAYS = 126
RETRAIN_INTERVAL = 63

from src.preopen_features import PREOPEN_FEATURE_COLS


# ---------------------------------------------------------------------------
# Target computation
# ---------------------------------------------------------------------------

def add_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Add target columns from coincident_truth and nifty_return."""
    # 3-class target
    df["target_3class"] = df["coincident_truth"].map(LABEL_MAP_3CLASS)

    # Binary up/down
    df["target_updown"] = (df["nifty_return"] > 0).astype(int)  # 1=Up, 0=Down

    # Binary trending/range
    df["target_trend_range"] = (df["nifty_return"].abs() > 0.005).astype(int)  # 1=Trending, 0=Range

    return df


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def compute_baselines(df: pd.DataFrame) -> dict:
    """Compute baseline accuracies on the test portion of the dataframe.

    Returns dict of baseline_name -> {accuracy, predictions}.
    """
    baselines = {}
    valid = df.dropna(subset=["target_3class"]).copy()
    if valid.empty:
        return baselines

    y_true = valid["target_3class"].values
    n = len(y_true)

    # 1. Always Range
    pred_neutral = np.ones(n, dtype=int)
    baselines["Always Range"] = {
        "accuracy_3class": accuracy_score(y_true, pred_neutral),
        "predictions": pred_neutral,
    }

    # 2. Persistence (today = yesterday's coincident label)
    pred_persist = valid["prev_coincident_regime"].fillna(1).astype(int).values
    baselines["Persistence"] = {
        "accuracy_3class": accuracy_score(y_true, pred_persist),
        "predictions": pred_persist,
    }

    # 3. GIFT direction
    gap = valid["gift_overnight_gap_pct"].fillna(0).values
    pred_gift = np.where(gap > 0.0, 2, np.where(gap < 0.0, 0, 1)).astype(int)
    baselines["GIFT direction"] = {
        "accuracy_3class": accuracy_score(y_true, pred_gift),
        "predictions": pred_gift,
    }

    # 4. Previous regime
    baselines["Previous regime"] = baselines["Persistence"]  # Same logic

    # 5. GIFT + persistence
    prev_regime = valid["prev_coincident_regime"].fillna(1).astype(int).values
    pred_combo = np.where(pred_gift == prev_regime, pred_gift, 1).astype(int)
    baselines["GIFT + persistence"] = {
        "accuracy_3class": accuracy_score(y_true, pred_combo),
        "predictions": pred_combo,
    }

    # Binary up/down baselines
    y_updown = valid["target_updown"].values
    # Persistence binary
    pred_persist_bin = (valid["prev_nifty_return"].fillna(0) > 0).astype(int).values
    baselines["Persistence (up/down)"] = {
        "accuracy_updown": accuracy_score(y_updown, pred_persist_bin),
    }
    # GIFT binary
    pred_gift_bin = (gap > 0).astype(int)
    baselines["GIFT (up/down)"] = {
        "accuracy_updown": accuracy_score(y_updown, pred_gift_bin),
    }

    # Binary trending/range
    y_trend = valid["target_trend_range"].values
    # Always Range
    baselines["Always Range"] = {
        "accuracy_trend": accuracy_score(y_trend, np.zeros(n, dtype=int)),
    }

    return baselines


# ---------------------------------------------------------------------------
# Walk-forward training
# ---------------------------------------------------------------------------

def _get_model(model_name: str, target_type: str):
    """Create a fresh model instance."""
    if model_name == "xgboost":
        from xgboost import XGBClassifier
        n_classes = 3 if target_type == "3class" else 2
        params = dict(
            max_depth=4, n_estimators=200, learning_rate=0.05,
            subsample=0.8, use_label_encoder=False,
            eval_metric="mlogloss" if n_classes > 2 else "logloss",
            verbosity=0, random_state=42,
        )
        if n_classes > 2:
            params["objective"] = "multi:softprob"
            params["num_class"] = 3
        return XGBClassifier(**params)

    elif model_name == "lightgbm":
        from lightgbm import LGBMClassifier
        return LGBMClassifier(
            num_leaves=31, n_estimators=200, learning_rate=0.05,
            verbosity=-1, random_state=42,
        )

    elif model_name == "logreg":
        return LogisticRegression(
            C=1.0, max_iter=1000, random_state=42,
        )

    raise ValueError(f"Unknown model: {model_name}")


def walk_forward_train(
    df: pd.DataFrame,
    feature_cols: list[str],
    target_col: str,
    model_name: str,
    target_type: str = "3class",
) -> pd.DataFrame:
    """Walk-forward expanding window training.

    Returns DataFrame with columns: date, actual, predicted, probability (if available).
    """
    valid = df.dropna(subset=[target_col]).copy()
    valid = valid.reset_index(drop=True)

    results = []
    last_train_end = -1
    model = None

    for i in range(MIN_TRAIN_DAYS, len(valid)):
        # Retrain if needed
        if model is None or (i - last_train_end) >= RETRAIN_INTERVAL:
            train = valid.iloc[:i]
            X_train = train[feature_cols].fillna(0).values
            y_train = train[target_col].values

            model = _get_model(model_name, target_type)
            model.fit(X_train, y_train)
            last_train_end = i

        # Predict
        row = valid.iloc[i]
        X_test = pd.DataFrame([row[feature_cols].fillna(0)]).values

        pred = model.predict(X_test)[0]
        prob = None
        if hasattr(model, "predict_proba"):
            proba = model.predict_proba(X_test)[0]
            prob = float(proba.max())

        results.append({
            "date": row["date"],
            "actual": int(row[target_col]),
            "predicted": int(pred),
            "probability": prob,
        })

    # Save final model
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_DIR / f"{model_name}_{target_type}.joblib")

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def evaluate_predictions(preds: pd.DataFrame, label_map_inv: dict | None = None) -> dict:
    """Compute standard metrics on prediction DataFrame."""
    if preds.empty:
        return {}

    y_true = preds["actual"].values
    y_pred = preds["predicted"].values

    acc = accuracy_score(y_true, y_pred)
    f1_macro = f1_score(y_true, y_pred, average="macro", zero_division=0)
    f1_per_class = f1_score(y_true, y_pred, average=None, zero_division=0)
    cm = confusion_matrix(y_true, y_pred)

    result = {
        "accuracy": acc,
        "f1_macro": f1_macro,
        "f1_per_class": f1_per_class.tolist(),
        "confusion_matrix": cm,
        "n_predictions": len(preds),
    }

    # Per-class accuracy
    if label_map_inv:
        for code, label in label_map_inv.items():
            mask = y_true == code
            if mask.sum() > 0:
                result[f"accuracy_{label}"] = accuracy_score(y_true[mask], y_pred[mask])

    return result


def conditional_accuracy(preds: pd.DataFrame, feature_df: pd.DataFrame) -> dict:
    """Compute conditional accuracy slices."""
    # Merge features into predictions
    merged = preds.merge(feature_df[["date", "gift_overnight_gap_pct", "prev_vix_close",
                                      "day_of_week", "is_expiry_week", "nifty_return"]],
                         on="date", how="left")

    slices = {}

    # Expiry week vs non-expiry
    for val, name in [(1.0, "expiry_week"), (0.0, "non_expiry_week")]:
        mask = merged["is_expiry_week"] == val
        subset = merged[mask]
        if len(subset) > 0:
            slices[name] = {
                "accuracy": accuracy_score(subset["actual"], subset["predicted"]),
                "count": len(subset),
            }

    # Gap days
    gap = merged["gift_overnight_gap_pct"].fillna(0)
    for cond, name in [
        (gap > 0.3, "gap_up"),
        (gap < -0.3, "gap_down"),
        ((gap >= -0.3) & (gap <= 0.3), "flat_gap"),
    ]:
        subset = merged[cond]
        if len(subset) > 0:
            slices[name] = {
                "accuracy": accuracy_score(subset["actual"], subset["predicted"]),
                "count": len(subset),
            }

    # Vol sessions
    vix = merged["prev_vix_close"].fillna(15)
    for cond, name in [(vix > 20, "high_vol"), (vix < 15, "low_vol")]:
        subset = merged[cond]
        if len(subset) > 0:
            slices[name] = {
                "accuracy": accuracy_score(subset["actual"], subset["predicted"]),
                "count": len(subset),
            }

    # Day of week
    for dow in range(5):
        dow_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][dow]
        subset = merged[merged["day_of_week"] == dow]
        if len(subset) > 0:
            slices[dow_name] = {
                "accuracy": accuracy_score(subset["actual"], subset["predicted"]),
                "count": len(subset),
            }

    # Confidence buckets
    if "probability" in preds.columns:
        prob = preds["probability"].fillna(0)
        for threshold, name in [(0.6, "conf_gt_60"), (0.7, "conf_gt_70"), (0.5, "conf_gt_50")]:
            subset = preds[prob >= threshold]
            if len(subset) > 0:
                slices[name] = {
                    "accuracy": accuracy_score(subset["actual"], subset["predicted"]),
                    "count": len(subset),
                }

    return slices


def compute_return_separation(preds: pd.DataFrame, feature_df: pd.DataFrame) -> dict:
    """Compute mean Nifty return per predicted class."""
    merged = preds.merge(feature_df[["date", "nifty_return"]], on="date", how="left")
    sep = {}
    for code, label in LABEL_MAP_3CLASS_INV.items():
        subset = merged[merged["predicted"] == code]
        if len(subset) > 0:
            sep[label] = {
                "mean_return": float(subset["nifty_return"].mean()),
                "count": int(len(subset)),
            }
    return sep


def compute_shap_importance(model, feature_cols: list[str], X_sample: np.ndarray, n_features: int = 10) -> list[tuple[str, float]]:
    """Compute SHAP feature importance for top N features."""
    try:
        import shap
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_sample[:min(500, len(X_sample))])

        # For multi-class, average across classes
        if isinstance(shap_values, list):
            mean_abs = np.mean([np.abs(sv).mean(axis=0) for sv in shap_values], axis=0)
        elif shap_values.ndim == 3:
            # shape: (n_samples, n_features, n_classes)
            mean_abs = np.abs(shap_values).mean(axis=(0, 2))
        else:
            mean_abs = np.abs(shap_values).mean(axis=0)

        importance = sorted(zip(feature_cols, [float(v) for v in mean_abs]), key=lambda x: x[1], reverse=True)
        return importance[:n_features]
    except Exception as e:
        logger.warning("SHAP failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_full_pipeline(input_path: str) -> dict:
    """Run the complete pre-open model pipeline.

    Returns dict with all results for report generation.
    """
    print("Loading feature matrix...")
    df = pd.read_csv(input_path, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = add_targets(df)

    # Drop rows without ground truth
    valid_mask = df["coincident_truth"].notna() & df["nifty_return"].notna()
    df_valid = df[valid_mask].copy().reset_index(drop=True)
    print(f"  Valid rows: {len(df_valid)} / {len(df)}")

    # Feature columns that actually exist in the dataframe
    feature_cols = [c for c in PREOPEN_FEATURE_COLS if c in df_valid.columns]
    print(f"  Feature columns: {len(feature_cols)}")

    # Split: test portion = everything after MIN_TRAIN_DAYS
    test_df = df_valid.iloc[MIN_TRAIN_DAYS:].copy()

    # --- Baselines ---
    print("\nComputing baselines...")
    baselines = compute_baselines(test_df)
    for name, bl in baselines.items():
        for k, v in bl.items():
            if "accuracy" in k:
                print(f"  {name}: {k} = {v:.4f}")

    # --- Walk-forward training ---
    results = {"baselines": baselines, "models": {}, "feature_cols": feature_cols}

    model_names = ["xgboost", "lightgbm", "logreg"]
    target_configs = [
        ("3class", "target_3class"),
        ("updown", "target_updown"),
        ("trend_range", "target_trend_range"),
    ]

    for model_name in model_names:
        results["models"][model_name] = {}
        for target_type, target_col in target_configs:
            print(f"\nTraining {model_name} ({target_type})...")
            preds = walk_forward_train(
                df_valid, feature_cols, target_col, model_name, target_type
            )
            if preds.empty:
                print(f"  No predictions generated")
                continue

            label_inv = LABEL_MAP_3CLASS_INV if target_type == "3class" else None
            metrics = evaluate_predictions(preds, label_inv)
            cond_acc = conditional_accuracy(preds, df_valid) if target_type == "3class" else {}
            ret_sep = compute_return_separation(preds, df_valid) if target_type == "3class" else {}

            print(f"  Accuracy: {metrics['accuracy']:.4f} | F1 macro: {metrics['f1_macro']:.4f} | N={metrics['n_predictions']}")

            results["models"][model_name][target_type] = {
                "predictions": preds,
                "metrics": metrics,
                "conditional_accuracy": cond_acc,
                "return_separation": ret_sep,
            }

    # --- SHAP for best model (XGBoost 3-class) ---
    print("\nComputing SHAP importance...")
    xgb_3class = results["models"].get("xgboost", {}).get("3class")
    shap_importance = []
    if xgb_3class:
        model_path = MODEL_DIR / "xgboost_3class.joblib"
        if model_path.exists():
            model = joblib.load(model_path)
            X_all = df_valid[feature_cols].fillna(0).values
            shap_importance = compute_shap_importance(model, feature_cols, X_all)
            if shap_importance:
                print("  Top 10 features:")
                for feat, imp in shap_importance:
                    print(f"    {feat}: {imp:.4f}")

    results["shap_importance"] = shap_importance
    results["feature_matrix"] = df_valid

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(results: dict) -> str:
    """Generate comprehensive markdown report."""
    lines = []
    lines.append("# Pre-Open Session Predictor — Evaluation Report")
    lines.append(f"\nGenerated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    fm = results.get("feature_matrix")
    if fm is not None:
        lines.append(f"**Dataset:** {len(fm)} trading days")
        lines.append(f"**Date range:** {fm['date'].min()} to {fm['date'].max()}")
        lines.append(f"**Features:** {len(results['feature_cols'])}")
        lines.append(f"**Walk-forward:** min {MIN_TRAIN_DAYS} days train, retrain every {RETRAIN_INTERVAL} days")
        lines.append("")

        # Class distribution
        lines.append("## Target Distribution")
        lines.append("")
        if "coincident_truth" in fm.columns:
            dist = fm["coincident_truth"].value_counts()
            for label, count in dist.items():
                lines.append(f"- **{label}:** {count} ({count/len(fm)*100:.1f}%)")
        lines.append("")

    # --- Baselines ---
    lines.append("## Baselines")
    lines.append("")
    lines.append("| Baseline | 3-Class Accuracy |")
    lines.append("|----------|-----------------|")
    baselines = results.get("baselines", {})
    for name, bl in baselines.items():
        acc = bl.get("accuracy_3class")
        if acc is not None:
            lines.append(f"| {name} | {acc:.4f} |")
        elif bl.get("accuracy_updown") is not None:
            lines.append(f"| {name} | n/a (up/down: {bl['accuracy_updown']:.4f}) |")
        elif bl.get("accuracy_trend") is not None:
            lines.append(f"| {name} | n/a (trend: {bl['accuracy_trend']:.4f}) |")
    lines.append("")

    # --- Model Comparison ---
    lines.append("## Model Comparison")
    lines.append("")
    lines.append("| Model | 3-Class Acc | 3-Class F1 | Binary Up/Down | Binary Trend/Range |")
    lines.append("|-------|------------|------------|----------------|-------------------|")

    models = results.get("models", {})
    for model_name in ["xgboost", "lightgbm", "logreg"]:
        model_data = models.get(model_name, {})
        acc_3 = model_data.get("3class", {}).get("metrics", {}).get("accuracy", "n/a")
        f1_3 = model_data.get("3class", {}).get("metrics", {}).get("f1_macro", "n/a")
        acc_ud = model_data.get("updown", {}).get("metrics", {}).get("accuracy", "n/a")
        acc_tr = model_data.get("trend_range", {}).get("metrics", {}).get("accuracy", "n/a")

        acc_3_str = f"{acc_3:.4f}" if isinstance(acc_3, float) else str(acc_3)
        f1_3_str = f"{f1_3:.4f}" if isinstance(f1_3, float) else str(f1_3)
        acc_ud_str = f"{acc_ud:.4f}" if isinstance(acc_ud, float) else str(acc_ud)
        acc_tr_str = f"{acc_tr:.4f}" if isinstance(acc_tr, float) else str(acc_tr)

        lines.append(f"| {model_name} | {acc_3_str} | {f1_3_str} | {acc_ud_str} | {acc_tr_str} |")
    lines.append("")

    # --- Per-model detailed results ---
    for model_name in ["xgboost", "lightgbm", "logreg"]:
        model_data = models.get(model_name, {})
        three_class = model_data.get("3class", {})
        metrics = three_class.get("metrics", {})
        if not metrics:
            continue

        lines.append(f"### {model_name.upper()} — 3-Class Details")
        lines.append("")

        # Confusion matrix
        cm = metrics.get("confusion_matrix")
        if cm is not None:
            lines.append("**Confusion Matrix** (rows=actual, cols=predicted):")
            lines.append("")
            lines.append("|  | Trend-Down | Range | Trend-Up |")
            lines.append("|--|-----------|-------|----------|")
            for i, label in enumerate(["Trend-Down", "Range", "Trend-Up"]):
                if i < len(cm):
                    row_vals = " | ".join(str(int(v)) for v in cm[i])
                    lines.append(f"| **{label}** | {row_vals} |")
            lines.append("")

        # Per-class accuracy
        for label in ["Trend-Down", "Range", "Trend-Up"]:
            key = f"accuracy_{label}"
            if key in metrics:
                lines.append(f"- {label} accuracy: {metrics[key]:.4f}")
        lines.append("")

        # Return separation
        ret_sep = three_class.get("return_separation", {})
        if ret_sep:
            lines.append("**Return Separation:**")
            lines.append("")
            lines.append("| Predicted | Mean Return | Count |")
            lines.append("|-----------|-------------|-------|")
            for label in ["Trend-Up", "Range", "Trend-Down"]:
                data = ret_sep.get(label, {})
                if data:
                    lines.append(f"| {label} | {data['mean_return']*100:.3f}% | {data['count']} |")
            lines.append("")

        # Conditional accuracy
        cond = three_class.get("conditional_accuracy", {})
        if cond:
            lines.append("**Conditional Accuracy:**")
            lines.append("")
            lines.append("| Slice | Accuracy | Count |")
            lines.append("|-------|----------|-------|")
            for slice_name, data in sorted(cond.items()):
                lines.append(f"| {slice_name} | {data['accuracy']:.4f} | {data['count']} |")
            lines.append("")

    # --- SHAP ---
    shap_imp = results.get("shap_importance", [])
    if shap_imp:
        lines.append("## SHAP Feature Importance (XGBoost 3-Class)")
        lines.append("")
        lines.append("| Rank | Feature | Mean SHAP |")
        lines.append("|------|---------|-----------|")
        for i, (feat, imp) in enumerate(shap_imp, 1):
            lines.append(f"| {i} | {feat} | {imp:.4f} |")
        lines.append("")

    # --- Decision gate ---
    lines.append("## Decision Gate")
    lines.append("")

    best_model = None
    best_acc = 0
    for model_name in ["xgboost", "lightgbm", "logreg"]:
        acc = models.get(model_name, {}).get("3class", {}).get("metrics", {}).get("accuracy", 0)
        if acc > best_acc:
            best_acc = acc
            best_model = model_name

    baseline_accs = [bl.get("accuracy_3class", 0) for bl in baselines.values() if bl.get("accuracy_3class") is not None]
    max_baseline = max(baseline_accs) if baseline_accs else 0

    beats_baselines = best_acc > max_baseline
    lines.append(f"- Best model: **{best_model}** ({best_acc:.4f})")
    lines.append(f"- Best baseline: {max_baseline:.4f}")
    lines.append(f"- Beats all baselines: **{'YES' if beats_baselines else 'NO'}** (margin: {best_acc - max_baseline:+.4f})")

    # Return separation check
    ret_sep = models.get(best_model, {}).get("3class", {}).get("return_separation", {})
    up_ret = ret_sep.get("Trend-Up", {}).get("mean_return", 0)
    range_ret = ret_sep.get("Range", {}).get("mean_return", 0)
    down_ret = ret_sep.get("Trend-Down", {}).get("mean_return", 0)
    monotonic = up_ret > range_ret > down_ret
    lines.append(f"- Return separation (Up > Range > Down): **{'YES' if monotonic else 'NO'}** ({up_ret*100:.3f}% > {range_ret*100:.3f}% > {down_ret*100:.3f}%)")

    # Confidence check
    cond = models.get(best_model, {}).get("3class", {}).get("conditional_accuracy", {})
    overall_acc = best_acc
    conf_60 = cond.get("conf_gt_60", {}).get("accuracy", 0)
    conf_better = conf_60 > overall_acc if conf_60 > 0 else False
    lines.append(f"- High-confidence (>60%) accuracy > overall: **{'YES' if conf_better else 'NO'}** ({conf_60:.4f} vs {overall_acc:.4f})")
    lines.append("")

    return "\n".join(lines)
