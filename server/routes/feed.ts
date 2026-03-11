import { Hono } from 'hono'
import { spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'
import db from '../db'

const router = new Hono()

const ENGINE_PATH = path.resolve(process.cwd(), 'engine/algotrix')
const FEED_CONFIG = path.resolve(process.cwd(), 'engine/feed/config.yaml')

interface FeedState {
  proc: ChildProcess | null
  pid: number | null
  startedAt: string | null
  symbolCount: number
  status: 'disconnected' | 'connecting' | 'connected' | 'error'
  lastError: string | null
  ticksLastMinute: number
  tickTimestamps: number[] // rolling window for ticks/min
}

export const state: FeedState = {
  proc: null,
  pid: null,
  startedAt: null,
  symbolCount: 0,
  status: 'disconnected',
  lastError: null,
  ticksLastMinute: 0,
  tickTimestamps: [],
}

function recordTick() {
  const now = Date.now()
  state.tickTimestamps.push(now)
  // Keep only last 60s
  const cutoff = now - 60_000
  state.tickTimestamps = state.tickTimestamps.filter((t) => t > cutoff)
  state.ticksLastMinute = state.tickTimestamps.length
}

// GET /api/feed/status
router.get('/status', async (c) => {
  let ticksLastMinute = 0

  if (state.status === 'connected') {
    try {
      const result = await db.query(
        `SELECT COUNT(*) FROM nse_cm_ticks WHERE ts > NOW() - INTERVAL '1 minute'`
      )
      ticksLastMinute = parseInt(result.rows[0]?.count ?? '0', 10)
    } catch { /* db unreachable */ }
  }

  return c.json({
    status: state.status,
    pid: state.pid,
    startedAt: state.startedAt,
    symbolCount: state.symbolCount,
    ticksLastMinute,
    lastError: state.lastError,
  })
})

// POST /api/feed/start
router.post('/start', async (c) => {
  if (state.proc && state.status !== 'disconnected' && state.status !== 'error') {
    return c.json({ error: 'Feed already running' }, 400)
  }

  // Fetch all active equity fy_symbols + index fy_symbols
  const [equityRows, indexRows] = await Promise.all([
    db.query(`SELECT fy_symbol FROM symbols WHERE status = 'active' ORDER BY fy_symbol`),
    db.query(`SELECT fy_symbol FROM indices WHERE is_active = true ORDER BY fy_symbol`),
  ])

  const symbols = [
    ...equityRows.rows.map((r: { fy_symbol: string }) => r.fy_symbol),
    ...indexRows.rows.map((r: { fy_symbol: string }) => r.fy_symbol),
  ]

  if (symbols.length === 0) {
    return c.json({ error: 'No active symbols found' }, 400)
  }

  state.status = 'connecting'
  state.lastError = null
  state.symbolCount = symbols.length
  state.tickTimestamps = []
  state.ticksLastMinute = 0

  const proc = spawn(ENGINE_PATH, ['feed', '--symbols', symbols.join(','), '--config', FEED_CONFIG], {
    cwd: path.resolve(process.cwd(), 'engine'),
    env: process.env,
    detached: false,
  })

  state.proc = proc
  state.pid = proc.pid ?? null
  state.startedAt = new Date().toISOString()

  proc.stdout.on('data', (chunk: Buffer) => {
    const line = chunk.toString()
    if (
      line.includes('all feeds running') ||
      line.includes('websocket connected') ||
      line.includes('DataSocket] connected') ||
      line.includes('TBT] websocket connected')
    ) {
      state.status = 'connected'
    }
  })

  proc.stderr.on('data', (chunk: Buffer) => {
    const line = chunk.toString().trim()
    if (line) state.lastError = line
  })

  proc.on('close', (code) => {
    state.proc = null
    state.pid = null
    state.status = code === 0 ? 'disconnected' : 'error'
    if (code !== 0 && code !== null) {
      state.lastError = `Process exited with code ${code}`
    }
  })

  return c.json({ started: true, pid: proc.pid, symbolCount: symbols.length })
})

// POST /api/feed/stop
router.post('/stop', (c) => {
  if (!state.proc) {
    return c.json({ error: 'Feed not running' }, 400)
  }

  state.proc.kill('SIGTERM')
  state.status = 'disconnected'
  state.proc = null
  state.pid = null

  return c.json({ stopped: true })
})

export default router
