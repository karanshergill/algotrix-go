"""NSEIX (NSE International Exchange / GIFT Nifty) data ingestion pipeline.

Downloads overnight futures, options, and volatility CSV files from NSEIX API,
parses them, and upserts into PostgreSQL tables.

Auth: GET /api/generate-token → Bearer token (no credentials needed).
Rate limit: 1 request per second.
"""

import io
import logging
import re
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from src.config import DB_CONFIG
from src.db import get_connection

logger = logging.getLogger(__name__)

NSEIX_BASE_URL = "https://www.nseix.com/api/daily-reports"
NSEIX_TOKEN_URL = "https://www.nseix.com/api/generate-token"
NSEIX_CONTENT_URL = "https://www.nseix.com/api/content/daily_report"

# Retry config
MAX_RETRIES = 3
INITIAL_BACKOFF = 2  # seconds
REQUEST_TIMEOUT = 60  # seconds (NSEIX can be slow)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_token() -> str:
    """Fetch a fresh Bearer token from NSEIX API."""
    resp = requests.get(
        NSEIX_TOKEN_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token") or data.get("Token")
    if not token:
        raise ValueError(f"No token in response: {data}")
    return token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}


# ---------------------------------------------------------------------------
# Date formatting
# ---------------------------------------------------------------------------

def _fmt_ddmmyy(d: date) -> str:
    """Format date as DDMMYY for FO/OP files."""
    return d.strftime("%d%m%y")


def _fmt_ddmmyyyy(d: date) -> str:
    """Format date as DDMMYYYY for volatility files."""
    return d.strftime("%d%m%Y")


# ---------------------------------------------------------------------------
# CSV download
# ---------------------------------------------------------------------------

def _download_csv(token: str, filename: str) -> pd.DataFrame | None:
    """Download a CSV file from NSEIX. Returns DataFrame or None on 404."""
    url = f"{NSEIX_CONTENT_URL}/{filename}"
    logger.debug("Fetching %s", url)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=_auth_headers(token), timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                logger.info("404 — file not found: %s (likely holiday)", filename)
                return None
            resp.raise_for_status()
            # Parse CSV from response content
            df = pd.read_csv(io.StringIO(resp.text))
            # Strip whitespace from column names
            df.columns = df.columns.str.strip()
            return df
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.info("404 — file not found: %s", filename)
                return None
            backoff = INITIAL_BACKOFF ** attempt
            logger.warning(
                "HTTP error fetching %s (attempt %d/%d): %s — retrying in %ds",
                filename, attempt, MAX_RETRIES, e, backoff,
            )
            if attempt < MAX_RETRIES:
                time.sleep(backoff)
        except requests.exceptions.RequestException as e:
            backoff = INITIAL_BACKOFF ** attempt
            logger.warning(
                "Request error fetching %s (attempt %d/%d): %s — retrying in %ds",
                filename, attempt, MAX_RETRIES, e, backoff,
            )
            if attempt < MAX_RETRIES:
                time.sleep(backoff)

    logger.error("Failed to download %s after %d attempts", filename, MAX_RETRIES)
    return None


# ---------------------------------------------------------------------------
# CONTRACT_D parsing
# ---------------------------------------------------------------------------

# Matches patterns like: FUTIDXNIFTY30-MAR-2026, OPTSTKNIFTY30-MAR-202624000CE
_CONTRACT_RE = re.compile(
    r"^(FUTIDX|FUTSTK|FUTCUR|FUTCBIC\d*|OPTIDX|OPTSTK|OPTCUR)"  # instrument_type
    r"(.+?)"                             # symbol (non-greedy)
    r"(\d{2}-[A-Z]{3}-\d{4})"           # expiry: DD-MMM-YYYY
    r"(?:(CE|PE)([\d.]+)|([\d.]+)(CE|PE))?"  # option_type+strike OR strike+option_type
    r"$"
)


def parse_contract_d(contract: str) -> dict:
    """Parse CONTRACT_D field into components.

    Examples:
        'FUTIDXNIFTY30-MAR-2026' → {instrument_type: 'FUTIDX', symbol: 'NIFTY', expiry: date, ...}
        'OPTIDXNIFTY30-MAR-202624000CE' → {... strike: 24000.0, option_type: 'CE'}
    """
    contract = contract.strip()
    m = _CONTRACT_RE.match(contract)
    if not m:
        return {"raw": contract, "instrument_type": None, "symbol": None, "expiry": None}

    instrument_type = m.group(1)
    symbol = m.group(2)
    expiry_str = m.group(3)

    # Handle both orderings: CE23000 or 23000CE
    if m.group(4):  # CE/PE before strike
        option_type = m.group(4)
        strike_str = m.group(5)
    elif m.group(7):  # CE/PE after strike
        option_type = m.group(7)
        strike_str = m.group(6)
    else:
        option_type = None
        strike_str = None

    try:
        expiry = datetime.strptime(expiry_str, "%d-%b-%Y").date()
    except ValueError:
        expiry = None

    return {
        "instrument_type": instrument_type,
        "symbol": symbol,
        "expiry": expiry,
        "strike": float(strike_str) if strike_str else None,
        "option_type": option_type,
    }


