"""GMM (Gaussian Mixture Model) regime classifier — shadow model.

Uses sklearn.GaussianMixture to discover natural clusters in the 4D feature space.
Clusters are mapped to regime labels by comparing cluster centers to profile centroids.

Anti-leakage: uses expanding window training during backfill.
Refit cadence: weekly in v1 (not nightly).
"""

import logging
import pickle
from pathlib import Path

import numpy as np
from scipy.spatial.distance import euclidean
from sklearn.mixture import GaussianMixture

from src.config import REGIME_LABELS
from src.profiles import REGIME_PROFILES
from src.scorer import compute_dimension_scores

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent.parent / "models"
N_CLUSTERS = 5
RANDOM_SEED = 42


def _get_model_path() -> Path:
    MODEL_DIR.mkdir(exist_ok=True)
    return MODEL_DIR / "gmm_model.pkl"


def _map_clusters_to_labels(model: GaussianMixture) -> dict[int, str]:
    """Map GMM clusters to regime labels using nearest centroid."""
    profiles = list(REGIME_PROFILES.items())
    mapping = {}
    used_labels = set()

    cluster_dists = []
    for cluster_idx in range(model.n_components):
        cluster_mean = model.means_[cluster_idx]
        for label, profile in profiles:
            dist = euclidean(cluster_mean, profile)
            cluster_dists.append((dist, cluster_idx, label))

    cluster_dists.sort()
    used_clusters = set()

    for dist, cluster_idx, label in cluster_dists:
        if cluster_idx in used_clusters or label in used_labels:
            continue
        mapping[cluster_idx] = label
        used_clusters.add(cluster_idx)
        used_labels.add(label)
        if len(mapping) == model.n_components:
            break

    for cluster_idx in range(model.n_components):
        if cluster_idx not in mapping:
            mapping[cluster_idx] = "neutral"

    return mapping


def train_gmm(score_history: np.ndarray) -> tuple[GaussianMixture, dict[int, str]]:
    """Train GMM on historical 4D score vectors.

    Args:
        score_history: array of shape (n_days, 4)

    Returns:
        (fitted model, cluster→label mapping)
    """
    if len(score_history) < 20:
        raise ValueError(f"Need >= 20 days for GMM training, got {len(score_history)}")

    model = GaussianMixture(
        n_components=N_CLUSTERS,
        covariance_type="full",
        n_init=5,
        max_iter=200,
        random_state=RANDOM_SEED,
        tol=0.01,
    )
    model.fit(score_history)
    mapping = _map_clusters_to_labels(model)

    logger.info("GMM trained on %d days. Cluster mapping: %s", len(score_history), mapping)
    return model, mapping


def classify_gmm(
    features: dict[str, float],
    score_history: np.ndarray | None = None,
    model: GaussianMixture | None = None,
    cluster_mapping: dict[int, str] | None = None,
) -> dict:
    """Classify a single day using GMM.

    Returns dict with: label, confidence, cluster
    """
    scores = compute_dimension_scores(features)
    scores_2d = scores.reshape(1, -1)

    if model is None or cluster_mapping is None:
        model_path = _get_model_path()
        if model_path.exists():
            with open(model_path, "rb") as f:
                saved = pickle.load(f)
            model = saved["model"]
            cluster_mapping = saved["mapping"]
            logger.info("Loaded persisted GMM model from %s", model_path)
        elif score_history is not None and len(score_history) >= 20:
            model, cluster_mapping = train_gmm(score_history)
            save_gmm_model(model, cluster_mapping)
        else:
            logger.warning("No GMM model available and insufficient history to train")
            return {
                "label": None,
                "confidence": None,
                "cluster": None,
            }

    cluster = int(model.predict(scores_2d)[0])
    probs = model.predict_proba(scores_2d)[0]
    confidence = float(probs[cluster])
    label = cluster_mapping.get(cluster, "neutral")

    logger.info("GMM: cluster=%d → %s (conf=%.2f)", cluster, label, confidence)

    return {
        "label": label,
        "confidence": confidence,
        "cluster": cluster,
    }


def save_gmm_model(model: GaussianMixture, mapping: dict[int, str]) -> None:
    model_path = _get_model_path()
    with open(model_path, "wb") as f:
        pickle.dump({"model": model, "mapping": mapping}, f)
    logger.info("GMM model saved to %s", model_path)
