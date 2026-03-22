#!/usr/bin/env python3
"""Evaluate regime backtest results against ground truth.

All metrics are reported SEPARATELY for each availability regime (full vs pre_ix).

Usage:
    PGPASSWORD=algotrix python scripts/evaluate_regime.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from src.db import _read_sql

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
LABELS = ["Bullish", "Neutral", "Bearish"]


def load_backtest_with_truth():
    """Load backtest results joined with ground truth."""
    bt = _read_sql("""
        SELECT b.*, g.nifty_return, g.next_day_return
        FROM regime_backtest b
        LEFT JOIN regime_ground_truth g ON b.date = g.date
        ORDER BY b.date
    """)
    return bt


def confusion_matrix(actual: pd.Series, predicted: pd.Series, labels=LABELS) -> pd.DataFrame:
    """Build 3x3 confusion matrix."""
    cm = pd.crosstab(actual, predicted, rownames=["Actual"], colnames=["Predicted"], dropna=False)
    for label in labels:
        if label not in cm.columns:
            cm[label] = 0
        if label not in cm.index:
            cm.loc[label] = 0
    return cm[labels].reindex(labels)


def evaluate_segment(df: pd.DataFrame, segment_name: str) -> dict:
    """Run full evaluation on a segment of backtest data."""
    n = len(df)
    results = {"segment": segment_name, "n_dates": n}

    if n == 0:
        return results

    # --- Coincident validation ---
    coincident_mask = df["coincident_truth"].notna()
    if coincident_mask.sum() > 0:
        c_df = df[coincident_mask]
        match = (c_df["regime_label"] == c_df["coincident_truth"]).sum()
        results["coincident_accuracy"] = match / len(c_df)
        results["coincident_n"] = len(c_df)
        results["coincident_cm"] = confusion_matrix(c_df["coincident_truth"], c_df["regime_label"])

        # Regime distribution
        dist = c_df["regime_label"].value_counts(normalize=True)
        results["regime_distribution"] = dist.to_dict()

    # --- Predictive validation ---
    pred_mask = df["predictive_truth"].notna() & df["predicted_label"].notna()
    if pred_mask.sum() > 0:
        p_df = df[pred_mask]
        match = (p_df["predicted_label"] == p_df["predictive_truth"]).sum()
        results["predictive_accuracy"] = match / len(p_df)
        results["predictive_n"] = len(p_df)
        results["predictive_cm"] = confusion_matrix(p_df["predictive_truth"], p_df["predicted_label"])

        # Hit rate by confidence bucket
        p_df = p_df.copy()
        p_df["correct"] = p_df["predicted_label"] == p_df["predictive_truth"]
        p_df["conf_bucket"] = pd.cut(p_df["predicted_confidence"], bins=[0, 0.1, 0.2, 0.3, 0.5, 1.0],
                                     labels=["0-10%", "10-20%", "20-30%", "30-50%", "50-100%"])
        results["hit_rate_by_confidence"] = p_df.groupby("conf_bucket", observed=True)["correct"].agg(["mean", "count"]).to_dict()

        # Transition accuracy
        p_df["prev_label"] = p_df["regime_label"].shift(1)
        transitions = p_df[p_df["predicted_label"] != p_df["prev_label"]]
        if len(transitions) > 0:
            transition_correct = (transitions["predicted_label"] == transitions["predictive_truth"]).sum()
            results["transition_accuracy"] = transition_correct / len(transitions)
            results["transition_n"] = len(transitions)

        # Mean return by predicted regime
        if "next_day_return" in p_df.columns:
            mean_returns = p_df.groupby("predicted_label")["next_day_return"].mean()
            results["mean_return_by_prediction"] = mean_returns.to_dict()

        # Per-dimension correlation with next-day returns
        dim_cols = ["vol_score", "trend_score", "participation_score",
                    "sentiment_score", "institutional_flow_score", "composite_score"]
        if "next_day_return" in p_df.columns:
            correlations = {}
            for col in dim_cols:
                valid = p_df[[col, "next_day_return"]].dropna()
                if len(valid) > 10:
                    correlations[col] = float(valid[col].corr(valid["next_day_return"]))
            results["dimension_correlations"] = correlations

    return results


def print_report(results: dict):
    """Pretty-print evaluation results."""
    seg = results["segment"]
    n = results["n_dates"]
    print(f"\n{'=' * 60}")
    print(f"  Segment: {seg}  ({n} dates)")
    print(f"{'=' * 60}")

    # Coincident
    if "coincident_accuracy" in results:
        print(f"\n--- Coincident Validation (regime_label vs coincident_truth) ---")
        print(f"  Accuracy: {results['coincident_accuracy']:.1%}  ({results['coincident_n']} dates)")
        print(f"\n  Regime Distribution:")
        for label, pct in sorted(results.get("regime_distribution", {}).items()):
            print(f"    {label:10s}: {pct:.1%}")
        print(f"\n  Confusion Matrix:")
        print(results["coincident_cm"].to_string())

    # Predictive
    if "predictive_accuracy" in results:
        print(f"\n--- Predictive Validation (predicted_label vs predictive_truth) ---")
        print(f"  Directional Accuracy: {results['predictive_accuracy']:.1%}  ({results['predictive_n']} dates)")
        print(f"\n  Confusion Matrix:")
        print(results["predictive_cm"].to_string())

        if "hit_rate_by_confidence" in results:
            print(f"\n  Hit Rate by Confidence Bucket:")
            hr = results["hit_rate_by_confidence"]
            for bucket in hr.get("mean", {}):
                rate = hr["mean"].get(bucket, 0)
                count = hr["count"].get(bucket, 0)
                print(f"    {bucket:10s}: {rate:.1%}  (n={int(count)})")

        if "transition_accuracy" in results:
            print(f"\n  Transition Accuracy: {results['transition_accuracy']:.1%}  ({results['transition_n']} transitions)")

        if "mean_return_by_prediction" in results:
            print(f"\n  Mean Next-Day Nifty Return by Predicted Regime:")
            for label in LABELS:
                ret = results["mean_return_by_prediction"].get(label, 0)
                print(f"    {label:10s}: {ret:+.4%}")

        if "dimension_correlations" in results:
            print(f"\n  Dimension Correlation with Next-Day Return:")
            for dim, corr in sorted(results["dimension_correlations"].items(), key=lambda x: -abs(x[1])):
                print(f"    {dim:30s}: {corr:+.4f}")

    print()


def save_csv_report(all_results: list[dict]):
    """Save a CSV summary report."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    rows = []
    for r in all_results:
        row = {
            "segment": r["segment"],
            "n_dates": r["n_dates"],
            "coincident_accuracy": r.get("coincident_accuracy"),
            "predictive_accuracy": r.get("predictive_accuracy"),
            "transition_accuracy": r.get("transition_accuracy"),
        }
        for label in LABELS:
            row[f"mean_return_{label}"] = r.get("mean_return_by_prediction", {}).get(label)
            row[f"regime_dist_{label}"] = r.get("regime_distribution", {}).get(label)
        rows.append(row)

    df = pd.DataFrame(rows)
    path = os.path.join(REPORTS_DIR, "evaluation_report.csv")
    df.to_csv(path, index=False)
    logger.info("CSV report saved to %s", path)


def main():
    bt = load_backtest_with_truth()
    if bt.empty:
        logger.error("No backtest data found. Run backtest_regime.py first.")
        sys.exit(1)

    logger.info("Loaded %d backtest rows", len(bt))

    all_results = []

    # Evaluate by availability regime
    for regime in ["full", "pre_ix", "partial"]:
        segment = bt[bt["availability_regime"] == regime]
        if len(segment) > 0:
            result = evaluate_segment(segment, f"availability={regime}")
            all_results.append(result)
            print_report(result)

    # Also evaluate overall (clearly labeled)
    overall = evaluate_segment(bt, "ALL (combined — use with caution)")
    all_results.append(overall)
    print_report(overall)

    save_csv_report(all_results)


if __name__ == "__main__":
    main()