# ---------------------------------------------------------------------------
# Parsers — transform raw CSV DataFrames into DB-ready rows
# ---------------------------------------------------------------------------

def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return f if pd.notna(f) else None
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def parse_fo_csv(df: pd.DataFrame, target_date: date) -> list[dict]:
    """Parse FO bhavcopy CSV into rows for nseix_overnight_fo."""
    rows = []
    for _, raw in df.iterrows():
        contract = str(raw.get("CONTRACT_D", ""))
        parsed = parse_contract_d(contract)
        if parsed["instrument_type"] is None:
            logger.debug("Skipping unparseable FO contract: %s", contract)
            continue

        rows.append({
            "date": target_date,
            "instrument_type": parsed["instrument_type"],
            "symbol": parsed["symbol"],
            "expiry": parsed["expiry"],
            "open": _safe_float(raw.get("OPEN_PRICE")),
            "high": _safe_float(raw.get("HIGH_PRICE")),
            "low": _safe_float(raw.get("LOW_PRICE")),
            "close": _safe_float(raw.get("CLOSE_PRIC")),
            "settlement": _safe_float(raw.get("SETTLEMENT")),
            "prev_settlement": _safe_float(raw.get("PREVIOUS_S")),
            "net_change_pct": _safe_float(raw.get("NET_CHANGE")),
            "oi": _safe_int(raw.get("OI_NO_CON")),
            "volume": _safe_int(raw.get("TRADED_QUA")),
            "num_trades": _safe_int(raw.get("TRD_NO_CON")),
            "traded_value": _safe_float(raw.get("TRADED_VAL")),
        })
    return rows


def parse_op_csv(df: pd.DataFrame, target_date: date) -> list[dict]:
    """Parse OP bhavcopy CSV into rows for nseix_overnight_op."""
    rows = []
    for _, raw in df.iterrows():
        contract = str(raw.get("CONTRACT_D", ""))
        parsed = parse_contract_d(contract)
        if parsed["instrument_type"] is None:
            logger.debug("Skipping unparseable OP contract: %s", contract)
            continue

        rows.append({
            "date": target_date,
            "instrument_type": parsed["instrument_type"],
            "symbol": parsed["symbol"],
            "expiry": parsed["expiry"],
            "strike": parsed.get("strike"),
            "option_type": parsed.get("option_type"),
            "open": _safe_float(raw.get("OPEN_PRICE")),
            "high": _safe_float(raw.get("HIGH_PRICE")),
            "low": _safe_float(raw.get("LOW_PRICE")),
            "close": _safe_float(raw.get("CLOSE_PRIC")),
            "settlement": _safe_float(raw.get("SETTLEMENT")),
            "prev_settlement": _safe_float(raw.get("PREVIOUS_S")),
            "net_change_pct": _safe_float(raw.get("NET_CHANGE")),
            "oi": _safe_int(raw.get("OI_NO_CON")),
            "volume": _safe_int(raw.get("TRADED_QUA")),
            "num_trades": _safe_int(raw.get("TRD_NO_CON")),
            "underlying_settle": _safe_float(raw.get("UNDRLNG_ST")),
            "notional_value": _safe_float(raw.get("NOTIONAL_V")),
            "premium_traded": _safe_float(raw.get("PREMIUM_TR")),
        })
    return rows


