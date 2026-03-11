import { Hono } from 'hono'
import pool from '../db'

const router = new Hono()

// GET /api/indices
router.get('/', async (c) => {
  const result = await pool.query(
    `SELECT id, symbol, name, fy_symbol, category, is_active
     FROM indices
     ORDER BY category, name`
  )
  return c.json(result.rows)
})

// GET /api/indices/active — just fy_symbol list for feed subscription
router.get('/active', async (c) => {
  const result = await pool.query(
    `SELECT fy_symbol FROM indices WHERE is_active = true ORDER BY category, name`
  )
  return c.json(result.rows.map((r: { fy_symbol: string }) => r.fy_symbol))
})

export default router
