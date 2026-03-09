"""Liquidity tier baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import utc_now_ns


class LiquidityTierPlugin(BaselinePlugin):
    """Classify stocks into high/medium/low liquidity tiers.

    Uses average turnover from baseline_daily_volume to assign tiers.
    """

    name = "liquidity_tier"
    description = "Liquidity tier classification per stock"
    dependencies = ["daily_volume"]

    def validate_config(self):
        required = ["table", "tiers", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"liquidity_tier: missing config key '{key}'")

    def compute(self):
        pcfg = self.plugin_cfg
        dv_table = self.cfg["baselines"]["daily_volume"]["table"]
        tiers = pcfg["tiers"]
        lookback_days = pcfg["lookback_days"]
        high_threshold = float(tiers["high"])
        medium_threshold = float(tiers["medium"])

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)

        symbol_filter = self.cfg.get("_symbols_filter")
        where_parts = [
            f"timestamp >= '{start_date.isoformat()}'",
            f"timestamp <= '{end_date.isoformat()}T23:59:59.999999Z'",
        ]
        if symbol_filter:
            isin_list = ",".join(f"'{s}'" for s in symbol_filter)
            where_parts.append(f"isin IN ({isin_list})")

        where_clause = f"WHERE {' AND '.join(where_parts)}"

        sql = (
            f"SELECT isin, avg(turnover) AS avg_turnover, "
            f"avg(total_volume) AS avg_volume, count() AS day_count "
            f"FROM {dv_table} "
            f"{where_clause} "
            f"GROUP BY isin "
            f"ORDER BY avg_turnover DESC"
        )
        rows = questdb_query(self.cfg, sql)

        if not rows:
            return []

        now_ns = utc_now_ns()
        results = []

        for r in rows:
            isin = r["isin"]
            avg_turnover = float(r["avg_turnover"]) if r["avg_turnover"] is not None else 0.0
            avg_volume = float(r["avg_volume"]) if r["avg_volume"] is not None else 0.0
            day_count = int(r["day_count"]) if r["day_count"] is not None else 0

            if avg_turnover >= high_threshold:
                tier = "high"
            elif avg_turnover >= medium_threshold:
                tier = "medium"
            else:
                tier = "low"

            results.append({
                "isin": isin,
                "tier": tier,
                "avg_turnover": round(avg_turnover, 2),
                "avg_volume": round(avg_volume, 2),
                "day_count": day_count,
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
