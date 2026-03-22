"""CLI entry point for regime classifier.

Usage:
    python cli.py classify --date 2026-03-19
    python cli.py classify --from 2025-09-01 --to 2026-03-19
    python cli.py validate --from 2025-09-01 --to 2026-03-19
    python cli.py compare --from 2025-09-01 --to 2026-03-19
"""

import logging
import sys
import uuid
from datetime import date, datetime, timedelta

import click
import numpy as np
import pandas as pd

from src.classifier import classify_euclidean
from src.config import CLASSIFIER_VERSION, FEATURE_VERSION
from src.db import (
    fetch_recent_regimes,
    fetch_regime_range,
    transaction,
    upsert_features,
    upsert_regime,
)
from src.features import DataNotAvailableError, compute_features
from src.gmm_classifier import classify_gmm
from src.hmm_classifier import classify_hmm
from src.scorer import compute_dimension_scores
from src.smoother import apply_smoothing
from src.validate import print_validation_report, run_validation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("regime-classifier")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def classify_single_date(
    target_date: date,
    run_id: uuid.UUID,
    score_history: np.ndarray | None = None,
) -> dict | None:
    """Run full classification pipeline for a single date.

    Returns regime dict on success, None if skipped.
    """
    logger.info("--- Classifying %s (run_id=%s) ---", target_date, run_id)

    # Step 1: Compute features
    try:
        features = compute_features(target_date)
    except DataNotAvailableError as e:
        logger.warning("SKIP %s: %s", target_date, e)
        return None

    # Step 2: Run Euclidean classifier (production)
    euclidean_result = classify_euclidean(features)

    # Step 3: Run HMM classifier (shadow)
    hmm_result = classify_hmm(features, score_history=score_history)

    # Step 4: Run GMM classifier (shadow)
    gmm_result = classify_gmm(features, score_history=score_history)

    # Step 5: Apply smoothing / transition policy
    recent = fetch_recent_regimes(target_date, n=20)
    smoothing = apply_smoothing(
        raw_label=euclidean_result["label"],
        raw_scores=np.array(euclidean_result["dimension_scores"]),
        euclidean_confidence=euclidean_result["confidence"],
        euclidean_label=euclidean_result["label"],
        hmm_label=hmm_result.get("label"),
        gmm_label=gmm_result.get("label"),
        recent_regimes=recent,
    )

    # Step 6: Build regime row
    regime = {
        "euclidean_label": euclidean_result["label"],
        "euclidean_confidence": euclidean_result["confidence"],
        "euclidean_distances": euclidean_result["distances"],
        "hmm_label": hmm_result.get("label"),
        "hmm_confidence": hmm_result.get("confidence"),
        "hmm_state": hmm_result.get("state"),
        "gmm_label": gmm_result.get("label"),
        "gmm_confidence": gmm_result.get("confidence"),
        "gmm_cluster": gmm_result.get("cluster"),
        "raw_label": euclidean_result["label"],
        "final_label": smoothing["final_label"],
        "final_confidence": smoothing["final_confidence"],
        "dimension_scores": euclidean_result["dimension_scores"],
        "features_snapshot": {
            k: v for k, v in features.items() if not k.startswith("_")
        },
        "smoothed": smoothing["smoothed"],
        "smoothing_reason": smoothing.get("smoothing_reason"),
    }

    # Step 7: Atomic write — features + regime in one transaction
    # Remove meta keys from features before upserting
    db_features = {k: v for k, v in features.items() if not k.startswith("_")}

    with transaction() as conn:
        upsert_features(conn, target_date, db_features, run_id)
        upsert_regime(conn, target_date, regime, run_id)

    logger.info(
        "DONE %s: %s (raw=%s, conf=%.2f, smoothed=%s)",
        target_date,
        regime["final_label"],
        regime["raw_label"],
        regime["final_confidence"],
        regime["smoothed"],
    )

    return regime


@click.group()
def main():
    """AlgoTrix Regime Classifier — market regime detection for watchlist building."""
    pass


