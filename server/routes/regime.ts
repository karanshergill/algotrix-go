import { Hono } from 'hono'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import pool from '../db'

const execFileAsync = promisify(execFile)
const CLASSIFIER_DIR = path.resolve(__dirname, '..', '..', 'regime-classifier')

const regime = new Hono()

async function runCliCommand(args: string[]): Promise<any> {
  const { stdout } = await execFileAsync('python3', ['cli.py', ...args], {
    cwd: CLASSIFIER_DIR,
    env: { ...process.env, PGPASSWORD: 'algotrix' },
    timeout: 120000,
  })
  // Extract JSON from output (skip log lines)
  const lines = stdout.trim().split('\n')
  const jsonLines = lines.filter(l => l.startsWith('{') || l.startsWith('['))
  const jsonStr = jsonLines.join('\n') || lines[lines.length - 1]
  return JSON.parse(jsonStr)
}

// GET /api/regime/today — current regime score
regime.get('/today', async (c) => {
  try {
    const date = c.req.query('date') || new Date().toISOString().slice(0, 10)

    // Try DB first (regime_daily table)
    const dbResult = await pool.query(
      'SELECT * FROM regime_daily WHERE date = $1',
      [date]
    )
    if (dbResult.rows.length > 0) {
      const row = dbResult.rows[0]
      return c.json({
        date: row.date,
        scores: {
          volatility: row.vol_score,
          trend: row.trend_score,
          participation: row.participation_score,
          sentiment: row.sentiment_score,
          institutional_flow: row.institutional_flow_score,
        },
        composite_score: row.composite_score,
        regime_label: row.regime_label,
        predicted_next_label: row.predicted_next_label,
        predicted_confidence: row.predicted_confidence,
        availability_regime: row.availability_regime,
        source: 'db',
      })
    }

    // Fall back to live computation
    const result = await runCliCommand(['regime', 'score', '--date', date])
    return c.json({ ...result, source: 'computed' })
  } catch (err: any) {
    return c.json({ error: err.message || 'Failed to compute regime' }, 500)
  }
})

// GET /api/regime/predict — next-day prediction
regime.get('/predict', async (c) => {
  try {
    const date = c.req.query('date') || new Date().toISOString().slice(0, 10)
    const result = await runCliCommand(['regime', 'predict', '--date', date])
    return c.json(result)
  } catch (err: any) {
    return c.json({ error: err.message || 'Failed to predict regime' }, 500)
  }
})

// GET /api/regime/history — historical regime data for charts
regime.get('/history', async (c) => {
  try {
    const days = parseInt(c.req.query('days') || '90')
    const result = await pool.query(
      `SELECT date, composite_score, regime_label, predicted_label,
              vol_score, trend_score, participation_score,
              sentiment_score, institutional_flow_score,
              availability_regime
       FROM regime_backtest
       ORDER BY date DESC
       LIMIT $1`,
      [days]
    )
    return c.json(result.rows.reverse())
  } catch (err: any) {
    return c.json({ error: err.message }, 500)
  }
})

// POST /api/regime/run-daily — trigger daily regime scoring
regime.post('/run-daily', async (c) => {
  try {
    const body = await c.req.json().catch(() => ({}))
    const date = body.date || new Date().toISOString().slice(0, 10)
    const result = await runCliCommand(['regime', 'daily', '--date', date])
    return c.json(result)
  } catch (err: any) {
    return c.json({ error: err.message || 'Failed to run daily regime' }, 500)
  }
})

export default regime
