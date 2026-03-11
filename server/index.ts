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


const port = 3001
console.log(`API server running on http://localhost:${port}`)
serve({ fetch: app.fetch, port })
