#!/usr/bin/env python3
"""Data health check for trading data pipeline."""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta

import psycopg2
import yaml


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)["data_health"]


def get_nse_holidays(cfg):
    """Return set of date objects for NSE holidays from config."""
    holidays = set()
    for h in cfg.get("nse_holidays", []):
        holidays.add(datetime.strptime(str(h), "%Y-%m-%d").date())
    return holidays


def is_trading_day(d, nse_holidays):
    """Check if a date is a trading day (weekday and not an NSE holiday)."""
    return d.weekday() < 5 and d not in nse_holidays


def get_previous_trading_day(cfg):
    """Get the most recent completed trading day before today."""
    nse_holidays = get_nse_holidays(cfg)
    d = date.today() - timedelta(days=1)
    while not is_trading_day(d, nse_holidays):
        d -= timedelta(days=1)
    return d


def questdb_query(cfg, sql):
    """Query QuestDB via HTTP API, return list of dicts."""
    host = cfg["questdb"]["host"]
    port = cfg["questdb"]["http_port"]
    url = f"http://{host}:{port}/exec?query={urllib.parse.quote(sql)}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    if "error" in data:
        raise RuntimeError(f"QuestDB error: {data['error']}")
    columns = [c["name"] for c in data["columns"]]
    return [dict(zip(columns, row)) for row in data.get("dataset", [])]


