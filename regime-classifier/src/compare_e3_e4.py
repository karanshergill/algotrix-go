"""Compare E3 vs E4 ground truth labels.

Analysis:
1. Distribution comparison: E3 vs E4 (strict/moderate/loose) class balance
2. Flipped days analysis: which days changed and why (dimension breakdown)
3. Economic separation: mean return by label for E3 vs each E4 variant
4. Dimension correlation matrix (D3–D6)

Run after backfill_e4.py has populated regime_ground_truth with E4 columns.
"""

import numpy as np
import pandas as pd
import psycopg2

DB_DSN = "host=localhost dbname=atdb user=me password=algotrix"


def load_data(conn) -> pd.DataFrame:
    """Load regime_ground_truth with all E3 + E4 columns."""
    return pd.read_sql(
        """SELECT date, nifty_return, breadth_ratio, coincident_label,
                  label_e4_strict, label_e4_moderate, label_e4_loose,
                  d3_volume_score, d4_dispersion_score,
                  d5_concentration_score, d6_sector_score,
                  d3_raw, d4_raw, d5_raw, d6_raw
           FROM regime_ground_truth
           WHERE coincident_label IS NOT NULL
           ORDER BY date""",
        conn, parse_dates=["date"],
    )


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def distribution_comparison(df: pd.DataFrame):
    """1. Distribution comparison: E3 vs E4 variants."""
    print_section("1. DISTRIBUTION COMPARISON")

    variants = {
        "E3 (coincident)": "coincident_label",
        "E4-strict (>=3)": "label_e4_strict",
        "E4-moderate (>=2)": "label_e4_moderate",
        "E4-loose (>=0)": "label_e4_loose",
    }

    total = len(df)
    labels = ["Trend-Up", "Range", "Trend-Down"]

    # Header
    print(f"\n{'Variant':<22s}", end="")
    for label in labels:
        print(f"  {label:>12s}", end="")
    print(f"  {'Total':>7s}")
    print("-" * 70)

    for name, col in variants.items():
        valid = df[col].dropna()
        print(f"{name:<22s}", end="")
        for label in labels:
            count = (valid == label).sum()
            pct = count / len(valid) * 100
            print(f"  {count:5d} ({pct:4.1f}%)", end="")
        print(f"  {len(valid):7d}")


def flipped_days_analysis(df: pd.DataFrame):
    """2. Flipped days: which days changed and why."""
    print_section("2. FLIPPED DAYS ANALYSIS")

    for variant, col in [("strict", "label_e4_strict"),
                          ("moderate", "label_e4_moderate"),
                          ("loose", "label_e4_loose")]:
        valid = df.dropna(subset=[col])
        flipped = valid[valid["coincident_label"] != valid[col]].copy()

        print(f"\n  E4-{variant}: {len(flipped)} days flipped ({len(flipped)/len(valid)*100:.1f}%)")

        if len(flipped) == 0:
            continue

        # Breakdown by direction
        flip_types = flipped.apply(
            lambda r: f"{r['coincident_label']} -> {r[col]}", axis=1
        )
        print(f"    Flip breakdown:")
        for flip, count in flip_types.value_counts().items():
            print(f"      {flip}: {count}")

        # Dimension score distribution for flipped days
        score_cols = ["d3_volume_score", "d4_dispersion_score",
                      "d5_concentration_score", "d6_sector_score"]
        print(f"    Mean dimension scores on flipped days:")
        for sc in score_cols:
            dim_name = sc.replace("_score", "").replace("d3_volume", "D3-volume").replace(
                "d4_dispersion", "D4-dispersion").replace(
                "d5_concentration", "D5-concentration").replace(
                "d6_sector", "D6-sector")
            vals = flipped[sc].dropna()
            if len(vals) > 0:
                print(f"      {dim_name}: mean={vals.mean():+.2f}  "
                      f"(+1: {(vals==1).sum()}, 0: {(vals==0).sum()}, -1: {(vals==-1).sum()})")

        # Show 10 sample flipped days
        sample = flipped.head(10)
        print(f"    Sample flipped days (first 10):")
        print(f"      {'Date':<12s} {'E3':>12s} {'E4':>12s}  D3  D4  D5  D6  Return%")
        for _, row in sample.iterrows():
            ret_pct = row["nifty_return"] * 100 if not pd.isna(row["nifty_return"]) else 0
            print(f"      {str(row['date'].date()):<12s} {row['coincident_label']:>12s} "
                  f"{row[col]:>12s}  {int(row['d3_volume_score']):+d}  "
                  f"{int(row['d4_dispersion_score']):+d}  "
                  f"{int(row['d5_concentration_score']):+d}  "
                  f"{int(row['d6_sector_score']):+d}  "
                  f"{ret_pct:+.2f}%")


