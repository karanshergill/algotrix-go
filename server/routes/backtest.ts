import { Hono } from 'hono'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import pool from '../db'

const execFileAsync = promisify(execFile)
const ENGINE_DIR = path.resolve(__dirname, '..', '..', 'engine')
const ENGINE_BIN = path.join(ENGINE_DIR, 'algotrix')

const backtest = new Hono()

// Types for engine JSON output
type EnginePick = {
  isin: string
  rank: number
  score: number
  open: number
  high: number
  low: number
  close: number
  max_opp: number
  oc_return: number
  range_pct: number
}

type EngineDateResult = {
  build_date: string
  horizon: number
  metrics: Record<string, number>
  benchmark: Record<string, number>
  picks: EnginePick[]
}

type EngineSummary = {
  avg_max_opp: number
  avg_oc_ret: number
  avg_range: number
  avg_hit_rate: number
  edge_max_opp: number
  edge_range: number
  win_count: number
  total_count: number
}

type EngineOutput = {
  config: Record<string, number>
  dates: EngineDateResult[]
  summary: Record<string, EngineSummary>
}

// POST /api/backtests/run — run a new backtest
backtest.post('/run', async (c) => {
  const body = await c.req.json<{
    type?: string
    name?: string
    config?: { top_n?: number; step?: number; min_mcap?: number; max_mcap?: number }
  }>()

  const type = body.type ?? 'builder'
  const name = body.name ?? `Builder Backtest`
  const topN = body.config?.top_n ?? 25
  const step = body.config?.step ?? 1
  const minMcap = body.config?.min_mcap ?? 0
  const maxMcap = body.config?.max_mcap ?? 0

  // Create run record
  const runResult = await pool.query(
    `INSERT INTO backtest_runs (type, name, config, status, created_by)
     VALUES ($1, $2, $3, 'running', 'manual')
     RETURNING id, started_at`,
    [type, name, JSON.stringify({ top_n: topN, step, min_mcap: minMcap, max_mcap: maxMcap })]
  )
  const runId = runResult.rows[0].id

  try {
    // Run engine
    const args = ['backtest', '--json', '--top', String(topN), '--step', String(step)]
    if (minMcap > 0) args.push('--min-mcap', String(minMcap))
    if (maxMcap > 0) args.push('--max-mcap', String(maxMcap))
    const { stdout } = await execFileAsync(ENGINE_BIN, args, {
      cwd: ENGINE_DIR,
      timeout: 300_000,
      maxBuffer: 50 * 1024 * 1024,
    })

    const output: EngineOutput = JSON.parse(stdout)

    // Persist date results and picks
    let totalDates = 0
    const uniqueDates = new Set<string>()

    for (const dr of output.dates) {
      uniqueDates.add(dr.build_date)

      const dateResult = await pool.query(
        `INSERT INTO backtest_date_results (run_id, build_date, horizon, metrics, benchmark)
         VALUES ($1, $2, $3, $4, $5)
         RETURNING id`,
        [runId, dr.build_date, dr.horizon, JSON.stringify(dr.metrics), JSON.stringify(dr.benchmark)]
      )
      const dateResultId = dateResult.rows[0].id

      // Insert picks in batch
      if (dr.picks && dr.picks.length > 0) {
        const values: unknown[] = []
        const placeholders: string[] = []
        let idx = 1

        for (const pick of dr.picks) {
          placeholders.push(
            `($${idx}, $${idx + 1}, $${idx + 2}, $${idx + 3}, $${idx + 4}, $${idx + 5}, $${idx + 6}, $${idx + 7}, $${idx + 8}, $${idx + 9}, $${idx + 10})`
          )
          values.push(
            dateResultId, pick.isin, pick.rank, pick.score,
            pick.open, pick.high, pick.low, pick.close,
            pick.max_opp, pick.oc_return, pick.range_pct
          )
          idx += 11
        }

        await pool.query(
          `INSERT INTO backtest_picks (date_result_id, isin, rank, score, open_price, high_price, low_price, close_price, max_opp, oc_return, range_pct)
           VALUES ${placeholders.join(', ')}`,
          values
        )
      }
    }
    totalDates = uniqueDates.size

    // Update run as completed
    await pool.query(
      `UPDATE backtest_runs
       SET status = 'completed', summary = $1, build_dates_tested = $2, completed_at = NOW()
       WHERE id = $3`,
      [JSON.stringify(output.summary), totalDates, runId]
    )

    // Return the completed run
    const run = await pool.query(`SELECT * FROM backtest_runs WHERE id = $1`, [runId])
    return c.json(run.rows[0])
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Engine failed'
    await pool.query(
      `UPDATE backtest_runs SET status = 'failed', completed_at = NOW() WHERE id = $1`,
      [runId]
    )
    return c.json({ error: message, run_id: runId }, 500)
  }
})