def pg_query(cfg, sql):
    """Query PostgreSQL, return list of dicts."""
    pc = cfg["postgres"]
    conn = psycopg2.connect(
        host=pc["host"], port=pc["port"],
        user=pc["user"], password=pc["password"],
        database=pc["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_reference_isins(cfg):
    """Get set of active ISINs from scrip_master."""
    table = cfg["reference_table"]
    col = cfg["reference_isin_column"]
    rows = pg_query(cfg, f'SELECT DISTINCT "{col}" FROM {table}')
    return {r[col] for r in rows}


def check_date_range(cfg, source):
    """Check earliest and latest timestamps against previous trading day."""
    table = source["name"]
    rows = questdb_query(cfg, f"SELECT min(timestamp) AS earliest, max(timestamp) AS latest FROM {table}")
    if not rows or rows[0]["earliest"] is None:
        return {"earliest": None, "latest": None, "previous_trading_day": None, "stale": True}
    earliest = rows[0]["earliest"]
    latest = rows[0]["latest"]
    # QuestDB HTTP returns timestamps as strings like "2024-01-01T00:00:00.000000Z"
    latest_dt = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
    latest_date = latest_dt.date()
    prev_trading_day = get_previous_trading_day(cfg)
    nse_holidays = get_nse_holidays(cfg)

    # Count missing trading days between latest_date and prev_trading_day
    missing_trading_days = 0
    if latest_date < prev_trading_day:
        d = latest_date + timedelta(days=1)
        while d <= prev_trading_day:
            if is_trading_day(d, nse_holidays):
                missing_trading_days += 1
            d += timedelta(days=1)

    stale = latest_date < prev_trading_day
    return {
        "earliest": str(earliest),
        "latest": str(latest),
        "latest_date": latest_date.isoformat(),
        "previous_trading_day": prev_trading_day.isoformat(),
        "missing_trading_days": missing_trading_days,
        "stale": stale,
    }


def check_coverage(cfg, source, ref_isins):
    """Check stock coverage vs scrip_master."""
    table = source["name"]
    rows = questdb_query(cfg, f"SELECT DISTINCT isin FROM {table}")
    table_isins = {r["isin"] for r in rows}
    missing = sorted(ref_isins - table_isins)
    coverage_pct = (len(table_isins & ref_isins) / len(ref_isins) * 100) if ref_isins else 0
    min_cov = cfg["min_coverage_pct"]
    return {
        "unique_isins": len(table_isins),
        "reference_isins": len(ref_isins),
        "matched_isins": len(table_isins & ref_isins),
        "coverage_pct": round(coverage_pct, 2),
        "below_threshold": coverage_pct < min_cov,
        "missing_isins": missing,
    }


def check_trading_day_completeness(cfg, source):
    """Check expected vs actual trading days (excluding weekends and NSE holidays)."""
    table = source["name"]
    nse_holidays = get_nse_holidays(cfg)
    rows = questdb_query(cfg, (
        f"SELECT DISTINCT cast(timestamp AS DATE) AS day FROM {table} ORDER BY day"
    ))
    if not rows:
        return {"expected_days": 0, "actual_days": 0, "missing_days": [], "completeness_pct": 0}
    days = []
    for r in rows:
        d = str(r["day"])[:10]
        days.append(datetime.strptime(d, "%Y-%m-%d").date())
    actual_set = set(days)
    first, last = min(days), max(days)
    expected = set()
    cur = first
    while cur <= last:
        if is_trading_day(cur, nse_holidays):
            expected.add(cur)
        cur += timedelta(days=1)
    missing = sorted(expected - actual_set)
    completeness = (len(actual_set & expected) / len(expected) * 100) if expected else 0
    return {
        "expected_days": len(expected),
        "actual_days": len(actual_set),
        "missing_days": [d.isoformat() for d in missing[:50]],
        "missing_days_count": len(missing),
        "completeness_pct": round(completeness, 2),
    }


def check_per_stock_gaps(cfg, source):
    """Find stocks with missing trading days (>20% missing flagged)."""
    table = source["name"]
    nse_holidays = get_nse_holidays(cfg)
    rows = questdb_query(cfg, (
        f"SELECT isin, count(DISTINCT timestamp_floor('d', timestamp)) AS day_count "
        f"FROM {table} GROUP BY isin"
    ))
    if not rows:
        return {"flagged_stocks": [], "total_checked": 0}
    # Get total trading day span
    range_rows = questdb_query(cfg, (
        f"SELECT min(cast(timestamp AS DATE)) AS first_day, max(cast(timestamp AS DATE)) AS last_day FROM {table}"
    ))
    first_str = str(range_rows[0]["first_day"])[:10]
    last_str = str(range_rows[0]["last_day"])[:10]
    first = datetime.strptime(first_str, "%Y-%m-%d").date()
    last = datetime.strptime(last_str, "%Y-%m-%d").date()
    expected = 0
    cur = first
    while cur <= last:
        if is_trading_day(cur, nse_holidays):
            expected += 1
        cur += timedelta(days=1)
    if expected == 0:
        return {"flagged_stocks": [], "total_checked": len(rows)}
    flagged = []
    for r in rows:
        actual = int(r["day_count"])
        missing_pct = ((expected - actual) / expected) * 100
        if missing_pct > 20:
            flagged.append({
                "isin": r["isin"],
                "actual_days": actual,
                "expected_days": expected,
                "missing_pct": round(missing_pct, 2),
            })
    flagged.sort(key=lambda x: x["missing_pct"], reverse=True)
    return {
        "flagged_stocks": flagged[:50],
        "flagged_count": len(flagged),
        "total_checked": len(rows),
    }


def check_row_counts(cfg, source):
    """Row count sanity: total, avg per stock per day, outliers."""
    table = source["name"]
    total_rows = questdb_query(cfg, f"SELECT count() AS cnt FROM {table}")
    total = int(total_rows[0]["cnt"]) if total_rows else 0

    per_stock_day = questdb_query(cfg, (
        f"SELECT isin, cast(timestamp AS DATE) AS day, count() AS cnt "
        f"FROM {table} GROUP BY isin, day ORDER BY cnt DESC LIMIT 20"
    ))
    avg_rows = questdb_query(cfg, (
        f"SELECT avg(cnt) AS avg_cnt, min(cnt) AS min_cnt, max(cnt) AS max_cnt FROM "
        f"(SELECT isin, cast(timestamp AS DATE) AS day, count() AS cnt FROM {table} GROUP BY isin, day)"
    ))
    stats = avg_rows[0] if avg_rows else {}
    return {
        "total_rows": total,
        "avg_per_stock_day": round(float(stats.get("avg_cnt", 0)), 2),
        "min_per_stock_day": int(stats.get("min_cnt", 0)),
        "max_per_stock_day": int(stats.get("max_cnt", 0)),
        "top_outliers": [
            {"isin": r["isin"], "day": str(r["day"])[:10], "count": int(r["cnt"])}
            for r in per_stock_day[:10]
        ],
    }


def check_data_quality(cfg, source):
    """Check zero-volume, null/zero prices, duplicate timestamps."""
    table = source["name"]

    zero_vol = questdb_query(cfg, f"SELECT count() AS cnt FROM {table} WHERE volume = 0")
    zero_vol_count = int(zero_vol[0]["cnt"]) if zero_vol else 0

    bad_price = questdb_query(cfg, (
        f"SELECT count() AS cnt FROM {table} WHERE "
        f"open = 0 OR high = 0 OR low = 0 OR close = 0 OR "
        f"open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL"
    ))
    bad_price_count = int(bad_price[0]["cnt"]) if bad_price else 0

    dupes = questdb_query(cfg, (
        f"SELECT isin, timestamp, cnt FROM "
        f"(SELECT isin, timestamp, count() AS cnt "
        f"FROM {table} GROUP BY isin, timestamp) "
        f"WHERE cnt > 1 ORDER BY cnt DESC LIMIT 20"
    ))
    total_rows = questdb_query(cfg, f"SELECT count() AS cnt FROM {table}")
    total = int(total_rows[0]["cnt"]) if total_rows else 1

    return {
        "zero_volume_candles": zero_vol_count,
        "zero_volume_pct": round(zero_vol_count / max(total, 1) * 100, 4),
        "null_or_zero_prices": bad_price_count,
        "null_or_zero_prices_pct": round(bad_price_count / max(total, 1) * 100, 4),
        "duplicate_timestamps": len(dupes),
        "duplicate_samples": [
            {"isin": r["isin"], "timestamp": str(r["timestamp"]), "count": int(r["cnt"])}
            for r in dupes[:10]
        ],
    }


def check_cross_resolution_consistency(cfg, ref_isins):
    """Check ISIN and date consistency across all source tables."""
    sources = cfg["sources"]
    # Get distinct ISINs per source
    source_isins = {}
    for source in sources:
        table = source["name"]
        res = source["resolution"]
        rows = questdb_query(cfg, f"SELECT DISTINCT isin FROM {table}")
        source_isins[res] = {r["isin"] for r in rows}

    all_source_isins = set()
    for s in source_isins.values():
        all_source_isins |= s

    # Classify ISINs
    universal_missing = sorted(ref_isins - all_source_isins)
    in_all = set.intersection(*source_isins.values()) if source_isins else set()
    in_any = all_source_isins & ref_isins

    # Partial missing: in some tables but not all
    partial_missing = {}
    for source in sources:
        res = source["resolution"]
        table = source["name"]
        missing_from = sorted((in_any | in_all) - source_isins[res] - set(universal_missing))
        if missing_from:
            partial_missing[f"missing_from_{res}"] = missing_from
    total_partial = set()
    for v in partial_missing.values():
        total_partial |= set(v)

    # Build combo breakdown for display
    combo_counts = {}
    res_list = [s["resolution"] for s in sources]
    for isin in total_partial:
        present_in = tuple(r for r in res_list if isin in source_isins[r])
        missing_from = tuple(r for r in res_list if isin not in source_isins[r])
        key = f"In {'+'.join(present_in)} but not {'+'.join(missing_from)}"
        combo_counts[key] = combo_counts.get(key, 0) + 1

    # Date gaps: stocks in all tables but with mismatched day counts
    # Compare only resolutions with the SAME max history limit (apples to apples)
    # e.g. 5s (30-day limit) should not be compared against 1d (years of data)
    res_limits = cfg.get("resolution_limits", {})
    date_gaps = []
    date_gap_notes = []
    compared_pairs = []
    if in_all:
        # Group resolutions by comparable history depth
        # Find the actual date range per source
        source_ranges = {}  # res -> (first_day, last_day)
        for source in sources:
            table = source["name"]
            res = source["resolution"]
            range_rows = questdb_query(cfg, (
                f"SELECT min(cast(timestamp AS DATE)) AS first_day, "
                f"max(cast(timestamp AS DATE)) AS last_day FROM {table}"
            ))
            if range_rows:
                fs = datetime.strptime(str(range_rows[0]["first_day"])[:10], "%Y-%m-%d").date()
                ls = datetime.strptime(str(range_rows[0]["last_day"])[:10], "%Y-%m-%d").date()
                source_ranges[res] = (fs, ls)

        # For date gap comparison, group by similar max_history
        # Compare each pair of resolutions only within their shared date range
        # But skip pairs where the limits differ drastically (>2x)
        compared_pairs = []
        for i, s1 in enumerate(sources):
            for s2 in sources[i+1:]:
                r1, r2 = s1["resolution"], s2["resolution"]
                l1 = res_limits.get(r1, {}).get("max_history_trading_days", 9999)
                l2 = res_limits.get(r2, {}).get("max_history_trading_days", 9999)
                # Only compare if limits are within 2x of each other
                if max(l1, l2) > 2 * min(l1, l2):
                    date_gap_notes.append(f"Skipping {r1} vs {r2} date gap check (different history limits: {l1} vs {l2} days)")
                    continue
                compared_pairs.append((s1, s2))

        # For comparable pairs, find per-ISIN day counts within overlap
        if compared_pairs:
            # Get the overlap range across comparable resolutions
            comparable_res = set()
            for s1, s2 in compared_pairs:
                comparable_res.add(s1["resolution"])
                comparable_res.add(s2["resolution"])

            overlap_start = None
            overlap_end = None
            for res in comparable_res:
                if res in source_ranges:
                    fs, ls = source_ranges[res]
                    overlap_start = max(overlap_start, fs) if overlap_start else fs
                    overlap_end = min(overlap_end, ls) if overlap_end else ls

            if overlap_start and overlap_end and overlap_start <= overlap_end:
                isin_days = {}  # res -> {isin: day_count}
                for source in sources:
                    res = source["resolution"]
                    if res not in comparable_res:
                        continue
                    table = source["name"]
                    rows = questdb_query(cfg, (
                        f"SELECT isin, count(DISTINCT timestamp_floor('d', timestamp)) AS day_count "
                        f"FROM {table} "
                        f"WHERE timestamp >= '{overlap_start.isoformat()}' "
                        f"AND timestamp <= '{overlap_end.isoformat()}T23:59:59.999999Z' "
                        f"GROUP BY isin"
                    ))
                    isin_days[res] = {r["isin"]: int(r["day_count"]) for r in rows}

                comparable_list = sorted(comparable_res)
                for isin in sorted(in_all):
                    counts = {}
                    for res in comparable_list:
                        counts[res] = isin_days.get(res, {}).get(isin, 0)
                    values = list(counts.values())
                    if values and max(values) != min(values):
                        entry = {"isin": isin}
                        for res in comparable_list:
                            entry[f"{res}_days"] = counts[res]
                        date_gaps.append(entry)

    # Print results
    print(f"\n{'='*60}")
    print(f"  Cross-resolution consistency")
    print(f"{'='*60}")
    print(f"\n[7/7] Cross-resolution consistency...")
    print(f"  Stocks in all sources:          {len(in_all):,}")
    print(f"  Universal missing (not in any): {len(universal_missing)} (Fyers availability)")
    print(f"  Partial missing:                {len(total_partial)}")
    for desc, count in sorted(combo_counts.items()):
        print(f"    {desc}: {count:>6}")
    for note in date_gap_notes:
        print(f"  ℹ {note}")
    comparable_list = sorted(set(r for s1, s2 in compared_pairs for r in [s1["resolution"], s2["resolution"]])) if compared_pairs else []
    print(f"  Date gaps (comparable resolutions only): {len(date_gaps)} stocks")
    for entry in date_gaps[:15]:
        parts = []
        max_days = 0
        for res in comparable_list:
            key = f"{res}_days"
            if key in entry:
                d = entry[key]
                parts.append(f"{res}={d}")
                max_days = max(max_days, d)
        detail_parts = []
        for res in comparable_list:
            key = f"{res}_days"
            if key in entry:
                d = entry[key]
                if d < max_days:
                    detail_parts.append(f"{max_days - d} days missing in {res}")
        detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
        print(f"    {entry['isin']}: {', '.join(parts)}{detail}")
    if len(date_gaps) > 15:
        print(f"    ... and {len(date_gaps) - 15} more")

    # Save missing stocks JSON
    today = datetime.now().strftime("%Y-%m-%d")
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(report_dir, exist_ok=True)
    missing_report = {
        "universal_missing": universal_missing,
        "partial_missing": partial_missing,
        "date_gaps": date_gaps,
    }
    missing_path = os.path.join(report_dir, f"missing_stocks_{today}.json")
    with open(missing_path, "w") as f:
        json.dump(missing_report, f, indent=2, default=str)
    print(f"\n  Missing stocks report: {missing_path}")

    return {
        "stocks_in_all_sources": len(in_all),
        "universal_missing_count": len(universal_missing),
        "universal_missing": universal_missing,
        "partial_missing_count": len(total_partial),
        "partial_missing": partial_missing,
        "combo_counts": combo_counts,
        "date_gaps_count": len(date_gaps),
        "date_gaps": date_gaps,
        "missing_stocks_report": missing_path,
    }


def run_health_check(cfg):
    """Run all checks for all sources."""
    ref_isins = get_reference_isins(cfg)
    prev_trading_day = get_previous_trading_day(cfg)
    report = {
        "generated_at": datetime.now().isoformat(),
        "previous_trading_day": prev_trading_day.isoformat(),
        "reference_isins_count": len(ref_isins),
        "sources": {},
    }

    for source in cfg["sources"]:
        name = source["name"]
        print(f"\n{'='*60}")
        print(f"  Source: {name} (resolution: {source['resolution']})")
        print(f"{'='*60}")

        src_report = {}

        # Date range & staleness
        print("\n[1/6] Date range & staleness...")
        dr = check_date_range(cfg, source)
        src_report["date_range"] = dr
        print(f"  Previous trading day: {dr['previous_trading_day']}")
        print(f"  Latest data: {dr.get('latest_date', dr['latest'])}")
        if dr["stale"]:
            print(f"  Status: ⚠️  STALE (missing {dr['missing_trading_days']} trading day(s))")
        else:
            print(f"  Status: ✅ Current")

        # Coverage
        print("\n[2/6] Stock coverage...")
        cov = check_coverage(cfg, source, ref_isins)
        src_report["coverage"] = cov
        status = "LOW" if cov["below_threshold"] else "OK"
        print(f"  Matched: {cov['matched_isins']}/{cov['reference_isins']} ({cov['coverage_pct']}%) [{status}]")
        print(f"  Unique ISINs in table: {cov['unique_isins']}")
        if cov["missing_isins"]:
            preview = cov["missing_isins"][:10]
            print(f"  Missing (first 10): {', '.join(preview)}")

        # Trading day completeness
        print("\n[3/6] Trading day completeness...")
        td = check_trading_day_completeness(cfg, source)
        src_report["trading_days"] = td
        print(f"  Expected: {td['expected_days']}  Actual: {td['actual_days']}  "
              f"Completeness: {td['completeness_pct']}%")
        if td.get("missing_days_count", 0) > 0:
            print(f"  Missing days: {td['missing_days_count']}")

        # Per-stock gaps
        print("\n[4/6] Per-stock gaps (>20% missing)...")
        gaps = check_per_stock_gaps(cfg, source)
        src_report["per_stock_gaps"] = gaps
        print(f"  Flagged: {gaps.get('flagged_count', 0)}/{gaps['total_checked']} stocks")
        for s in gaps["flagged_stocks"][:5]:
            print(f"    {s['isin']}: {s['actual_days']}/{s['expected_days']} days "
                  f"({s['missing_pct']}% missing)")

        # Row counts
        print("\n[5/6] Row count sanity...")
        rc = check_row_counts(cfg, source)
        src_report["row_counts"] = rc
        print(f"  Total rows: {rc['total_rows']:,}")
        print(f"  Per stock/day — avg: {rc['avg_per_stock_day']}, "
              f"min: {rc['min_per_stock_day']}, max: {rc['max_per_stock_day']}")

        # Data quality
        print("\n[6/6] Data quality...")
        dq = check_data_quality(cfg, source)
        src_report["data_quality"] = dq
        print(f"  Zero-volume candles: {dq['zero_volume_candles']:,} ({dq['zero_volume_pct']}%)")
        print(f"  Null/zero prices:    {dq['null_or_zero_prices']:,} ({dq['null_or_zero_prices_pct']}%)")
        print(f"  Duplicate timestamps: {dq['duplicate_timestamps']}")

        report["sources"][name] = src_report

    # Cross-resolution consistency (runs after all sources)
    cross_res = check_cross_resolution_consistency(cfg, ref_isins)
    report["cross_resolution"] = cross_res

    return report


def main():
    parser = argparse.ArgumentParser(description="Data health check for trading pipeline")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)

    print("=" * 60)
    print("  DATA HEALTH CHECK")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    report = run_health_check(cfg)

    # Save JSON report
    today = datetime.now().strftime("%Y-%m-%d")
    report_dir = os.path.join(os.path.dirname(args.config), "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"data_health_{today}.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n{'='*60}")
    print(f"  Report saved to: {report_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