@main.command()
@click.option("--date", "single_date", type=str, help="Classify a single date (YYYY-MM-DD)")
@click.option("--from", "from_date", type=str, help="Backfill start date (YYYY-MM-DD)")
@click.option("--to", "to_date", type=str, help="Backfill end date (YYYY-MM-DD)")
def classify(single_date: str | None, from_date: str | None, to_date: str | None):
    """Classify market regime for one or more dates."""
    run_id = uuid.uuid4()
    logger.info("Run ID: %s | Feature: %s | Classifier: %s", run_id, FEATURE_VERSION, CLASSIFIER_VERSION)

    if single_date:
        result = classify_single_date(_parse_date(single_date), run_id)
        if result:
            _print_result(single_date, result)
        sys.exit(0 if result else 1)

    if from_date and to_date:
        start = _parse_date(from_date)
        end = _parse_date(to_date)
        logger.info("Backfill: %s to %s", start, end)

        classified = 0
        skipped = 0
        score_history = []
        current = start

        while current <= end:
            result = classify_single_date(current, run_id, score_history=np.array(score_history) if len(score_history) >= 20 else None)
            if result:
                classified += 1
                score_history.append(result["dimension_scores"])
            else:
                skipped += 1
            current += timedelta(days=1)

        logger.info("Backfill complete: %d classified, %d skipped", classified, skipped)
        sys.exit(0)

    click.echo("Error: provide --date or --from/--to", err=True)
    sys.exit(1)


@main.command()
@click.option("--from", "from_date", type=str, required=True)
@click.option("--to", "to_date", type=str, required=True)
def validate(from_date: str, to_date: str):
    """Run validation diagnostics on classified date range."""
    results = run_validation(_parse_date(from_date), _parse_date(to_date))
    print_validation_report(results)


@main.command()
@click.option("--from", "from_date", type=str, required=True)
@click.option("--to", "to_date", type=str, required=True)
def compare(from_date: str, to_date: str):
    """Compare Euclidean vs HMM vs GMM classifier outputs."""
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    regimes = fetch_regime_range(start, end)

    if regimes.empty:
        click.echo("No regime data found for this range.")
        sys.exit(1)

    click.echo(f"\nClassifier Comparison: {start} to {end} ({len(regimes)} days)\n")

    for classifier in ["euclidean", "hmm", "gmm"]:
        col = f"{classifier}_label"
        if col not in regimes.columns:
            continue
        dist = regimes[col].value_counts()
        click.echo(f"--- {classifier.upper()} ---")
        for label, count in dist.items():
            pct = count / len(regimes) * 100
            click.echo(f"  {label:20s} {count:4d} ({pct:5.1f}%)")
        click.echo()

    # Agreement analysis
    click.echo("--- Agreement ---")
    agree_count = 0
    for _, row in regimes.iterrows():
        labels = [row.get("euclidean_label"), row.get("hmm_label"), row.get("gmm_label")]
        labels = [l for l in labels if l is not None]
        if len(set(labels)) == 1 and len(labels) > 1:
            agree_count += 1
    click.echo(f"  All classifiers agree: {agree_count}/{len(regimes)} ({agree_count/len(regimes)*100:.1f}%)")


def _print_result(date_str: str, result: dict):
    """Print a single classification result."""
    click.echo(f"\n{'='*50}")
    click.echo(f"Date:       {date_str}")
    click.echo(f"Regime:     {result['final_label']}")
    click.echo(f"Confidence: {result['final_confidence']:.2f}")
    click.echo(f"Raw label:  {result['raw_label']}")
    click.echo(f"Smoothed:   {result['smoothed']}")
    if result.get("smoothing_reason"):
        click.echo(f"Reason:     {result['smoothing_reason']}")
    click.echo(f"Scores:     {result['dimension_scores']}")
    click.echo(f"Euclidean:  {result['euclidean_label']} ({result['euclidean_confidence']:.2f})")
    if result.get("hmm_label"):
        click.echo(f"HMM:        {result['hmm_label']} ({result['hmm_confidence']:.2f})")
    if result.get("gmm_label"):
        click.echo(f"GMM:        {result['gmm_label']} ({result['gmm_confidence']:.2f})")
    click.echo(f"{'='*50}\n")


