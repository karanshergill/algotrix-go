import { Hono } from 'hono'
import pool from '../db'

const news = new Hono()

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

function todayIST(): string {
  return new Date().toLocaleString('en-CA', { timeZone: 'Asia/Kolkata' }).slice(0, 10)
}

// GET / — Feed: announcements + block deals for a date
news.get('/', async (c) => {
  const date = c.req.query('date') ?? todayIST()
  if (!DATE_RE.test(date)) return c.json({ error: 'Invalid date format' }, 400)

  const source = c.req.query('source')
  const symbol = c.req.query('symbol')
  const marketMoving = c.req.query('market_moving') === 'true'
  const limit = Math.min(parseInt(c.req.query('limit') || '50', 10), 200)
  const offset = parseInt(c.req.query('offset') || '0', 10)

  const params: unknown[] = []
  let idx = 1

  params.push(date)
  const dateIdx = idx++

  let symbolIdx = 0
  if (symbol) {
    params.push(symbol)
    symbolIdx = idx++
  }

  const symbolClause = symbol ? `AND symbol = $${symbolIdx}` : ''
  const mmClause = marketMoving ? 'AND is_market_moving = true' : ''

  const parts: string[] = []

  if (!source || source === 'announcements') {
    parts.push(`
      SELECT id, symbol, 'announcement' AS source,
             announcement_dt AS timestamp, description AS title,
             category, is_market_moving, attachment_url,
             NULL::numeric AS traded_volume, NULL::numeric AS price, NULL::numeric AS traded_value
      FROM nse_announcements
      WHERE announcement_dt >= $${dateIdx}::date
        AND announcement_dt < $${dateIdx}::date + interval '1 day'
        ${symbolClause}
        ${mmClause}
    `)
  }

  if (!source || source === 'block_deals') {
    parts.push(`
      SELECT id, symbol, 'block_deal' AS source,
             deal_date::timestamp AS timestamp, NULL AS title,
             NULL AS category, false AS is_market_moving, NULL AS attachment_url,
             traded_volume, price, traded_value
      FROM nse_block_deals
      WHERE deal_date = $${dateIdx}::date
        ${symbolClause}
    `)
  }

  if (parts.length === 0) {
    return c.json({ items: [], has_more: false })
  }

  params.push(limit)
  const limitIdx = idx++
  params.push(offset)
  const offsetIdx = idx++

  const sql = `
    SELECT * FROM (
      ${parts.join(' UNION ALL ')}
    ) combined
    ORDER BY timestamp DESC
    LIMIT $${limitIdx} + 1 OFFSET $${offsetIdx}
  `

  const result = await pool.query(sql, params)
  const hasMore = result.rows.length > limit
  const items = hasMore ? result.rows.slice(0, limit) : result.rows

  return c.json({ items, has_more: hasMore })
})

// GET /summary — counts for summary cards
news.get('/summary', async (c) => {
  const date = c.req.query('date') ?? todayIST()
  if (!DATE_RE.test(date)) return c.json({ error: 'Invalid date format' }, 400)

  const result = await pool.query(
    `WITH ann AS (
       SELECT COUNT(*)::int AS total,
              COUNT(*) FILTER (WHERE is_market_moving)::int AS market_moving
       FROM nse_announcements
       WHERE announcement_dt >= $1::date
         AND announcement_dt < $1::date + interval '1 day'
     ),
     blocks AS (
       SELECT COUNT(*)::int AS total FROM nse_block_deals WHERE deal_date = $1::date
     ),
     meetings AS (
       SELECT COUNT(*)::int AS total FROM nse_board_meetings
       WHERE meeting_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
     ),
     actions AS (
       SELECT COUNT(*)::int AS total FROM nse_corporate_actions
       WHERE ex_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
     )
     SELECT ann.total AS announcements, ann.market_moving,
            blocks.total AS block_deals,
            meetings.total AS upcoming_meetings,
            actions.total AS upcoming_actions
     FROM ann, blocks, meetings, actions`,
    [date]
  )

  return c.json(result.rows[0])
})

// GET /upcoming — future board meetings + corporate actions
news.get('/upcoming', async (c) => {
  const symbol = c.req.query('symbol')
  const limit = Math.min(parseInt(c.req.query('limit') || '100', 10), 200)

  const meetingParams: unknown[] = []
  const actionParams: unknown[] = []
  let mIdx = 1
  let aIdx = 1

  let meetingSymbolClause = ''
  if (symbol) {
    meetingParams.push(symbol)
    meetingSymbolClause = `AND symbol = $${mIdx++}`
  }
  meetingParams.push(limit)
  const meetingLimitIdx = mIdx++

  let actionSymbolClause = ''
  if (symbol) {
    actionParams.push(symbol)
    actionSymbolClause = `AND symbol = $${aIdx++}`
  }
  actionParams.push(limit)
  const actionLimitIdx = aIdx++

  const [meetings, actions] = await Promise.all([
    pool.query(
      `SELECT id, symbol, 'board_meeting' AS source, meeting_date, purpose, description
       FROM nse_board_meetings
       WHERE meeting_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
         ${meetingSymbolClause}
       ORDER BY meeting_date ASC
       LIMIT $${meetingLimitIdx}`,
      meetingParams
    ),
    pool.query(
      `SELECT id, symbol, 'corporate_action' AS source, ex_date, record_date, subject
       FROM nse_corporate_actions
       WHERE ex_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
         ${actionSymbolClause}
       ORDER BY ex_date ASC
       LIMIT $${actionLimitIdx}`,
      actionParams
    ),
  ])

  return c.json({ meetings: meetings.rows, actions: actions.rows })
})

