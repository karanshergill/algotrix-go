"""Euclidean distance regime classifier — production classifier.

Compares 4D score vector against hand-tuned regime profiles.
The nearest profile (by euclidean distance) determines the regime label.
Confidence is derived from the ratio of best-to-second-best distance.
"""

import logging

import numpy as np
from scipy.spatial.distance import euclidean

from src.profiles import REGIME_PROFILES
from src.scorer import compute_dimension_scores

logger = logging.getLogger(__name__)


def classify_euclidean(features: dict[str, float]) -> dict:
    """Classify a single day using Euclidean distance to regime profiles.

    Args:
        features: dict of raw indicator values (from features.py)

    Returns:
        dict with: label, confidence, distances (per-profile), dimension_scores
    """
    scores = compute_dimension_scores(features)

    # Compute distance to each profile
    distances = {}
    for label, profile in REGIME_PROFILES.items():
        distances[label] = float(euclidean(scores, profile))

    # Sort by distance (nearest first)
    sorted_profiles = sorted(distances.items(), key=lambda x: x[1])
    best_label = sorted_profiles[0][0]
    best_dist = sorted_profiles[0][1]
    second_dist = sorted_profiles[1][1] if len(sorted_profiles) > 1 else best_dist

    # Confidence: how much closer is the best vs second-best?
    # confidence = 1 - (best / second), clamped to [0, 1]
    if second_dist == 0:
        confidence = 0.0
    else:
        confidence = float(np.clip(1 - (best_dist / second_dist), 0, 1))

    logger.info(
        "Euclidean: %s (conf=%.2f, dist=%.1f) | scores=[%.0f, %.0f, %.0f, %.0f]",
        best_label, confidence, best_dist,
        scores[0], scores[1], scores[2], scores[3],
    )

    return {
        "label": best_label,
        "confidence": confidence,
        "distances": {k: round(v, 4) for k, v in distances.items()},
        "dimension_scores": scores.tolist(),
    }