# ---------------------------------------------------------------------------
# Phase 2: regime subcommand — 5-dimension scoring engine
# ---------------------------------------------------------------------------


@main.group()
def regime():
    """Phase 2 regime scoring engine — score, predict, backtest, evaluate."""
    pass


@regime.command()
@click.option("--date", "target_date", type=str, required=True, help="Date to score (YYYY-MM-DD)")
def score(target_date: str):
    """Score today's market regime (coincident — 5 dimensions)."""
    import json as json_mod
    from src.features import DataNotAvailableError, compute_features
    from src.scorer import score_date as score_date_fn

    d = _parse_date(target_date)
    try:
        features = compute_features(d)
    except DataNotAvailableError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    result = score_date_fn(features, target_date=d)
    output = {
        "date": target_date,
        "scores": {
            "volatility": result["vol_score"],
            "trend": result["trend_score"],
            "participation": result["participation_score"],
            "sentiment": result["sentiment_score"],
            "institutional_flow": result["institutional_flow_score"],
        },
        "composite_score": result["composite_score"],
        "regime_label": result["regime_label"],
        "availability_regime": result["availability_regime"],
        "missing_indicators": result["missing_indicators"],
    }
    click.echo(json_mod.dumps(output, indent=2))


@regime.command()
@click.option("--date", "target_date", type=str, required=True, help="Date to predict FROM (YYYY-MM-DD)")
def predict(target_date: str):
    """Predict tomorrow's regime from today's leading indicators."""
    import json as json_mod
    from src.features import DataNotAvailableError, compute_features
    from src.predictor import predict_next_day
    from src.scorer import score_date as score_date_fn

    d = _parse_date(target_date)
    try:
        features = compute_features(d)
    except DataNotAvailableError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    result = score_date_fn(features, target_date=d)
    prediction = predict_next_day(features, target_date=d)

    output = {
        "date": target_date,
        "scores": {
            "volatility": result["vol_score"],
            "trend": result["trend_score"],
            "participation": result["participation_score"],
            "sentiment": result["sentiment_score"],
            "institutional_flow": result["institutional_flow_score"],
        },
        "composite_score": result["composite_score"],
        "regime_label": result["regime_label"],
        "prediction": {
            "next_day_label": prediction["predicted_label"],
            "confidence": prediction["confidence"],
            "leading_score": prediction["leading_score"],
        },
    }
    click.echo(json_mod.dumps(output, indent=2))


@regime.command()
@click.option("--from", "from_date", type=str, required=True)
@click.option("--to", "to_date", type=str, required=True)
@click.option("--bounds-mode", type=click.Choice(["production", "walkforward"]), default="production")
def backtest(from_date: str, to_date: str, bounds_mode: str):
    """Run historical backtest on date range."""
    from scripts.backtest_regime import run_backtest
    run_backtest(
        from_date=_parse_date(from_date),
        to_date=_parse_date(to_date),
        bounds_mode=bounds_mode,
    )


@regime.command()
def evaluate():
    """Evaluate backtest results against ground truth."""
    from scripts.evaluate_regime import load_backtest_with_truth, evaluate_segment, print_report, save_csv_report

    bt = load_backtest_with_truth()
    if bt.empty:
        click.echo("No backtest data. Run 'regime backtest' first.", err=True)
        sys.exit(1)

    all_results = []
    for ar in ["full", "pre_ix", "partial"]:
        segment = bt[bt["availability_regime"] == ar]
        if len(segment) > 0:
            result = evaluate_segment(segment, f"availability={ar}")
            all_results.append(result)
            print_report(result)

    overall = evaluate_segment(bt, "ALL (combined)")
    all_results.append(overall)
    print_report(overall)
    save_csv_report(all_results)


