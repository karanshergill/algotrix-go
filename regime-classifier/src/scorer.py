"""Normalize raw indicators to 0-100 dimension scores (Phase 2).

5 dimensions: volatility, trend, participation, sentiment, institutional_flow.
Supports data-driven bounds (production JSON or walk-forward).
Handles missing indicators by re-normalizing weights.
"""

import json
import logging
import os
from datetime import date

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default indicator bounds: (min_val, max_val, invert)
# These are overridden by calibrated bounds from data/indicator_bounds.json
# ---------------------------------------------------------------------------

DEFAULT_INDICATOR_BOUNDS = {
    # Volatility
    "india_vix_close":       (10.0, 35.0, False),
    "vix_roc_5d":            (-30.0, 30.0, False),
    "nifty_yang_zhang_vol":  (8.0, 35.0, False),
    "nifty_garman_klass_vol": (5.0, 25.0, False),
    "nifty_atr_pctile_60d":  (0.0, 100.0, False),
    # Trend
    "nifty_ema20_distance":  (-5.0, 5.0, False),
    "nifty_ema50_distance":  (-8.0, 8.0, False),
    "nifty_ema200_distance": (-15.0, 15.0, False),
    "nifty_ema20_slope":     (-3.0, 3.0, False),
    "nifty_adx14":           (10.0, 50.0, False),
    "nifty_return_5d":       (-5.0, 5.0, False),
    "breadth_momentum_5d":   (-20.0, 20.0, False),
    # Participation
    "ad_ratio":              (0.3, 3.0, False),
    "ad_ratio_5d_avg":       (0.5, 2.5, False),
    "universe_pct_above_ema20": (10.0, 80.0, False),
    "nifty50_pct_above_ema20":  (10.0, 90.0, False),
    "volume_trend_ratio":    (0.6, 1.5, False),
    "up_volume_ratio":       (0.3, 0.7, False),
    # Sentiment
    "nifty_pcr_oi":          (0.5, 1.5, False),  # NSE: High PCR = bullish (put writing)
    "nifty_fut_basis_pct":   (-0.5, 0.5, False),
    "fii_net_idx_fut_oi":    (-300000, 100000, False),
    "fii_vs_client_ratio":   (-0.2, 0.2, False),
    # Institutional Flow
    "fii_flow_delta":        (-30000, 30000, False),
    "dii_flow_delta":        (-15000, 15000, False),
    "gift_nifty_overnight_gap": (-1.5, 1.5, False),
    "sp500_overnight_return": (-2.0, 2.0, False),
    "dxy_overnight_change":  (-1.5, 1.5, True),  # DXY up = bearish → invert
    "us10y_overnight_change": (-5.0, 5.0, True),  # Yield up = bearish → invert
}

# Dimension composition: which indicators and weights
DIMENSION_WEIGHTS = {
    "volatility": {
        "india_vix_close": 0.25,
        "vix_roc_5d": 0.25,
        "nifty_yang_zhang_vol": 0.20,
        "nifty_garman_klass_vol": 0.15,
        "nifty_atr_pctile_60d": 0.15,
    },
    "trend": {
        "nifty_ema20_distance": 0.20,
        "nifty_ema50_distance": 0.15,
        "nifty_ema200_distance": 0.10,
        "nifty_ema20_slope": 0.20,
        "nifty_adx14": 0.15,
        "nifty_return_5d": 0.10,
        "breadth_momentum_5d": 0.10,
    },
    "participation": {
        "ad_ratio": 0.15,
        "ad_ratio_5d_avg": 0.15,
        "universe_pct_above_ema20": 0.25,
        "nifty50_pct_above_ema20": 0.20,
        "volume_trend_ratio": 0.15,
        "up_volume_ratio": 0.10,
    },
    "sentiment": {
        "nifty_pcr_oi": 0.30,
        "nifty_fut_basis_pct": 0.20,
        "fii_net_idx_fut_oi": 0.25,
        "fii_vs_client_ratio": 0.25,
    },
    "institutional_flow": {
        "fii_flow_delta": 0.25,
        "dii_flow_delta": 0.10,
        "gift_nifty_overnight_gap": 0.25,
        "sp500_overnight_return": 0.15,
        "dxy_overnight_change": 0.10,
        "us10y_overnight_change": 0.15,
    },
}

