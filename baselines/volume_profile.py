"""Volume profile baseline computation plugin."""

import json
from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import to_utc_ns, get_isins, chunked, isin_where_clause


class VolumeProfilePlugin(BaselinePlugin):
    """Compute volume profile per (isin, trading_day).

    For each stock-day:
    1. Bucket close prices by bucket_size
    2. Sum volume per bucket -> POC = highest volume bucket
    3. Value Area: expand from POC until value_area_pct reached -> VAH, VAL
    4. HVN: buckets with volume > hvn_threshold * average
    5. LVN: buckets with volume < lvn_threshold * average
    """

    name = "volume_profile"
    description = "Price-volume distribution profile per stock per day"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "bucket_size", "value_area_pct",
                     "hvn_threshold", "lvn_threshold", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"volume_profile: missing config key '{key}'")

    def compute(self):
        pcfg = self.plugin_cfg
        source_table = self.cfg["sources"][pcfg["source"]]
        lookback_days = pcfg["lookback_days"]
        bucket_size = float(pcfg["bucket_size"])
        value_area_pct = float(pcfg["value_area_pct"])
        hvn_threshold = float(pcfg["hvn_threshold"])
        lvn_threshold = float(pcfg["lvn_threshold"])

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)

        all_isins = get_isins(self.cfg, source_table, start_date.isoformat(), end_date.isoformat())
        if not all_isins:
            return []

        results = []
        # Use small chunks (10 ISINs) since we fetch raw 5s candles
        for chunk in chunked(all_isins, size=10):
            where = isin_where_clause(chunk, start_date.isoformat(), end_date.isoformat())
            sql = (
                f"SELECT isin, timestamp_floor('d', timestamp) AS trade_date, close, volume "
                f"FROM {source_table} "
                f"WHERE {where} "
                f"ORDER BY isin, trade_date, timestamp"
            )
            rows = questdb_query(self.cfg, sql)
            if not rows:
                continue

            # Group by (isin, trade_date)
            groups = {}
            for r in rows:
                isin = r["isin"]
                td = str(r["trade_date"])[:10]
                key = (isin, td)
                if key not in groups:
                    groups[key] = ([], [])
                close = r["close"]
                vol = r["volume"]
                if close is not None and vol is not None:
                    groups[key][0].append(float(close))
                    groups[key][1].append(int(vol))

            for (isin, td), (closes, volumes) in groups.items():
                if not closes or not volumes:
                    continue

                closes_arr = np.array(closes, dtype=np.float64)
                volumes_arr = np.array(volumes, dtype=np.int64)

                total_volume = int(volumes_arr.sum())
                if total_volume == 0:
                    continue

                price_min = float(np.floor(closes_arr.min() / bucket_size) * bucket_size)
                price_max = float(np.ceil(closes_arr.max() / bucket_size) * bucket_size)
                if price_min == price_max:
                    price_max = price_min + bucket_size

                n_buckets = max(1, int(round((price_max - price_min) / bucket_size)))
                bucket_volumes = np.zeros(n_buckets, dtype=np.int64)
                bucket_prices = np.array([price_min + (i + 0.5) * bucket_size for i in range(n_buckets)])

                bucket_indices = np.clip(
                    ((closes_arr - price_min) / bucket_size).astype(np.int64),
                    0, n_buckets - 1
                )
                np.add.at(bucket_volumes, bucket_indices, volumes_arr)

                poc_idx = int(np.argmax(bucket_volumes))
                poc = float(bucket_prices[poc_idx])

                va_target = total_volume * (value_area_pct / 100.0)
                va_volume = int(bucket_volumes[poc_idx])
                lo = poc_idx
                hi = poc_idx

                while va_volume < va_target and (lo > 0 or hi < n_buckets - 1):
                    lo_vol = int(bucket_volumes[lo - 1]) if lo > 0 else -1
                    hi_vol = int(bucket_volumes[hi + 1]) if hi < n_buckets - 1 else -1
                    if lo_vol >= hi_vol:
                        lo -= 1
                        va_volume += int(bucket_volumes[lo])
                    else:
                        hi += 1
                        va_volume += int(bucket_volumes[hi])

                vah = float(bucket_prices[hi] + bucket_size / 2.0)
                val_ = float(bucket_prices[lo] - bucket_size / 2.0)

                avg_volume = float(bucket_volumes.mean())
                hvn_mask = bucket_volumes > (hvn_threshold * avg_volume)
                lvn_mask = (bucket_volumes < (lvn_threshold * avg_volume)) & (bucket_volumes > 0)
                hvn_levels = bucket_prices[hvn_mask].tolist()
                lvn_levels = bucket_prices[lvn_mask].tolist()

                price_buckets = []
                for i in range(n_buckets):
                    bv = int(bucket_volumes[i])
                    if bv > 0:
                        price_buckets.append({
                            "price": round(float(bucket_prices[i]), 2),
                            "volume": bv,
                            "pct": round(bv / total_volume * 100, 2),
                        })

                ts_ns = to_utc_ns(td)

                results.append({
                    "isin": isin,
                    "trade_date": ts_ns,
                    "poc": round(poc, 2),
                    "vah": round(vah, 2),
                    "val": round(val_, 2),
                    "total_volume": total_volume,
                    "bucket_count": n_buckets,
                    "hvn_count": int(hvn_mask.sum()),
                    "lvn_count": int(lvn_mask.sum()),
                    "price_buckets": json.dumps(price_buckets),
                    "hvn_levels": json.dumps([round(p, 2) for p in hvn_levels]),
                    "lvn_levels": json.dumps([round(p, 2) for p in lvn_levels]),
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
