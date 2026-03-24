import { Hono } from 'hono'
import pg from 'pg'

// Separate pool for algotrix database (signals live here, not in atdb)
const algotrixPool = new pg.Pool({
  host: 'localhost',
  port: 5432,
  user: 'me',
  password: 'algotrix',
  database: 'algotrix',
  max: 5,
})

const signals = new Hono()

// GET / — list signals for a date, with optional screener/type filters
signals.get('/', async (c) => {
  const date = c.req.query('date') ?? new Date().toISOString().slice(0, 10)
  const screener = c.req.query('screener')
  const type = c.req.query('type')

  const conditions = ['session_date = $1']
  const params: unknown[] = [date]
  let idx = 2

  if (screener) {
    conditions.push(`screener_name = $${idx}`)
    params.push(screener)
    idx++
  }
  if (type) {
    conditions.push(`signal_type = $${idx}`)
    params.push(type)
    idx++
  }

  const limit = c.req.query('limit')
  let limitClause = ''
  if (limit) {
    limitClause = ` LIMIT $${idx}`
    params.push(parseInt(limit, 10))
    idx++
  }

  const where = conditions.join(' AND ')
  const result = await algotrixPool.query(
    `SELECT id, session_date, triggered_at, screener_name, security_id,
            trading_symbol, signal_type, trigger_price, threshold_price,
            ltp, percent_above, metadata, trigger_values
     FROM signals
     WHERE ${where}
     ORDER BY triggered_at DESC${limitClause}`,
    params
  )

  return c.json(result.rows)
})

// GET /summary — count by screener for a date
signals.get('/summary', async (c) => {
  const date = c.req.query('date') ?? new Date().toISOString().slice(0, 10)

  const result = await algotrixPool.query(
    `SELECT screener_name, COUNT(*)::int AS count
     FROM signals
     WHERE session_date = $1
     GROUP BY screener_name
     ORDER BY count DESC`,
    [date]
  )

  return c.json(result.rows)
})

export default signals
