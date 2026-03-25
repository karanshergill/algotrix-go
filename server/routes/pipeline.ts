import { Hono } from 'hono'
import pool from '../db'

const pipeline = new Hono()

const FEEDS = [
  { name: 'cm_bhavcopy',    label: 'CM Bhavcopy',    table: 'nse_cm_bhavcopy' },
  { name: 'indices_daily',  label: 'Indices Daily',  table: 'nse_indices_daily' },
  { name: 'fo_bhavcopy',    label: 'F&O Bhavcopy',   table: 'nse_fo_bhavcopy' },
  { name: 'fii_dii_participant', label: 'FII/DII Participant', table: 'nse_fii_dii_participant' },
  { name: 'nseix_settlement', label: 'NSEIX Settlement', table: 'nseix_settlement_prices' },
  { name: 'nseix_combined_oi', label: 'NSEIX Combined OI', table: 'nseix_combined_oi' },
] as const

type FetchLogRow = {
  status: string
  rows_inserted: string
  fetched_at: string | null
  date: string
  error_message: string | null
}

type TableStatsRow = {
  total_rows: string
  date_from: string | null
  date_to: string | null
  trading_days: string
}

pipeline.get('/health', async (c) => {
  const results = await Promise.all(
    FEEDS.map(async (feed) => {
      // Last fetch event from fetch log
      const logRes = await pool.query<FetchLogRow>(
        `SELECT status, rows_inserted, fetched_at, date::text, error_message
         FROM nse_fetch_log
         WHERE feed_name = $1
           AND status != 'skipped'
         ORDER BY date DESC, id DESC
         LIMIT 1`,
        [feed.name]
      )
      const log = logRes.rows[0] ?? null

      // Actual data coverage from target table
      const statsRes = await pool.query<TableStatsRow>(
        `SELECT
           COUNT(*)                       AS total_rows,
           MIN(date)::text                AS date_from,
           MAX(date)::text                AS date_to,
           COUNT(DISTINCT date)::text     AS trading_days
         FROM ${feed.table}`
      )
      const stats = statsRes.rows[0]

      // Freshness: compare latest loaded market date vs last trading day
      // Last trading day = most recent weekday date present in cm_bhavcopy (source of truth)
      const freshnessStatus = deriveFreshness(stats.date_to)

      return {
        name: feed.name,
        label: feed.label,
        lastStatus:       log?.status       ?? null,
        lastFetchTime:    log?.fetched_at   ?? null,
        lastFetchDate:    log?.date         ?? null,
        rowsOnLastFetch:  log ? Number(log.rows_inserted) : null,
        errorMessage:     log?.error_message ?? null,
        latestMarketDate: stats.date_to     ?? null,
        dateFrom:         stats.date_from   ?? null,
        dateTo:           stats.date_to     ?? null,
        totalRows:        Number(stats.total_rows),
        tradingDays:      Number(stats.trading_days),
        freshnessStatus,
      }
    })
  )

  return c.json({ feeds: results, fetchedAt: new Date().toISOString() })
})

// Freshness: "fresh" if latest date is within last 2 calendar days (covers weekends),
// "stale" if older, "empty" if no data.
function deriveFreshness(latestDate: string | null): 'fresh' | 'stale' | 'empty' {
  if (!latestDate) return 'empty'
  const latest = new Date(latestDate)
  const now = new Date()
  const diffMs = now.getTime() - latest.getTime()
  const diffDays = diffMs / (1000 * 60 * 60 * 24)
  // Allow up to 4 calendar days (covers long weekends + 1 missed day)
  if (diffDays <= 4) return 'fresh'
  return 'stale'
}

export default pipeline
