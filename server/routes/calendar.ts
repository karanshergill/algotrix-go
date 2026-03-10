import { Hono } from 'hono'
import pool from '../db'

const calendar = new Hono()

calendar.get('/', async (c) => {
  const from = c.req.query('from')
  const to = c.req.query('to')

  if (!from || !to) {
    return c.json({ error: 'from and to query params required' }, 400)
  }

  const result = await pool.query(
    `SELECT
      date::text,
      is_trading_day,
      holiday_name,
      pre_open_start::text,
      exchange_open::text,
      exchange_close::text,
      post_close_end::text,
      is_muhurat,
      notes
    FROM calendar
    WHERE date >= $1::date AND date < $2::date
    ORDER BY date`,
    [from, to]
  )

  return c.json(result.rows)
})

calendar.get('/upcoming-holidays', async (c) => {
  const today = new Date().toISOString().slice(0, 10)
  const result = await pool.query(
    `SELECT
      date::text,
      is_trading_day,
      holiday_name,
      pre_open_start::text,
      exchange_open::text,
      exchange_close::text,
      post_close_end::text,
      is_muhurat,
      notes
    FROM calendar
    WHERE date >= $1::date
      AND holiday_name IS NOT NULL
      AND holiday_name != 'Weekend'
    ORDER BY date
    LIMIT 5`,
    [today]
  )
  return c.json(result.rows)
})

export default calendar
