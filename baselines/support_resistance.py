"""Support & resistance levels baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import to_utc_ns, utc_now_ns, get_isins, chunked, isin_where_clause


class SupportResistancePlugin(BaselinePlugin):
    """Identify multi-day support/resistance levels per stock.

    Collects POC/VAH/VAL from baseline_volume_profile, clusters nearby
    levels, scores by appearances + recency, returns top max_levels per stock.
    """

    name = "support_resistance"
    description = "Multi-day support and resistance levels per stock"
    dependencies = ["volume_profile"]

    def validate_config(self):
        required = ["table", "cluster_pct", "max_levels",
                     "recency_weight", "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"support_resistance: missing config key '{key}'")

    def compute(self):
        pcfg = self.plugin_cfg
        vp_table = self.cfg["baselines"]["volume_profile"]["table"]
        lookback_days = pcfg["lookback_days"]
        cluster_pct = float(pcfg["cluster_pct"]) / 100.0
        max_levels = int(pcfg["max_levels"])
        recency_weight = float(pcfg["recency_weight"])

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        # Get distinct ISINs from volume_profile table, then query in chunks
        all_isins = get_isins(self.cfg, vp_table, start_str, end_str)
        if not all_isins:
            return []

        # Fetch VP data in ISIN chunks
        all_rows = []
        for chunk in chunked(all_isins):
            where = isin_where_clause(chunk, start_str, end_str)
            sql = (
                f"SELECT isin, timestamp_floor('d', timestamp) AS trade_date, "
                f"poc, vah, val "
                f"FROM {vp_table} "
                f"WHERE {where} "
                f"ORDER BY isin, trade_date"
            )
            rows = questdb_query(self.cfg, sql)
            if rows:
                all_rows.extend(rows)

        if not all_rows:
            return []

        # Group levels by isin
        isin_levels = {}
        for r in all_rows:
            isin = r["isin"]
            td = str(r["trade_date"])[:10]
            if isin not in isin_levels:
                isin_levels[isin] = []
            for field, source in [("poc", "poc"), ("vah", "vah"), ("val", "val")]:
                val = r.get(field)
                if val is not None and float(val) > 0:
                    isin_levels[isin].append((float(val), source, td))

        now_ns = utc_now_ns()

        results = []
        for isin, levels in isin_levels.items():
            if not levels:
                continue

            # Cluster nearby levels
            sorted_levels = sorted(levels, key=lambda x: x[0])
            clusters = []

            for price, source, td in sorted_levels:
                merged = False
                for cluster in clusters:
                    center = np.mean(cluster["prices"])
                    if center > 0 and abs(price - center) / center <= cluster_pct:
                        cluster["prices"].append(price)
                        cluster["sources"].append(source)
                        cluster["dates"].append(td)
                        merged = True
                        break
                if not merged:
                    clusters.append({
                        "prices": [price],
                        "sources": [source],
                        "dates": [td],
                    })

            # Score each cluster
            scored = []
            for rank, cluster in enumerate(sorted(
                clusters,
                key=lambda c: len(c["prices"]),
                reverse=True,
            )):
                center = float(np.mean(cluster["prices"]))
                appearances = len(cluster["prices"])

                recency_scores = []
                for td in cluster["dates"]:
                    dt = datetime.strptime(td, "%Y-%m-%d").date()
                    days_ago = (end_date - dt).days
                    recency_scores.append(max(0, 1.0 - days_ago / (lookback_days + 10)))

                avg_recency = float(np.mean(recency_scores)) if recency_scores else 0.0

                score = (1.0 - recency_weight) * appearances + recency_weight * avg_recency * appearances
                score = round(score * 10, 2)

                source_counts = {}
                for s in cluster["sources"]:
                    source_counts[s] = source_counts.get(s, 0) + 1
                dominant_source = max(source_counts, key=source_counts.get)

                last_date = max(cluster["dates"])
                last_ts_ns = to_utc_ns(last_date)

                scored.append({
                    "price": center,
                    "strength": score,
                    "appearances": appearances,
                    "source": dominant_source,
                    "last_tested_ns": last_ts_ns,
                    "rank": rank,
                })

            scored.sort(key=lambda x: x["strength"], reverse=True)
            top = scored[:max_levels]

            for level_rank, level in enumerate(top):
                level_type = "poc_cluster" if level["source"] == "poc" else level["source"]

                results.append({
                    "isin": isin,
                    "level_rank": level_rank,
                    "level_price": round(level["price"], 2),
                    "level_type": level_type,
                    "strength": level["strength"],
                    "appearances": level["appearances"],
                    "last_tested": level["last_tested_ns"],
                    "source": level["source"],
                    "timestamp": now_ns,
                })

        return results

    def store(self, results):
        if not results:
            return 0
        table = self.plugin_cfg["table"]
        return questdb_write_ilp(
            self.cfg, table, results,
            symbols=["isin", "level_type"],
            timestamps=["timestamp"],
        )
