"""Tests for Euclidean classifier + scorer."""

import numpy as np
import pytest

from src.classifier import classify_euclidean
from src.profiles import REGIME_PROFILES
from src.scorer import (
    INDICATOR_BOUNDS,
    compute_dimension_scores,
    normalize_indicator,
)


class TestNormalizeIndicator:
    def test_min_value(self):
        assert normalize_indicator(10.0, 10.0, 40.0, False) == 0.0

    def test_max_value(self):
        assert normalize_indicator(40.0, 10.0, 40.0, False) == 100.0

    def test_mid_value(self):
        assert normalize_indicator(25.0, 10.0, 40.0, False) == 50.0

    def test_clamp_below(self):
        assert normalize_indicator(5.0, 10.0, 40.0, False) == 0.0

    def test_clamp_above(self):
        assert normalize_indicator(50.0, 10.0, 40.0, False) == 100.0

    def test_invert(self):
        assert normalize_indicator(10.0, 10.0, 40.0, True) == 100.0
        assert normalize_indicator(40.0, 10.0, 40.0, True) == 0.0

    def test_nan_returns_neutral(self):
        assert normalize_indicator(float("nan"), 10.0, 40.0, False) == 50.0


class TestDimensionScores:
    def test_output_shape(self):
        features = {
            "india_vix_close": 20.0,
            "nifty_atr_pctile_60d": 50.0,
            "nifty_bbw_pctile_60d": 50.0,
            "nifty_adx14": 30.0,
            "nifty_ema20_distance": 1.0,
            "nifty_ema20_slope": 0.5,
            "ad_ratio": 1.5,
            "ad_ratio_5d_avg": 1.3,
            "trin": 0.9,
            "universe_pct_above_ema20": 60.0,
            "nifty50_pct_above_ema20": 65.0,
            "pcr_oi": 1.0,
            "fut_basis_pct": 0.1,
        }
        scores = compute_dimension_scores(features)
        assert scores.shape == (4,)
        assert all(0 <= s <= 100 for s in scores)

    def test_all_missing_returns_neutral(self):
        scores = compute_dimension_scores({})
        assert scores.shape == (4,)
        assert all(s == 50.0 for s in scores)


class TestEuclideanClassifier:
    def _features_near_profile(self, profile_name: str) -> dict:
        """Create features that should classify as the given profile."""
        profile = REGIME_PROFILES[profile_name]
        # Map profile scores back to approximate raw values
        vol, trend, part, sent = profile

        return {
            "india_vix_close": 10 + vol * 0.3,  # 10-40 range
            "nifty_atr_pctile_60d": vol,
            "nifty_bbw_pctile_60d": vol,
            "nifty_adx14": 10 + trend * 0.4,  # 10-50 range
            "nifty_ema20_distance": -5 + trend * 0.1,  # -5 to 5
            "nifty_ema20_slope": -3 + trend * 0.06,  # -3 to 3
            "ad_ratio": 0.3 + part * 0.027,  # 0.3-3.0
            "ad_ratio_5d_avg": 0.5 + part * 0.02,
            "trin": 2.0 - part * 0.015,  # Inverted: low trin = high part
            "universe_pct_above_ema20": 20 + part * 0.6,
            "nifty50_pct_above_ema20": 20 + part * 0.6,
            "pcr_oi": 1.5 - sent * 0.01,  # Inverted: low pcr = bullish
            "fut_basis_pct": -0.5 + sent * 0.01,
        }

    def test_strong_bull_classification(self):
        features = self._features_near_profile("strong_bull")
        result = classify_euclidean(features)
        assert result["label"] == "strong_bull"
        assert result["confidence"] > 0

    def test_bearish_classification(self):
        features = self._features_near_profile("bearish")
        result = classify_euclidean(features)
        assert result["label"] == "bearish"

    def test_volatile_choppy_classification(self):
        features = self._features_near_profile("volatile_choppy")
        result = classify_euclidean(features)
        assert result["label"] == "volatile_choppy"

    def test_result_has_all_fields(self):
        features = self._features_near_profile("neutral")
        result = classify_euclidean(features)
        assert "label" in result
        assert "confidence" in result
        assert "distances" in result
        assert "dimension_scores" in result
        assert len(result["distances"]) == 5
        assert len(result["dimension_scores"]) == 4

    def test_confidence_range(self):
        features = self._features_near_profile("strong_bull")
        result = classify_euclidean(features)
        assert 0 <= result["confidence"] <= 1

    def test_all_profiles_are_reachable(self):
        """Each profile should be the nearest to features designed for it."""
        for profile_name in REGIME_PROFILES:
            features = self._features_near_profile(profile_name)
            result = classify_euclidean(features)
            assert result["label"] == profile_name, (
                f"Expected {profile_name}, got {result['label']} "
                f"(distances: {result['distances']})"
            )
