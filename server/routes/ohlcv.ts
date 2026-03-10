import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { Hono } from 'hono'
import pool from '../db'

const ohlcv = new Hono()

const RESOLUTIONS = ['1d', '1m', '5s'] as const

type Resolution = (typeof RESOLUTIONS)[number]
type CoverageStatus = 'full' | 'partial' | 'missing'
type JobStatus = 'running' | 'completed' | 'failed'

type ResolutionCounts = {
  count: number
  status: CoverageStatus
}

type DayStatus = Record<Resolution, ResolutionCounts>

type FetchJobState = {
  jobId: string
  status: JobStatus
  resolution: Resolution
  from: string
  to: string
  done: number
  total: number
  errors: number
  message?: string
  startedAt: string
  finishedAt?: string
}

type ProgressLine = {
  done: number
  total: number
  errors: number
}

type CountRow = {
  count: string
}

type DayCountRow = {
  day: string
  scrip_count: string
}

type SymbolRow = {
  isin: string
  symbol: string
  row_count: string
}

type ColumnRow = {
  column_name: string
}

type FetchRequestBody = {
  resolution: Resolution
  from: string
  to: string
}

const RESOLUTION_TABLES: Record<Resolution, string> = {
  '1d': 'nse_cm_ohlcv_1d',
  '1m': 'nse_cm_ohlcv_1m',
  '5s': 'nse_cm_ohlcv_5s',
}

const jobs = new Map<string, FetchJobState>()

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const engineDir = path.resolve(__dirname, '../../engine')

ohlcv.get('/status', async (c) => {
  const from = c.req.query('from')
  const to = c.req.query('to')

  if (!isValidDateInput(from) || !isValidDateInput(to)) {
    return c.json({ error: 'from and to are required in YYYY-MM-DD format' }, 400)
  }

  if (to < from) {
    return c.json({ error: 'to must be on or after from' }, 400)
  }

  try {
    const activePredicate = await resolveActiveSymbolPredicate()
    const totalSymbols = await getTotalSymbols(activePredicate)
    const days = buildDayMap(from, to)

    for (const resolution of RESOLUTIONS) {
      const counts = await getResolutionCounts(RESOLUTION_TABLES[resolution], from, to)
      for (const [day, count] of counts.entries()) {
        days[day][resolution] = {
          count,
          status: toCoverageStatus(count, totalSymbols),
        }
      }
    }

    return c.json({
      totalSymbols,
      days,
    })
  } catch (error) {
    return c.json(
      { error: error instanceof Error ? error.message : 'Failed to load OHLCV status' },
      500
    )
  }
})

ohlcv.get('/symbols', async (c) => {
  const date = c.req.query('date')
  const resolution = c.req.query('resolution')

  if (!isValidDateInput(date)) {
    return c.json({ error: 'date is required in YYYY-MM-DD format' }, 400)
  }

  if (!isResolution(resolution)) {
    return c.json({ error: 'resolution must be one of 1d, 1m, 5s' }, 400)
  }

  try {
    const activePredicate = await resolveActiveSymbolPredicate()
    const table = RESOLUTION_TABLES[resolution]

    const result = await pool.query<SymbolRow>(
      `
        SELECT
          s.isin,
          s.symbol,
          COUNT(o.isin) AS row_count
        FROM symbols s
        LEFT JOIN ${table} o
          ON o.isin = s.isin
         AND DATE(o.timestamp AT TIME ZONE 'Asia/Kolkata') = $1::date
        WHERE ${activePredicate}
        GROUP BY s.isin, s.symbol
        ORDER BY s.symbol
      `,
      [date]
    )

    return c.json({
      date,
      resolution,
      symbols: result.rows.map((row) => {
        const rowCount = Number(row.row_count)
        return {
          isin: row.isin,
          symbol: row.symbol,
          hasData: rowCount > 0,
          rowCount,
        }
      }),
    })
  } catch (error) {
    return c.json(
      { error: error instanceof Error ? error.message : 'Failed to load OHLCV symbols' },
      500
    )
  }
})

