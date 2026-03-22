"""Unsupervised regime discovery — find natural day-type clusters.

Feeds realized-day metrics into KMeans, GMM, and DBSCAN.
Inspects clusters to see what natural market regimes exist.
"""

import numpy as np
import pandas as pd
import psycopg2
from pathlib import Path
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans, DBSCAN
from sklearn.mixture import GaussianMixture
from sklearn.metrics import silhouette_score


def get_conn():
    return psycopg2.connect(host="localhost", user="me", password="algotrix", dbname="atdb")


def load_realized_day_metrics():
    """Load all realized-day metrics for clustering."""
    conn = get_conn()

    # Nifty OHLC + return + CIR + range
    nifty = pd.read_sql("""
        SELECT date, open, high, low, close
        FROM nse_indices_daily
        WHERE index = 'Nifty 50'
        ORDER BY date
    """, conn)
    nifty["date"] = pd.to_datetime(nifty["date"]).dt.date
    nifty["prev_close"] = nifty["close"].shift(1)
    nifty["return_pct"] = (nifty["close"] / nifty["prev_close"] - 1) * 100
    nifty["day_range_pct"] = (nifty["high"] - nifty["low"]) / nifty["close"] * 100
    nifty["cir"] = np.where(
        nifty["high"] == nifty["low"], 0.5,
        (nifty["close"] - nifty["low"]) / (nifty["high"] - nifty["low"])
    )

    # Breadth
    breadth = pd.read_sql("""
        SELECT date,
            SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as advances,
            SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as declines
        FROM nse_cm_bhavcopy
        GROUP BY date ORDER BY date
    """, conn)
    breadth["date"] = pd.to_datetime(breadth["date"]).dt.date
    breadth["breadth_ratio"] = breadth["advances"] / (breadth["advances"] + breadth["declines"])

    # Volume conviction (market turnover / 20d avg)
    vol = pd.read_sql("""
        SELECT date, SUM(traded_value) as market_turnover
        FROM nse_cm_bhavcopy GROUP BY date ORDER BY date
    """, conn)
    vol["date"] = pd.to_datetime(vol["date"]).dt.date
    vol["turnover_20d_avg"] = vol["market_turnover"].rolling(20, min_periods=5).mean()
    vol["volume_ratio"] = vol["market_turnover"] / vol["turnover_20d_avg"]

    # Cross-sectional dispersion
    disp = pd.read_sql("""
        SELECT date,
            STDDEV((close - prev_close) / NULLIF(prev_close, 0)) as stock_dispersion
        FROM nse_cm_bhavcopy
        WHERE prev_close > 0
        GROUP BY date ORDER BY date
    """, conn)
    disp["date"] = pd.to_datetime(disp["date"]).dt.date
    disp["disp_20d_avg"] = disp["stock_dispersion"].rolling(20, min_periods=5).mean()
    disp["dispersion_ratio"] = disp["stock_dispersion"] / disp["disp_20d_avg"]

    # Turnover concentration (top 10 share)
    conc = pd.read_sql("""
        WITH ranked AS (
            SELECT date, traded_value,
                ROW_NUMBER() OVER (PARTITION BY date ORDER BY traded_value DESC) as rn,
                SUM(traded_value) OVER (PARTITION BY date) as total_tv
            FROM nse_cm_bhavcopy
        )
        SELECT date,
            SUM(CASE WHEN rn <= 10 THEN traded_value ELSE 0 END) / NULLIF(MAX(total_tv), 0) as top10_share
        FROM ranked
        GROUP BY date ORDER BY date
    """, conn)
    conc["date"] = pd.to_datetime(conc["date"]).dt.date
    conc["conc_20d_avg"] = conc["top10_share"].rolling(20, min_periods=5).mean()
    conc["concentration_ratio"] = conc["top10_share"] / conc["conc_20d_avg"]

    # Sector participation
    sectors = [
        "Nifty Bank", "Nifty IT", "Nifty Pharma", "Nifty Auto", "Nifty Metal", "Nifty FMCG",
        "Nifty Energy", "Nifty Realty", "Nifty Financial Services", "Nifty Infrastructure",
        "Nifty Media", "Nifty PSU Bank"
    ]
    sect_df = pd.read_sql("""
        SELECT date, index, close
        FROM nse_indices_daily
        WHERE index = ANY(%s)
        ORDER BY date, index
    """, conn, params=[sectors])
    sect_df["date"] = pd.to_datetime(sect_df["date"]).dt.date

    # Compute sector returns and participation
    sect_pivot = sect_df.pivot_table(index="date", columns="index", values="close")
    sect_returns = sect_pivot.pct_change()

    # Get nifty direction per day
    nifty_dir = nifty.set_index("date")["return_pct"]
    sect_part = pd.DataFrame(index=sect_returns.index)
    for d in sect_returns.index:
        if d in nifty_dir.index and not pd.isna(nifty_dir[d]):
            n_sign = np.sign(nifty_dir[d])
            if n_sign == 0:
                sect_part.loc[d, "sector_participation"] = 0.5
            else:
                agreeing = (np.sign(sect_returns.loc[d]) == n_sign).sum()
                total = sect_returns.loc[d].notna().sum()
                sect_part.loc[d, "sector_participation"] = agreeing / total if total > 0 else 0.5
        else:
            sect_part.loc[d, "sector_participation"] = np.nan

    sect_part = sect_part.reset_index().rename(columns={"index": "date"})

    # VIX
    vix = pd.read_sql("""
        SELECT date, close as vix_close
        FROM nse_vix_daily ORDER BY date
    """, conn)
    vix["date"] = pd.to_datetime(vix["date"]).dt.date
    vix["vix_change_pct"] = vix["vix_close"].pct_change() * 100
    vix["vix_range_pct"] = None  # we don't have H/L in vix_daily with just close

    conn.close()

    # Merge everything
    df = nifty[["date", "return_pct", "day_range_pct", "cir"]].copy()
    df = df.merge(breadth[["date", "breadth_ratio"]], on="date", how="left")
    df = df.merge(vol[["date", "volume_ratio"]], on="date", how="left")
    df = df.merge(disp[["date", "dispersion_ratio"]], on="date", how="left")
    df = df.merge(conc[["date", "concentration_ratio"]], on="date", how="left")
    df = df.merge(sect_part, on="date", how="left")
    df = df.merge(vix[["date", "vix_close", "vix_change_pct"]], on="date", how="left")

    # Also get E3 labels for comparison
    conn2 = get_conn()
    e3 = pd.read_sql("SELECT date, coincident_label FROM regime_ground_truth ORDER BY date", conn2)
    conn2.close()
    e3["date"] = pd.to_datetime(e3["date"]).dt.date
    df = df.merge(e3, on="date", how="left")

    return df


