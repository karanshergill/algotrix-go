import { Hono } from 'hono'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import pool from '../db'

const router = new Hono()

const TOKEN_PATH = path.resolve(process.cwd(), 'engine/token.json')

// GET /api/indices
router.get('/', async (c) => {
  const result = await pool.query(
    `SELECT id, symbol, name, fy_symbol, category, is_active
     FROM indices
     ORDER BY category, name`
  )
  return c.json(result.rows)
})

// GET /api/indices/active — just fy_symbol list for feed subscription
router.get('/active', async (c) => {
  const result = await pool.query(
    `SELECT fy_symbol FROM indices WHERE is_active = true ORDER BY category, name`
  )
  return c.json(result.rows.map((r: { fy_symbol: string }) => r.fy_symbol))
})

// GET /api/indices/quotes?symbols=NSE:NIFTY50-INDEX,NSE:BANKNIFTY-INDEX,...
// Single Fyers /data/quotes call — returns ltp, ch, chp for each symbol
router.get('/quotes', async (c) => {
  const symbolsParam = c.req.query('symbols')
  if (!symbolsParam) {
    return c.json({ error: 'symbols query param required' }, 400)
  }

  // Read access token
  let accessToken: string
  try {
    const raw = await readFile(TOKEN_PATH, 'utf-8')
    const tokenFile = JSON.parse(raw) as { access_token: string }
    accessToken = tokenFile.access_token
  } catch {
    return c.json({ error: 'token not available' }, 503)
  }

  // Call Fyers quotes API
  const symbols = symbolsParam.split(',').map((s) => s.trim()).filter(Boolean)
  const url = `https://api-t1.fyers.in/data/quotes?symbols=${encodeURIComponent(symbols.join(','))}`

  const res = await fetch(url, {
    headers: { Authorization: accessToken },
  })

  if (!res.ok) {
    return c.json({ error: `Fyers API error: ${res.status}` }, 502)
  }

  const data = await res.json() as {
    s: string
    d?: Array<{
      n: string  // name
      v: {
        symbol: string
        lp: number   // last price
        ch: number   // change
        chp: number  // change %
        open_price: number
        high_price: number
        low_price: number
        prev_close_price: number
      }
    }>
    message?: string
  }

  if (data.s !== 'ok' || !data.d) {
    return c.json({ error: data.message ?? 'Fyers error' }, 502)
  }

  const quotes = data.d.map((item) => ({
    symbol: item.v.symbol,
    ltp: item.v.lp,
    ch: item.v.ch,
    chp: item.v.chp,
    open: item.v.open_price,
    high: item.v.high_price,
    low: item.v.low_price,
    prevClose: item.v.prev_close_price,
  }))

  return c.json(quotes)
})

export default router