ohlcv.post('/fetch', async (c) => {
  let body: unknown

  try {
    body = await c.req.json()
  } catch {
    return c.json({ error: 'Invalid JSON body' }, 400)
  }

  if (!isFetchRequestBody(body)) {
    return c.json(
      { error: 'Body must include resolution, from, and to in YYYY-MM-DD format' },
      400
    )
  }

  if (body.to < body.from) {
    return c.json({ error: 'to must be on or after from' }, 400)
  }

  const jobId = randomUUID()
  const job: FetchJobState = {
    jobId,
    status: 'running',
    resolution: body.resolution,
    from: body.from,
    to: body.to,
    done: 0,
    total: 0,
    errors: 0,
    startedAt: new Date().toISOString(),
  }

  jobs.set(jobId, job)

  const child = spawn(
    './algotrix',
    ['ohlcv', '--resolution', body.resolution, '--from', body.from, '--to', body.to],
    {
      cwd: engineDir,
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  )

  let stdoutBuffer = ''
  let stderrBuffer = ''
  let lastMessage = ''

  child.stdout.setEncoding('utf8')
  child.stdout.on('data', (chunk: string) => {
    stdoutBuffer += chunk
    const lines = stdoutBuffer.split('\n')
    stdoutBuffer = lines.pop() ?? ''

    for (const line of lines) {
      const progress = parseProgressLine(line)
      if (!progress) {
        continue
      }

      updateJob(jobId, {
        done: progress.done,
        total: progress.total,
        errors: progress.errors,
      })
    }
  })

  child.stderr.setEncoding('utf8')
  child.stderr.on('data', (chunk: string) => {
    stderrBuffer += chunk
    const lines = stderrBuffer.split('\n')
    stderrBuffer = lines.pop() ?? ''

    for (const rawLine of lines) {
      const line = rawLine.trim()
      if (line.length > 0) {
        lastMessage = line
      }
    }
  })

  child.on('error', (error) => {
    updateJob(jobId, {
      status: 'failed',
      message: error.message,
      finishedAt: new Date().toISOString(),
    })
  })

  child.on('close', (code) => {
    const finishedAt = new Date().toISOString()
    if (code === 0) {
      updateJob(jobId, {
        status: 'completed',
        finishedAt,
        message: lastMessage || undefined,
      })
      return
    }

    const residualProgress = parseProgressLine(stdoutBuffer.trim())
    if (residualProgress) {
      updateJob(jobId, {
        done: residualProgress.done,
        total: residualProgress.total,
        errors: residualProgress.errors,
      })
    }

    const residualError = stderrBuffer.trim()
    if (residualError.length > 0) {
      lastMessage = residualError
    }

    updateJob(jobId, {
      status: 'failed',
      finishedAt,
      message: lastMessage || `Process exited with code ${code ?? 'unknown'}`,
    })
  })

  return c.json({ jobId, status: 'running' as const })
})

ohlcv.get('/fetch/:jobId', (c) => {
  const jobId = c.req.param('jobId')
  const job = jobs.get(jobId)

  if (!job) {
    return c.json({ error: 'Job not found' }, 404)
  }

  return c.json(job)
})

async function resolveActiveSymbolPredicate(): Promise<string> {
  const columnResult = await pool.query<ColumnRow>(
    `
      SELECT column_name
      FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = 'symbols'
    `
  )

  const columns = new Set(columnResult.rows.map((row) => row.column_name))
  if (columns.has('status')) {
    return "status = 'active'"
  }

  if (columns.has('is_active')) {
    return 'is_active = true'
  }

  throw new Error('symbols table is missing both status and is_active columns')
}

async function getTotalSymbols(activePredicate: string): Promise<number> {
  const result = await pool.query<CountRow>(
    `SELECT COUNT(*) AS count FROM symbols WHERE ${activePredicate}`
  )

  return Number(result.rows[0]?.count ?? 0)
}

async function getResolutionCounts(
  table: string,
  from: string,
  to: string
): Promise<Map<string, number>> {
  const result = await pool.query<DayCountRow>(
    `
      SELECT
        TO_CHAR(DATE(timestamp AT TIME ZONE 'Asia/Kolkata'), 'YYYY-MM-DD') AS day,
        COUNT(DISTINCT isin) AS scrip_count
      FROM ${table}
      WHERE timestamp >= $1::date
        AND timestamp < ($2::date + INTERVAL '1 day')
      GROUP BY day
      ORDER BY day
    `,
    [from, to]
  )

  return new Map(
    result.rows.map((row) => [row.day, Number(row.scrip_count)] as const)
  )
}

function buildDayMap(from: string, to: string): Record<string, DayStatus> {
  const days: Record<string, DayStatus> = {}
  for (const day of listDates(from, to)) {
    days[day] = createMissingDayStatus()
  }
  return days
}

function createMissingDayStatus(): DayStatus {
  return {
    '1d': { count: 0, status: 'missing' },
    '1m': { count: 0, status: 'missing' },
    '5s': { count: 0, status: 'missing' },
  }
}

function listDates(from: string, to: string): string[] {
  const dates: string[] = []
  const current = new Date(`${from}T00:00:00Z`)
  const end = new Date(`${to}T00:00:00Z`)

  while (current <= end) {
    dates.push(current.toISOString().slice(0, 10))
    current.setUTCDate(current.getUTCDate() + 1)
  }

  return dates
}

function toCoverageStatus(count: number, totalSymbols: number): CoverageStatus {
  if (count === 0) {
    return 'missing'
  }

  if (count >= totalSymbols) {
    return 'full'
  }

  return 'partial'
}

function updateJob(jobId: string, patch: Partial<FetchJobState>) {
  const current = jobs.get(jobId)
  if (!current) {
    return
  }

  jobs.set(jobId, {
    ...current,
    ...patch,
  })
}

function parseProgressLine(line: string): ProgressLine | null {
  const trimmed = line.trim()
  if (!trimmed.startsWith('{')) {
    return null
  }

  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      'done' in parsed &&
      'total' in parsed &&
      'errors' in parsed &&
      typeof parsed.done === 'number' &&
      typeof parsed.total === 'number' &&
      typeof parsed.errors === 'number'
    ) {
      return {
        done: parsed.done,
        total: parsed.total,
        errors: parsed.errors,
      }
    }
  } catch {
    return null
  }

  return null
}

function isResolution(value: string | undefined): value is Resolution {
  return value !== undefined && RESOLUTIONS.includes(value as Resolution)
}

function isValidDateInput(value: string | undefined): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
}

function isFetchRequestBody(value: unknown): value is FetchRequestBody {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  if (!('resolution' in value) || !('from' in value) || !('to' in value)) {
    return false
  }

  return (
    isResolution(String(value.resolution)) &&
    isValidDateInput(String(value.from)) &&
    isValidDateInput(String(value.to))
  )
}

export default ohlcv