COMPOSITE_WEIGHTS = {
    "volatility": 0.0,           # DISABLED: adds noise, pending redesign
    "trend": 0.25,               # Batch 2: rebalanced
    "participation": 0.35,       # Batch 2: best Bullish/Neutral separator
    "sentiment": 0.0,            # DISABLED: inverted/unstable, needs rebuild
    "institutional_flow": 0.25,  # Batch 2: strong in full era
}

DIMENSION_ORDER = ["volatility", "trend", "participation", "sentiment", "institutional_flow"]

# Regime label thresholds (tuned via walk-forward validation)
BULLISH_THRESHOLD = 54.3   # empirical p67, Core 3 (trend+part+IF)
BEARISH_THRESHOLD = 44.6   # empirical p33, Core 3 (trend+part+IF)

SCHEMA_VERSION = 2


# Backward compatibility for Phase 1 classifier imports
def compute_dimension_scores(features: dict) -> np.ndarray:
    """Legacy 4D score vector for Phase 1 classifiers (euclidean/hmm/gmm).
    Returns numpy array of shape (4,) with values in 0-100.
    """
    result = compute_all_dimension_scores(features)
    dim = result["dimension_scores"]
    return np.array([
        dim.get("volatility", 50.0) or 50.0,
        dim.get("trend", 50.0) or 50.0,
        dim.get("participation", 50.0) or 50.0,
        dim.get("sentiment", 50.0) or 50.0,
    ])


# ---------------------------------------------------------------------------
# Bounds loading
# ---------------------------------------------------------------------------

_loaded_bounds: dict | None = None
_loaded_walkforward_bounds: dict | None = None


def _get_bounds_path():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "indicator_bounds.json")


def _get_walkforward_path():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "walkforward_bounds.json")


def load_production_bounds() -> dict:
    """Load calibrated bounds from JSON, falling back to defaults."""
    global _loaded_bounds
    if _loaded_bounds is not None:
        return _loaded_bounds
    path = _get_bounds_path()
    if os.path.exists(path):
        with open(path) as f:
            _loaded_bounds = json.load(f)
        logger.info("Loaded calibrated bounds from %s", path)
    else:
        _loaded_bounds = {}
        logger.info("No calibrated bounds file — using defaults")
    return _loaded_bounds


def load_walkforward_bounds() -> dict:
    """Load walk-forward bounds keyed by date string."""
    global _loaded_walkforward_bounds
    if _loaded_walkforward_bounds is not None:
        return _loaded_walkforward_bounds
    path = _get_walkforward_path()
    if os.path.exists(path):
        with open(path) as f:
            _loaded_walkforward_bounds = json.load(f)
    else:
        _loaded_walkforward_bounds = {}
    return _loaded_walkforward_bounds


def get_bounds(indicator: str, bounds_mode: str = "production", target_date: date | None = None) -> tuple:
    """Get (min, max, invert) for an indicator.
    bounds_mode: "production" | "walkforward"
    """
    default = DEFAULT_INDICATOR_BOUNDS.get(indicator, (0, 100, False))
    invert = default[2]

    if bounds_mode == "walkforward" and target_date is not None:
        wf = load_walkforward_bounds()
        date_key = str(target_date)
        if date_key in wf and indicator in wf[date_key]:
            b = wf[date_key][indicator]
            return (b[0], b[1], invert)

    prod = load_production_bounds()
    if indicator in prod:
        b = prod[indicator]
        return (b[0], b[1], invert)

    return default


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