@regime.command()
@click.option("--date", "target_date", type=str, required=True, help="Date to score and store (YYYY-MM-DD)")
def daily(target_date: str):
    """Score today and store in regime_daily table (for pipeline use)."""
    import json as json_mod
    from src.features import DataNotAvailableError, compute_features
    from src.predictor import predict_next_day
    from src.scorer import score_date as score_date_fn
    from src.db import transaction as db_transaction, upsert_regime_daily

    d = _parse_date(target_date)
    try:
        features = compute_features(d)
    except DataNotAvailableError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    result = score_date_fn(features, target_date=d)
    prediction = predict_next_day(features, target_date=d)

    row = {
        "date": d,
        "vol_score": result["vol_score"],
        "trend_score": result["trend_score"],
        "participation_score": result["participation_score"],
        "sentiment_score": result["sentiment_score"],
        "institutional_flow_score": result["institutional_flow_score"],
        "composite_score": result["composite_score"],
        "regime_label": result["regime_label"],
        "predicted_next_label": prediction["predicted_label"],
        "predicted_confidence": prediction["confidence"],
        "availability_regime": result["availability_regime"],
        "missing_indicators": result["missing_indicators"],
    }

    with db_transaction() as conn:
        upsert_regime_daily(conn, row)

    output = {
        "date": target_date,
        "regime_label": result["regime_label"],
        "composite_score": result["composite_score"],
        "predicted_next_label": prediction["predicted_label"],
        "predicted_confidence": prediction["confidence"],
    }
    click.echo(json_mod.dumps(output, indent=2))


# ---------------------------------------------------------------------------
# v2: ML-based regime model
# ---------------------------------------------------------------------------


@main.group()
def v2():
    """v2 ML-based regime model — extract features, train models, evaluate."""
    pass


