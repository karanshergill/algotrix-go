import { Hono } from 'hono'

const GO_FEED = 'http://127.0.0.1:3003'

const app = new Hono()

// GET /api/prices?isins=INE009A01021,INE002A01018,...
// Returns { "INE009A01021": 1287.9, "INE002A01018": 345.6 }
app.get('/', async (c) => {
  const isinsParam = c.req.query('isins')
  if (!isinsParam) return c.json({})

  const isins = isinsParam.split(',').filter(Boolean)
  if (isins.length === 0) return c.json({})

  try {
    // Fetch all features from go-feed (cached in memory, fast)
    const res = await fetch(`${GO_FEED}/features`)
    if (!res.ok) return c.json({}, 502)

    const all = await res.json() as Record<string, { LTP: number }>
    const prices: Record<string, number> = {}

    for (const isin of isins) {
      if (all[isin]?.LTP != null) {
        prices[isin] = all[isin].LTP
      }
    }

    return c.json(prices)
  } catch {
    return c.json({}, 502)
  }
})

export default app