def parse_vol_csv(df: pd.DataFrame, target_date: date) -> list[dict]:
    """Parse volatility CSV into rows for nseix_overnight_vol."""
    rows = []
    for _, raw in df.iterrows():
        symbol = str(raw.get("Symbol", "")).strip()
        if not symbol:
            continue

        rows.append({
            "date": target_date,
            "symbol": symbol,
            "underlying_close": _safe_float(raw.get("Underlying Close Price (A)")),
            "underlying_prev_close": _safe_float(raw.get("Underlying Previous Day Close Price (B)")),
            "underlying_log_returns": _safe_float(raw.get("Underlying Log Returns (C)")),
            "prev_underlying_vol": _safe_float(raw.get("Previous Day Underlying Volatility (D)")),
            "current_underlying_vol": _safe_float(raw.get("Current Day Underlying Daily Volatility (E)")),
            "underlying_ann_vol": _safe_float(raw.get("Underlying Annualised Volatility (F)")),
            "futures_close": _safe_float(raw.get("Futures Close Price (G)")),
            "futures_prev_close": _safe_float(raw.get("Futures Previous Day Close Price (H)")),
            "futures_log_returns": _safe_float(raw.get("Futures Log Returns (I)")),
            "prev_futures_vol": _safe_float(raw.get("Previous Day Futures Volatility (J)")),
            "current_futures_vol": _safe_float(raw.get("Current Day Futures Daily Volatility (K)")),
            "futures_ann_vol": _safe_float(raw.get("Futures Annualised Volatility (L)")),
            "applicable_daily_vol": _safe_float(raw.get("Applicable Daily Volatility (M)")),
            "applicable_ann_vol": _safe_float(raw.get("Applicable Annualised Volatility (N)")),
        })
    return rows


# ---------------------------------------------------------------------------
# Upsert into PostgreSQL
# ---------------------------------------------------------------------------

def _upsert_fo_rows(conn, rows: list[dict]) -> int:
    """Upsert FO rows into nseix_overnight_fo. Returns count."""
    if not rows:
        return 0
    cur = conn.cursor()
    sql = """
        INSERT INTO nseix_overnight_fo
            (date, instrument_type, symbol, expiry, open, high, low, close,
             settlement, prev_settlement, net_change_pct, oi, volume, num_trades, traded_value, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (date, instrument_type, symbol, expiry) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, settlement = EXCLUDED.settlement,
            prev_settlement = EXCLUDED.prev_settlement, net_change_pct = EXCLUDED.net_change_pct,
            oi = EXCLUDED.oi, volume = EXCLUDED.volume, num_trades = EXCLUDED.num_trades,
            traded_value = EXCLUDED.traded_value, fetched_at = NOW()
    """
    count = 0
    for r in rows:
        cur.execute(sql, [
            r["date"], r["instrument_type"], r["symbol"], r["expiry"],
            r["open"], r["high"], r["low"], r["close"],
            r["settlement"], r["prev_settlement"], r["net_change_pct"],
            r["oi"], r["volume"], r["num_trades"], r["traded_value"],
        ])
        count += 1
    return count


def _upsert_op_rows(conn, rows: list[dict]) -> int:
    """Upsert OP rows into nseix_overnight_op. Returns count."""
    if not rows:
        return 0
    cur = conn.cursor()
    sql = """
        INSERT INTO nseix_overnight_op
            (date, instrument_type, symbol, expiry, strike, option_type,
             open, high, low, close, settlement, prev_settlement, net_change_pct,
             oi, volume, num_trades, underlying_settle, notional_value, premium_traded, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (date, instrument_type, symbol, expiry, strike, option_type) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, settlement = EXCLUDED.settlement,
            prev_settlement = EXCLUDED.prev_settlement, net_change_pct = EXCLUDED.net_change_pct,
            oi = EXCLUDED.oi, volume = EXCLUDED.volume, num_trades = EXCLUDED.num_trades,
            underlying_settle = EXCLUDED.underlying_settle, notional_value = EXCLUDED.notional_value,
            premium_traded = EXCLUDED.premium_traded, fetched_at = NOW()
    """
    count = 0
    for r in rows:
        cur.execute(sql, [
            r["date"], r["instrument_type"], r["symbol"], r["expiry"],
            r["strike"], r["option_type"],
            r["open"], r["high"], r["low"], r["close"],
            r["settlement"], r["prev_settlement"], r["net_change_pct"],
            r["oi"], r["volume"], r["num_trades"],
            r["underlying_settle"], r["notional_value"], r["premium_traded"],
        ])
        count += 1
    return count