@v2.command("extract")
@click.option("--from", "from_date", type=str, required=True, help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", type=str, required=True, help="End date (YYYY-MM-DD)")
@click.option("--output", type=str, default="data/v2_feature_matrix.csv", help="Output CSV path")
def v2_extract(from_date: str, to_date: str, output: str):
    """Extract v2 feature matrix for a date range (one row per trading day)."""
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
    from src.v2_features import compute_v2_features
    from src.ground_truth import compute_coincident_truth, compute_predictive_truth
    from src.db import _read_sql

    start = _parse_date(from_date)
    end = _parse_date(to_date)

    # Get all trading days in range (from nse_cm_bhavcopy)
    trading_days_df = _read_sql(
        "SELECT DISTINCT date FROM nse_cm_bhavcopy WHERE date >= %s AND date <= %s ORDER BY date",
        params=[start, end],
    )
    trading_days = [d.date() if hasattr(d, 'date') else d for d in trading_days_df["date"]]
    logger.info("Found %d trading days from %s to %s", len(trading_days), start, end)

    # Fetch ground truth data (Nifty returns + breadth + VIX)
    gt_df = _read_sql(
        """
        SELECT date, close FROM nse_indices_daily
        WHERE index = 'Nifty 50' AND date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start - timedelta(days=10), end + timedelta(days=5)],
    )
    nifty_closes = dict(zip(gt_df["date"], gt_df["close"].astype(float)))

    vix_df = _read_sql(
        """
        SELECT date, close FROM nse_indices_daily
        WHERE index = 'India VIX' AND date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start - timedelta(days=10), end + timedelta(days=5)],
    )
    vix_closes = dict(zip(vix_df["date"], vix_df["close"].astype(float)))

    breadth_df = _read_sql(
        """
        SELECT date,
               SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as advances,
               SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as declines
        FROM nse_cm_bhavcopy
        WHERE date >= %s AND date <= %s
        GROUP BY date
        ORDER BY date
        """,
        params=[start, end + timedelta(days=5)],
    )
    breadth_map = {}
    for _, row in breadth_df.iterrows():
        total = row["advances"] + row["declines"]
        if total > 0:
            breadth_map[row["date"]] = row["advances"] / total

    sorted_nifty_dates = sorted(nifty_closes.keys())
    sorted_vix_dates = sorted(vix_closes.keys())

    def _prev_close(d, closes_dict, sorted_dates):
        """Get previous trading day's close."""
        idx = None
        for i, dt in enumerate(sorted_dates):
            if dt == d:
                idx = i
                break
        if idx and idx > 0:
            return closes_dict[sorted_dates[idx - 1]]
        return None

    def _next_return(d):
        """Get next trading day's return."""
        idx = None
        for i, dt in enumerate(sorted_nifty_dates):
            if dt == d:
                idx = i
                break
        if idx is not None and idx + 1 < len(sorted_nifty_dates):
            next_d = sorted_nifty_dates[idx + 1]
            if d in nifty_closes and next_d in nifty_closes:
                return (nifty_closes[next_d] - nifty_closes[d]) / nifty_closes[d]
        return None

    rows = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("Extracting v2 features...", total=len(trading_days))

        for td in trading_days:
            try:
                features = compute_v2_features(td)
            except Exception as e:
                logger.warning("SKIP %s: %s", td, e)
                progress.advance(task)
                continue

            # Remove meta keys
            row = {k: v for k, v in features.items() if not k.startswith("_")}
            row["date"] = td

            # Availability regime
            missing_families = features.get("_missing_v2_families", [])
            if "tier0_overnight" in missing_families:
                row["availability_regime"] = "pre_ix"
            elif missing_families:
                row["availability_regime"] = "partial"
            else:
                row["availability_regime"] = "full"

            row["missing_count"] = sum(1 for k, v in row.items()
                                       if v is None and k not in ("date", "availability_regime", "missing_count"))

            # Ground truth: coincident
            nifty_return = None
            if td in nifty_closes:
                prev_c = _prev_close(td, nifty_closes, sorted_nifty_dates)
                if prev_c and prev_c > 0:
                    nifty_return = (nifty_closes[td] - prev_c) / prev_c

            breadth = breadth_map.get(td)
            vix_change = None
            if td in vix_closes:
                prev_vix = _prev_close(td, vix_closes, sorted_vix_dates)
                if prev_vix and prev_vix > 0:
                    vix_change = (vix_closes[td] - prev_vix) / prev_vix * 100

            row["nifty_return"] = nifty_return
            row["breadth_ratio"] = breadth
            row["vix_change_pct"] = vix_change

            if nifty_return is not None and breadth is not None and vix_change is not None:
                row["coincident_truth"] = compute_coincident_truth(nifty_return, breadth, vix_change)
            else:
                row["coincident_truth"] = None

            # Ground truth: predictive (next day return)
            next_ret = _next_return(td)
            row["next_day_return"] = next_ret
            if next_ret is not None:
                row["predictive_truth"] = compute_predictive_truth(next_ret)
            else:
                row["predictive_truth"] = None

            rows.append(row)
            progress.advance(task)

    df = pd.DataFrame(rows)

    # Ensure output directory exists
    import os
    os.makedirs(os.path.dirname(output) if os.path.dirname(output) else ".", exist_ok=True)
    df.to_csv(output, index=False)

    click.echo(f"\nExtracted {len(df)} trading days to {output}")
    click.echo(f"Columns: {len(df.columns)}")
    click.echo(f"Null features (mean across dates): {df.drop(columns=['date']).isnull().mean().mean():.1%}")

    # Quick feature quality summary
    if "coincident_truth" in df.columns:
        click.echo(f"\nCoincident truth distribution:")
        for label, count in df["coincident_truth"].value_counts().items():
            click.echo(f"  {label}: {count} ({count/len(df)*100:.1f}%)")

    if "predictive_truth" in df.columns:
        click.echo(f"\nPredictive truth distribution:")
        for label, count in df["predictive_truth"].value_counts().items():
            click.echo(f"  {label}: {count} ({count/len(df)*100:.1f}%)")