def run_clustering(df):
    """Run multiple clustering algorithms and compare."""

    feature_cols = [
        "return_pct", "day_range_pct", "cir", "breadth_ratio",
        "volume_ratio", "dispersion_ratio", "concentration_ratio",
        "sector_participation", "vix_close", "vix_change_pct"
    ]

    clean = df.dropna(subset=feature_cols).copy()
    print(f"\nClean rows for clustering: {len(clean)}")

    X_raw = clean[feature_cols].values
    scaler = StandardScaler()
    X = scaler.fit_transform(X_raw)

    # ---- KMeans with different k ----
    print("\n" + "=" * 70)
    print("  KMEANS CLUSTERING")
    print("=" * 70)

    for k in [3, 4, 5, 6, 7]:
        km = KMeans(n_clusters=k, random_state=42, n_init=20)
        labels = km.fit_predict(X)
        sil = silhouette_score(X, labels)
        print(f"\n  k={k}: silhouette={sil:.3f}")

        # Describe each cluster
        clean[f"km_{k}"] = labels
        for c in range(k):
            mask = labels == c
            n = mask.sum()
            pct = n / len(labels) * 100
            mean_ret = clean.loc[mask, "return_pct"].mean()
            mean_range = clean.loc[mask, "day_range_pct"].mean()
            mean_cir = clean.loc[mask, "cir"].mean()
            mean_breadth = clean.loc[mask, "breadth_ratio"].mean()
            mean_vol = clean.loc[mask, "volume_ratio"].mean()
            mean_disp = clean.loc[mask, "dispersion_ratio"].mean()
            mean_conc = clean.loc[mask, "concentration_ratio"].mean()
            mean_sect = clean.loc[mask, "sector_participation"].mean()
            mean_vix = clean.loc[mask, "vix_close"].mean()

            # E3 label distribution within cluster
            e3_dist = clean.loc[mask, "coincident_label"].value_counts()
            e3_str = ", ".join([f"{l}:{n}" for l, n in e3_dist.items()])

            print(f"    Cluster {c}: n={n} ({pct:.1f}%)  ret={mean_ret:+.2f}%  range={mean_range:.2f}%  "
                  f"CIR={mean_cir:.2f}  breadth={mean_breadth:.2f}  vol={mean_vol:.2f}  "
                  f"disp={mean_disp:.2f}  conc={mean_conc:.2f}  sect={mean_sect:.2f}  vix={mean_vix:.1f}")
            print(f"             E3: {e3_str}")

    # ---- GMM ----
    print("\n" + "=" * 70)
    print("  GAUSSIAN MIXTURE MODEL")
    print("=" * 70)

    for k in [3, 4, 5]:
        gmm = GaussianMixture(n_components=k, random_state=42, covariance_type="full", n_init=5)
        labels = gmm.fit_predict(X)
        bic = gmm.bic(X)
        aic = gmm.aic(X)
        sil = silhouette_score(X, labels)
        print(f"\n  k={k}: BIC={bic:.0f}  AIC={aic:.0f}  silhouette={sil:.3f}")

        clean[f"gmm_{k}"] = labels
        for c in range(k):
            mask = labels == c
            n = mask.sum()
            pct = n / len(labels) * 100
            mean_ret = clean.loc[mask, "return_pct"].mean()
            mean_range = clean.loc[mask, "day_range_pct"].mean()
            mean_cir = clean.loc[mask, "cir"].mean()
            mean_breadth = clean.loc[mask, "breadth_ratio"].mean()
            mean_vol = clean.loc[mask, "volume_ratio"].mean()
            mean_disp = clean.loc[mask, "dispersion_ratio"].mean()

            e3_dist = clean.loc[mask, "coincident_label"].value_counts()
            e3_str = ", ".join([f"{l}:{n}" for l, n in e3_dist.items()])

            print(f"    Cluster {c}: n={n} ({pct:.1f}%)  ret={mean_ret:+.2f}%  range={mean_range:.2f}%  "
                  f"CIR={mean_cir:.2f}  breadth={mean_breadth:.2f}  vol={mean_vol:.2f}  disp={mean_disp:.2f}")
            print(f"             E3: {e3_str}")

    # ---- Best k analysis (detailed) ----
    print("\n" + "=" * 70)
    print("  DETAILED ANALYSIS — BEST KMEANS (k=5)")
    print("=" * 70)

    km5 = KMeans(n_clusters=5, random_state=42, n_init=20)
    labels = km5.fit_predict(X)
    clean["best_cluster"] = labels

    for c in sorted(clean["best_cluster"].unique()):
        cluster = clean[clean["best_cluster"] == c]
        n = len(cluster)
        print(f"\n  --- Cluster {c} ({n} days, {n/len(clean)*100:.1f}%) ---")

        for col in feature_cols:
            vals = cluster[col]
            print(f"    {col:<25}  mean={vals.mean():>8.3f}  std={vals.std():>8.3f}  "
                  f"min={vals.min():>8.3f}  max={vals.max():>8.3f}")

        # E3 breakdown
        e3_dist = cluster["coincident_label"].value_counts()
        print(f"    E3 labels: {dict(e3_dist)}")

        # Return quantiles
        rets = cluster["return_pct"]
        print(f"    Return quantiles: P10={rets.quantile(0.1):+.2f}%  P25={rets.quantile(0.25):+.2f}%  "
              f"P50={rets.quantile(0.5):+.2f}%  P75={rets.quantile(0.75):+.2f}%  P90={rets.quantile(0.9):+.2f}%")

    # Export
    out = Path(__file__).resolve().parent.parent / "data" / "cluster_analysis.csv"
    clean.to_csv(out, index=False)
    print(f"\nExported to {out}")


def main():
    print("=" * 70)
    print("  UNSUPERVISED REGIME DISCOVERY")
    print("=" * 70)

    df = load_realized_day_metrics()
    print(f"Loaded {len(df)} days with {len(df.columns)} columns")
    print(f"Columns: {list(df.columns)}")
    print(f"\nDate range: {df['date'].min()} → {df['date'].max()}")
    print(f"NaN counts:")
    for c in df.columns:
        nn = df[c].isna().sum()
        if nn > 0:
            print(f"  {c}: {nn}")

    run_clustering(df)


if __name__ == "__main__":
    main()
