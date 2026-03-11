import { Hono } from 'hono'
import { readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'

const router = new Hono()

const TOKEN_PATH = path.resolve(process.cwd(), 'engine/token.json')
const CONFIG_PATH = path.resolve(process.cwd(), 'engine/internal/config/fyers.yaml')
const VALIDATE_URL = 'https://api-t1.fyers.in/api/v3/validate-authcode'

interface TokenFile {
  access_token: string
  refresh_token: string
  created_at: string
}

interface JWTPayload {
  exp: number
  fy_id?: string
  display_name?: string
}

function parseToken(token: string): JWTPayload | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString())
    return payload as JWTPayload
  } catch {
    return null
  }
}

// GET /api/auth/status
router.get('/status', async (c) => {
  try {
    const raw = await readFile(TOKEN_PATH, 'utf-8')
    const tokenFile = JSON.parse(raw) as TokenFile
    const payload = parseToken(tokenFile.access_token)

    if (!payload) {
      return c.json({ valid: false, expiresAt: null, userId: null })
    }

    const expiresAt = new Date(payload.exp * 1000).toISOString()
    const valid = Date.now() < payload.exp * 1000
    const userId = payload.fy_id ?? payload.display_name ?? null

    return c.json({ valid, expiresAt, userId })
  } catch {
    return c.json({ valid: false, expiresAt: null, userId: null })
  }
})

// GET /api/auth/login-url — build URL directly from config, no engine spawn needed
router.get('/login-url', async (c) => {
  try {
    const configRaw = await readFile(
      path.resolve(process.cwd(), 'engine/internal/config/fyers.yaml'),
      'utf-8'
    )
    const appIdMatch = configRaw.match(/app_id:\s*"([^"]+)"/)
    const redirectMatch = configRaw.match(/redirect_url:\s*"([^"]+)"/)

    if (!appIdMatch || !redirectMatch) {
      return c.json({ error: 'Could not read Fyers config' }, 500)
    }

    const appId = appIdMatch[1]
    const redirectUrl = redirectMatch[1]
    const url =
      `https://api-t1.fyers.in/api/v3/generate-authcode` +
      `?client_id=${encodeURIComponent(appId)}` +
      `&redirect_uri=${encodeURIComponent(redirectUrl)}` +
      `&response_type=code` +
      `&state=sample_state`

    return c.json({ url })
  } catch {
    return c.json({ error: 'Could not get login URL' }, 500)
  }
})

// POST /api/auth/exchange  body: { code: string } — code may be the auth_code or full redirect URL
router.post('/exchange', async (c) => {
  let { code } = await c.req.json<{ code: string }>()
  if (!code?.trim()) return c.json({ error: 'Auth code required' }, 400)

  // Accept full redirect URL — extract auth_code param
  if (code.includes('://')) {
    try {
      const u = new URL(code.trim())
      code = u.searchParams.get('auth_code') ?? code
    } catch { /* not a URL, use as-is */ }
  }
  code = code.trim()

  // Read app credentials from config
  const configRaw = await readFile(CONFIG_PATH, 'utf-8')
  const appIdMatch = configRaw.match(/app_id:\s*"([^"]+)"/)
  const secretMatch = configRaw.match(/secret_key:\s*"([^"]+)"/)
  if (!appIdMatch || !secretMatch) return c.json({ error: 'Could not read config' }, 500)

  const appId = appIdMatch[1]
  const secretKey = secretMatch[1]
  const appIdHash = createHash('sha256').update(`${appId}:${secretKey}`).digest('hex')

  // Exchange code for token via Fyers API
  const res = await fetch(VALIDATE_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, appIdHash, grant_type: 'authorization_code' }),
  })
  const data = await res.json() as Record<string, unknown>

  if (data.s !== 'ok' || !data.access_token) {
    return c.json({ error: (data.message as string) ?? 'Token exchange failed' }, 400)
  }

  // Save token to token.json (same format as Go engine)
  const tokenData = {
    access_token: data.access_token as string,
    refresh_token: (data.refresh_token as string) ?? '',
    created_at: new Date().toISOString(),
  }
  await writeFile(TOKEN_PATH, JSON.stringify(tokenData, null, 2))

  return c.json({ success: true })
})

export default router