@v2.command("train")
@click.option("--input", "input_path", type=str, default="data/v2_feature_matrix.csv", help="Feature matrix CSV")
def v2_train(input_path: str):
    """Train v2 ML models using walk-forward validation."""
    from src.v2_model import run_full_training

    results = run_full_training(input_path)

    for target_name, data in results.items():
        preds = data["predictions"]
        if not preds.empty:
            click.echo(f"\n{target_name.upper()} models:")
            click.echo(f"  Test predictions: {len(preds)} dates")

            # Quick accuracy summary
            model_cols = [c for c in preds.columns if c.endswith("_label") and c != "actual"]
            for col in model_cols:
                valid = preds[preds[col].notna()]
                if len(valid) > 0:
                    from sklearn.metrics import accuracy_score
                    acc = accuracy_score(valid["actual"], valid[col])
                    click.echo(f"  {col.replace('_label', '')}: {acc:.1%}")

    # Save predictions for evaluation
    for target_name, data in results.items():
        preds = data["predictions"]
        if not preds.empty:
            pred_path = f"data/v2_{target_name}_predictions.csv"
            preds.to_csv(pred_path, index=False)
            click.echo(f"\nSaved {target_name} predictions to {pred_path}")

    return results


@v2.command("evaluate")
@click.option("--input", "input_path", type=str, default="data/v2_feature_matrix.csv")
def v2_evaluate(input_path: str):
    """Evaluate v2 models and generate report."""
    import pandas as pd
    from src.v2_model import run_full_training
    from src.v2_evaluate import generate_report

    # Train and evaluate in one go
    results = run_full_training(input_path)
    fm = pd.read_csv(input_path, parse_dates=["date"])
    fm["date"] = pd.to_datetime(fm["date"]).dt.date

    report = generate_report(fm, results)
    click.echo("\n" + report)


@v2.command("pipeline")
@click.option("--from", "from_date", type=str, default="2020-01-15")
@click.option("--to", "to_date", type=str, default="2026-03-20")
@click.option("--output", type=str, default="data/v2_feature_matrix.csv")
def v2_pipeline(from_date: str, to_date: str, output: str):
    """Run full v2 pipeline: extract → train → evaluate → report."""
    import pandas as pd
    from src.v2_model import run_full_training
    from src.v2_evaluate import generate_report

    # Step 1: Extract
    click.echo("=" * 60)
    click.echo("STEP 1: Feature Extraction")
    click.echo("=" * 60)

    ctx = click.get_current_context()
    ctx.invoke(v2_extract, from_date=from_date, to_date=to_date, output=output)

    # Step 2: Train
    click.echo("\n" + "=" * 60)
    click.echo("STEP 2: Walk-Forward Training")
    click.echo("=" * 60)

    results = run_full_training(output)

    # Save predictions
    for target_name, data in results.items():
        preds = data["predictions"]
        if not preds.empty:
            pred_path = f"data/v2_{target_name}_predictions.csv"
            preds.to_csv(pred_path, index=False)
            click.echo(f"Saved {target_name} predictions: {len(preds)} rows → {pred_path}")

    # Step 3: Evaluate
    click.echo("\n" + "=" * 60)
    click.echo("STEP 3: Evaluation & Report")
    click.echo("=" * 60)

    fm = pd.read_csv(output, parse_dates=["date"])
    fm["date"] = pd.to_datetime(fm["date"]).dt.date

    report = generate_report(fm, results)
    click.echo("\n" + report)


# ---------------------------------------------------------------------------
# NSEIX ingestion commands
# ---------------------------------------------------------------------------


@main.group()
def nseix():
    """NSEIX (GIFT Nifty) overnight data ingestion."""
    pass


@nseix.command()
@click.option("--date", "target_date", type=str, required=True, help="Date to fetch (YYYY-MM-DD)")
def fetch(target_date: str):
    """Fetch NSEIX data for a single date."""
    from src.nseix import fetch_date

    d = _parse_date(target_date)
    logger.info("Fetching NSEIX data for %s", d)

    result = fetch_date(d)
    if result["skipped"]:
        click.echo(f"Skipped {d} (no data — likely holiday)")
    else:
        click.echo(f"Done {d}: FO={result['fo']} rows, OP={result['op']} rows, VOL={result['vol']} rows")


