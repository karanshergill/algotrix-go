"""Daily volume baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import to_utc_ns, get_isins, chunked, isin_where_clause


class DailyVolumePlugin(BaselinePlugin):
    """Compute daily volume stats per stock per day.

    Total volume, VWAP, and volume vs lookback average ratio.
    """

    name = "daily_volume"
    description = "Daily volume, VWAP, and relative volume per stock"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"daily_volume: missing config key '{key}'")

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

        # Use SAMPLE BY 1d to aggregate server-side, avoiding raw 5s data transfer.
        # VWAP uses typical price (high+low+close)/3 weighted by volume.
        groups = {}  # (isin, td) -> {total_vol, sum_pv}

        for chunk in chunked(all_isins):
            where = isin_where_clause(chunk, start_str, end_str)
            sql = (
                f"SELECT isin, timestamp, "
                f"sum(volume) AS total_vol, "
                f"sum((high + low + close) / 3.0 * volume) AS sum_pv "
                f"FROM {source_table} "
                f"WHERE {where} "
                f"SAMPLE BY 1d FILL(NULL) ALIGN TO CALENDAR "
                f"ORDER BY isin, timestamp"
            )
            rows = questdb_query(self.cfg, sql)
            if not rows:
                continue

            for r in rows:
                isin = r["isin"]
                total_vol = r["total_vol"]
                sum_pv = r["sum_pv"]
                if total_vol is None or sum_pv is None:
                    continue

                total_vol = int(total_vol)
                sum_pv = float(sum_pv)
                if total_vol == 0:
                    continue

                # Extract trade date from the SAMPLE BY timestamp
                td = str(r.get("timestamp", ""))[:10]
                if not td or len(td) != 10:
                    continue

                key = (isin, td)
                if key not in groups:
                    groups[key] = {"total_vol": 0, "sum_pv": 0.0}

                g = groups[key]
                g["total_vol"] += total_vol
                g["sum_pv"] += sum_pv

        # Collect per isin for avg volume calculation
        isin_days = {}
        for (isin, td), g in groups.items():
            vwap = g["sum_pv"] / g["total_vol"] if g["total_vol"] > 0 else 0.0
            turnover = g["sum_pv"]
            if isin not in isin_days:
                isin_days[isin] = []
            isin_days[isin].append((td, g["total_vol"], vwap, turnover))

        for isin in isin_days:
            isin_days[isin].sort(key=lambda x: x[0])

        results = []
        for isin, day_list in isin_days.items():
            volumes = [d[1] for d in day_list]

            for i, (td, total_vol, vwap, turnover) in enumerate(day_list):
                if i > 0:
                    lookback_vols = volumes[max(0, i - lookback_days):i]
                    avg_vol = float(np.mean(lookback_vols)) if lookback_vols else 0.0
                else:
                    avg_vol = float(total_vol)

                vol_ratio = total_vol / avg_vol if avg_vol > 0 else 1.0
                ts_ns = to_utc_ns(td)

                results.append({
                    "isin": isin,
                    "trade_date": ts_ns,
                    "total_volume": total_vol,
                    "vwap": round(vwap, 2),
                    "turnover": round(turnover, 2),
                    "avg_volume": round(avg_vol, 2),
                    "volume_ratio": round(vol_ratio, 4),
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