// GET /insider-activity — aggregated insider data or per-symbol drill-down
news.get('/insider-activity', async (c) => {
  const ALLOWED_DAYS = [7, 30, 90]
  const days = parseInt(c.req.query('days') || '7', 10)
  if (!ALLOWED_DAYS.includes(days)) {
    return c.json({ error: 'days must be 7, 30, or 90' }, 400)
  }

  const symbol = c.req.query('symbol')
  const limit = Math.min(parseInt(c.req.query('limit') || '20', 10), 100)
  const offset = parseInt(c.req.query('offset') || '0', 10)

  if (symbol) {
    // Drill-down mode
    const result = await pool.query(
      `SELECT id, acquirer_name, acquisition_mode, shares_acquired, value, transaction_date
       FROM nse_insider_trading
       WHERE symbol = $1
         AND transaction_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date - $2::int
       ORDER BY transaction_date DESC, value DESC
       LIMIT $3 + 1 OFFSET $4`,
      [symbol, days, limit, offset]
    )

    const hasMore = result.rows.length > limit
    const transactions = hasMore ? result.rows.slice(0, limit) : result.rows

    return c.json({ transactions, has_more: hasMore })
  }

  // Aggregated mode
  const result = await pool.query(
    `SELECT symbol,
            SUM(CASE WHEN acquisition_mode IN (
              'Market Purchase', 'Off Market - Loss of Securities',
              'Purchase', 'Allotment', 'ESOP', 'Inter-se Transfer - Acquisition'
            ) THEN value ELSE 0 END) AS buy_value,
            SUM(CASE WHEN acquisition_mode IN (
              'Market Sale', 'Sale', 'Off Market - Invocation of Pledge',
              'Disposal', 'Inter-se Transfer - Disposal'
            ) THEN value ELSE 0 END) AS sell_value,
            SUM(CASE WHEN acquisition_mode IN (
              'Market Purchase', 'Off Market - Loss of Securities',
              'Purchase', 'Allotment', 'ESOP', 'Inter-se Transfer - Acquisition'
            ) THEN value ELSE -value END) AS net_value,
            COUNT(*) AS txn_count
     FROM nse_insider_trading
     WHERE transaction_date >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date - $1::int
       AND value IS NOT NULL AND value > 0
     GROUP BY symbol
     ORDER BY ABS(SUM(CASE WHEN acquisition_mode IN (
       'Market Purchase', 'Off Market - Loss of Securities',
       'Purchase', 'Allotment', 'ESOP', 'Inter-se Transfer - Acquisition'
     ) THEN value ELSE -value END)) DESC
     LIMIT $2`,
    [days, limit]
  )

  const topBuyers = result.rows
    .filter((r: { net_value: number }) => Number(r.net_value) > 0)
    .map((r: { net_value: string; buy_value: string; sell_value: string; txn_count: string }) => ({
      ...r,
      net_value: Number(r.net_value),
      buy_value: Number(r.buy_value),
      sell_value: Number(r.sell_value),
      txn_count: Number(r.txn_count),
    }))

  const topSellers = result.rows
    .filter((r: { net_value: number }) => Number(r.net_value) < 0)
    .map((r: { net_value: string; buy_value: string; sell_value: string; txn_count: string }) => ({
      ...r,
      net_value: Number(r.net_value),
      buy_value: Number(r.buy_value),
      sell_value: Number(r.sell_value),
      txn_count: Number(r.txn_count),
    }))

  return c.json({ top_buyers: topBuyers, top_sellers: topSellers, period_days: days })
})

// GET /:source/:id — detail (raw_json) for expandable row
const TABLE_MAP: Record<string, string> = {
  announcements: 'nse_announcements',
  block_deals: 'nse_block_deals',
  board_meetings: 'nse_board_meetings',
  corporate_actions: 'nse_corporate_actions',
  insider_trading: 'nse_insider_trading',
}

news.get('/:source/:id', async (c) => {
  const source = c.req.param('source')
  const id = parseInt(c.req.param('id'), 10)

  const table = TABLE_MAP[source]
  if (!table) return c.json({ error: 'Invalid source' }, 400)
  if (isNaN(id)) return c.json({ error: 'Invalid id' }, 400)

  const result = await pool.query(`SELECT raw_json FROM ${table} WHERE id = $1`, [id])
  if (result.rows.length === 0) return c.json({ error: 'Not found' }, 404)

  return c.json({ raw_json: result.rows[0].raw_json })
})

export default news
