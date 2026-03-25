import { Hono } from 'hono'
import pool from '../db'

const universe = new Hono()

type MetricRow = {
  isin: string
  symbol: string
  name: string | null
  series: string
  sector: string | null
  market_cap: string | null
  is_fno: boolean | null
  index_membership: string[] | null
  last_price: number
  avg_vol: string
  avg_turnover: string
  days_traded: string
}

universe.get('/metrics', async (c) => {
  try {
    const result = await pool.query<MetricRow>(`
      WITH recent_dates AS (
        SELECT DISTINCT date FROM nse_cm_bhavcopy ORDER BY date DESC LIMIT 20
      ),
      last_day AS (
        SELECT MAX(date) AS dt FROM recent_dates
      ),
      last_prices AS (
        SELECT isin, close AS last_price
        FROM nse_cm_bhavcopy
        WHERE date = (SELECT dt FROM last_day)
      ),
      aggregates AS (
        SELECT
          isin,
          AVG(volume) AS avg_vol,
          AVG(traded_value) AS avg_turnover,
          COUNT(DISTINCT date) AS days_traded
        FROM nse_cm_bhavcopy
        WHERE date IN (SELECT date FROM recent_dates)
        GROUP BY isin
      )
      SELECT
        lp.isin,
        s.symbol,
        s.name,
        s.series,
        s.sector,
        s.market_cap::text,
        s.is_fno,
        s.index_membership,
        lp.last_price,
        a.avg_vol::text,
        a.avg_turnover::text,
        a.days_traded::text
      FROM last_prices lp
      JOIN aggregates a ON lp.isin = a.isin
      LEFT JOIN symbols s ON lp.isin = s.isin
      ORDER BY a.avg_turnover DESC
    `)

    const lastDateResult = await pool.query<{ dt: string }>(
      `SELECT MAX(date)::text AS dt FROM (SELECT DISTINCT date FROM nse_cm_bhavcopy ORDER BY date DESC LIMIT 20) sub`
    )

    const stocks = result.rows.map((row) => ({
      isin: row.isin,
      symbol: row.symbol ?? row.isin,
      name: row.name,
      series: row.series ?? 'EQ',
      sector: row.sector,
      marketCap: row.market_cap ? Number(row.market_cap) : null,
      isFnO: row.is_fno ?? false,
      indexMemberships: row.index_membership ?? [],
      lastPrice: Number(row.last_price),
      avgVolume20d: Math.round(Number(row.avg_vol)),
      avgTurnover20d: Math.round(Number(row.avg_turnover)),
      tradedDays: Number(row.days_traded),
    }))

    return c.json({
      asOf: lastDateResult.rows[0]?.dt ?? null,
      tradingDays: 20,
      stocks,
    })
  } catch (err) {
    console.error('universe/metrics error:', err)
    return c.json({ error: 'Failed to fetch universe metrics' }, 500)
  }
})

export default universe