@nseix.command("backfill")
@click.option("--from", "from_date", type=str, required=True, help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", type=str, required=True, help="End date (YYYY-MM-DD)")
def nseix_backfill(from_date: str, to_date: str):
    """Backfill NSEIX data for a date range (1s rate limit, retry 3x)."""
    from src.nseix import backfill

    start = _parse_date(from_date)
    end = _parse_date(to_date)
    logger.info("NSEIX backfill: %s to %s", start, end)

    summary = backfill(start, end)
    click.echo(
        f"Backfill complete: {summary['fetched']} fetched, "
        f"{summary['skipped']} skipped, {summary['failed']} failed "
        f"out of {summary['total_days']} weekdays"
    )


# ---------------------------------------------------------------------------
# Pre-Open Session Predictor
# ---------------------------------------------------------------------------


@main.group()
def preopen():
    """Pre-open session predictor — predict today's regime before 9:15 AM."""
    pass


@preopen.command("download")
def preopen_download():
    """Download S&P 500 + USD/INR daily data from Yahoo Finance."""
    from src.global_data import download_global_data

    download_global_data()
    click.echo("Global data download complete.")


@preopen.command("extract")
@click.option("--from", "from_date", type=str, default="2020-01-15", help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", type=str, default="2026-03-20", help="End date (YYYY-MM-DD)")
@click.option("--output", type=str, default="data/preopen_feature_matrix.csv", help="Output CSV path")
def preopen_extract(from_date: str, to_date: str, output: str):
    """Extract pre-open feature matrix for a date range."""
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
    from src.preopen_features import compute_preopen_features, PREOPEN_FEATURE_COLS
    from src.ground_truth import compute_coincident_truth
    from src.db import _read_sql

    start = _parse_date(from_date)
    end = _parse_date(to_date)

    # Get trading days
    trading_days_df = _read_sql(
        "SELECT DISTINCT date FROM nse_cm_bhavcopy WHERE date >= %s AND date <= %s ORDER BY date",
        params=[start, end],
    )
    trading_days = [d.date() if hasattr(d, 'date') else d for d in trading_days_df["date"]]
    logger.info("Found %d trading days from %s to %s", len(trading_days), start, end)

    # Pre-load global data
    try:
        from src.global_data import load_sp500, load_usdinr
        sp500_df = load_sp500()
        usdinr_df = load_usdinr()
        logger.info("Loaded global data: S&P 500 (%d rows), USD/INR (%d rows)", len(sp500_df), len(usdinr_df))
    except FileNotFoundError:
        logger.warning("Global data CSVs not found. Run 'preopen download' first. Tier 2 features will be null.")
        sp500_df = None
        usdinr_df = None

    # Pre-load ground truth data
    gt_nifty = _read_sql(
        "SELECT date, close FROM nse_indices_daily WHERE index = 'Nifty 50' AND date >= %s AND date <= %s ORDER BY date",
        params=[start - timedelta(days=10), end + timedelta(days=5)],
    )
    nifty_closes = dict(zip(gt_nifty["date"], gt_nifty["close"].astype(float)))
    sorted_nifty_dates = sorted(nifty_closes.keys())

    gt_vix = _read_sql(
        "SELECT date, close FROM nse_indices_daily WHERE index = 'India VIX' AND date >= %s AND date <= %s ORDER BY date",
        params=[start - timedelta(days=10), end + timedelta(days=5)],
    )
    vix_closes = dict(zip(gt_vix["date"], gt_vix["close"].astype(float)))
    sorted_vix_dates = sorted(vix_closes.keys())

    breadth_df = _read_sql(
        """
        SELECT date,
               SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as advances,
               SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as declines
        FROM nse_cm_bhavcopy WHERE date >= %s AND date <= %s GROUP BY date ORDER BY date
        """,
        params=[start, end + timedelta(days=5)],
    )
    breadth_map = {}
    for _, row in breadth_df.iterrows():
        total = row["advances"] + row["declines"]
        if total > 0:
            breadth_map[row["date"]] = row["advances"] / total

    def _prev_close_for(d, closes_dict, sorted_dates):
        for i, dt in enumerate(sorted_dates):
            if dt == d and i > 0:
                return closes_dict[sorted_dates[i - 1]]
        return None

    def _next_return(d):
        for i, dt in enumerate(sorted_nifty_dates):
            if dt == d and i + 1 < len(sorted_nifty_dates):
                next_d = sorted_nifty_dates[i + 1]
                if d in nifty_closes and next_d in nifty_closes:
                    return (nifty_closes[next_d] - nifty_closes[d]) / nifty_closes[d]
        return None

    rows = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("Extracting pre-open features...", total=len(trading_days))

        for td in trading_days:
            try:
                features = compute_preopen_features(td, sp500_df=sp500_df, usdinr_df=usdinr_df)
            except Exception as e:
                logger.warning("SKIP %s: %s", td, e)
                progress.advance(task)
                continue

            row = dict(features)
            row["date"] = td

            # Ground truth: coincident
            nifty_return = None
            if td in nifty_closes:
                prev_c = _prev_close_for(td, nifty_closes, sorted_nifty_dates)
                if prev_c and prev_c > 0:
                    nifty_return = (nifty_closes[td] - prev_c) / prev_c

            breadth = breadth_map.get(td)
            vix_change = None
            if td in vix_closes:
                prev_vix = _prev_close_for(td, vix_closes, sorted_vix_dates)
                if prev_vix and prev_vix > 0:
                    vix_change = (vix_closes[td] - prev_vix) / prev_vix * 100

            row["nifty_return"] = nifty_return
            row["breadth_ratio"] = breadth
            row["vix_change_pct"] = vix_change

            if nifty_return is not None and breadth is not None and vix_change is not None:
                row["coincident_truth"] = compute_coincident_truth(nifty_return, breadth, vix_change)
            else:
                row["coincident_truth"] = None

            row["next_day_return"] = _next_return(td)

            rows.append(row)
            progress.advance(task)

    df = pd.DataFrame(rows)
    import os as _os
    _os.makedirs(_os.path.dirname(output) if _os.path.dirname(output) else ".", exist_ok=True)
    df.to_csv(output, index=False)

    click.echo(f"\nExtracted {len(df)} trading days to {output}")
    click.echo(f"Columns: {len(df.columns)}")

    if "coincident_truth" in df.columns:
        click.echo(f"\nCoincident truth distribution:")
        for label, count in df["coincident_truth"].value_counts().items():
            click.echo(f"  {label}: {count} ({count/len(df)*100:.1f}%)")


@preopen.command("train")
@click.option("--input", "input_path", type=str, default="data/preopen_feature_matrix.csv")
def preopen_train(input_path: str):
    """Train pre-open models and generate evaluation report."""
    from src.preopen_model import run_full_pipeline, generate_report

    results = run_full_pipeline(input_path)

    report = generate_report(results)
    click.echo("\n" + report)

    # Save report
    report_path = "reports/preopen_evaluation.md"
    import os as _os
    _os.makedirs("reports", exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report)
    click.echo(f"\nReport saved to {report_path}")

    # Save predictions
    models = results.get("models", {})
    for model_name, model_data in models.items():
        for target_type, data in model_data.items():
            preds = data.get("predictions")
            if preds is not None and not preds.empty:
                pred_path = f"data/preopen_{model_name}_{target_type}_predictions.csv"
                preds.to_csv(pred_path, index=False)


@preopen.command("pipeline")
@click.option("--from", "from_date", type=str, default="2020-01-15")
@click.option("--to", "to_date", type=str, default="2026-03-20")
@click.option("--output", type=str, default="data/preopen_feature_matrix.csv")
def preopen_pipeline(from_date: str, to_date: str, output: str):
    """Run full pre-open pipeline: download → extract → train → report."""
    ctx = click.get_current_context()

    click.echo("=" * 60)
    click.echo("STEP 1: Download Global Data")
    click.echo("=" * 60)
    ctx.invoke(preopen_download)

    click.echo("\n" + "=" * 60)
    click.echo("STEP 2: Extract Pre-Open Features")
    click.echo("=" * 60)
    ctx.invoke(preopen_extract, from_date=from_date, to_date=to_date, output=output)

    click.echo("\n" + "=" * 60)
    click.echo("STEP 3: Train Models & Evaluate")
    click.echo("=" * 60)
    ctx.invoke(preopen_train, input_path=output)


if __name__ == "__main__":
    main()
