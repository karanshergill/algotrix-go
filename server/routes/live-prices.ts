import { Hono } from 'hono'

const GO_FEED_URL = 'http://127.0.0.1:3003'

const app = new Hono()

// Proxy to go-feed /features endpoint for live LTP data
app.get('/all', async (c) => {
  try {
    const res = await fetch(`${GO_FEED_URL}/features`, { signal: AbortSignal.timeout(3000) })
    if (!res.ok) return c.json({}, 502)
    const data = await res.json()
    return c.json(data)
  } catch {
    return c.json({}, 502)
  }
})

export default app
