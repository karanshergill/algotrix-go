"""Diagnostics and validation for regime classification.

- Transition matrix: how often does each regime follow each other?
- Label distribution: are we stuck in one regime?
- Returns-per-regime: do different regimes actually have different return distributions?
- Classifier agreement: how often do Euclidean, HMM, GMM agree?
"""

import logging
from datetime import date

import numpy as np
import pandas as pd

from src.db import fetch_regime_range

logger = logging.getLogger(__name__)


def compute_transition_matrix(regimes: pd.DataFrame) -> pd.DataFrame:
    """Compute regime transition matrix.

    Returns DataFrame where cell (i, j) = count of transitions from regime i to regime j.
    """
    labels = regimes["final_label"].tolist()
    unique_labels = sorted(set(labels))

    matrix = pd.DataFrame(0, index=unique_labels, columns=unique_labels)
    for i in range(len(labels) - 1):
        matrix.loc[labels[i], labels[i + 1]] += 1

    return matrix


def compute_label_distribution(regimes: pd.DataFrame) -> pd.Series:
    """Count of each regime label."""
    return regimes["final_label"].value_counts()


def compute_classifier_agreement(regimes: pd.DataFrame) -> dict:
    """How often do the 3 classifiers agree?"""
    total = len(regimes)
    if total == 0:
        return {"total": 0, "unanimous": 0, "majority": 0, "disagree": 0}

    unanimous = 0
    majority = 0
    disagree = 0

    for _, row in regimes.iterrows():
        labels = [
            row.get("euclidean_label"),
            row.get("hmm_label"),
            row.get("gmm_label"),
        ]
        labels = [l for l in labels if l is not None]
        if len(labels) <= 1:
            continue
        from collections import Counter
        counts = Counter(labels)
        max_count = counts.most_common(1)[0][1]
        if max_count == len(labels):
            unanimous += 1
        elif max_count >= 2:
            majority += 1
        else:
            disagree += 1

    return {
        "total": total,
        "unanimous": unanimous,
        "majority": majority,
        "disagree": disagree,
        "unanimous_pct": round(unanimous / total * 100, 1) if total else 0,
    }


def compute_stability_score(regimes: pd.DataFrame) -> dict:
    """Measure transition stability — fewer false flips = better."""
    labels = regimes["final_label"].tolist()
    if len(labels) < 2:
        return {"transitions": 0, "days": len(labels), "stability": 1.0}

    transitions = sum(1 for i in range(len(labels) - 1) if labels[i] != labels[i + 1])
    stability = 1 - (transitions / (len(labels) - 1))

    return {
        "transitions": transitions,
        "days": len(labels),
        "stability": round(stability, 4),
        "avg_regime_duration": round(len(labels) / max(transitions + 1, 1), 1),
    }


def run_validation(start_date: date, end_date: date) -> dict:
    """Run full validation suite on classified date range."""
    regimes = fetch_regime_range(start_date, end_date)

    if regimes.empty:
        logger.warning("No regime data found for %s to %s", start_date, end_date)
        return {"error": "No regime data found"}

    results = {
        "date_range": f"{start_date} to {end_date}",
        "total_days": len(regimes),
        "distribution": compute_label_distribution(regimes).to_dict(),
        "transition_matrix": compute_transition_matrix(regimes).to_dict(),
        "agreement": compute_classifier_agreement(regimes),
        "stability": compute_stability_score(regimes),
    }

    return results


def print_validation_report(results: dict) -> None:
    """Pretty-print validation results."""
    if "error" in results:
        print(f"Error: {results['error']}")
        return

    print(f"\n{'='*60}")
    print(f"REGIME VALIDATION REPORT")
    print(f"{'='*60}")
    print(f"Period: {results['date_range']}")
    print(f"Total days: {results['total_days']}")

    print(f"\n--- Label Distribution ---")
    for label, count in sorted(results["distribution"].items()):
        pct = count / results["total_days"] * 100
        bar = "#" * int(pct / 2)
        print(f"  {label:20s} {count:4d} ({pct:5.1f}%) {bar}")

    print(f"\n--- Classifier Agreement ---")
    ag = results["agreement"]
    print(f"  Unanimous: {ag['unanimous']} ({ag['unanimous_pct']}%)")
    print(f"  Majority:  {ag['majority']}")
    print(f"  Disagree:  {ag['disagree']}")

    print(f"\n--- Stability ---")
    st = results["stability"]
    print(f"  Transitions: {st['transitions']}")
    print(f"  Stability:   {st['stability']:.1%}")
    print(f"  Avg duration: {st['avg_regime_duration']} days")

    print(f"\n--- Transition Matrix ---")
    tm = results["transition_matrix"]
    labels = sorted(tm.keys())
    header = f"{'From / To':20s}" + "".join(f"{l:>14s}" for l in labels)
    print(f"  {header}")
    for from_label in labels:
        row = f"  {from_label:20s}"
        for to_label in labels:
            row += f"{tm[from_label].get(to_label, 0):14d}"
        print(row)

    print(f"{'='*60}\n")
