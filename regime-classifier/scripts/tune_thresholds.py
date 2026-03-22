#!/usr/bin/env python3
"""Threshold tuning for regime scoring engine.

Walk-forward validation:
- Train: 2020-2023
- Validate: 2024-2025
- Test: 2026

Sweeps label thresholds and measures accuracy at each.

Usage:
    PGPASSWORD=algotrix python scripts/tune_thresholds.py
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

LABELS = ["Bullish", "Neutral", "Bearish"]


def load_data():
    return _read_sql("""
        SELECT b.date, b.composite_score, b.predicted_label, b.predicted_confidence,
               b.availability_regime,
               g.nifty_return, g.next_day_return, g.coincident_label, g.predictive_label
        FROM regime_backtest b
        JOIN regime_ground_truth g ON b.date = g.date
        ORDER BY b.date
    """)


def label_regime(score, bull_thresh, bear_thresh):
    if score >= bull_thresh:
        return "Bullish"
    elif score <= bear_thresh:
        return "Bearish"
    return "Neutral"


def evaluate_thresholds(df, bull_thresh, bear_thresh):
    """Evaluate a set of thresholds on the given data."""
    df = df.copy()
    df["label"] = df["composite_score"].apply(lambda s: label_regime(s, bull_thresh, bear_thresh))

    # Coincident accuracy
    c_match = (df["label"] == df["coincident_label"]).sum()
    c_acc = c_match / len(df) if len(df) > 0 else 0

    # Predictive accuracy
    p_valid = df[df["predictive_label"].notna()]
    p_match = (p_valid["predicted_label"] == p_valid["predictive_label"]).sum()
    p_acc = p_match / len(p_valid) if len(p_valid) > 0 else 0

    # Mean return by predicted regime
    mean_ret = {}
    for label in LABELS:
        subset = p_valid[p_valid["predicted_label"] == label]
        if len(subset) > 0:
            mean_ret[label] = subset["next_day_return"].mean()

    # Distribution check
    dist = df["label"].value_counts(normalize=True).to_dict()

    return {
        "coincident_acc": c_acc,
        "predictive_acc": p_acc,
        "mean_return": mean_ret,
        "distribution": dist,
        "bull_thresh": bull_thresh,
        "bear_thresh": bear_thresh,
    }


def main():
    df = load_data()
    if df.empty:
        logger.error("No data. Run backtest first.")
        sys.exit(1)

    logger.info("Loaded %d dates for tuning", len(df))

    # Split into train/val/test
    from datetime import date as date_type
    df["date"] = pd.to_datetime(df["date"]).dt.date
    train = df[df["date"] < date_type(2024, 1, 1)]
    val = df[(df["date"] >= date_type(2024, 1, 1)) & (df["date"] < date_type(2026, 1, 1))]
    test = df[df["date"] >= date_type(2026, 1, 1)]

    logger.info("Train: %d, Val: %d, Test: %d", len(train), len(val), len(test))

    # Sweep thresholds
    best = None
    best_score = -1

    threshold_pairs = [
        (55, 45), (57, 43), (58, 42), (60, 40), (62, 38), (65, 35),
    ]

    print(f"\n{'Bull':>6} {'Bear':>6} | {'Train C':>8} {'Train P':>8} | {'Val C':>8} {'Val P':>8} | {'Bull Ret':>8} {'Bear Ret':>8}")
    print("-" * 80)

    for bull, bear in threshold_pairs:
        train_r = evaluate_thresholds(train, bull, bear)
        val_r = evaluate_thresholds(val, bull, bear)

        bull_ret = val_r["mean_return"].get("Bullish", 0) or 0
        bear_ret = val_r["mean_return"].get("Bearish", 0) or 0

        print(f"{bull:>6} {bear:>6} | {train_r['coincident_acc']:>8.1%} {train_r['predictive_acc']:>8.1%} | "
              f"{val_r['coincident_acc']:>8.1%} {val_r['predictive_acc']:>8.1%} | "
              f"{bull_ret:>+8.4%} {bear_ret:>+8.4%}")

        # Score: predictive accuracy where bull_ret > 0 and bear_ret < 0
        score = val_r["predictive_acc"]
        if bull_ret > 0 and bear_ret < 0:
            score += 0.05  # Bonus for correct directional returns

        # Penalize if train-val gap > 5%
        gap = abs(train_r["predictive_acc"] - val_r["predictive_acc"])
        if gap > 0.05:
            score -= 0.1

        if score > best_score:
            best_score = score
            best = (bull, bear, val_r)

    if best:
        bull, bear, val_r = best
        print(f"\nBest thresholds: Bullish >= {bull}, Bearish <= {bear}")
        print(f"Val predictive accuracy: {val_r['predictive_acc']:.1%}")

        # Run on test set
        if len(test) > 0:
            test_r = evaluate_thresholds(test, bull, bear)
            print(f"\nTest set ({len(test)} dates):")
            print(f"  Coincident accuracy: {test_r['coincident_acc']:.1%}")
            print(f"  Predictive accuracy: {test_r['predictive_acc']:.1%}")
            for label in LABELS:
                ret = test_r["mean_return"].get(label)
                if ret is not None:
                    print(f"  Mean return when predicted {label}: {ret:+.4%}")


if __name__ == "__main__":
    main()