def _upsert_vol_rows(conn, rows: list[dict]) -> int:
    """Upsert vol rows into nseix_overnight_vol. Returns count."""
    if not rows:
        return 0
    cur = conn.cursor()
    sql = """
        INSERT INTO nseix_overnight_vol
            (date, symbol, underlying_close, underlying_prev_close, underlying_log_returns,
             prev_underlying_vol, current_underlying_vol, underlying_ann_vol,
             futures_close, futures_prev_close, futures_log_returns,
             prev_futures_vol, current_futures_vol, futures_ann_vol,
             applicable_daily_vol, applicable_ann_vol, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (date, symbol) DO UPDATE SET
            underlying_close = EXCLUDED.underlying_close,
            underlying_prev_close = EXCLUDED.underlying_prev_close,
            underlying_log_returns = EXCLUDED.underlying_log_returns,
            prev_underlying_vol = EXCLUDED.prev_underlying_vol,
            current_underlying_vol = EXCLUDED.current_underlying_vol,
            underlying_ann_vol = EXCLUDED.underlying_ann_vol,
            futures_close = EXCLUDED.futures_close,
            futures_prev_close = EXCLUDED.futures_prev_close,
            futures_log_returns = EXCLUDED.futures_log_returns,
            prev_futures_vol = EXCLUDED.prev_futures_vol,
            current_futures_vol = EXCLUDED.current_futures_vol,
            futures_ann_vol = EXCLUDED.futures_ann_vol,
            applicable_daily_vol = EXCLUDED.applicable_daily_vol,
            applicable_ann_vol = EXCLUDED.applicable_ann_vol,
            fetched_at = NOW()
    """
    count = 0
    for r in rows:
        cur.execute(sql, [
            r["date"], r["symbol"],
            r["underlying_close"], r["underlying_prev_close"], r["underlying_log_returns"],
            r["prev_underlying_vol"], r["current_underlying_vol"], r["underlying_ann_vol"],
            r["futures_close"], r["futures_prev_close"], r["futures_log_returns"],
            r["prev_futures_vol"], r["current_futures_vol"], r["futures_ann_vol"],
            r["applicable_daily_vol"], r["applicable_ann_vol"],
        ])
        count += 1
    return count


# ---------------------------------------------------------------------------
# High-level fetch for a single date
# ---------------------------------------------------------------------------

def fetch_date(target_date: date, token: str | None = None) -> dict:
    """Fetch all 3 NSEIX files for a single date and upsert into DB.

    Returns dict with counts: {fo: N, op: N, vol: N, skipped: bool}.
    """
    if token is None:
        token = get_token()

    ddmmyy = _fmt_ddmmyy(target_date)
    ddmmyyyy = _fmt_ddmmyyyy(target_date)

    result = {"fo": 0, "op": 0, "vol": 0, "skipped": False, "date": target_date}

    # Download all 3 CSVs with rate limiting
    fo_df = _download_csv(token, f"G_T1_Bhavcopy_FO_{ddmmyy}.CSV")
    time.sleep(1)
    op_df = _download_csv(token, f"G_T1_Bhavcopy_OP_{ddmmyy}.CSV")
    time.sleep(1)
    vol_df = _download_csv(token, f"G_T1_VOLT_{ddmmyyyy}.CSV")

    if fo_df is None and op_df is None and vol_df is None:
        logger.info("SKIP %s — all 3 files returned 404 (holiday)", target_date)
        result["skipped"] = True
        return result

    # Parse
    fo_rows = parse_fo_csv(fo_df, target_date) if fo_df is not None else []
    op_rows = parse_op_csv(op_df, target_date) if op_df is not None else []
    vol_rows = parse_vol_csv(vol_df, target_date) if vol_df is not None else []

    # Upsert in a single transaction
    conn = get_connection()
    try:
        result["fo"] = _upsert_fo_rows(conn, fo_rows)
        result["op"] = _upsert_op_rows(conn, op_rows)
        result["vol"] = _upsert_vol_rows(conn, vol_rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(
        "DONE %s — FO: %d rows, OP: %d rows, VOL: %d rows",
        target_date, result["fo"], result["op"], result["vol"],
    )
    return result


def backfill(from_date: date, to_date: date) -> dict:
    """Backfill NSEIX data for a date range.

    Iterates calendar days, skipping weekends. Rate limits at 1 req/sec.
    Returns summary: {fetched: N, skipped: N, failed: N}.
    """
    token = get_token()
    summary = {"fetched": 0, "skipped": 0, "failed": 0, "total_days": 0}

    current = from_date
    while current <= to_date:
        # Skip weekends
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        summary["total_days"] += 1

        try:
            result = fetch_date(current, token=token)
            if result["skipped"]:
                summary["skipped"] += 1
            else:
                summary["fetched"] += 1
        except Exception as e:
            logger.error("FAIL %s: %s", current, e)
            summary["failed"] += 1
            # Refresh token in case it expired
            try:
                token = get_token()
            except Exception:
                logger.warning("Token refresh failed, continuing with old token")

        # Rate limit between dates
        time.sleep(1)
        current += timedelta(days=1)

    logger.info(
        "Backfill complete: %d fetched, %d skipped, %d failed out of %d weekdays",
        summary["fetched"], summary["skipped"], summary["failed"], summary["total_days"],
    )
    return summary
