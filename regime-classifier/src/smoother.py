"""Smoother — transition policy for regime labels.

Not just a filter: governs when and how regimes change.

Rules:
1. EMA(3) applied to 4D score vector before classification
2. Hysteresis: challenger must beat incumbent by margin for 2+ consecutive days
3. Shock-day override: >2σ move in any dimension bypasses hysteresis
4. Consensus rule: agreement across 3 classifiers affects confidence

Stores both raw_label and final_label with smoothing_reason.
"""

import logging

import numpy as np
import pandas as pd

from src.config import (
    EMA_SMOOTH_SPAN,
    HYSTERESIS_DAYS,
    SHOCK_LOOKBACK,
    SHOCK_SIGMA_THRESHOLD,
)

logger = logging.getLogger(__name__)


def smooth_scores(
    current_scores: np.ndarray,
    recent_scores: list[np.ndarray],
) -> np.ndarray:
    """Apply EMA(3) smoothing to dimension scores.

    Args:
        current_scores: today's raw 4D scores
        recent_scores: list of recent 4D score arrays (oldest first)

    Returns:
        smoothed 4D score array
    """
    if not recent_scores:
        return current_scores

    all_scores = np.vstack(recent_scores + [current_scores])
    df = pd.DataFrame(all_scores, columns=["vol", "trend", "part", "sent"])
    smoothed = df.ewm(span=EMA_SMOOTH_SPAN, adjust=False).mean().iloc[-1].values
    return smoothed


def detect_shock(
    current_scores: np.ndarray,
    recent_scores: list[np.ndarray],
    sigma_threshold: float = SHOCK_SIGMA_THRESHOLD,
    lookback: int = SHOCK_LOOKBACK,
) -> tuple[bool, str | None]:
    """Detect if any dimension has a >2σ move (shock day).

    Returns:
        (is_shock, reason_string_or_None)
    """
    if len(recent_scores) < 5:
        return False, None

    history = np.array(recent_scores[-lookback:])
    means = history.mean(axis=0)
    stds = history.std(axis=0)

    dim_names = ["volatility", "trend", "participation", "sentiment"]
    for i, (name, std) in enumerate(zip(dim_names, stds)):
        if std == 0:
            continue
        z_score = abs(current_scores[i] - means[i]) / std
        if z_score > sigma_threshold:
            reason = f"shock: {name} moved {z_score:.1f}σ (value={current_scores[i]:.0f}, mean={means[i]:.0f})"
            logger.warning("Shock detected: %s", reason)
            return True, reason

    return False, None


def apply_hysteresis(
    raw_label: str,
    recent_labels: list[str],
    days_required: int = HYSTERESIS_DAYS,
) -> tuple[str, bool, str | None]:
    """Apply hysteresis: challenger must appear N consecutive days to flip.

    Args:
        raw_label: today's unsmoothed label
        recent_labels: recent final_labels (oldest first)

    Returns:
        (final_label, was_smoothed, reason)
    """
    if not recent_labels:
        return raw_label, False, None

    incumbent = recent_labels[-1]

    if raw_label == incumbent:
        return raw_label, False, None

    # Check if challenger has appeared in the last N days
    recent_window = recent_labels[-(days_required):]
    if all(label == raw_label for label in recent_window):
        # Challenger has persisted — allow flip
        return raw_label, False, None

    # Block the flip — keep incumbent
    reason = (
        f"hysteresis: raw={raw_label} blocked, incumbent={incumbent} "
        f"held (need {days_required} consecutive days)"
    )
    logger.info("Hysteresis applied: %s", reason)
    return incumbent, True, reason


def compute_consensus(
    euclidean_label: str | None,
    hmm_label: str | None,
    gmm_label: str | None,
) -> tuple[float, str]:
    """Compute consensus confidence modifier based on classifier agreement.

    Returns:
        (confidence_modifier, description)
        - All 3 agree: 1.0, "unanimous"
        - 2/3 agree: 0.8, "majority"
        - All disagree: 0.5, "no_consensus"
    """
    labels = [l for l in [euclidean_label, hmm_label, gmm_label] if l is not None]

    if len(labels) <= 1:
        return 0.7, "single_classifier"

    from collections import Counter
    counts = Counter(labels)
    max_count = counts.most_common(1)[0][1]

    if max_count == len(labels):
        return 1.0, "unanimous"
    elif max_count >= 2:
        return 0.8, "majority"
    else:
        return 0.5, "no_consensus"


def apply_smoothing(
    raw_label: str,
    raw_scores: np.ndarray,
    euclidean_confidence: float,
    euclidean_label: str,
    hmm_label: str | None,
    gmm_label: str | None,
    recent_regimes: pd.DataFrame,
) -> dict:
    """Full smoothing pipeline: EMA scores → hysteresis → shock override → consensus.

    Args:
        raw_label: unsmoothed Euclidean label
        raw_scores: 4D dimension scores
        euclidean_confidence: Euclidean classifier confidence
        euclidean_label: Euclidean label
        hmm_label: HMM label (or None)
        gmm_label: GMM label (or None)
        recent_regimes: DataFrame of recent regime rows (from DB)

    Returns:
        dict with final_label, final_confidence, smoothed, smoothing_reason
    """
    # Extract recent scores and labels
    recent_scores = []
    recent_labels = []
    if not recent_regimes.empty:
        for _, row in recent_regimes.iterrows():
            if row.get("dimension_scores") is not None:
                scores = row["dimension_scores"]
                if isinstance(scores, (list, np.ndarray)) and len(scores) == 4:
                    recent_scores.append(np.array(scores, dtype=float))
            if row.get("final_label") is not None:
                recent_labels.append(row["final_label"])

    # Step 1: Smooth scores via EMA(3)
    smoothed_scores = smooth_scores(raw_scores, recent_scores)

    # Step 2: Check for shock day
    is_shock, shock_reason = detect_shock(raw_scores, recent_scores)

    # Step 3: Apply hysteresis (unless shock)
    if is_shock:
        final_label = raw_label
        smoothed = shock_reason is not None
        reason = shock_reason
    else:
        final_label, was_smoothed, hysteresis_reason = apply_hysteresis(
            raw_label, recent_labels
        )
        smoothed = was_smoothed
        reason = hysteresis_reason

    # Step 4: Consensus confidence
    consensus_mod, consensus_desc = compute_consensus(euclidean_label, hmm_label, gmm_label)
    final_confidence = euclidean_confidence * consensus_mod

    if reason:
        reason = f"{reason} | consensus={consensus_desc}"
    elif consensus_desc != "unanimous" and consensus_desc != "single_classifier":
        reason = f"consensus={consensus_desc}"
        smoothed = True

    return {
        "final_label": final_label,
        "final_confidence": round(final_confidence, 4),
        "smoothed": smoothed,
        "smoothing_reason": reason,
        "smoothed_scores": smoothed_scores.tolist(),
    }
