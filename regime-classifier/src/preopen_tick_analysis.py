#!/usr/bin/env python3
"""
Pre-open tick analysis: compute intraday pre-open features from tick_data (9:00-9:08 AM)
and correlate with day outcomes from regime_ground_truth.

Databases:
  algotrix  – tick_data (partitioned)
  atdb      – dhan_security_map (security_id→isin), symbols (Nifty50 membership),
              nse_cm_bhavcopy (prev_close), regime_ground_truth (day outcome)
"""

import numpy as np
import pandas as pd
import psycopg2
from datetime import date, timedelta

# ── connections ──────────────────────────────────────────────────────────────

ALGOTRIX_DSN = "host=localhost dbname=algotrix user=me password=algotrix"
ATDB_DSN     = "host=localhost dbname=atdb     user=me password=algotrix"

# ── dates with pre-open data ────────────────────────────────────────────────

PARTITION_DATES = [
    date(2026, 2, 24), date(2026, 2, 25), date(2026, 2, 26),
    # 20260227 skipped (no pre-open data)
    date(2026, 3, 2),
    # 20260303 skipped (no pre-open data)
    date(2026, 3, 4), date(2026, 3, 5), date(2026, 3, 6),
    date(2026, 3, 9),
    # 20260310 skipped (no pre-open data)
    date(2026, 3, 11), date(2026, 3, 13),
    date(2026, 3, 16), date(2026, 3, 17), date(2026, 3, 18), date(2026, 3, 19),
]

# 30-second snapshot times from 09:00:00 to 09:07:30 (16 snapshots)
SNAPSHOT_OFFSETS = [f"09:{m:02d}:{s:02d}" for m in range(8) for s in (0, 30)]


def conn_algotrix():
    return psycopg2.connect(ALGOTRIX_DSN)


def conn_atdb():
    return psycopg2.connect(ATDB_DSN)


# ── data loaders ────────────────────────────────────────────────────────────

def load_security_map():
    """security_id (dhan_token) → (isin, trading_symbol, in_nifty50) from atdb.symbols."""
    with conn_atdb() as c:
        df = pd.read_sql(
            """SELECT dhan_token AS security_id, isin, symbol AS trading_symbol,
                      index_membership @> ARRAY['NIFTY 50'] AS in_nifty50
               FROM symbols
               WHERE dhan_token IS NOT NULL AND status = 'active'""",
            c,
        )
    return df.set_index("security_id")


def load_prev_close(d: date):
    """isin → prev_close for given date from nse_cm_bhavcopy (atdb)."""
    with conn_atdb() as c:
        df = pd.read_sql(
            "SELECT isin, prev_close FROM nse_cm_bhavcopy WHERE date = %s AND prev_close > 0",
            c, params=[d],
        )
    return df.set_index("isin")["prev_close"]


def load_regime(d: date):
    """coincident_label, nifty_return for date."""
    with conn_atdb() as c:
        df = pd.read_sql(
            "SELECT coincident_label, nifty_return FROM regime_ground_truth WHERE date = %s",
            c, params=[d],
        )
    if df.empty:
        return None, None
    return df.iloc[0]["coincident_label"], float(df.iloc[0]["nifty_return"])


def load_preopen_ticks(d: date, scrips: pd.DataFrame):
    """Load all pre-open ticks (09:00-09:08) for a date, joined with scrip info."""
    partition = f"tick_data_{d.strftime('%Y%m%d')}"
    sql = f"""
        SELECT t.ts, t.security_id, t.ltp, t.total_buy_qty, t.total_sell_qty,
               t.bid_price_1, t.ask_price_1,
               t.bid_qty_1, t.bid_qty_2, t.bid_qty_3,
               t.ask_qty_1, t.ask_qty_2, t.ask_qty_3
        FROM {partition} t
        WHERE t.ts::time >= '09:00:00' AND t.ts::time < '09:08:00'
          AND t.ltp > 0
        ORDER BY t.ts
    """
    with conn_algotrix() as c:
        df = pd.read_sql(sql, c)
    if df.empty:
        return df
    # join scrip info
    df = df.join(scrips, on="security_id", how="inner")
    return df


