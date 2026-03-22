"""Tests for smoothing / transition policy."""

import numpy as np
import pandas as pd
import pytest

from src.smoother import (
    apply_hysteresis,
    compute_consensus,
    detect_shock,
    smooth_scores,
)


class TestSmoothScores:
    def test_no_history(self):
        scores = np.array([50.0, 60.0, 70.0, 80.0])
        result = smooth_scores(scores, [])
        np.testing.assert_array_equal(result, scores)

    def test_with_history(self):
        current = np.array([50.0, 60.0, 70.0, 80.0])
        history = [
            np.array([40.0, 50.0, 60.0, 70.0]),
            np.array([45.0, 55.0, 65.0, 75.0]),
        ]
        result = smooth_scores(current, history)
        assert result.shape == (4,)
        # Smoothed should be between history and current
        assert all(35 <= r <= 85 for r in result)

    def test_ema_dampens_spikes(self):
        # Spike in volatility
        history = [np.array([30.0, 50.0, 50.0, 50.0])] * 5
        spike = np.array([90.0, 50.0, 50.0, 50.0])
        result = smooth_scores(spike, history)
        # Smoothed vol should be between 30 and 90
        assert 30 < result[0] < 90


class TestDetectShock:
    def test_no_shock_normal_day(self):
        current = np.array([50.0, 50.0, 50.0, 50.0])
        history = [np.array([48.0, 52.0, 49.0, 51.0]) for _ in range(20)]
        is_shock, _ = detect_shock(current, history)
        assert not is_shock

    def test_shock_detected(self):
        current = np.array([95.0, 50.0, 50.0, 50.0])  # Vol spike
        np.random.seed(42)
        # History with some variance so std != 0
        history = [np.array([30.0 + np.random.randn(), 50.0, 50.0, 50.0]) for _ in range(20)]
        is_shock, reason = detect_shock(current, history)
        assert is_shock
        assert "volatility" in reason

    def test_insufficient_history(self):
        current = np.array([95.0, 50.0, 50.0, 50.0])
        history = [np.array([30.0, 50.0, 50.0, 50.0])]
        is_shock, _ = detect_shock(current, history)
        assert not is_shock


class TestHysteresis:
    def test_no_change(self):
        label, smoothed, reason = apply_hysteresis("strong_bull", ["strong_bull", "strong_bull"])
        assert label == "strong_bull"
        assert not smoothed

    def test_blocks_single_day_flip(self):
        label, smoothed, reason = apply_hysteresis(
            "bearish", ["strong_bull", "strong_bull"]
        )
        assert label == "strong_bull"  # Incumbent holds
        assert smoothed
        assert "hysteresis" in reason

    def test_allows_persistent_challenger(self):
        # Challenger appeared for 2 consecutive days
        label, smoothed, reason = apply_hysteresis(
            "bearish", ["strong_bull", "bearish", "bearish"]
        )
        assert label == "bearish"  # Challenger wins
        assert not smoothed

    def test_empty_history(self):
        label, smoothed, reason = apply_hysteresis("bearish", [])
        assert label == "bearish"
        assert not smoothed


class TestConsensus:
    def test_unanimous(self):
        mod, desc = compute_consensus("strong_bull", "strong_bull", "strong_bull")
        assert mod == 1.0
        assert desc == "unanimous"

    def test_majority(self):
        mod, desc = compute_consensus("strong_bull", "strong_bull", "bearish")
        assert mod == 0.8
        assert desc == "majority"

    def test_no_consensus(self):
        mod, desc = compute_consensus("strong_bull", "bearish", "neutral")
        assert mod == 0.5
        assert desc == "no_consensus"

    def test_single_classifier(self):
        mod, desc = compute_consensus("strong_bull", None, None)
        assert mod == 0.7
        assert desc == "single_classifier"

    def test_two_classifiers_agree(self):
        mod, desc = compute_consensus("bearish", "bearish", None)
        assert mod == 1.0
        assert desc == "unanimous"
