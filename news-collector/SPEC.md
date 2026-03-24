# NSE News Collector — Specification

## Goal
Collect market-moving news from NSE India APIs and store in PostgreSQL for later correlation with price data.

## Data Sources (all from nseindia.com JSON APIs)

### 1. Corporate Announcements
- **Endpoint:** `GET /api/corporate-announcements?index=equities`
- **Key fields:** symbol, desc (category), an_dt (timestamp), attchmntFile (PDF link)
- **Filter for:** Outcome of Board Meeting, Acquisition, Credit Rating, Spurt in Volume, Disclosure under SEBI Takeover Regulations, Agreements
- **Store all categories** but flag the market-moving ones

### 2. Block Deals
- **Endpoint:** `GET /api/block-deal`
- **Returns:** { timestamp, data: [...] }
- **Key fields:** symbol, series, totalTradedVolume, totalTradedValue, lastUpdateTime

### 3. Insider Trading (PIT)
- **Endpoint:** `GET /api/corporates-pit?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`
- **Returns:** dict with acqNameList, data, etc.
- **Key fields:** acquirer name, symbol, acqMode (buy/sell), secVal (value), secAcq (shares)

### 4. Board Meetings (upcoming)
- **Endpoint:** `GET /api/corporate-board-meetings?index=equities`
- **Key fields:** bm_symbol, bm_date, bm_purpose, bm_desc

### 5. Corporate Actions
- **Endpoint:** `GET /api/corporates-corporateActions?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`
- **Key fields:** symbol, subject (dividend/split/bonus), exDate, recDate

## Session Handling
NSE requires a session cookie. Flow:
1. `GET https://www.nseindia.com` to get cookies
2. Use those cookies for API calls
3. Re-establish session if 403 received
- User-Agent must look like a real browser

## Database

- **Host:** localhost:5432
- **Database:** `atdb` (consolidated AlgoTrix database)
- **User:** me / algotrix

### Tables

```sql
CREATE TABLE IF NOT EXISTS nse_announcements (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    category TEXT,
    description TEXT,
    announcement_dt TIMESTAMP,
    attachment_url TEXT,
    is_market_moving BOOLEAN DEFAULT FALSE,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, announcement_dt, category)
);

CREATE TABLE IF NOT EXISTS nse_block_deals (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    series TEXT,
    session TEXT,
    traded_volume BIGINT,
    traded_value NUMERIC,
    price NUMERIC,
    deal_date DATE,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, deal_date, session, traded_volume)
);

CREATE TABLE IF NOT EXISTS nse_insider_trading (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    acquirer_name TEXT,
    acquisition_mode TEXT,
    shares_acquired NUMERIC,
    value NUMERIC,
    transaction_date DATE,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, acquirer_name, transaction_date, shares_acquired)
);

CREATE TABLE IF NOT EXISTS nse_board_meetings (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    meeting_date DATE,
    purpose TEXT,
    description TEXT,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, meeting_date, purpose)
);

CREATE TABLE IF NOT EXISTS nse_corporate_actions (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    subject TEXT,
    ex_date DATE,
    record_date DATE,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, ex_date, subject)
);
```

### Market-Moving Flag Logic
Set `is_market_moving = TRUE` for announcements where category IN:
- 'Outcome of Board Meeting'
- 'Disclosure under SEBI Takeover Regulations'
- 'Spurt in Volume'
- 'Credit Rating- Others'
- 'Agreements'
- Any containing 'Acquisition', 'Order', 'Award'

## Polling Schedule
- During market hours (9:00-15:45 IST): every 2 minutes
- After hours (15:45-20:00): every 10 minutes (filings come in after close)
- Night (20:00-9:00): every 30 minutes (catch late filings)

## Deployment

- **Process manager:** PM2 (bare metal on Command Center VPS)
- **PM2 name:** `nse-news`
- **Ecosystem config:** `/home/me/projects/algotrix-go/ecosystem.config.cjs`
- **Logs:** `/tmp/nse-news-out.log`, `/tmp/nse-news-error.log`
- **Auto-restart:** via PM2 (max_memory_restart: 200M)
- **Start:** `pm2 start ecosystem.config.cjs --only nse-news`
- **Graceful shutdown** on SIGTERM

### Environment Variables (set in ecosystem.config.cjs)
- `DB_HOST` — default: localhost
- `DB_PORT` — default: 5432
- `DB_NAME` — default: atdb
- `DB_USER` — default: me
- `DB_PASS` — default: algotrix

## Tech Stack
- Python 3.12
- `requests` for HTTP
- `psycopg2` for PostgreSQL
- `schedule` for timing
- No frameworks, keep it simple

## Data Volumes (as of 2026-03-24)
- **Announcements:** ~8,700 rows (Feb 23 – present)
- **Block deals:** ~24 rows
- **Board meetings:** ~370 rows (includes future dates)
- **Corporate actions:** ~290 rows (includes future dates)
- **Insider trading:** ~2.99M rows (Feb 2024 – present)