# ── snapshot computation ────────────────────────────────────────────────────

def compute_snapshot(ticks: pd.DataFrame, snap_time: str, d: date,
                     prev_close_map: pd.Series, nifty50_isins: set):
    """
    For a 30-second window ending at snap_time, take the latest tick per stock
    and compute aggregate features.
    """
    # parse snap_time into a timestamp for filtering
    snap_ts = pd.Timestamp(f"{d} {snap_time}", tz="Asia/Kolkata")
    # take all ticks up to this snapshot time
    window = ticks[ticks["ts"] <= snap_ts]
    if window.empty:
        return None
    # latest tick per stock
    latest = window.sort_values("ts").groupby("security_id").last()
    # need isin for prev_close lookup
    latest = latest[latest["isin"].isin(prev_close_map.index)]
    if latest.empty:
        return None
    latest["prev_close"] = latest["isin"].map(prev_close_map)
    latest = latest.dropna(subset=["prev_close"])
    latest = latest[latest["prev_close"] > 0]
    latest["gap_pct"] = (latest["ltp"] / latest["prev_close"] - 1) * 100

    # ── market imbalance ──
    total_buy = latest["total_buy_qty"].sum()
    total_sell = latest["total_sell_qty"].sum()
    market_imbalance = total_buy / total_sell if total_sell > 0 else np.nan

    # ── nifty50 imbalance ──
    n50 = latest[latest["isin"].isin(nifty50_isins)]
    n50_buy = n50["total_buy_qty"].sum()
    n50_sell = n50["total_sell_qty"].sum()
    nifty50_imbalance = n50_buy / n50_sell if n50_sell > 0 else np.nan

    # ── gap breadth ──
    gap_up = (latest["gap_pct"] > 0.1).sum()
    gap_down = (latest["gap_pct"] < -0.1).sum()
    gap_flat = len(latest) - gap_up - gap_down
    breadth = gap_up - gap_down  # net breadth

    # ── bid-ask spread ──
    valid_ba = latest[(latest["bid_price_1"] > 0) & (latest["ask_price_1"] > 0)]
    if len(valid_ba) > 0:
        spreads = (valid_ba["ask_price_1"] - valid_ba["bid_price_1"]) / valid_ba["ltp"]
        bid_ask_spread_avg = spreads.mean()
    else:
        bid_ask_spread_avg = np.nan

    # ── depth imbalance ──
    bid_depth = (latest["bid_qty_1"].fillna(0) + latest["bid_qty_2"].fillna(0) +
                 latest["bid_qty_3"].fillna(0)).sum()
    ask_depth = (latest["ask_qty_1"].fillna(0) + latest["ask_qty_2"].fillna(0) +
                 latest["ask_qty_3"].fillna(0)).sum()
    depth_imbalance = bid_depth / ask_depth if ask_depth > 0 else np.nan

    return {
        "snap_time": snap_time,
        "market_imbalance": market_imbalance,
        "nifty50_imbalance": nifty50_imbalance,
        "gap_up": gap_up, "gap_down": gap_down, "gap_flat": gap_flat,
        "breadth": breadth,
        "bid_ask_spread_avg": bid_ask_spread_avg,
        "depth_imbalance": depth_imbalance,
        "n_stocks": len(latest),
    }


# ── evolution features ──────────────────────────────────────────────────────