// GET /api/backtests — list all runs
backtest.get('/', async (c) => {
  const result = await pool.query(
    `SELECT id, type, name, config, status, summary, build_dates_tested, started_at, completed_at, created_by
     FROM backtest_runs
     ORDER BY started_at DESC`
  )
  return c.json(result.rows)
})

// GET /api/backtests/:id — single run with date results + picks
backtest.get('/:id', async (c) => {
  const id = c.req.param('id')

  const runResult = await pool.query(`SELECT * FROM backtest_runs WHERE id = $1`, [id])
  if (runResult.rows.length === 0) {
    return c.json({ error: 'Backtest run not found' }, 404)
  }

  const dateResults = await pool.query(
    `SELECT id, build_date, horizon, metrics, benchmark
     FROM backtest_date_results
     WHERE run_id = $1
     ORDER BY build_date, horizon`,
    [id]
  )

  // Build symbol lookup once (isin → symbol name)
  const symbolRows = await pool.query(
    `SELECT isin, symbol FROM symbols WHERE status = 'active'`
  )
  const symbolMap = new Map<string, string>(
    symbolRows.rows.map((r: { isin: string; symbol: string }) => [r.isin, r.symbol])
  )

  // Fetch all picks for this run's date results in one query
  const dateResultIds = dateResults.rows.map((r: { id: number }) => r.id)
  let picksMap = new Map<number, unknown[]>()

  if (dateResultIds.length > 0) {
    const picksResult = await pool.query(
      `SELECT date_result_id, isin, rank, score,
              open_price, high_price, low_price, close_price,
              max_opp, oc_return
       FROM backtest_picks
       WHERE date_result_id = ANY($1)
       ORDER BY date_result_id, rank ASC`,
      [dateResultIds]
    )

    for (const pick of picksResult.rows) {
      const picks = picksMap.get(pick.date_result_id) ?? []
      picks.push({
        symbol: symbolMap.get(pick.isin) ?? pick.isin,
        isin: pick.isin,
        rank: pick.rank,
        score: pick.score,
        open_price: pick.open_price,
        high_price: pick.high_price,
        low_price: pick.low_price,
        close_price: pick.close_price,
        max_opp: pick.max_opp,
        oc_return: pick.oc_return,
      })
      picksMap.set(pick.date_result_id, picks)
    }
  }

  // Attach picks to each date result
  const dateResultsWithPicks = dateResults.rows.map((r: { id: number }) => ({
    ...r,
    picks: picksMap.get(r.id) ?? [],
  }))

  return c.json({
    ...runResult.rows[0],
    date_results: dateResultsWithPicks,
  })
})

// DELETE /api/backtests/:id — delete run (cascades to results + picks)
backtest.delete('/:id', async (c) => {
  const id = c.req.param('id')
  const result = await pool.query(`DELETE FROM backtest_runs WHERE id = $1 RETURNING id`, [id])
  if (result.rows.length === 0) {
    return c.json({ error: 'Backtest run not found' }, 404)
  }
  return c.body(null, 204)
})

export default backtest
