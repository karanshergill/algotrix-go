"""ATR & volatility baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import to_utc_ns, get_isins, chunked, isin_where_clause


def _compute_atr(highs, lows, closes, period=14):
    """Compute ATR given bar OHLC arrays. Returns the last ATR value."""
    n = len(highs)
    if n < 2:
        return 0.0

    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    if n < period:
        return float(np.mean(tr))

    # Wilder smoothing
    atr = np.mean(tr[:period])
    for i in range(period, n):
        atr = (atr * (period - 1) + tr[i]) / period
    return float(atr)


class ATRVolatilityPlugin(BaselinePlugin):
    """Compute ATR at multiple timeframes per stock per day.

    Uses QuestDB SAMPLE BY to pre-aggregate 5s candles to 5m/15m/1h
    directly in SQL (avoids fetching 194M raw rows). Queries per-stock
    in chunks. Computes ATR(14) at each timeframe per day.
    Derives ATR percentile, trend, and volatility regime.
    """

    name = "atr_volatility"
    description = "ATR and volatility regime per stock per day"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "atr_period", "timeframes",
                     "regime_thresholds", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"atr_volatility: missing config key '{key}'")

    def _fetch_resampled_bars(self, source_table, isins, tf, start_date, end_date):
        """Fetch SAMPLE BY resampled bars for a chunk of ISINs at given timeframe.

        Returns {isin: {trade_date: (high, low, close)_list_in_order}}.
        """
        where = isin_where_clause(isins, start_date, end_date)
        sql = (
            f"SELECT isin, timestamp_floor('d', timestamp) AS trade_date, "
            f"max(high) AS high, min(low) AS low, last(close) AS close "
            f"FROM {source_table} "
            f"WHERE {where} "
            f"SAMPLE BY {tf} FILL(NULL) ALIGN TO CALENDAR "
            f"ORDER BY isin, timestamp"
        )
        rows = questdb_query(self.cfg, sql)

        data = {}
        for r in rows:
            isin = r["isin"]
            h = r["high"]
            l = r["low"]
            c = r["close"]
            if h is None or l is None or c is None:
                continue
            td = str(r["trade_date"])[:10]
            if isin not in data:
                data[isin] = {}
            if td not in data[isin]:
                data[isin][td] = []
            data[isin][td].append((float(h), float(l), float(c)))

        return data

    def compute(self):
        pcfg = self.plugin_cfg
        source_table = self.cfg["sources"][pcfg["source"]]
        lookback_days = pcfg["lookback_days"]
        atr_period = int(pcfg["atr_period"])
        timeframes = pcfg["timeframes"]
        regime = pcfg["regime_thresholds"]
        compressed_th = float(regime["compressed"])
        expanded_th = float(regime["expanded"])

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        all_isins = get_isins(self.cfg, source_table, start_str, end_str)
        if not all_isins:
            return []

        # For each timeframe, fetch resampled bars in ISIN chunks
        isin_tf_atrs = {}  # isin -> tf -> [(td, atr)]

        for tf in timeframes:
            for chunk in chunked(all_isins):
                chunk_data = self._fetch_resampled_bars(
                    source_table, chunk, tf, start_str, end_str
                )

                for isin, days in chunk_data.items():
                    if isin not in isin_tf_atrs:
                        isin_tf_atrs[isin] = {}
                    if tf not in isin_tf_atrs[isin]:
                        isin_tf_atrs[isin][tf] = []

                    for td in sorted(days.keys()):
                        bars = days[td]
                        if len(bars) < 2:
                            continue
                        h_arr = np.array([b[0] for b in bars])
                        l_arr = np.array([b[1] for b in bars])
                        c_arr = np.array([b[2] for b in bars])
                        atr_val = _compute_atr(h_arr, l_arr, c_arr, atr_period)
                        isin_tf_atrs[isin][tf].append((td, atr_val))

        # Build results — one row per (isin, trade_date)
        isin_dates = {}
        for isin, tf_map in isin_tf_atrs.items():
            isin_dates[isin] = set()
            for tf, td_list in tf_map.items():
                for td, _ in td_list:
                    isin_dates[isin].add(td)

        results = []
        for isin, tds in isin_dates.items():
            tf_lookup = {}
            for tf in timeframes:
                if tf in isin_tf_atrs.get(isin, {}):
                    tf_lookup[tf] = {td: atr for td, atr in isin_tf_atrs[isin][tf]}
                else:
                    tf_lookup[tf] = {}

            ref_tf = timeframes[0] if timeframes else "5m"
            ref_atrs_sorted = sorted(isin_tf_atrs.get(isin, {}).get(ref_tf, []), key=lambda x: x[0])
            ref_atr_values = np.array([a for _, a in ref_atrs_sorted]) if ref_atrs_sorted else np.array([])

            for td in sorted(tds):
                rec = {}
                for tf in timeframes:
                    atr_val = tf_lookup[tf].get(td)
                    if atr_val is not None:
                        rec[tf] = atr_val

                if not rec:
                    continue

                ref_atr = rec.get(ref_tf, 0.0)
                percentile = 50.0
                if len(ref_atr_values) > 1:
                    percentile = float(
                        np.searchsorted(np.sort(ref_atr_values), ref_atr)
                        / len(ref_atr_values) * 100
                    )

                trend = "flat"
                if ref_atrs_sorted:
                    idx = next((i for i, (d, _) in enumerate(ref_atrs_sorted) if d == td), None)
                    if idx is not None and idx >= 4:
                        recent = [a for _, a in ref_atrs_sorted[max(0, idx - 4):idx + 1]]
                        if len(recent) >= 3:
                            first_half = np.mean(recent[:len(recent) // 2])
                            second_half = np.mean(recent[len(recent) // 2:])
                            if second_half > first_half * 1.1:
                                trend = "rising"
                            elif second_half < first_half * 0.9:
                                trend = "falling"

                if percentile <= compressed_th:
                    regime_label = "compressed"
                elif percentile >= expanded_th:
                    regime_label = "expanded"
                else:
                    regime_label = "normal"

                ts_ns = to_utc_ns(td)

                row = {
                    "isin": isin,
                    "trade_date": ts_ns,
                    "atr_percentile": round(percentile, 2),
                    "atr_trend": trend,
                    "volatility_regime": regime_label,
                    "timestamp": ts_ns,
                }
                for tf in timeframes:
                    col = f"atr_{tf}"
                    row[col] = round(rec.get(tf, 0.0), 4)

                results.append(row)

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
