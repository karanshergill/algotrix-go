"""Tests for indicator calculations — pure math, no DB needed."""

import numpy as np
import pandas as pd
import pytest

from src.indicators import (
    compute_ad_ratio,
    compute_adx,
    compute_atr,
    compute_bbw,
    compute_ema,
    compute_ema_distance_pct,
    compute_ema_slope,
    compute_futures_basis_pct,
    compute_hurst,
    compute_pct_above_ema20,
    compute_pcr_oi,
    compute_trin,
    is_above_ema,
)


def _make_ohlcv(n=60, base=100, volatility=2):
    """Generate synthetic OHLCV data."""
    np.random.seed(42)
    closes = base + np.cumsum(np.random.randn(n) * volatility)
    highs = closes + abs(np.random.randn(n)) * volatility
    lows = closes - abs(np.random.randn(n)) * volatility
    return (
        pd.Series(highs, name="high"),
        pd.Series(lows, name="low"),
        pd.Series(closes, name="close"),
    )


class TestVolatilityIndicators:
    def test_atr_length(self):
        high, low, close = _make_ohlcv(60)
        atr = compute_atr(high, low, close, period=14)
        assert len(atr) == 60
        assert not np.isnan(atr.iloc[-1])

    def test_atr_positive(self):
        high, low, close = _make_ohlcv(60)
        atr = compute_atr(high, low, close)
        valid = atr.dropna()
        assert (valid > 0).all()

    def test_bbw_positive(self):
        _, _, close = _make_ohlcv(60)
        bbw = compute_bbw(close)
        valid = bbw.dropna()
        assert (valid >= 0).all()


class TestTrendIndicators:
    def test_adx_range(self):
        high, low, close = _make_ohlcv(60)
        adx = compute_adx(high, low, close, period=14)
        valid = adx.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_ema_tracks_close(self):
        _, _, close = _make_ohlcv(60)
        ema = compute_ema(close, period=20)
        # EMA should be close to close on average
        valid_ema = ema.dropna()
        valid_close = close.iloc[-len(valid_ema):]
        assert abs(valid_ema.mean() - valid_close.mean()) < 10

    def test_ema_distance(self):
        _, _, close = _make_ohlcv(60)
        ema = compute_ema(close, period=20)
        dist = compute_ema_distance_pct(close, ema)
        valid = dist.dropna()
        # Distance should be small for random walk
        assert abs(valid.mean()) < 10

    def test_is_above_ema(self):
        _, _, close = _make_ohlcv(60)
        ema = compute_ema(close, period=20)
        above = is_above_ema(close, ema)
        assert above.dtype == bool


class TestBreadthIndicators:
    def test_ad_ratio_bullish_day(self):
        # All stocks up
        data = pd.DataFrame({
            "isin": ["A", "B", "C"],
            "close": [110, 210, 310],
            "prev_close": [100, 200, 300],
            "volume": [1000, 2000, 3000],
        })
        ratio = compute_ad_ratio(data)
        assert ratio == 3.0  # 3 advances, 0 declines → returns advances count

    def test_ad_ratio_mixed(self):
        data = pd.DataFrame({
            "isin": ["A", "B", "C", "D"],
            "close": [110, 190, 310, 410],
            "prev_close": [100, 200, 300, 400],
            "volume": [1000, 2000, 3000, 4000],
        })
        ratio = compute_ad_ratio(data)
        assert ratio == 3.0  # 3 up, 1 down → 3/1

    def test_ad_ratio_empty(self):
        data = pd.DataFrame(columns=["isin", "close", "prev_close", "volume"])
        assert np.isnan(compute_ad_ratio(data))

    def test_trin_bullish(self):
        # More advancing volume → TRIN < 1
        data = pd.DataFrame({
            "isin": ["A", "B", "C"],
            "close": [110, 90, 310],
            "prev_close": [100, 100, 300],
            "volume": [5000, 1000, 5000],
        })
        trin = compute_trin(data)
        assert trin < 1.0  # Bullish

    def test_pct_above_ema20(self):
        np.random.seed(42)
        n_days = 30
        n_stocks = 10
        # Create uptrending stocks (most above EMA)
        data = {}
        for i in range(n_stocks):
            data[f"STOCK{i}"] = 100 + np.arange(n_days) * 0.5 + np.random.randn(n_days)
        df = pd.DataFrame(data)
        pct = compute_pct_above_ema20(df)
        assert 0 <= pct <= 100


class TestSentimentIndicators:
    def test_pcr_oi(self):
        data = pd.DataFrame({
            "instrument_type": ["IDO", "IDO", "IDO", "IDO"],
            "option_type": ["PE", "PE", "CE", "CE"],
            "oi": [100000, 50000, 80000, 70000],
        })
        pcr = compute_pcr_oi(data)
        assert pcr == pytest.approx(150000 / 150000, rel=0.01)

    def test_pcr_oi_no_calls(self):
        data = pd.DataFrame({
            "instrument_type": ["IDO"],
            "option_type": ["PE"],
            "oi": [100000],
        })
        assert np.isnan(compute_pcr_oi(data))

    def test_futures_basis(self):
        data = pd.DataFrame({
            "instrument_type": ["IDF", "IDF"],
            "option_type": [None, None],
            "expiry": [pd.Timestamp("2026-03-27"), pd.Timestamp("2026-04-24")],
            "close": [23050.0, 23100.0],
            "underlying": [23000.0, 23000.0],
        })
        basis = compute_futures_basis_pct(data)
        assert basis == pytest.approx(50.0 / 23000 * 100, rel=0.01)

    def test_futures_basis_empty(self):
        data = pd.DataFrame(columns=["instrument_type", "option_type", "expiry", "close", "underlying"])
        assert np.isnan(compute_futures_basis_pct(data))


class TestHurst:
    def test_hurst_range(self):
        np.random.seed(42)
        close = pd.Series(np.cumsum(np.random.randn(200)) + 100)
        h = compute_hurst(close, window=100)
        assert 0 < h < 1

    def test_hurst_insufficient_data(self):
        close = pd.Series([100, 101, 102])
        assert np.isnan(compute_hurst(close, window=100))
