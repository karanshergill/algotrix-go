import { Hono } from 'hono'
import pool from '../db'

const router = new Hono()

type StrengthRow = {
  group_name: string
  stock_count: number
  score: number | null
  ret_1d: number | null
  ret_1w: number | null
  ret_1m: number | null
  ret_3m: number | null
  ret_6m: number | null
  ret_1y: number | null
  adv_count: number
  dec_count: number
  unch_count: number
  vol_total_1d: number | null
  vol_avg_20d: number | null
  vol_ratio: number | null
}

const VALID_LEVELS = ['macro', 'sector', 'industry', 'sub_industry']

// GET /api/sectors/strength?level=macro|sector|industry|sub_industry
// Returns latest date data for all groups at the given level.
router.get('/strength', async (c) => {
  const level = c.req.query('level') ?? 'sector'

  if (!VALID_LEVELS.includes(level)) {
    return c.json({ error: `Invalid level. Must be one of: ${VALID_LEVELS.join(', ')}` }, 400)
  }

  // Get the latest date with data for this level
  const latestRes = await pool.query<{ date: string }>(
    `SELECT MAX(date) AS date FROM sector_strength WHERE level = $1`,
    [level]
  )
  const latestDate = latestRes.rows[0]?.date
  if (!latestDate) {
    return c.json({ date: null, groups: [] })
  }

  const result = await pool.query<StrengthRow>(
    `SELECT
       group_name, stock_count,
       score::float,
       ret_1d::float, ret_1w::float, ret_1m::float,
       ret_3m::float, ret_6m::float, ret_1y::float,
       adv_count, dec_count, unch_count,
       vol_total_1d, vol_avg_20d, vol_ratio::float
     FROM sector_strength
     WHERE level = $1 AND date = $2
     ORDER BY score DESC NULLS LAST`,
    [level, latestDate]
  )

  return c.json({
    date: latestDate,
    level,
    groups: result.rows,
  })
})

// GET /api/sectors/levels — list all levels and group counts
router.get('/levels', async (c) => {
  const result = await pool.query<{ level: string; group_count: number; latest_date: string }>(
    `SELECT level,
            COUNT(DISTINCT group_name) AS group_count,
            MAX(date) AS latest_date
     FROM sector_strength
     GROUP BY level
     ORDER BY CASE level
       WHEN 'macro'        THEN 1
       WHEN 'sector'       THEN 2
       WHEN 'industry'     THEN 3
       WHEN 'sub_industry' THEN 4
     END`
  )
  return c.json(result.rows)
})

export default router
