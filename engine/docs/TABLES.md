# Table Naming Convention

## Pattern
`{exchange}_{segment}_ohlcv_{resolution}`

## Exchange & Segment
| Prefix | Description |
|--------|-------------|
| nse_cm | NSE Cash Market (Equities) |
| nse_fo | NSE Futures & Options |
| bse_cm | BSE Cash Market |
| mcx_com | MCX Commodities |
| cds | Currency Derivatives |

## Resolutions
| Resolution | API Value | Table Suffix | Example |
|------------|-----------|-------------|---------|
| 5 seconds | 5S | _5s | nse_cm_ohlcv_5s |
| 10 seconds | 10S | _10s | nse_cm_ohlcv_10s |
| 15 seconds | 15S | _15s | nse_cm_ohlcv_15s |
| 30 seconds | 30S | _30s | nse_cm_ohlcv_30s |
| 45 seconds | 45S | _45s | nse_cm_ohlcv_45s |
| 1 minute | 1 | _1m | nse_cm_ohlcv_1m |
| 2 minutes | 2 | _2m | nse_cm_ohlcv_2m |
| 3 minutes | 3 | _3m | nse_cm_ohlcv_3m |
| 5 minutes | 5 | _5m | nse_cm_ohlcv_5m |
| 10 minutes | 10 | _10m | nse_cm_ohlcv_10m |
| 15 minutes | 15 | _15m | nse_cm_ohlcv_15m |
| 20 minutes | 20 | _20m | nse_cm_ohlcv_20m |
| 30 minutes | 30 | _30m | nse_cm_ohlcv_30m |
| 45 minutes | 45 | _45m | nse_cm_ohlcv_45m |
| 1 hour | 60 | _1h | nse_cm_ohlcv_1h |
| 2 hours | 120 | _2h | nse_cm_ohlcv_2h |
| 3 hours | 180 | _3h | nse_cm_ohlcv_3h |
| 4 hours | 240 | _4h | nse_cm_ohlcv_4h |
| 1 day | 1D | _1d | nse_cm_ohlcv_1d |

## OHLCV Table Schema (QuestDB)
All OHLCV tables share the same schema:
| Column | Type | Description |
|--------|------|-------------|
| isin | SYMBOL | ISIN identifier (e.g. INE062A01020) |
| open | DOUBLE | Open price |
| high | DOUBLE | High price |
| low | DOUBLE | Low price |
| close | DOUBLE | Close price |
| volume | LONG | Trade volume |
| timestamp | TIMESTAMP | Candle timestamp (designated timestamp) |

Partitioned by DAY for seconds/minutes, by MONTH for daily.
Dedup upsert keys: (timestamp, isin) — prevents duplicate candles on re-runs.

## Symbol Tables (PostgreSQL)
| Table | Description |
|-------|-------------|
| nse_cm_symbols | NSE Cash Market symbol master |
| nse_fo_symbols | NSE F&O symbol master (future) |
