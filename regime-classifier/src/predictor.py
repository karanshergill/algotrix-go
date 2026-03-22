"""Prediction layer — next-day regime forecast from leading indicators.

Uses today's scored leading indicators to predict tomorrow's regime.
Separate from coincident scoring: uses only indicators with predictive power.
"""

from datetime import date

import numpy as np

from src.scorer import get_bounds, normalize_indicator, label_regime

# Leading indicator weights for next-day prediction
LEADING_WEIGHTS = {
    "fii_flow_delta": (0.25, False),      # FII flow delta (higher = more bullish)
    "gift_nifty_overnight_gap": (0.25, False),  # GIFT Nifty gap (higher = bullish)
    "sp500_overnight_return": (0.15, False),     # S&P 500 (higher = risk-on)
    "dxy_overnight_change": (0.10, True),        # DXY up = bearish for EM → invert
    "us10y_overnight_change": (0.10, True),      # Yield up = bearish → invert
    "vix_roc_5d": (0.10, True),                  # VIX rising = bearish → invert
    "breadth_momentum_5d": (0.05, False),        # Breadth improving = bullish
}


def predict_next_day(
    features: dict,
    bounds_mode: str = "production",
    target_date: date | None = None,
) -> dict:
    """Predict tomorrow's regime from today's leading indicators.

    Returns dict with leading_score, predicted_label, confidence.
    """
    weighted_sum = 0.0
    total_weight = 0.0
    used_indicators = []
    missing = []

    for indicator, (weight, invert_override) in LEADING_WEIGHTS.items():
        raw_val = features.get(indicator)
        if raw_val is None:
            missing.append(indicator)
            continue

        bounds = get_bounds(indicator, bounds_mode, target_date)
        min_val, max_val, _default_invert = bounds

        # Use our own invert flag for prediction (some differ from scorer direction)
        norm = normalize_indicator(raw_val, min_val, max_val, invert_override)
        if norm is None:
            missing.append(indicator)
            continue

        weighted_sum += norm * weight
        total_weight += weight
        used_indicators.append(indicator)

    if total_weight == 0:
        return {
            "leading_score": 50.0,
            "predicted_label": "Neutral",
            "confidence": 0.0,
            "used_indicators": [],
            "missing_indicators": list(LEADING_WEIGHTS.keys()),
        }

    leading_score = weighted_sum / total_weight
    predicted_label = label_regime(leading_score)
    confidence = abs(leading_score - 50) / 50  # 0.0 = uncertain, 1.0 = extreme

    return {
        "leading_score": float(leading_score),
        "predicted_label": predicted_label,
        "confidence": float(confidence),
        "used_indicators": used_indicators,
        "missing_indicators": missing,
    }