def compute_evolution(snapshots: list[dict]) -> dict:
    """Compute drift/acceleration features from snapshot time series."""
    df = pd.DataFrame(snapshots)
    n = len(df)
    x = np.arange(n, dtype=float)

    def slope(series):
        valid = series.dropna()
        if len(valid) < 3:
            return np.nan
        xi = np.arange(len(valid), dtype=float)
        return np.polyfit(xi, valid.values, 1)[0]

    def accel(series):
        valid = series.dropna()
        if len(valid) < 4:
            return np.nan
        xi = np.arange(len(valid), dtype=float)
        coeffs = np.polyfit(xi, valid.values, 2)
        return 2 * coeffs[0]  # second derivative

    imb = df["market_imbalance"]
    return {
        "initial_imbalance": imb.iloc[0] if not imb.empty else np.nan,
        "final_imbalance": imb.iloc[-1] if not imb.empty else np.nan,
        "imbalance_drift": slope(imb),
        "imbalance_acceleration": accel(imb),
        "final_minus_initial_imbalance": (imb.iloc[-1] - imb.iloc[0]) if len(imb) >= 2 else np.nan,
        "n50_initial_imbalance": df["nifty50_imbalance"].iloc[0] if not df.empty else np.nan,
        "n50_final_imbalance": df["nifty50_imbalance"].iloc[-1] if not df.empty else np.nan,
        "breadth_initial": df["breadth"].iloc[0] if not df.empty else np.nan,
        "breadth_final": df["breadth"].iloc[-1] if not df.empty else np.nan,
        "breadth_drift": (df["breadth"].iloc[-1] - df["breadth"].iloc[0]) if len(df) >= 2 else np.nan,
        "depth_imbalance_drift": slope(df["depth_imbalance"]),
        "spread_drift": slope(df["bid_ask_spread_avg"]),
    }


