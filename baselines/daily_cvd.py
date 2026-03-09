"""Daily CVD (Cumulative Volume Delta) baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import to_utc_ns, get_isins, chunked, isin_where_clause


class DailyCVDPlugin(BaselinePlugin):
    """Approximate CVD per stock per day using tick rule on 5s candles.

    For each 5s candle:
      close > open -> buy volume
      close < open -> sell volume
      close == open -> split 50/50
    Track cumulative delta and multi-day trend.
    """

    name = "daily_cvd"
    description = "Approximate cumulative volume delta per stock per day"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "trend_lookback_days", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"daily_cvd: missing config key '{key}'")

    def compute(self):
        pcfg = self.plugin_cfg
        source_table = self.cfg["sources"][pcfg["source"]]
        lookback_days = pcfg["lookback_days"]
        trend_lookback = int(pcfg["trend_lookback_days"])

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        all_isins = get_isins(self.cfg, source_table, start_str, end_str)
        if not all_isins:
            return []

        # Process in chunks, accumulate groups
        groups = {}  # (isin, td) -> {buy, sell, total}

        # Use small chunks (10 ISINs) since we fetch raw 5s candles
        for chunk in chunked(all_isins, size=10):
            where = isin_where_clause(chunk, start_str, end_str)
            sql = (
                f"SELECT isin, timestamp_floor('d', timestamp) AS trade_date, "
                f"open, close, volume "
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
                key = (isin, td)

                o = r["open"]
                c = r["close"]
                v = r["volume"]
                if o is None or c is None or v is None:
                    continue

                o = float(o)
                c = float(c)
                v = int(v)

                if key not in groups:
                    groups[key] = {"buy": 0, "sell": 0, "total": 0}

                g = groups[key]
                g["total"] += v

                if c > o:
                    g["buy"] += v
                elif c < o:
                    g["sell"] += v
                else:
                    half = v // 2
                    g["buy"] += half
                    g["sell"] += v - half

        # Collect per isin for trend computation
        isin_days = {}
        for (isin, td), g in groups.items():
            net = g["buy"] - g["sell"]
            if isin not in isin_days:
                isin_days[isin] = []
            isin_days[isin].append((td, net, g))

        for isin in isin_days:
            isin_days[isin].sort(key=lambda x: x[0])

        results = []
        for isin, day_list in isin_days.items():
            # Cumulative within the lookback window only (resets each run)
            running_cvd = 0
            for i, (td, net_delta, g) in enumerate(day_list):
                running_cvd += net_delta

                trend = "neutral"
                if i >= trend_lookback - 1:
                    recent_deltas = [d[1] for d in day_list[i - trend_lookback + 1:i + 1]]
                    positive_days = sum(1 for d in recent_deltas if d > 0)
                    negative_days = sum(1 for d in recent_deltas if d < 0)
                    if positive_days >= trend_lookback * 0.7:
                        trend = "accumulating"
                    elif negative_days >= trend_lookback * 0.7:
                        trend = "distributing"

                ts_ns = to_utc_ns(td)

                results.append({
                    "isin": isin,
                    "trade_date": ts_ns,
                    "total_volume": g["total"],
                    "buy_volume": g["buy"],
                    "sell_volume": g["sell"],
                    "net_delta": net_delta,
                    "cvd_end_of_day": running_cvd,
                    "cvd_trend": trend,
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
