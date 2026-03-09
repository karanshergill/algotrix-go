"""Intraday volume curve baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import utc_now_ns, get_isins, chunked, isin_where_clause


class IntradayVolumePlugin(BaselinePlugin):
    """Compute per-stock volume stats by time-of-day bin.

    Groups 5s candles into 5-minute bins (9:15-15:30 = 75 bins).
    For each bin: mean, std, median, min, max volume across all trading days.
    Used as RVOL baseline.
    """

    name = "intraday_volume"
    description = "Average volume per time-of-day bin per stock"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "bin_size_minutes", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"intraday_volume: missing config key '{key}'")

    def compute(self):
        pcfg = self.plugin_cfg
        source_table = self.cfg["sources"][pcfg["source"]]
        lookback_days = pcfg["lookback_days"]
        bin_size = int(pcfg["bin_size_minutes"])

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        all_isins = get_isins(self.cfg, source_table, start_str, end_str)
        if not all_isins:
            return []

        # Use SAMPLE BY to aggregate volume server-side into bin_size bins
        tf = f"{bin_size}m"

        # Process in chunks, accumulate into day_bins
        # (isin, trade_date, time_bin_str) -> accumulated volume
        day_bins = {}

        for chunk in chunked(all_isins):
            where = isin_where_clause(chunk, start_str, end_str)
            sql = (
                f"SELECT isin, timestamp_floor('d', timestamp) AS trade_date, "
                f"timestamp, sum(volume) AS vol "
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
                vol = r["vol"]
                if vol is None:
                    continue

                td = str(r["trade_date"])[:10]
                ts_str = str(r["timestamp"])

                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    total_minutes = dt.hour * 60 + dt.minute
                except (ValueError, AttributeError):
                    continue

                bin_minutes = (total_minutes // bin_size) * bin_size
                bin_h, bin_m = divmod(bin_minutes, 60)
                time_bin = f"{bin_h:02d}:{bin_m:02d}"

                key = (isin, td, time_bin)
                day_bins[key] = day_bins.get(key, 0) + int(vol)

        # Aggregate across days per (isin, time_bin)
        agg = {}
        for (isin, td, time_bin), vol in day_bins.items():
            k = (isin, time_bin)
            if k not in agg:
                agg[k] = []
            agg[k].append(vol)

        now_ns = utc_now_ns()
        results = []
        for (isin, time_bin), vols in agg.items():
            arr = np.array(vols, dtype=np.float64)
            results.append({
                "isin": isin,
                "time_bin": time_bin,
                "avg_volume": round(float(np.mean(arr)), 2),
                "std_volume": round(float(np.std(arr)), 2),
                "median_volume": round(float(np.median(arr)), 2),
                "min_volume": int(np.min(arr)),
                "max_volume": int(np.max(arr)),
                "sample_days": len(vols),
                "timestamp": now_ns,
            })

        return results

    def store(self, results):
        if not results:
            return 0
        table = self.plugin_cfg["table"]
        return questdb_write_ilp(
            self.cfg, table, results,
            symbols=["isin", "time_bin"],
            timestamps=["timestamp"],
        )
