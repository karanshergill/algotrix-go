import { serve } from '@hono/node-server'
import { Hono } from 'hono'
import { cors } from 'hono/cors'
import symbols from './routes/symbols'

const app = new Hono()

app.use('/api/*', cors({ origin: '*' }))
app.route('/api/symbols', symbols)

app.get('/api/health', (c) => c.json({ status: 'ok' }))

const port = 3001
console.log(`API server running on http://localhost:${port}`)
serve({ fetch: app.fetch, port })
