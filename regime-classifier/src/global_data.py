"""Download and load S&P 500 + USD/INR daily data from Yahoo Finance."""

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def download_global_data(start: str = "2019-12-01", end: str = "2026-03-21") -> None:
    """Download S&P 500 and USD/INR daily OHLCV from Yahoo Finance and save as CSVs."""
    DATA_DIR.mkdir(exist_ok=True)

    for ticker, filename in [("^GSPC", "sp500_daily.csv"), ("USDINR=X", "usdinr_daily.csv")]:
        print(f"Downloading {ticker}...")
        df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
        if df.empty:
            print(f"  WARNING: No data for {ticker}")
            continue
        # Flatten multi-level columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        df.to_csv(DATA_DIR / filename, index=False)
        print(f"  Saved {len(df)} rows to {filename}")


def _load_csv(filename: str) -> pd.DataFrame:
    """Load a global daily CSV, parsing dates."""
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run download_global_data() first.")
    df = pd.read_csv(path, parse_dates=["Date"])
    df["date"] = pd.to_datetime(df["Date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df


def load_sp500() -> pd.DataFrame:
    """Load S&P 500 daily data. Returns DataFrame with date, Close columns."""
    return _load_csv("sp500_daily.csv")


def load_usdinr() -> pd.DataFrame:
    """Load USD/INR daily data. Returns DataFrame with date, Close columns."""
    return _load_csv("usdinr_daily.csv")


def get_sp500_overnight_return(target_date: date, sp500_df: pd.DataFrame) -> float | None:
    """Get S&P 500 return for the most recent US trading day before target_date.

    US market closes ~7 AM IST, so this is the overnight return available before 9:15 AM.
    """
    prior = sp500_df[sp500_df["date"] < target_date]
    if len(prior) < 2:
        return None
    last_close = float(prior.iloc[-1]["Close"])
    prev_close = float(prior.iloc[-2]["Close"])
    if prev_close == 0:
        return None
    return (last_close - prev_close) / prev_close


def get_usdinr_overnight_change(target_date: date, usdinr_df: pd.DataFrame) -> float | None:
    """Get USD/INR change for the most recent trading day before target_date."""
    prior = usdinr_df[usdinr_df["date"] < target_date]
    if len(prior) < 2:
        return None
    last_close = float(prior.iloc[-1]["Close"])
    prev_close = float(prior.iloc[-2]["Close"])
    if prev_close == 0:
        return None
    return (last_close - prev_close) / prev_close


if __name__ == "__main__":
    download_global_data()
