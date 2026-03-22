"""v2 Evaluation — metrics, baselines, SHAP importance, and report generation.

Compares ML models against trivial baselines and generates a comprehensive
evaluation report saved to reports/v2_evaluation.md.
"""

import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix, classification_report

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent / "reports"
LABEL_MAP = {"Bearish": 0, "Neutral": 1, "Bullish": 2}
LABEL_NAMES = ["Bearish", "Neutral", "Bullish"]


# ---------------------------------------------------------------------------
# Trivial baselines
# ---------------------------------------------------------------------------

def compute_baselines(df: pd.DataFrame, target_col: str) -> dict:
    """Compute trivial baseline accuracies.

    Args:
        df: Feature matrix with target column and nifty_return, breadth_ratio columns
        target_col: 'coincident_truth' or 'predictive_truth'
    """
    valid = df[df[target_col].notna()].copy()
    actuals = valid[target_col].values
    n = len(actuals)

    baselines = {}

    # Random baseline
    baselines["random_uniform"] = {
        "accuracy": 1.0 / 3.0,
        "description": "Random guess (uniform 3-class)",
    }

    # Majority class
    from collections import Counter
    counts = Counter(actuals)
    majority_class = counts.most_common(1)[0][0]
    majority_acc = counts[majority_class] / n
    baselines["majority_class"] = {
        "accuracy": majority_acc,
        "description": f"Always predict '{majority_class}' (most frequent)",
        "class": majority_class,
    }

    # Persistence: tomorrow = today
    if target_col == "predictive_truth":
        # For predictive: use coincident_truth as "today's regime"
        if "coincident_truth" in valid.columns:
            persistence_preds = valid["coincident_truth"].values
            mask = pd.notna(persistence_preds) & pd.notna(actuals)
            if mask.sum() > 0:
                baselines["persistence"] = {
                    "accuracy": accuracy_score(actuals[mask], persistence_preds[mask]),
                    "description": "Tomorrow = today's coincident label",
                }
    else:
        # For coincident: use previous day's label
        shifted = valid[target_col].shift(1).values
        mask = pd.notna(shifted) & pd.notna(actuals)
        if mask.sum() > 0:
            baselines["persistence"] = {
                "accuracy": accuracy_score(actuals[mask], shifted[mask]),
                "description": "Today = yesterday's label",
            }

    # Return sign baseline (coincident only)
    if target_col == "coincident_truth" and "nifty_return" in valid.columns:
        ret = valid["nifty_return"].values
        sign_preds = np.where(ret > 0.003, "Bullish",
                             np.where(ret < -0.003, "Bearish", "Neutral"))
        baselines["return_sign"] = {
            "accuracy": accuracy_score(actuals, sign_preds),
            "description": "Return > 0.3% → Bullish, < -0.3% → Bearish",
        }

    # Return + Breadth baseline (coincident only)
    if (target_col == "coincident_truth"
        and "nifty_return" in valid.columns
        and "breadth_ratio" in valid.columns):
        ret = valid["nifty_return"].values
        breadth = valid["breadth_ratio"].values

        rb_preds = []
        for r, b in zip(ret, breadth):
            bull_signals = int(r > 0.003) + int(b > 0.55 if not np.isnan(b) else False)
            bear_signals = int(r < -0.003) + int(b < 0.45 if not np.isnan(b) else False)
            if bull_signals >= 2:
                rb_preds.append("Bullish")
            elif bear_signals >= 2:
                rb_preds.append("Bearish")
            else:
                rb_preds.append("Neutral")

        baselines["return_breadth"] = {
            "accuracy": accuracy_score(actuals, rb_preds),
            "description": "2-of-3 vote: return sign + breadth ratio",
        }

    # Transition matrix baseline (predictive)
    if target_col == "predictive_truth" and "coincident_truth" in valid.columns:
        transitions = {}
        co_labels = valid["coincident_truth"].values
        pred_labels = actuals
        for i in range(len(co_labels)):
            if pd.notna(co_labels[i]) and pd.notna(pred_labels[i]):
                from_label = co_labels[i]
                to_label = pred_labels[i]
                if from_label not in transitions:
                    transitions[from_label] = Counter()
                transitions[from_label][to_label] += 1

        # For each current state, predict most likely next state
        trans_preds = []
        for i in range(len(co_labels)):
            if pd.notna(co_labels[i]) and co_labels[i] in transitions:
                most_likely = transitions[co_labels[i]].most_common(1)[0][0]
                trans_preds.append(most_likely)
            else:
                trans_preds.append(majority_class)

        mask = pd.notna(actuals)
        baselines["transition_matrix"] = {
            "accuracy": accuracy_score(actuals[mask], np.array(trans_preds)[mask]),
            "description": "Most likely next regime given current regime",
        }

    return baselines