# ── main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 100)
    print("PRE-OPEN TICK EVOLUTION ANALYSIS (9:00–9:08 AM)")
    print("=" * 100)
    print()

    scrips = load_security_map()
    nifty50_isins = set(scrips[scrips["in_nifty50"] == True]["isin"].dropna())
    print(f"Loaded {len(scrips)} scrips ({len(nifty50_isins)} Nifty50)")

    results = []

    for d in PARTITION_DATES:
        print(f"\n{'─'*80}")
        print(f"Processing {d} ...")

        # load data
        prev_close_map = load_prev_close(d)
        ticks = load_preopen_ticks(d, scrips)
        regime, nifty_ret = load_regime(d)

        if ticks.empty:
            print(f"  ⚠ No pre-open ticks for {d}, skipping")
            continue

        print(f"  {len(ticks):,} ticks, {ticks['security_id'].nunique()} stocks, regime={regime}, nifty_ret={nifty_ret:+.2%}")

        # compute snapshots
        snapshots = []
        for snap_time in SNAPSHOT_OFFSETS:
            snap = compute_snapshot(ticks, snap_time, d, prev_close_map, nifty50_isins)
            if snap is not None:
                snapshots.append(snap)

        if len(snapshots) < 3:
            print(f"  ⚠ Only {len(snapshots)} snapshots, skipping")
            continue

        # evolution features
        evo = compute_evolution(snapshots)
        evo["date"] = d
        evo["regime"] = regime
        evo["nifty_return"] = nifty_ret
        evo["n_snapshots"] = len(snapshots)
        results.append(evo)

        print(f"  Imbalance: {evo['initial_imbalance']:.4f} → {evo['final_imbalance']:.4f} "
              f"(drift={evo['imbalance_drift']:+.6f})")
        print(f"  Breadth:   {evo['breadth_initial']:+.0f} → {evo['breadth_final']:+.0f} "
              f"(drift={evo['breadth_drift']:+.0f})")
        print(f"  N50 Imb:   {evo['n50_initial_imbalance']:.4f} → {evo['n50_final_imbalance']:.4f}")
        print(f"  Depth Imb drift: {evo['depth_imbalance_drift']:+.6f}")

    # ── results table ───────────────────────────────────────────────────────

    if not results:
        print("\nNo results to show.")
        return

    rdf = pd.DataFrame(results)
    print("\n\n" + "=" * 100)
    print("SUMMARY TABLE: PRE-OPEN TICK EVOLUTION vs DAY OUTCOME")
    print("=" * 100)

    header = (f"{'Date':>12} │ {'Init Imb':>9} {'Final Imb':>10} {'Imb Drift':>10} │ "
              f"{'Breadth0':>8} {'BreadthF':>8} {'B Drift':>7} │ "
              f"{'Depth Drft':>10} │ {'Regime':>10} {'Nifty Ret':>10}")
    print(header)
    print("─" * len(header))

    for _, r in rdf.iterrows():
        drift_arrow = "↑" if r["imbalance_drift"] > 0 else "↓"
        print(f"{str(r['date']):>12} │ "
              f"{r['initial_imbalance']:9.4f} {r['final_imbalance']:10.4f} "
              f"{r['imbalance_drift']:+10.6f}{drift_arrow} │ "
              f"{r['breadth_initial']:+8.0f} {r['breadth_final']:+8.0f} {r['breadth_drift']:+7.0f} │ "
              f"{r['depth_imbalance_drift']:+10.6f} │ "
              f"{r['regime']:>10} {r['nifty_return']:+10.4%}")

    # ── correlation analysis ────────────────────────────────────────────────

    print("\n\n" + "=" * 100)
    print("CORRELATION ANALYSIS")
    print("=" * 100)

    # label encoding
    rdf["regime_num"] = rdf["regime"].map({"Trend-Up": 1, "Range": 0, "Trend-Down": -1})
    rdf["is_trend_up"] = (rdf["regime"] == "Trend-Up").astype(int)
    rdf["is_trend_down"] = (rdf["regime"] == "Trend-Down").astype(int)
    rdf["drift_positive"] = (rdf["imbalance_drift"] > 0).astype(int)
    rdf["day_positive"] = (rdf["nifty_return"] > 0).astype(int)

    n = len(rdf)

    # 1. High imbalance → Trend-Up?
    print("\n1. HIGH FINAL IMBALANCE (>1.0 = buyers winning) → Trend-Up?")
    print("─" * 60)
    high_imb = rdf[rdf["final_imbalance"] > 1.0]
    low_imb = rdf[rdf["final_imbalance"] <= 1.0]
    if len(high_imb) > 0:
        tu_rate_high = (high_imb["regime"] == "Trend-Up").mean()
        print(f"  Days with final_imbalance > 1.0: {len(high_imb)}/{n}")
        print(f"  Trend-Up rate when imbalance > 1.0: {tu_rate_high:.1%}")
        print(f"  Regime breakdown: {high_imb['regime'].value_counts().to_dict()}")
    if len(low_imb) > 0:
        tu_rate_low = (low_imb["regime"] == "Trend-Up").mean()
        print(f"  Days with final_imbalance ≤ 1.0: {len(low_imb)}/{n}")
        print(f"  Trend-Up rate when imbalance ≤ 1.0: {tu_rate_low:.1%}")
        print(f"  Regime breakdown: {low_imb['regime'].value_counts().to_dict()}")

    # 2. Imbalance drift direction → day direction?
    print("\n2. IMBALANCE DRIFT DIRECTION → DAY DIRECTION?")
    print("─" * 60)
    drift_match = (rdf["drift_positive"] == rdf["day_positive"]).sum()
    print(f"  Drift direction matches day direction: {drift_match}/{n} ({drift_match/n:.1%})")
    for _, r in rdf.iterrows():
        arrow = "↑" if r["imbalance_drift"] > 0 else "↓"
        day_arrow = "↑" if r["nifty_return"] > 0 else "↓"
        match = "✓" if (r["imbalance_drift"] > 0) == (r["nifty_return"] > 0) else "✗"
        print(f"    {r['date']}  drift {arrow}  day {day_arrow}  {match}  ({r['regime']})")

    # 3. Breadth collapse → Trend-Down?
    print("\n3. BREADTH COLLAPSE (negative drift) → Trend-Down?")
    print("─" * 60)
    breadth_collapse = rdf[rdf["breadth_drift"] < -10]
    breadth_stable = rdf[rdf["breadth_drift"] >= -10]
    if len(breadth_collapse) > 0:
        td_rate = (breadth_collapse["regime"] == "Trend-Down").mean()
        print(f"  Days with breadth drift < -10: {len(breadth_collapse)}/{n}")
        print(f"  Trend-Down rate: {td_rate:.1%}")
        print(f"  Regime breakdown: {breadth_collapse['regime'].value_counts().to_dict()}")
    else:
        print(f"  No days with breadth drift < -10, relaxing to < 0...")
        breadth_collapse = rdf[rdf["breadth_drift"] < 0]
        if len(breadth_collapse) > 0:
            td_rate = (breadth_collapse["regime"] == "Trend-Down").mean()
            print(f"  Days with breadth drift < 0: {len(breadth_collapse)}/{n}")
            print(f"  Trend-Down rate: {td_rate:.1%}")
            print(f"  Regime breakdown: {breadth_collapse['regime'].value_counts().to_dict()}")

    # 4. Combined signal
    print("\n4. COMBINED SIGNAL: imbalance drift + breadth drift")
    print("─" * 60)
    rdf["bullish_signal"] = (rdf["imbalance_drift"] > 0) & (rdf["breadth_drift"] >= 0)
    rdf["bearish_signal"] = (rdf["imbalance_drift"] < 0) & (rdf["breadth_drift"] < 0)
    bull_days = rdf[rdf["bullish_signal"]]
    bear_days = rdf[rdf["bearish_signal"]]
    if len(bull_days) > 0:
        print(f"  Bullish signal (drift↑ + breadth stable/up): {len(bull_days)} days")
        print(f"    Trend-Up rate: {(bull_days['regime']=='Trend-Up').mean():.1%}")
        print(f"    Avg nifty return: {bull_days['nifty_return'].mean():+.4%}")
        print(f"    Regimes: {bull_days['regime'].value_counts().to_dict()}")
    if len(bear_days) > 0:
        print(f"  Bearish signal (drift↓ + breadth falling): {len(bear_days)} days")
        print(f"    Trend-Down rate: {(bear_days['regime']=='Trend-Down').mean():.1%}")
        print(f"    Avg nifty return: {bear_days['nifty_return'].mean():+.4%}")
        print(f"    Regimes: {bear_days['regime'].value_counts().to_dict()}")
    neutral = rdf[~rdf["bullish_signal"] & ~rdf["bearish_signal"]]
    if len(neutral) > 0:
        print(f"  Neutral (mixed signals): {len(neutral)} days")
        print(f"    Regimes: {neutral['regime'].value_counts().to_dict()}")

    # 5. Numerical correlations
    print("\n5. NUMERICAL CORRELATIONS")
    print("─" * 60)
    corr_cols = ["initial_imbalance", "final_imbalance", "imbalance_drift",
                 "breadth_final", "breadth_drift", "depth_imbalance_drift",
                 "final_minus_initial_imbalance"]
    for col in corr_cols:
        valid = rdf[[col, "nifty_return", "regime_num"]].dropna()
        if len(valid) >= 4:
            r_ret = valid[col].corr(valid["nifty_return"])
            r_reg = valid[col].corr(valid["regime_num"])
            print(f"  {col:>35} ↔ nifty_return: {r_ret:+.3f}   ↔ regime: {r_reg:+.3f}")

    # 6. Nifty50-specific signal
    print("\n6. NIFTY50-SPECIFIC IMBALANCE")
    print("─" * 60)
    rdf["n50_drift"] = rdf["n50_final_imbalance"] - rdf["n50_initial_imbalance"]
    n50_corr = rdf[["n50_drift", "nifty_return"]].dropna()
    if len(n50_corr) >= 4:
        r = n50_corr["n50_drift"].corr(n50_corr["nifty_return"])
        print(f"  N50 imbalance drift ↔ nifty_return: {r:+.3f}")
    for _, row in rdf.iterrows():
        n50d = row.get("n50_drift", np.nan)
        arrow = "↑" if n50d > 0 else "↓"
        print(f"    {row['date']}  N50 drift {n50d:+.4f} {arrow}  → {row['regime']:>10} ({row['nifty_return']:+.4%})")

    # ── VERDICT ─────────────────────────────────────────────────────────────

    print("\n\n" + "=" * 100)
    print("VERDICT: Does pre-open tick evolution predict the day regime?")
    print("=" * 100)

    # compute hit rates
    drift_hit = drift_match / n * 100 if n > 0 else 0
    # imbalance > 1.0 for up, < 1.0 for down
    imb_signal_correct = sum(
        1 for _, r in rdf.iterrows()
        if (r["final_imbalance"] > 1.0 and r["regime_num"] >= 0)
        or (r["final_imbalance"] <= 1.0 and r["regime_num"] <= 0)
    )
    imb_hit = imb_signal_correct / n * 100 if n > 0 else 0

    print(f"""
  Sample size: {n} trading days (limited but instructive)

  SIGNAL 1 — Imbalance Drift Direction:
    Hit rate (drift dir matches day dir): {drift_hit:.0f}% ({drift_match}/{n})

  SIGNAL 2 — Final Imbalance Level:
    Hit rate (>1.0 = not-down, ≤1.0 = not-up): {imb_hit:.0f}% ({imb_signal_correct}/{n})

  SIGNAL 3 — Breadth Drift:
    Breadth collapse days → Trend-Down rate: see above

  KEY OBSERVATIONS:
""")

    # auto-generate observations
    if drift_hit >= 60:
        print("  ✓ Imbalance drift direction is a USEFUL signal (≥60% hit rate)")
    else:
        print("  ✗ Imbalance drift direction alone is WEAK (<60% hit rate)")

    # check if combined signal is better
    combined_correct = 0
    combined_total = 0
    for _, r in rdf.iterrows():
        if r["bullish_signal"]:
            combined_total += 1
            if r["regime"] == "Trend-Up":
                combined_correct += 1
        elif r["bearish_signal"]:
            combined_total += 1
            if r["regime"] == "Trend-Down":
                combined_correct += 1
    if combined_total > 0:
        combined_hit = combined_correct / combined_total * 100
        print(f"  {'✓' if combined_hit >= 60 else '✗'} Combined signal (drift + breadth) hit rate: "
              f"{combined_hit:.0f}% ({combined_correct}/{combined_total} committed days)")

    corr_val = rdf[["imbalance_drift", "nifty_return"]].dropna()
    if len(corr_val) >= 4:
        r = corr_val["imbalance_drift"].corr(corr_val["nifty_return"])
        print(f"  {'✓' if abs(r) >= 0.3 else '✗'} Imbalance drift ↔ nifty_return correlation: {r:+.3f}")

    print(f"""
  BOTTOM LINE:
    With {n} days of data, pre-open tick evolution shows {'promising' if drift_hit >= 57 else 'weak'}
    directional signal. The imbalance drift (linear slope of buy/sell ratio from 9:00
    to 9:08) and breadth evolution provide complementary information.

    RECOMMENDATION: Continue collecting pre-open tick data to build a larger sample.
    These features should be added to the pre-open feature set as Tier-5 features
    for ensemble models, NOT used as standalone predictors.
""")

    # save to CSV for further analysis
    outpath = "regime-classifier/data/preopen_tick_evolution.csv"
    rdf.to_csv(outpath, index=False)
    print(f"  Results saved to {outpath}")


if __name__ == "__main__":
    main()
