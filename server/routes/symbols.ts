import { Hono } from 'hono'
import pool from '../db'

const symbols = new Hono()

const GROUP_CHAIN_LEVELS = ['macro', 'sector', 'industry', 'sub_industry'] as const

type ColumnRow = {
  column_name: string
}

type SymbolsSchema = {
  activePredicate: string
  hasFnoColumn: boolean
  hasSkipReasonColumn: boolean
  hasStatusColumn: boolean
  nameExpression: string | null
}

type StatsRow = {
  total: string
  active: string
  skipped: string
  enriched: string
  skip_non_equity: string
  skip_sme: string
  skip_t2t: string
  fno: string
}

type SearchRow = {
  isin: string
  symbol: string
  name: string | null
  sector_macro: string | null
  sector: string | null
  industry: string | null
  industry_basic: string | null
}

type ChainRow = {
  level: (typeof GROUP_CHAIN_LEVELS)[number]
  group_name: string | null
  stock_count: number | null
  score: number | null
  ret_1d: number | null
  adv_count: number | null
  dec_count: number | null
}

type PeerRow = {
  isin: string
  symbol: string
  name: string | null
}

symbols.get('/stats', async (c) => {
  const schema = await resolveSymbolsSchema()
  const nameCountSelect = schema.nameExpression
    ? `count(*) FILTER (WHERE ${schema.nameExpression} IS NOT NULL) AS enriched,`
    : '0 AS enriched,'
  const skippedSelect = schema.hasStatusColumn
    ? `count(*) FILTER (WHERE status = 'skipped') AS skipped,`
    : '0 AS skipped,'
  const skipReasonSelect = schema.hasSkipReasonColumn
    ? `
      count(*) FILTER (WHERE skip_reason = 'non_equity') AS skip_non_equity,
      count(*) FILTER (WHERE skip_reason = 'sme') AS skip_sme,
      count(*) FILTER (WHERE skip_reason = 'trade_to_trade') AS skip_t2t,
    `
    : `
      0 AS skip_non_equity,
      0 AS skip_sme,
      0 AS skip_t2t,
    `
  const fnoSelect = schema.hasFnoColumn
    ? `count(*) FILTER (WHERE is_fno = true) AS fno`
    : '0 AS fno'

  const result = await pool.query<StatsRow>(`
    SELECT
      count(*) AS total,
      count(*) FILTER (WHERE ${schema.activePredicate}) AS active,
      ${skippedSelect}
      ${nameCountSelect}
      ${skipReasonSelect}
      ${fnoSelect}
    FROM symbols
  `)

  const row = result.rows[0]
  return c.json({
    total: Number(row.total),
    active: Number(row.active),
    skipped: Number(row.skipped),
    enriched: Number(row.enriched),
    fno: Number(row.fno),
    bySkipReason: {
      nonEquity: Number(row.skip_non_equity),
      sme: Number(row.skip_sme),
      tradToTrade: Number(row.skip_t2t),
    },
  })
})

symbols.get('/search', async (c) => {
  const schema = await resolveSymbolsSchema()
  const query = c.req.query('q')?.trim() ?? ''
  const limit = parseLimit(c.req.query('limit'))

  if (!query) {
    return c.json([])
  }

  const nameSelect = schema.nameExpression
    ? `${schema.nameExpression} AS name`
    : 'NULL::text AS name'
  const nameFilter = schema.nameExpression
    ? `OR ${schema.nameExpression} ILIKE $2`
    : ''
  const nameOrderPrefix = schema.nameExpression
    ? `WHEN ${schema.nameExpression} ILIKE $1 THEN 1`
    : ''
  const nameOrderContains = schema.nameExpression
    ? `WHEN ${schema.nameExpression} ILIKE $2 THEN 3`
    : ''
  const nameOrderValue = schema.nameExpression ?? 'symbol'

  const result = await pool.query<SearchRow>(
    `
      SELECT
        isin,
        symbol,
        ${nameSelect},
        sector_macro,
        sector,
        industry,
        industry_basic
      FROM symbols
      WHERE ${schema.activePredicate}
        AND (
          symbol ILIKE $2
          ${nameFilter}
        )
      ORDER BY
        CASE
          WHEN symbol ILIKE $1 THEN 0
          ${nameOrderPrefix}
          WHEN symbol ILIKE $2 THEN 2
          ${nameOrderContains}
          ELSE 4
        END,
        CASE
          WHEN symbol ILIKE $1 OR symbol ILIKE $2 THEN symbol
          ELSE ${nameOrderValue}
        END NULLS LAST,
        symbol
      LIMIT $3
    `,
    [`${query}%`, `%${query}%`, limit]
  )

  return c.json(result.rows)
})

