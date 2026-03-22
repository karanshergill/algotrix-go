"""HMM (Hidden Markov Model) regime classifier — shadow model.

Uses hmmlearn.GaussianHMM to learn hidden states from the 4D feature space.
States are mapped to regime labels by comparing state means to profile centroids.

Anti-leakage: uses expanding window training (train on [start, D]) during backfill.
Refit cadence: weekly in v1 (not nightly).
"""

import logging
import os
import pickle
from datetime import date
from pathlib import Path

import numpy as np
from hmmlearn.hmm import GaussianHMM
from scipy.spatial.distance import euclidean

from src.config import REGIME_LABELS
from src.profiles import REGIME_PROFILES
from src.scorer import compute_dimension_scores

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent.parent / "models"
N_STATES = 5  # Match our 5 regime labels
RANDOM_SEED = 42


def _get_model_path() -> Path:
    """Path for persisted HMM model artifact."""
    MODEL_DIR.mkdir(exist_ok=True)
    return MODEL_DIR / "hmm_model.pkl"


def _map_states_to_labels(model: GaussianHMM) -> dict[int, str]:
    """Map learned HMM states to regime labels using nearest centroid.

    Each HMM state has a mean vector (model.means_). We assign each state
    to the nearest regime profile by euclidean distance.
    """
    profiles = list(REGIME_PROFILES.items())
    mapping = {}
    used_labels = set()

    # For each state, find nearest unused profile
    state_dists = []
    for state_idx in range(model.n_components):
        state_mean = model.means_[state_idx]
        for label, profile in profiles:
            dist = euclidean(state_mean, profile)
            state_dists.append((dist, state_idx, label))

    state_dists.sort()
    used_states = set()

    for dist, state_idx, label in state_dists:
        if state_idx in used_states or label in used_labels:
            continue
        mapping[state_idx] = label
        used_states.add(state_idx)
        used_labels.add(label)
        if len(mapping) == model.n_components:
            break

    # Fallback: assign any unmapped states to nearest available label
    for state_idx in range(model.n_components):
        if state_idx not in mapping:
            mapping[state_idx] = "neutral"

    return mapping


def train_hmm(score_history: np.ndarray) -> tuple[GaussianHMM, dict[int, str]]:
    """Train HMM on historical 4D score vectors.

    Args:
        score_history: array of shape (n_days, 4) — dimension scores over time

    Returns:
        (fitted model, state→label mapping)
    """
    if len(score_history) < 20:
        raise ValueError(f"Need >= 20 days for HMM training, got {len(score_history)}")

    model = GaussianHMM(
        n_components=N_STATES,
        covariance_type="full",
        n_iter=200,
        random_state=RANDOM_SEED,
        tol=0.01,
    )
    model.fit(score_history)
    mapping = _map_states_to_labels(model)

    logger.info("HMM trained on %d days. State mapping: %s", len(score_history), mapping)
    return model, mapping


def classify_hmm(
    features: dict[str, float],
    score_history: np.ndarray | None = None,
    model: GaussianHMM | None = None,
    state_mapping: dict[int, str] | None = None,
) -> dict:
    """Classify a single day using HMM.

    If model is not provided, attempts to load persisted model.
    If no persisted model and score_history is provided, trains a new one.

    Returns dict with: label, confidence, state
    """
    scores = compute_dimension_scores(features)
    scores_2d = scores.reshape(1, -1)

    # Get or train model
    if model is None or state_mapping is None:
        model_path = _get_model_path()
        if model_path.exists():
            with open(model_path, "rb") as f:
                saved = pickle.load(f)
            model = saved["model"]
            state_mapping = saved["mapping"]
            logger.info("Loaded persisted HMM model from %s", model_path)
        elif score_history is not None and len(score_history) >= 20:
            model, state_mapping = train_hmm(score_history)
            save_hmm_model(model, state_mapping)
        else:
            logger.warning("No HMM model available and insufficient history to train")
            return {
                "label": None,
                "confidence": None,
                "state": None,
            }

    # Predict state
    state = int(model.predict(scores_2d)[0])

    # Confidence from posterior probability
    posteriors = model.predict_proba(scores_2d)[0]
    confidence = float(posteriors[state])

    label = state_mapping.get(state, "neutral")

    logger.info("HMM: state=%d → %s (conf=%.2f)", state, label, confidence)

    return {
        "label": label,
        "confidence": confidence,
        "state": state,
    }


def save_hmm_model(model: GaussianHMM, mapping: dict[int, str]) -> None:
    """Persist HMM model + mapping to disk."""
    model_path = _get_model_path()
    with open(model_path, "wb") as f:
        pickle.dump({"model": model, "mapping": mapping}, f)
    logger.info("HMM model saved to %s", model_path)
