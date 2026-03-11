import { serve } from '@hono/node-server'
import { Hono } from 'hono'
import { cors } from 'hono/cors'
import auth from './routes/auth'
import calendar from './routes/calendar'
import feed from './routes/feed'
import indices from './routes/indices'
import ohlcv from './routes/ohlcv'
import symbols from './routes/symbols'

const app = new Hono()

app.use('/api/*', cors({ origin: '*' }))
app.route('/api/auth', auth)
app.route('/api/calendar', calendar)
app.route('/api/feed', feed)
app.route('/api/indices', indices)
app.route('/api/ohlcv', ohlcv)
app.route('/api/symbols', symbols)

app.get('/api/health', (c) => c.json({ status: 'ok' }))

// QuestDB proxy — forward /api/questdb/* to localhost:9000
app.all('/api/questdb/*', async (c) => {
  const path = c.req.path.replace('/api/questdb', '')
  const search = new URL(c.req.url).search
  const url = `http://localhost:9000${path}${search}`
  const res = await fetch(url, { method: c.req.method, body: c.req.raw.body })
  const body = await res.text()
  return new Response(body, {
    status: res.status,
    headers: { 'Content-Type': res.headers.get('Content-Type') ?? 'application/json' },
  })
})

const port = 3001
console.log(`API server running on http://localhost:${port}`)
serve({ fetch: app.fetch, port })