symbols.get('/:isin/group-chain', async (c) => {
  const schema = await resolveSymbolsSchema()
  const isin = c.req.param('isin')
  const nameSelect = schema.nameExpression
    ? `${schema.nameExpression} AS name`
    : 'NULL::text AS name'

  const stockResult = await pool.query<SearchRow>(
    `
      SELECT
        isin,
        symbol,
        ${nameSelect},
        sector_macro,
        sector,
        industry,
        industry_basic
      FROM symbols
      WHERE ${schema.activePredicate}
        AND isin = $1
      LIMIT 1
    `,
    [isin]
  )

  const stock = stockResult.rows[0]
  if (!stock) {
    return c.json({ error: 'Stock not found' }, 404)
  }

  const chainResult = await pool.query<ChainRow>(
    `
      WITH latest_by_level AS (
        SELECT
          level,
          MAX(date) AS latest_date
        FROM sector_strength
        WHERE level = ANY($1::text[])
        GROUP BY level
      )
      SELECT
        levels.level,
        levels.group_name,
        strength.stock_count::int AS stock_count,
        strength.score::float AS score,
        strength.ret_1d::float AS ret_1d,
        strength.adv_count::int AS adv_count,
        strength.dec_count::int AS dec_count,
        strength.vol_ratio::float AS vol_ratio
      FROM (
        VALUES
          ('macro'::text, $2::text, 1),
          ('sector'::text, $3::text, 2),
          ('industry'::text, $4::text, 3),
          ('sub_industry'::text, $5::text, 4)
      ) AS levels(level, group_name, sort_order)
      LEFT JOIN latest_by_level latest
        ON latest.level = levels.level
      LEFT JOIN sector_strength strength
        ON strength.level = latest.level
       AND strength.date = latest.latest_date
       AND strength.group_name = levels.group_name
      ORDER BY levels.sort_order
    `,
    [
      [...GROUP_CHAIN_LEVELS],
      stock.sector_macro,
      stock.sector,
      stock.industry,
      stock.industry_basic,
    ]
  )

  const peersResult = stock.industry_basic
    ? await pool.query<PeerRow>(
        `
          SELECT
            isin,
            symbol,
            ${nameSelect}
          FROM symbols
          WHERE ${schema.activePredicate}
            AND industry_basic = $1
          ORDER BY symbol
        `,
        [stock.industry_basic]
      )
    : { rows: [] as PeerRow[] }

  return c.json({
    stock,
    chain: chainResult.rows.map((row) => ({
      level: row.level,
      group_name: row.group_name,
      stock_count: row.stock_count == null ? null : Number(row.stock_count),
      score: row.score == null ? null : Number(row.score),
      ret_1d: row.ret_1d == null ? null : Number(row.ret_1d),
      adv_count: row.adv_count == null ? null : Number(row.adv_count),
      dec_count: row.dec_count == null ? null : Number(row.dec_count),
    })),
    peers: peersResult.rows,
  })
})

export default symbols

async function resolveSymbolsSchema(): Promise<SymbolsSchema> {
  const result = await pool.query<ColumnRow>(
    `
      SELECT column_name
      FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = 'symbols'
    `
  )

  const columns = new Set(result.rows.map((row) => row.column_name))
  const nameExpression = columns.has('name')
    ? 'name'
    : columns.has('company_name')
      ? 'company_name'
      : null

  if (columns.has('status')) {
    return {
      activePredicate: "status = 'active'",
      hasFnoColumn: columns.has('is_fno'),
      hasSkipReasonColumn: columns.has('skip_reason'),
      hasStatusColumn: true,
      nameExpression,
    }
  }

  if (columns.has('is_active')) {
    return {
      activePredicate: 'is_active = true',
      hasFnoColumn: columns.has('is_fno'),
      hasSkipReasonColumn: columns.has('skip_reason'),
      hasStatusColumn: false,
      nameExpression,
    }
  }

  throw new Error('symbols table is missing both status and is_active columns')
}

function parseLimit(limitParam: string | undefined) {
  const parsed = Number.parseInt(limitParam ?? '10', 10)
  if (Number.isNaN(parsed)) {
    return 10
  }

  return Math.min(Math.max(parsed, 1), 50)
}