def economic_separation(df: pd.DataFrame):
    """3. Economic separation: mean return by label for E3 vs each E4 variant."""
    print_section("3. ECONOMIC SEPARATION (Mean Nifty Return by Label)")

    variants = {
        "E3": "coincident_label",
        "E4-strict": "label_e4_strict",
        "E4-moderate": "label_e4_moderate",
        "E4-loose": "label_e4_loose",
    }

    labels = ["Trend-Up", "Range", "Trend-Down"]

    # Header
    print(f"\n{'Variant':<16s}", end="")
    for label in labels:
        print(f"  {label:>14s}", end="")
    print(f"  {'Spread':>10s}")
    print("-" * 74)

    for name, col in variants.items():
        valid = df.dropna(subset=[col, "nifty_return"])
        print(f"{name:<16s}", end="")
        means = {}
        for label in labels:
            subset = valid[valid[col] == label]["nifty_return"]
            mean_ret = subset.mean() * 100 if len(subset) > 0 else 0
            means[label] = mean_ret
            count = len(subset)
            print(f"  {mean_ret:+.3f}% (n={count:d})", end="")

        spread = means.get("Trend-Up", 0) - means.get("Trend-Down", 0)
        print(f"  {spread:+.3f}%")

    # Median returns
    print(f"\n{'Variant':<16s}", end="")
    for label in labels:
        print(f"  {label:>14s}", end="")
    print(f"  {'Spread':>10s}")
    print("-" * 74)
    print("(Median returns)")

    for name, col in variants.items():
        valid = df.dropna(subset=[col, "nifty_return"])
        print(f"{name:<16s}", end="")
        medians = {}
        for label in labels:
            subset = valid[valid[col] == label]["nifty_return"]
            med_ret = subset.median() * 100 if len(subset) > 0 else 0
            medians[label] = med_ret
            print(f"  {med_ret:+.3f}%       ", end="")

        spread = medians.get("Trend-Up", 0) - medians.get("Trend-Down", 0)
        print(f"  {spread:+.3f}%")


def dimension_correlation(df: pd.DataFrame):
    """4. Dimension correlation matrix (D3–D6)."""
    print_section("4. DIMENSION CORRELATION MATRIX (D3–D6 Scores)")

    score_cols = ["d3_volume_score", "d4_dispersion_score",
                  "d5_concentration_score", "d6_sector_score"]
    raw_cols = ["d3_raw", "d4_raw", "d5_raw", "d6_raw"]

    # Score correlation
    valid = df[score_cols].dropna()
    if len(valid) > 10:
        corr = valid.corr()
        short_names = ["D3-vol", "D4-disp", "D5-conc", "D6-sect"]

        print(f"\n  Score correlation (N={len(valid)}):")
        print(f"  {'':>10s}", end="")
        for name in short_names:
            print(f"  {name:>8s}", end="")
        print()

        for i, (col, name) in enumerate(zip(score_cols, short_names)):
            print(f"  {name:>10s}", end="")
            for j, col2 in enumerate(score_cols):
                val = corr.loc[col, col2]
                marker = " *" if abs(val) > 0.7 and i != j else "  "
                print(f"  {val:+.3f}{marker}", end="")
            print()

        # Flag high correlations
        high_corr = []
        for i in range(len(score_cols)):
            for j in range(i + 1, len(score_cols)):
                val = corr.iloc[i, j]
                if abs(val) > 0.7:
                    high_corr.append((short_names[i], short_names[j], val))

        if high_corr:
            print(f"\n  WARNING: High correlations (>0.7) detected:")
            for a, b, val in high_corr:
                print(f"    {a} <-> {b}: {val:+.3f}")
        else:
            print(f"\n  All pairwise correlations < 0.7 — dimensions are sufficiently independent.")

    # Raw ratio correlation
    valid_raw = df[raw_cols].dropna()
    if len(valid_raw) > 10:
        corr_raw = valid_raw.corr()
        print(f"\n  Raw ratio correlation (N={len(valid_raw)}):")
        print(f"  {'':>10s}", end="")
        for name in short_names:
            print(f"  {name:>8s}", end="")
        print()

        for i, (col, name) in enumerate(zip(raw_cols, short_names)):
            print(f"  {name:>10s}", end="")
            for j, col2 in enumerate(raw_cols):
                val = corr_raw.loc[col, col2]
                print(f"  {val:+.3f}  ", end="")
            print()


