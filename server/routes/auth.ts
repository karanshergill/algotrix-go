import { Hono } from 'hono'
import { readFile } from 'node:fs/promises'
import { spawn } from 'node:child_process'
import path from 'node:path'

const router = new Hono()

const ENGINE_PATH = path.resolve(process.cwd(), 'engine/algotrix')
const TOKEN_PATH = path.resolve(process.cwd(), 'engine/token.json')

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

// GET /api/auth/login-url
router.get('/login-url', async (c) => {
  return new Promise<Response>((resolve) => {
    const proc = spawn(ENGINE_PATH, ['auth'], {
      cwd: path.resolve(process.cwd(), 'engine'),
      env: process.env,
    })

    let url = ''
    let buffer = ''

    proc.stdout.on('data', (chunk: Buffer) => {
      buffer += chunk.toString()
      const match = buffer.match(/https:\/\/api-t1\.fyers\.in\/[^\s\n]+/)
      if (match && !url) {
        url = match[0]
        resolve(c.json({ url }) as unknown as Response)
        // Don't kill proc — it's waiting for stdin (auth code)
        // Let it timeout naturally
        proc.kill()
      }
    })

    proc.stderr.on('data', (chunk: Buffer) => {
      buffer += chunk.toString()
      const match = buffer.match(/https:\/\/api-t1\.fyers\.in\/[^\s\n]+/)
      if (match && !url) {
        url = match[0]
        resolve(c.json({ url }) as unknown as Response)
        proc.kill()
      }
    })

    proc.on('close', () => {
      if (!url) {
        resolve(c.json({ error: 'Could not get login URL' }, 500) as unknown as Response)
      }
    })

    setTimeout(() => {
      if (!url) {
        proc.kill()
        resolve(c.json({ error: 'Timeout getting login URL' }, 500) as unknown as Response)
      }
    }, 10_000)
  })
})

// POST /api/auth/exchange  body: { code: string }
router.post('/exchange', async (c) => {
  const { code } = await c.req.json<{ code: string }>()
  if (!code?.trim()) {
    return c.json({ error: 'Auth code required' }, 400)
  }

  return new Promise<Response>((resolve) => {
    const proc = spawn(ENGINE_PATH, ['auth'], {
      cwd: path.resolve(process.cwd(), 'engine'),
      env: process.env,
    })

    let output = ''

    proc.stdout.on('data', (chunk: Buffer) => {
      output += chunk.toString()
      // When it asks for auth_code, write the code
      if (output.includes('Paste the auth_code')) {
        proc.stdin.write(code.trim() + '\n')
      }
    })

    proc.stderr.on('data', (chunk: Buffer) => {
      output += chunk.toString()
    })

    proc.on('close', (exitCode) => {
      if (exitCode === 0 && output.includes('Token saved')) {
        resolve(c.json({ success: true }) as unknown as Response)
      } else {
        const errMatch = output.match(/error[:\s]+(.+)/i)
        resolve(c.json({ error: errMatch?.[1] ?? 'Token exchange failed' }, 500) as unknown as Response)
      }
    })

    setTimeout(() => {
      proc.kill()
      resolve(c.json({ error: 'Timeout during token exchange' }, 500) as unknown as Response)
    }, 30_000)
  })
})

export default router
