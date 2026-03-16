import { Hono } from 'hono'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import pool from '../db'

const execFileAsync = promisify(execFile)
const ENGINE_DIR = path.resolve(__dirname, '..', '..', 'engine')
const ENGINE_BIN = path.join(ENGINE_DIR, 'algotrix')

const watchlist = new Hono()

type WatchlistRow = {
  id: number
  name: string
  type: string | null
  description: string | null
  is_active: boolean
  last_rebuilt_at: string | null
  created_at: string
  updated_at: string
  item_count: string
}

type WatchlistIsinRow = {
  isin: string
  tier: string
  source: string
  score: number | null
  rank: number | null
  metadata: unknown
  added_at: string
  expires_at: string | null
  symbol: string | null
  name: string | null
  sector_macro: string | null
  fy_symbol: string | null
  is_fno: boolean | null
}

type FySymbolRow = {
  fy_symbol: string
}

type InsertedRow = {
  watchlist_id: number
  isin: string
  tier: string
  source: string
  score: number | null
  rank: number | null
  metadata: unknown
  added_at: string
  expires_at: string | null
}

watchlist.get('/build-report', async (c) => {
  const lookback = c.req.query('lookback') ?? '30'
  const fnoOnly = c.req.query('fnoOnly') === 'true'
  const madtvFloor = c.req.query('madtvFloor')

  const weights = c.req.query('weights')

  const args = ['watchlist', 'build', '--json', '--lookback', lookback]
  if (fnoOnly) args.push('--fno-only')
  if (madtvFloor) args.push('--madtv-floor', madtvFloor)
  if (weights) args.push('--weights', weights)

  try {
    const { stdout } = await execFileAsync(ENGINE_BIN, args, {
      cwd: ENGINE_DIR,
      timeout: 30_000,
      maxBuffer: 10 * 1024 * 1024,
    })
    return c.json(JSON.parse(stdout))
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Engine failed'
    return c.json({ error: message }, 500)
  }
})

watchlist.get('/explain', async (c) => {
  const symbol = c.req.query('symbol')
  if (!symbol) return c.json({ error: 'symbol query param is required' }, 400)

  const lookback = c.req.query('lookback') ?? '30'
  const args = ['watchlist', 'explain', '--symbol', symbol, '--json', '--lookback', lookback]

  try {
    const { stdout } = await execFileAsync(ENGINE_BIN, args, {
      cwd: ENGINE_DIR,
      timeout: 30_000,
      maxBuffer: 10 * 1024 * 1024,
    })
    return c.json(JSON.parse(stdout))
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Engine failed'
    return c.json({ error: message }, 500)
  }
})

watchlist.get('/', async (c) => {
  const result = await pool.query<WatchlistRow>(`
    SELECT
      w.*,
      count(wi.isin)::text AS item_count
    FROM watchlists w
    LEFT JOIN watchlist_isins wi ON wi.watchlist_id = w.id
    GROUP BY w.id
    ORDER BY w.name
  `)

  return c.json(
    result.rows.map((row) => ({
      ...row,
      item_count: Number(row.item_count),
    }))
  )
})

watchlist.get('/:id', async (c) => {
  const id = c.req.param('id')

  const result = await pool.query<WatchlistRow>(
    `
    SELECT
      w.*,
      count(wi.isin)::text AS item_count
    FROM watchlists w
    LEFT JOIN watchlist_isins wi ON wi.watchlist_id = w.id
    WHERE w.id = $1
    GROUP BY w.id
    `,
    [id]
  )

  const row = result.rows[0]
  if (!row) {
    return c.json({ error: 'Watchlist not found' }, 404)
  }

  return c.json({
    ...row,
    item_count: Number(row.item_count),
  })
})

watchlist.get('/:id/isins', async (c) => {
  const id = c.req.param('id')
  const tier = c.req.query('tier')

  const params: (string | number)[] = [id]
  let tierFilter = ''
  if (tier) {
    tierFilter = ' AND wi.tier = $2'
    params.push(tier)
  }

  const result = await pool.query<WatchlistIsinRow>(
    `
    SELECT
      wi.isin,
      wi.tier,
      wi.source,
      wi.score,
      wi.rank,
      wi.metadata,
      wi.added_at,
      wi.expires_at,
      s.symbol,
      s.name,
      s.sector_macro,
      s.fy_symbol,
      s.is_fno
    FROM watchlist_isins wi
    LEFT JOIN symbols s ON s.isin = wi.isin
    WHERE wi.watchlist_id = $1${tierFilter}
    ORDER BY wi.score DESC NULLS LAST
    `,
    params
  )

  return c.json(result.rows)
})

watchlist.get('/:id/symbols', async (c) => {
  const id = c.req.param('id')

  const result = await pool.query<FySymbolRow>(
    `
    SELECT s.fy_symbol
    FROM watchlist_isins wi
    JOIN symbols s ON s.isin = wi.isin
    WHERE wi.watchlist_id = $1
      AND s.fy_symbol IS NOT NULL
    ORDER BY s.fy_symbol
    `,
    [id]
  )

  return c.json(result.rows.map((row) => row.fy_symbol))
})

watchlist.post('/:id/isins', async (c) => {
  const id = c.req.param('id')
  const body = await c.req.json<{ isin: string; tier?: string; source?: string }>()

  if (!body.isin) {
    return c.json({ error: 'isin is required' }, 400)
  }

  const result = await pool.query<InsertedRow>(
    `
    INSERT INTO watchlist_isins (watchlist_id, isin, tier, source)
    VALUES ($1, $2, $3, $4)
    RETURNING *
    `,
    [id, body.isin, body.tier ?? 'base', body.source ?? 'manual']
  )

  return c.json(result.rows[0], 201)
})

watchlist.delete('/:id/isins/:isin', async (c) => {
  const id = c.req.param('id')
  const isin = c.req.param('isin')

  await pool.query(
    `DELETE FROM watchlist_isins WHERE watchlist_id = $1 AND isin = $2`,
    [id, isin]
  )

  return c.body(null, 204)
})

export default watchlist