def dimension_score_distribution(df: pd.DataFrame):
    """Bonus: distribution of each dimension score."""
    print_section("5. DIMENSION SCORE DISTRIBUTION")

    score_cols = {
        "D3-volume": "d3_volume_score",
        "D4-dispersion": "d4_dispersion_score",
        "D5-concentration": "d5_concentration_score",
        "D6-sector": "d6_sector_score",
    }

    print(f"\n  {'Dimension':<20s}  {'+1':>7s}  {'0':>7s}  {'-1':>7s}  {'mean':>7s}")
    print("  " + "-" * 55)

    for name, col in score_cols.items():
        vals = df[col].dropna()
        pos = (vals == 1).sum()
        neu = (vals == 0).sum()
        neg = (vals == -1).sum()
        total = len(vals)
        mean = vals.mean()
        print(f"  {name:<20s}  {pos:4d} ({pos/total*100:4.1f}%)  "
              f"{neu:4d} ({neu/total*100:4.1f}%)  "
              f"{neg:4d} ({neg/total*100:4.1f}%)  {mean:+.3f}")


def confirm_score_distribution(df: pd.DataFrame):
    """Bonus: distribution of confirm_score (D3+D4+D5+D6)."""
    print_section("6. CONFIRM SCORE DISTRIBUTION (D3+D4+D5+D6)")

    score_cols = ["d3_volume_score", "d4_dispersion_score",
                  "d5_concentration_score", "d6_sector_score"]
    valid = df.dropna(subset=score_cols).copy()
    valid["confirm_score"] = valid[score_cols].sum(axis=1).astype(int)

    dist = valid["confirm_score"].value_counts().sort_index()
    total = len(valid)

    print(f"\n  {'Score':>7s}  {'Count':>7s}  {'Pct':>7s}  Bar")
    print("  " + "-" * 50)
    for score in range(-4, 5):
        count = dist.get(score, 0)
        pct = count / total * 100
        bar = "#" * int(pct)
        print(f"  {score:+5d}    {count:5d}    {pct:5.1f}%  {bar}")

    # By E3 label
    for e3_label in ["Trend-Up", "Trend-Down"]:
        subset = valid[valid["coincident_label"] == e3_label]
        if len(subset) == 0:
            continue
        print(f"\n  Confirm score for E3={e3_label} days (N={len(subset)}):")
        sub_dist = subset["confirm_score"].value_counts().sort_index()
        for score in range(-4, 5):
            count = sub_dist.get(score, 0)
            pct = count / len(subset) * 100
            if count > 0:
                print(f"    {score:+3d}: {count:5d} ({pct:5.1f}%)")


def main():
    conn = psycopg2.connect(DB_DSN)

    print("Loading E3/E4 ground truth data...")
    df = load_data(conn)
    conn.close()

    print(f"Loaded {len(df)} rows")

    if "label_e4_strict" not in df.columns or df["label_e4_strict"].isna().all():
        print("ERROR: E4 columns not populated. Run backfill_e4.py first.")
        return

    distribution_comparison(df)
    flipped_days_analysis(df)
    economic_separation(df)
    dimension_correlation(df)
    dimension_score_distribution(df)
    confirm_score_distribution(df)

    print(f"\n{'=' * 70}")
    print("  DONE — Review output above for E3 vs E4 comparison.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
