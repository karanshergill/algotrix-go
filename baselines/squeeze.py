"""Squeeze / compression detection baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import to_utc_ns


def _compute_rolling_adx(highs, lows, closes, period=14):
    """Compute rolling ADX from arrays. Returns array of ADX per bar (NaN where insufficient data)."""
    n = len(highs)
    adx_out = np.full(n, np.nan)

    if n < period + 1:
        return adx_out

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # Wilder smoothing for +DI, -DI, ATR
    atr_s = float(np.mean(tr[1:period + 1]))
    plus_di_s = float(np.mean(plus_dm[1:period + 1]))
    minus_di_s = float(np.mean(minus_dm[1:period + 1]))

    dx_values = []
    bar_dx = {}

    for i in range(period + 1, n):
        atr_s = (atr_s * (period - 1) + tr[i]) / period
        plus_di_s = (plus_di_s * (period - 1) + plus_dm[i]) / period
        minus_di_s = (minus_di_s * (period - 1) + minus_dm[i]) / period

        if atr_s == 0:
            dx_values.append(0.0)
            bar_dx[i] = 0.0
            continue
        plus_di = 100.0 * plus_di_s / atr_s
        minus_di = 100.0 * minus_di_s / atr_s
        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_values.append(0.0)
            bar_dx[i] = 0.0
            continue
        dx = 100.0 * abs(plus_di - minus_di) / di_sum
        dx_values.append(dx)
        bar_dx[i] = dx

    # ADX = Wilder-smoothed DX, compute rolling
    if len(dx_values) < period:
        adx_val = 0.0
        dx_idx = 0
        for i in range(period + 1, n):
            dx_idx = i - (period + 1)
            if dx_idx < len(dx_values):
                adx_val = float(np.mean(dx_values[:dx_idx + 1]))
                adx_out[i] = adx_val
        return adx_out

    adx_val = float(np.mean(dx_values[:period]))
    first_adx_bar = period + 1 + period - 1
    if first_adx_bar < n:
        adx_out[first_adx_bar] = adx_val

    for dx_i in range(period, len(dx_values)):
        adx_val = (adx_val * (period - 1) + dx_values[dx_i]) / period
        bar_i = period + 1 + dx_i
        if bar_i < n:
            adx_out[bar_i] = adx_val

    return adx_out


class SqueezePlugin(BaselinePlugin):
    """Detect Bollinger Band squeeze per stock per day.

    From 1d OHLCV: BB width percentile + ADX.
    Squeeze = BBW below threshold percentile AND ADX < 25.
    """

    name = "squeeze"
    description = "Bollinger Band squeeze and compression detection"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "bb_period", "bb_std",
                     "adx_period", "squeeze_bbw_percentile", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"squeeze: missing config key '{key}'")

    def compute(self):
        pcfg = self.plugin_cfg
        source_table = self.cfg["sources"][pcfg["source"]]
        lookback_days = pcfg["lookback_days"]
        bb_period = int(pcfg["bb_period"])
        bb_std_mult = float(pcfg["bb_std"])
        adx_period = int(pcfg["adx_period"])
        squeeze_bbw_pct = float(pcfg["squeeze_bbw_percentile"])

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + bb_period + adx_period + 30)

        symbol_filter = self.cfg.get("_symbols_filter")
        where_parts = [
            f"timestamp >= '{start_date.isoformat()}'",
            f"timestamp <= '{end_date.isoformat()}T23:59:59.999999Z'",
        ]
        if symbol_filter:
            isin_list = ",".join(f"'{s}'" for s in symbol_filter)
            where_parts.append(f"isin IN ({isin_list})")

        where_clause = " AND ".join(where_parts)

        sql = (
            f"SELECT isin, timestamp_floor('d', timestamp) AS trade_date, "
            f"open, high, low, close "
            f"FROM {source_table} "
            f"WHERE {where_clause} "
            f"ORDER BY isin, trade_date"
        )
        rows = questdb_query(self.cfg, sql)

        if not rows:
            return []

        # Group by isin, preserving day order
        isin_data = {}
        for r in rows:
            isin = r["isin"]
            td = str(r["trade_date"])[:10]
            if isin not in isin_data:
                isin_data[isin] = []
            o = float(r["open"]) if r["open"] is not None else 0.0
            h = float(r["high"]) if r["high"] is not None else 0.0
            l = float(r["low"]) if r["low"] is not None else 0.0
            c = float(r["close"]) if r["close"] is not None else 0.0
            isin_data[isin].append((td, o, h, l, c))

        results = []
        for isin, day_list in isin_data.items():
            if len(day_list) < bb_period:
                continue

            dates = [d[0] for d in day_list]
            highs = np.array([d[2] for d in day_list])
            lows = np.array([d[3] for d in day_list])
            closes = np.array([d[4] for d in day_list])
            n = len(closes)

            bbw_values = []
            for i in range(bb_period - 1, n):
                window = closes[i - bb_period + 1:i + 1]
                sma = np.mean(window)
                std = np.std(window, ddof=0)
                upper = sma + bb_std_mult * std
                lower = sma - bb_std_mult * std
                bbw = (upper - lower) / sma if sma > 0 else 0.0
                bbw_values.append((i, bbw))

            if not bbw_values:
                continue

            all_bbw = np.array([b[1] for b in bbw_values])
            rolling_adx = _compute_rolling_adx(highs, lows, closes, adx_period)
            daily_ranges = (highs - lows) / np.where(closes > 0, closes, 1.0) * 100.0
            cutoff_date = (end_date - timedelta(days=lookback_days + 10)).isoformat()

            consecutive_squeeze = 0
            for idx_pos, (day_idx, bbw) in enumerate(bbw_values):
                td = dates[day_idx]
                if td < cutoff_date:
                    continue

                bbw_so_far = all_bbw[:idx_pos + 1]
                if len(bbw_so_far) > 1:
                    bbw_percentile = float(
                        np.searchsorted(np.sort(bbw_so_far), bbw) / len(bbw_so_far) * 100
                    )
                else:
                    bbw_percentile = 50.0

                # Skip days where ADX is not yet valid (insufficient data
                # for Wilder smoothing) to avoid false squeeze signals
                if not np.isfinite(rolling_adx[day_idx]):
                    continue
                adx = float(rolling_adx[day_idx])
                daily_range_pct = float(daily_ranges[day_idx])

                range_trend = "flat"
                if day_idx >= 4:
                    # Compare first 2 vs last 2 of 5 bars; middle element
                    # is the pivot and intentionally excluded from both halves
                    recent_ranges = daily_ranges[day_idx - 4:day_idx + 1]
                    first_half = np.mean(recent_ranges[:2])
                    second_half = np.mean(recent_ranges[3:])
                    if second_half < first_half * 0.85:
                        range_trend = "declining"
                    elif second_half > first_half * 1.15:
                        range_trend = "expanding"

                is_compressed = bbw_percentile <= squeeze_bbw_pct and adx < 25.0

                if is_compressed:
                    consecutive_squeeze += 1
                else:
                    consecutive_squeeze = 0

                squeeze_score = round(
                    0.4 * max(0, 100 - bbw_percentile) +
                    0.4 * max(0, 25 - adx) +
                    0.2 * (10 if range_trend == "declining" else 0),
                    2
                )

                ts_ns = to_utc_ns(td)

                results.append({
                    "isin": isin,
                    "trade_date": ts_ns,
                    "bbw": round(bbw, 6),
                    "bbw_percentile": round(bbw_percentile, 2),
                    "adx": round(adx, 2),
                    "daily_range_pct": round(daily_range_pct, 4),
                    "range_trend": range_trend,
                    "squeeze_score": squeeze_score,
                    "is_compressed": is_compressed,
                    "days_in_squeeze": consecutive_squeeze,
                    "timestamp": ts_ns,
                })

        return results

    def store(self, results):
        if not results:
            return 0
        table = self.plugin_cfg["table"]
        return questdb_write_ilp(
            self.cfg, table, results,
            symbols=["isin"],
            timestamps=["timestamp"],
        )
