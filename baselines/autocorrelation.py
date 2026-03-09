"""Autocorrelation baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import utc_now_ns, get_isins, chunked, isin_where_clause


class AutocorrelationPlugin(BaselinePlugin):
    """Compute lag-1 autocorrelation of 5m returns per stock.

    Positive = trending tendency, negative = mean-reverting tendency.
    """

    name = "autocorrelation"
    description = "Lag-1 autocorrelation of intraday returns per stock"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "return_timeframe", "lag", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"autocorrelation: missing config key '{key}'")

    def compute(self):
        pcfg = self.plugin_cfg
        source_table = self.cfg["sources"][pcfg["source"]]
        lookback_days = pcfg["lookback_days"]
        lag = int(pcfg["lag"])
        tf = pcfg["return_timeframe"]

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        all_isins = get_isins(self.cfg, source_table, start_str, end_str)
        if not all_isins:
            return []

        # Fetch SAMPLE BY resampled closes in ISIN chunks
        isin_closes = {}
        for chunk in chunked(all_isins):
            where = isin_where_clause(chunk, start_str, end_str)
            sql = (
                f"SELECT isin, timestamp, last(close) AS close "
                f"FROM {source_table} "
                f"WHERE {where} "
                f"SAMPLE BY {tf} FILL(NULL) ALIGN TO CALENDAR "
                f"ORDER BY isin, timestamp"
            )
            rows = questdb_query(self.cfg, sql)
            if not rows:
                continue

            for r in rows:
                isin = r["isin"]
                close = r["close"]
                if close is None:
                    continue
                if isin not in isin_closes:
                    isin_closes[isin] = []
                isin_closes[isin].append(float(close))

        if not isin_closes:
            return []

        now_ns = utc_now_ns()
        results = []

        for isin, closes in isin_closes.items():
            if len(closes) < lag + 10:
                continue

            arr = np.array(closes, dtype=np.float64)
            returns = np.diff(np.log(arr))
            returns = returns[np.isfinite(returns)]

            if len(returns) < lag + 10:
                continue

            r1 = returns[:-lag]
            r2 = returns[lag:]
            if len(r1) < 2:
                continue

            corr = float(np.corrcoef(r1, r2)[0, 1])
            if not np.isfinite(corr):
                continue

            if corr > 0.05:
                regime = "trending"
            elif corr < -0.05:
                regime = "mean_reverting"
            else:
                regime = "random"

            results.append({
                "isin": isin,
                "lag": lag,
                "autocorrelation": round(corr, 6),
                "regime": regime,
                "sample_size": len(r1),
                "timestamp": now_ns,
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