def normalize_indicator(value: float | None, min_val: float, max_val: float, invert: bool) -> float | None:
    """Map a raw indicator value to 0-100 scale, clipped at bounds.
    Returns None if value is None (missing indicator).
    """
    if value is None:
        return None
    if np.isnan(value):
        return None
    if max_val == min_val:
        return 50.0
    clamped = np.clip(value, min_val, max_val)
    score = (clamped - min_val) / (max_val - min_val) * 100
    if invert:
        score = 100 - score
    return float(score)


def compute_dimension_score(
    features: dict,
    dimension: str,
    bounds_mode: str = "production",
    target_date: date | None = None,
) -> tuple[float | None, list[str]]:
    """Compute a single dimension score from features.
    Returns (score, missing_indicators_list).
    If ALL indicators are missing, returns (None, [...]).
    """
    weights = DIMENSION_WEIGHTS[dimension]
    weighted_sum = 0.0
    total_weight = 0.0
    missing = []

    for indicator, weight in weights.items():
        raw_val = features.get(indicator)
        bounds = get_bounds(indicator, bounds_mode, target_date)
        norm = normalize_indicator(raw_val, *bounds)

        if norm is None:
            missing.append(indicator)
            continue

        weighted_sum += norm * weight
        total_weight += weight

    if total_weight == 0:
        return (None, missing)

    # Re-normalize weights for available indicators
    score = weighted_sum / total_weight
    return (float(score), missing)


def compute_all_dimension_scores(
    features: dict,
    bounds_mode: str = "production",
    target_date: date | None = None,
) -> dict:
    """Compute all 5 dimension scores.
    Returns dict with dimension scores + metadata.
    """
    scores = {}
    all_missing = []

    for dim in DIMENSION_ORDER:
        score, missing = compute_dimension_score(features, dim, bounds_mode, target_date)
        scores[dim] = score
        all_missing.extend(missing)

    return {
        "dimension_scores": scores,
        "missing_indicators": all_missing,
    }


def compute_composite_score(dimension_scores: dict[str, float | None]) -> float:
    """Compute weighted composite score from dimension scores.
    Volatility is INVERTED: high vol = low composite (bearish).
    Missing dimensions are excluded and weights re-normalized.
    """
    weighted_sum = 0.0
    total_weight = 0.0

    for dim, weight in COMPOSITE_WEIGHTS.items():
        score = dimension_scores.get(dim)
        if score is None:
            continue

        # Invert volatility: high vol score → low composite contribution
        if dim == "volatility":
            score = 100 - score

        weighted_sum += score * weight
        total_weight += weight

    if total_weight == 0:
        return 50.0  # Fully neutral if nothing available

    return weighted_sum / total_weight


def label_regime(composite_score: float) -> str:
    """Convert composite score to regime label."""
    if composite_score >= BULLISH_THRESHOLD:
        return "Bullish"
    elif composite_score <= BEARISH_THRESHOLD:
        return "Bearish"
    else:
        return "Neutral"


def score_date(
    features: dict,
    bounds_mode: str = "production",
    target_date: date | None = None,
) -> dict:
    """Full scoring pipeline for a single date.
    Returns complete scoring result dict.
    """
    result = compute_all_dimension_scores(features, bounds_mode, target_date)
    dim_scores = result["dimension_scores"]

    composite = compute_composite_score(dim_scores)
    regime = label_regime(composite)

    # Availability regime from features meta
    availability_regime = features.get("_availability_regime", "partial")
    missing_indicators = result["missing_indicators"]

    active_dims = sum(1 for v in dim_scores.values() if v is not None)

    return {
        "vol_score": dim_scores.get("volatility"),
        "trend_score": dim_scores.get("trend"),
        "participation_score": dim_scores.get("participation"),
        "sentiment_score": dim_scores.get("sentiment"),
        "institutional_flow_score": dim_scores.get("institutional_flow"),
        "composite_score": composite,
        "regime_label": regime,
        "availability_regime": availability_regime,
        "missing_indicators": missing_indicators,
        "schema_version": SCHEMA_VERSION,
        "active_dimensions": active_dims,
    }
