"""Spread estimate baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import to_utc_ns, get_isins, chunked, isin_where_clause


class SpreadEstimatePlugin(BaselinePlugin):
    """Estimate bid-ask spread per stock per day from 5s candle ranges.

    Uses mean(high - low) for 5s candles with volume > 0 as a spread proxy.
    Also computes median and p90.
    """

    name = "spread_estimate"
    description = "Spread estimate from 5s candle ranges per stock per day"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"spread_estimate: missing config key '{key}'")

    def compute(self):
        pcfg = self.plugin_cfg
        source_table = self.cfg["sources"][pcfg["source"]]
        lookback_days = pcfg["lookback_days"]

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        all_isins = get_isins(self.cfg, source_table, start_str, end_str)
        if not all_isins:
            return []

        # Process in chunks, accumulate groups
        groups = {}  # (isin, td) -> list of spreads

        # Use small chunks (10 ISINs) since we fetch raw 5s candles
        for chunk in chunked(all_isins, size=10):
            where = isin_where_clause(chunk, start_str, end_str) + " AND volume > 0"
            sql = (
                f"SELECT isin, timestamp_floor('d', timestamp) AS trade_date, "
                f"high, low "
                f"FROM {source_table} "
                f"WHERE {where} "
                f"ORDER BY isin, trade_date"
            )
            rows = questdb_query(self.cfg, sql)
            if not rows:
                continue

            for r in rows:
                isin = r["isin"]
                td = str(r["trade_date"])[:10]
                h = r["high"]
                l = r["low"]
                if h is None or l is None:
                    continue

                spread = float(h) - float(l)
                if spread < 0:
                    continue

                key = (isin, td)
                if key not in groups:
                    groups[key] = []
                groups[key].append(spread)

        results = []
        for (isin, td), spreads in groups.items():
            if not spreads:
                continue

            arr = np.array(spreads, dtype=np.float64)
            ts_ns = to_utc_ns(td)

            results.append({
                "isin": isin,
                "trade_date": ts_ns,
                "mean_spread": round(float(np.mean(arr)), 4),
                "median_spread": round(float(np.median(arr)), 4),
                "p90_spread": round(float(np.percentile(arr, 90)), 4),
                "sample_count": len(spreads),
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
