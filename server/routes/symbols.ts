import { Hono } from 'hono'
import pool from '../db'

const symbols = new Hono()

symbols.get('/stats', async (c) => {
  const result = await pool.query(`
    SELECT
      count(*) AS total,
      count(*) FILTER (WHERE status = 'active') AS active,
      count(*) FILTER (WHERE status = 'skipped') AS skipped,
      count(*) FILTER (WHERE company_name IS NOT NULL) AS enriched,
      count(*) FILTER (WHERE skip_reason = 'non_equity') AS skip_non_equity,
      count(*) FILTER (WHERE skip_reason = 'sme') AS skip_sme,
      count(*) FILTER (WHERE skip_reason = 'trade_to_trade') AS skip_t2t,
      count(*) FILTER (WHERE is_fno = true) AS fno
    FROM symbols
  `)

  const row = result.rows[0]
  return c.json({
    total: Number(row.total),
    active: Number(row.active),
    skipped: Number(row.skipped),
    enriched: Number(row.enriched),
    fno: Number(row.fno),
    bySkipReason: {
      nonEquity: Number(row.skip_non_equity),
      sme: Number(row.skip_sme),
      tradToTrade: Number(row.skip_t2t),
    },
  })
})

export default symbols
