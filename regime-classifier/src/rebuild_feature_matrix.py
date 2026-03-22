"""Rebuild feature matrix with all 39 features."""
import pandas as pd
from datetime import date
from pathlib import Path
from src.preopen_features import compute_preopen_features, PREOPEN_FEATURE_COLS
from src.global_data import load_sp500, load_usdinr
from src.db import _read_sql

def main():
    print("Loading global data...")
    sp500 = load_sp500()
    usdinr = load_usdinr()

    print("Getting trading dates...")
    dates_df = _read_sql("SELECT DISTINCT date FROM regime_ground_truth ORDER BY date")
    dates = [d.date() if hasattr(d, 'date') else d for d in dates_df["date"]]
    print(f"  {len(dates)} trading dates")

    # Also get nifty return and breadth for the matrix
    nifty = _read_sql("""
        SELECT date, close FROM nse_indices_daily
        WHERE index = 'Nifty 50' ORDER BY date
    """)
    nifty["date"] = pd.to_datetime(nifty["date"]).dt.date
    nifty["nifty_return"] = nifty["close"].pct_change()
    nifty_map = dict(zip(nifty["date"], nifty["nifty_return"]))

    breadth = _read_sql("""
        SELECT date,
            SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END)::float /
            NULLIF(SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) +
                   SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END), 0) as breadth_ratio
        FROM nse_cm_bhavcopy GROUP BY date
    """)
    breadth["date"] = pd.to_datetime(breadth["date"]).dt.date
    breadth_map = dict(zip(breadth["date"], breadth["breadth_ratio"]))

    rows = []
    for i, d in enumerate(dates):
        if i % 50 == 0:
            print(f"  Computing features: {i}/{len(dates)} ({d})")
        try:
            feats = compute_preopen_features(d, sp500_df=sp500, usdinr_df=usdinr)
            feats["date"] = d
            feats["nifty_return"] = nifty_map.get(d)
            feats["breadth_ratio"] = breadth_map.get(d)
            rows.append(feats)
        except Exception as e:
            print(f"  ERROR on {d}: {e}")
            continue

    df = pd.DataFrame(rows)
    out = Path(__file__).resolve().parent.parent / "data" / "preopen_feature_matrix_v2.csv"
    df.to_csv(out, index=False)
    print(f"\nSaved {len(df)} rows × {len(df.columns)} cols to {out}")

    # Show feature coverage
    print("\nFeature coverage:")
    for col in PREOPEN_FEATURE_COLS:
        if col in df.columns:
            nn = df[col].notna().sum()
            print(f"  {col:<40} {nn:>5}/{len(df)} ({nn/len(df)*100:.1f}%)")
        else:
            print(f"  {col:<40} MISSING!")

if __name__ == "__main__":
    main()