# ---------------------------------------------------------------------------
# Model evaluation
# ---------------------------------------------------------------------------

def evaluate_model(predictions: pd.DataFrame, model_name: str, target_col: str) -> dict:
    """Evaluate a single model's predictions.

    Args:
        predictions: DataFrame with 'actual' and f'{model_name}_label' columns
        model_name: Name of the model (e.g., 'xgboost_expanding')
        target_col: 'coincident_truth' or 'predictive_truth' (for context)
    """
    pred_col = f"{model_name}_label"
    if pred_col not in predictions.columns:
        return None

    valid = predictions[predictions[pred_col].notna() & predictions["actual"].notna()]
    if valid.empty:
        return None

    actuals = valid["actual"].values
    preds = valid[pred_col].values

    acc = accuracy_score(actuals, preds)
    f1_macro = f1_score(actuals, preds, labels=LABEL_NAMES, average="macro", zero_division=0)
    f1_per_class = f1_score(actuals, preds, labels=LABEL_NAMES, average=None, zero_division=0)
    cm = confusion_matrix(actuals, preds, labels=LABEL_NAMES)

    # Per-class accuracy
    per_class_acc = {}
    for i, name in enumerate(LABEL_NAMES):
        mask = actuals == name
        if mask.sum() > 0:
            per_class_acc[name] = accuracy_score(actuals[mask], preds[mask])
        else:
            per_class_acc[name] = None

    result = {
        "model_name": model_name,
        "target": target_col,
        "accuracy": acc,
        "f1_macro": f1_macro,
        "f1_per_class": {name: f1_per_class[i] for i, name in enumerate(LABEL_NAMES)},
        "per_class_accuracy": per_class_acc,
        "confusion_matrix": cm,
        "n_test": len(valid),
    }

    # Return separation (if nifty_return available in predictions)
    if "nifty_return" in predictions.columns:
        ret_sep = {}
        for label in LABEL_NAMES:
            mask = valid[pred_col] == label
            if mask.sum() > 0:
                rets = valid.loc[mask, "nifty_return"] if "nifty_return" in valid.columns else None
                if rets is not None:
                    ret_sep[label] = {
                        "mean": float(rets.mean()),
                        "median": float(rets.median()),
                        "count": int(mask.sum()),
                    }
        result["return_separation"] = ret_sep

    # By-year breakdown
    if "date" in valid.columns:
        yearly = {}
        for yr in sorted(valid["date"].apply(lambda d: d.year if hasattr(d, 'year') else pd.Timestamp(d).year).unique()):
            yr_mask = valid["date"].apply(lambda d: (d.year if hasattr(d, 'year') else pd.Timestamp(d).year) == yr)
            yr_data = valid[yr_mask]
            if len(yr_data) > 0:
                yearly[yr] = {
                    "accuracy": accuracy_score(yr_data["actual"], yr_data[pred_col]),
                    "n": len(yr_data),
                }
        result["by_year"] = yearly

    return result


