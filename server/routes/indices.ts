import { Hono } from 'hono'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import pool from '../db'
import { state as feedState } from './feed'

const router = new Hono()

const TOKEN_PATH = path.resolve(process.cwd(), 'engine/token.json')

// In-memory cache of last known quotes — returned when market is closed and feed is off
const quoteCache = new Map<string, {
  symbol: string; ltp: number; ch: number; chp: number
  open: number; high: number; low: number; prevClose: number
}>()

function isMarketOpen(): boolean {
  const now = new Date()
  const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
  const day = ist.getDay() // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false
  const t = ist.getHours() * 60 + ist.getMinutes()
  return t >= 9 * 60 + 15 && t <= 15 * 60 + 30
}

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

// GET /api/indices/quotes?symbols=NSE:NIFTY50-INDEX,...
// Source routing:
//   feed connected  → query atdb nse_cm_ticks (latest row per symbol, real-time)
//   feed off + market open → call Fyers REST API
//   feed off + market closed → return cached last known values
router.get('/quotes', async (c) => {
  const symbolsParam = c.req.query('symbols')
  if (!symbolsParam) return c.json({ error: 'symbols query param required' }, 400)

  const symbols = symbolsParam.split(',').map((s) => s.trim()).filter(Boolean)

  // --- Source: live DB (feed connected) ---
  if (feedState.status === 'connected') {
    const result = await pool.query<{
      symbol: string; ltp: number; change: number; change_pct: number
      open: number; high: number; low: number; prev_close: number
    }>(
      `SELECT DISTINCT ON (isin) isin AS symbol, ltp, "change", change_pct,
              open, high, low, prev_close
       FROM nse_cm_ticks
       WHERE isin = ANY($1)
       ORDER BY isin, timestamp DESC`,
      [symbols]
    )
    if (result.rows.length > 0) {
      const quotes = result.rows.map((r) => ({
        symbol: r.symbol,
        ltp: r.ltp,
        ch: r.change,
        chp: r.change_pct,
        open: r.open,
        high: r.high,
        low: r.low,
        prevClose: r.prev_close,
      }))
      quotes.forEach((q) => quoteCache.set(q.symbol, q))
      return c.json(quotes)
    }
    // DB empty — fall through to Fyers REST
  }

  // --- Source: Fyers REST (market open, or feed DB empty) ---
  if (isMarketOpen() || feedState.status === 'connected') {
    let accessToken: string
    try {
      const raw = await readFile(TOKEN_PATH, 'utf-8')
      accessToken = (JSON.parse(raw) as { access_token: string }).access_token
    } catch {
      // Token unavailable — fall through to cache
      return c.json([...quoteCache.values()].filter((q) => symbols.includes(q.symbol)))
    }

    const url = `https://api-t1.fyers.in/data/quotes?symbols=${encodeURIComponent(symbols.join(','))}`
    const res = await fetch(url, { headers: { Authorization: accessToken } })

    if (res.ok) {
      const data = await res.json() as {
        s: string
        d?: Array<{ v: {
          symbol: string; lp: number; ch: number; chp: number
          open_price: number; high_price: number; low_price: number; prev_close_price: number
        }}>
      }
      if (data.s === 'ok' && data.d) {
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
        quotes.forEach((q) => quoteCache.set(q.symbol, q))
        return c.json(quotes)
      }
    }
  }

  // --- Source: cache (market closed or all else failed) ---
  return c.json([...quoteCache.values()].filter((q) => symbols.includes(q.symbol)))
})

export default router
