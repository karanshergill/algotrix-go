import { Hono } from 'hono'
import { execSync } from 'node:child_process'
import { setFeedIntent } from './feed-ws'

const router = new Hono()

const HEALTHZ_URL = 'http://127.0.0.1:3003/healthz'
const HEALTHZ_TIMEOUT = 2000

async function fetchHealthz(): Promise<Record<string, unknown> | null> {
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), HEALTHZ_TIMEOUT)
    const res = await fetch(HEALTHZ_URL, { signal: controller.signal })
    clearTimeout(timer)
    if (!res.ok) return null
    return (await res.json()) as Record<string, unknown>
  } catch {
    return null
  }
}

// GET /api/feed/status
router.get('/status', async (c) => {
  const health = await fetchHealthz()

  if (health) {
    return c.json({
      status: 'connected',
      pid: health.pid,
      startedAt: null,
      symbolCount: health.stocks_registered,
      ticksLastMinute: health.ticks_last_minute,
      uptimeSeconds: health.uptime_seconds,
      featuresActive: health.features_active,
      lastTickAt: health.last_tick_at,
      memoryMb: health.memory_mb,
      lastError: null,
    })
  }

  return c.json({
    status: 'disconnected',
    pid: null,
    startedAt: null,
    symbolCount: 0,
    ticksLastMinute: 0,
    lastError: null,
  })
})

// POST /api/feed/start
router.post('/start', async (c) => {
  const health = await fetchHealthz()
  if (health) {
    return c.json({ error: 'Feed already running' }, 400)
  }

  try {
    execSync('pm2 start ecosystem.config.cjs --only go-feed', {
      cwd: '/home/me/projects/algotrix-go',
      timeout: 10_000,
    })
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    return c.json({ error: `PM2 start failed: ${msg}` }, 500)
  }

  setFeedIntent(true)
  return c.json({ started: true })
})

// POST /api/feed/stop
router.post('/stop', async (c) => {
  try {
    execSync('pm2 stop go-feed', { timeout: 10_000 })
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    return c.json({ error: `PM2 stop failed: ${msg}` }, 500)
  }

  setFeedIntent(false)
  return c.json({ stopped: true })
})

export default router