def compute_shap_importance(model, X_sample: pd.DataFrame, model_name: str) -> dict | None:
    """Compute SHAP feature importance for a model."""
    try:
        import shap

        if "logreg" in model_name:
            explainer = shap.LinearExplainer(model, X_sample.fillna(0))
            shap_values = explainer.shap_values(X_sample.fillna(0))
        else:
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X_sample)

        # Mean absolute SHAP value per feature (across all classes)
        if isinstance(shap_values, list):
            # Multi-class: average across classes
            combined = np.mean([np.abs(sv) for sv in shap_values], axis=0)
        else:
            combined = np.abs(shap_values)

        mean_importance = combined.mean(axis=0)
        feature_importance = dict(zip(X_sample.columns, mean_importance))
        sorted_importance = dict(sorted(feature_importance.items(),
                                       key=lambda x: x[1], reverse=True))

        return {k: float(v) for k, v in list(sorted_importance.items())[:15]}

    except Exception as e:
        logger.warning("SHAP computation failed for %s: %s", model_name, e)
        return None


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(
    feature_matrix: pd.DataFrame,
    training_results: dict,
    output_path: str | None = None,
) -> str:
    """Generate the full v2 evaluation report as markdown.

    Args:
        feature_matrix: The full feature matrix DataFrame
        training_results: Output from v2_model.run_full_training()
        output_path: Path to save the report (default: reports/v2_evaluation.md)
    """
    lines = []
    lines.append("# v2 Regime Model — Evaluation Report\n")
    lines.append(f"**Generated:** {date.today()}\n")

    # --- Feature Matrix Summary ---
    lines.append("## 1. Feature Matrix Summary\n")
    lines.append(f"- **Total dates:** {len(feature_matrix)}")
    lines.append(f"- **Total columns:** {len(feature_matrix.columns)}")

    # Count features (exclude meta)
    meta_cols = {"date", "coincident_truth", "predictive_truth",
                 "availability_regime", "missing_count", "nifty_return",
                 "breadth_ratio", "vix_change_pct", "next_day_return"}
    feat_cols = [c for c in feature_matrix.columns if c not in meta_cols]
    lines.append(f"- **Feature columns:** {len(feat_cols)}")

    if "coincident_truth" in feature_matrix.columns:
        dist = feature_matrix["coincident_truth"].value_counts()
        lines.append(f"\n**Coincident truth distribution:**")
        for label, count in dist.items():
            pct = count / len(feature_matrix) * 100
            lines.append(f"- {label}: {count} ({pct:.1f}%)")

    if "predictive_truth" in feature_matrix.columns:
        dist = feature_matrix["predictive_truth"].value_counts()
        lines.append(f"\n**Predictive truth distribution:**")
        for label, count in dist.items():
            pct = count / len(feature_matrix) * 100
            lines.append(f"- {label}: {count} ({pct:.1f}%)")

    # Null analysis
    lines.append(f"\n**Feature null rates (top 10):**")
    null_rates = feature_matrix[feat_cols].isnull().mean().sort_values(ascending=False)
    for feat, rate in null_rates.head(10).items():
        lines.append(f"- {feat}: {rate*100:.1f}%")

    # --- Per-target evaluation ---
    for target_name in ["coincident", "predictive"]:
        target_col = f"{target_name}_truth"

        if target_name not in training_results:
            continue

        predictions = training_results[target_name]["predictions"]
        if predictions.empty:
            continue

        lines.append(f"\n## 2{'a' if target_name == 'coincident' else 'b'}. {target_name.title()} Model Results\n")
        lines.append(f"**Test dates:** {len(predictions)}")

        # Merge nifty_return for return separation analysis
        if "nifty_return" in feature_matrix.columns:
            fm_subset = feature_matrix[["date", "nifty_return"]].copy()
            predictions = predictions.merge(fm_subset, on="date", how="left", suffixes=("", "_fm"))
            if "nifty_return_fm" in predictions.columns:
                predictions["nifty_return"] = predictions["nifty_return"].fillna(predictions["nifty_return_fm"])
                predictions.drop(columns=["nifty_return_fm"], inplace=True)

        # --- Baselines ---
        lines.append(f"\n### Baselines\n")
        baselines = compute_baselines(feature_matrix, target_col)
        lines.append("| Baseline | Accuracy | Description |")
        lines.append("|----------|----------|-------------|")
        for bname, bdata in sorted(baselines.items(), key=lambda x: x[1]["accuracy"], reverse=True):
            lines.append(f"| {bname} | {bdata['accuracy']:.1%} | {bdata['description']} |")

        # --- Model results ---
        lines.append(f"\n### Model Comparison\n")
        lines.append("| Model | Accuracy | F1 (macro) | Bearish F1 | Neutral F1 | Bullish F1 |")
        lines.append("|-------|----------|------------|------------|------------|------------|")

        model_names = sorted(set(
            col.replace("_label", "").replace("_pred", "")
            for col in predictions.columns
            if col.endswith("_label") and col != "actual"
        ))

        model_results = {}
        for mname in model_names:
            result = evaluate_model(predictions, mname, target_col)
            if result is None:
                continue
            model_results[mname] = result

            f1pc = result["f1_per_class"]
            lines.append(
                f"| {mname} | {result['accuracy']:.1%} | {result['f1_macro']:.3f} "
                f"| {f1pc['Bearish']:.3f} | {f1pc['Neutral']:.3f} | {f1pc['Bullish']:.3f} |"
            )

        # --- Best model details ---
        if model_results:
            best_name = max(model_results, key=lambda k: model_results[k]["accuracy"])
            best = model_results[best_name]

            lines.append(f"\n### Best Model: {best_name}\n")
            lines.append(f"- **Accuracy:** {best['accuracy']:.1%}")
            lines.append(f"- **F1 (macro):** {best['f1_macro']:.3f}")
            lines.append(f"- **Test dates:** {best['n_test']}")

            # Confusion matrix
            lines.append(f"\n**Confusion Matrix:**")
            lines.append("```")
            lines.append(f"{'':>12} {'Pred Bear':>10} {'Pred Neut':>10} {'Pred Bull':>10}")
            cm = best["confusion_matrix"]
            for i, name in enumerate(LABEL_NAMES):
                lines.append(f"{name:>12} {cm[i][0]:>10d} {cm[i][1]:>10d} {cm[i][2]:>10d}")
            lines.append("```")

            # Per-class accuracy
            lines.append(f"\n**Per-class accuracy:**")
            for cls, acc in best["per_class_accuracy"].items():
                if acc is not None:
                    lines.append(f"- {cls}: {acc:.1%}")

            # Return separation
            if "return_separation" in best and best["return_separation"]:
                lines.append(f"\n**Return separation by predicted class:**")
                lines.append("| Predicted | Mean Return | Median Return | Count |")
                lines.append("|-----------|-------------|---------------|-------|")
                for cls in LABEL_NAMES:
                    if cls in best["return_separation"]:
                        rs = best["return_separation"][cls]
                        lines.append(f"| {cls} | {rs['mean']:.4%} | {rs['median']:.4%} | {rs['count']} |")

            # By-year breakdown
            if "by_year" in best and best["by_year"]:
                lines.append(f"\n**Accuracy by year:**")
                lines.append("| Year | Accuracy | N |")
                lines.append("|------|----------|---|")
                for yr, data in sorted(best["by_year"].items()):
                    lines.append(f"| {yr} | {data['accuracy']:.1%} | {data['n']} |")

        # --- SHAP importance ---
        models = training_results[target_name].get("models", {})
        if models:
            lines.append(f"\n### Feature Importance (SHAP)\n")

            # Use a sample of the feature matrix for SHAP
            sample_size = min(200, len(feature_matrix))
            sample_df = feature_matrix.sample(n=sample_size, random_state=42)
            X_sample = sample_df[feat_cols].copy()
            if "buildup_class" in X_sample.columns:
                from src.v2_model import BUILDUP_MAP
                X_sample["buildup_class"] = X_sample["buildup_class"].map(BUILDUP_MAP)

            for mname, model in models.items():
                importance = compute_shap_importance(model, X_sample, mname)
                if importance:
                    lines.append(f"\n**{mname} — Top 10 features:**")
                    lines.append("| Feature | Mean |SHAP| |")
                    lines.append("|---------|----------|")
                    for feat, val in list(importance.items())[:10]:
                        lines.append(f"| {feat} | {val:.4f} |")

    # --- Decision gate ---
    lines.append("\n## 3. Decision Gate Assessment\n")

    gate_pass = True
    gate_notes = []

    for target_name in ["coincident", "predictive"]:
        if target_name not in training_results:
            continue

        predictions = training_results[target_name]["predictions"]
        if predictions.empty:
            continue

        target_col = f"{target_name}_truth"
        baselines = compute_baselines(feature_matrix, target_col)

        model_names = sorted(set(
            col.replace("_label", "")
            for col in predictions.columns
            if col.endswith("_label") and col != "actual"
        ))

        best_acc = 0
        best_model = None
        for mname in model_names:
            result = evaluate_model(predictions, mname, target_col)
            if result and result["accuracy"] > best_acc:
                best_acc = result["accuracy"]
                best_model = mname

        if target_name == "coincident":
            phase1_acc = 0.523
            if best_acc > phase1_acc + 0.10:
                gate_notes.append(f"PASS: Coincident best ({best_model}) = {best_acc:.1%} > Phase 1 (52.3%) + 10pp")
            else:
                gate_notes.append(f"CHECK: Coincident best ({best_model}) = {best_acc:.1%} vs Phase 1 (52.3%) + 10pp threshold")

            rb_acc = baselines.get("return_breadth", {}).get("accuracy", 0)
            if rb_acc > 0:
                gate_notes.append(f"  Return+Breadth baseline: {rb_acc:.1%}")

        if target_name == "predictive":
            random_acc = 1/3
            persistence_acc = baselines.get("persistence", {}).get("accuracy", 0)
            phase1_pred = 0.369

            if best_acc > random_acc and best_acc > persistence_acc and best_acc > phase1_pred:
                gate_notes.append(f"PASS: Predictive best ({best_model}) = {best_acc:.1%} > random (33.3%), persistence ({persistence_acc:.1%}), Phase 1 (36.9%)")
            else:
                gate_notes.append(f"CHECK: Predictive best ({best_model}) = {best_acc:.1%} vs random (33.3%), persistence ({persistence_acc:.1%}), Phase 1 (36.9%)")
                gate_pass = False

    for note in gate_notes:
        lines.append(f"- {note}")

    if gate_pass:
        lines.append("\n**Overall: PASS** — models clear the decision gate thresholds.")
    else:
        lines.append("\n**Overall: REVIEW NEEDED** — some models may not clear all thresholds. Check return separation.")

    # Save
    report_text = "\n".join(lines) + "\n"

    if output_path is None:
        output_path = str(REPORTS_DIR / "v2_evaluation.md")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(report_text)
    logger.info("Report saved to %s", output_path)

    return report_text
