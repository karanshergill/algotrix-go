import { useState } from 'react'
import { KeyRound, ExternalLink, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { useToken } from './use-token'

export function TokenStatus() {
  const { data, isLoading, invalidate } = useToken()
  const [loginUrl, setLoginUrl] = useState<string | null>(null)
  const [authCode, setAuthCode] = useState('')
  const [fetchingUrl, setFetchingUrl] = useState(false)
  const [exchanging, setExchanging] = useState(false)

  const valid = data?.valid ?? false
  const expiresAt = data?.expiresAt
    ? new Date(data.expiresAt).toLocaleTimeString('en-IN', {
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'Asia/Kolkata',
      }) + ' IST'
    : null

  async function getLoginUrl() {
    setFetchingUrl(true)
    try {
      const res = await fetch('/api/auth/login-url')
      const json = await res.json() as { url?: string; error?: string }
      if (json.url) setLoginUrl(json.url)
      else toast.error(json.error ?? 'Could not get login URL')
    } catch {
      toast.error('Failed to reach server')
    } finally {
      setFetchingUrl(false)
    }
  }

  async function exchangeCode() {
    if (!authCode.trim()) return
    setExchanging(true)
    try {
      const res = await fetch('/api/auth/exchange', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: authCode.trim() }),
      })
      const json = await res.json() as { success?: boolean; error?: string }
      if (json.success) {
        toast.success('Token refreshed')
        setAuthCode('')
        setLoginUrl(null)
        invalidate()
      } else {
        toast.error(json.error ?? 'Token exchange failed')
      }
    } catch {
      toast.error('Failed to reach server')
    } finally {
      setExchanging(false)
    }
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant='ghost' size='icon' className='size-9'>
          <KeyRound
            className={cn(
              'size-[1.2rem] transition-colors',
              isLoading && 'text-muted-foreground',
              !isLoading && valid && 'text-green-500',
              !isLoading && !valid && 'text-red-500 animate-pulse'
            )}
          />
          <span className='sr-only'>Token status</span>
        </Button>
      </PopoverTrigger>

      <PopoverContent className='w-72' align='end'>
        <div className='space-y-3'>
          <p className='text-sm font-medium'>Token</p>

          <div className='space-y-1 text-sm'>
            <div className='flex items-center justify-between'>
              <span className='text-muted-foreground'>Status</span>
              <span
                className={cn(
                  'font-medium',
                  valid ? 'text-green-500' : 'text-red-500'
                )}
              >
                {isLoading ? '...' : valid ? 'Valid' : 'Expired'}
              </span>
            </div>

            {expiresAt && (
              <div className='flex items-center justify-between'>
                <span className='text-muted-foreground'>
                  {valid ? 'Expires' : 'Expired'}
                </span>
                <span>{expiresAt}</span>
              </div>
            )}

            {data?.userId && (
              <div className='flex items-center justify-between'>
                <span className='text-muted-foreground'>User</span>
                <span>{data.userId}</span>
              </div>
            )}
          </div>

          {!valid && !isLoading && (
            <div className='space-y-3 border-t pt-3'>
              {!loginUrl ? (
                <Button
                  size='sm'
                  variant='outline'
                  className='w-full'
                  onClick={getLoginUrl}
                  disabled={fetchingUrl}
                >
                  {fetchingUrl ? (
                    <Loader2 className='mr-2 size-3.5 animate-spin' />
                  ) : (
                    <ExternalLink className='mr-2 size-3.5' />
                  )}
                  Open Login URL
                </Button>
              ) : (
                <Button
                  size='sm'
                  variant='outline'
                  className='w-full'
                  asChild
                >
                  <a href={loginUrl} target='_blank' rel='noreferrer'>
                    <ExternalLink className='mr-2 size-3.5' />
                    Login with Fyers ↗
                  </a>
                </Button>
              )}

              <div className='space-y-1.5'>
                <Label className='text-xs'>Auth code</Label>
                <Input
                  placeholder='Paste auth_code here'
                  value={authCode}
                  onChange={(e) => setAuthCode(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && exchangeCode()}
                  className='h-8 text-xs'
                />
                <Button
                  size='sm'
                  className='w-full'
                  onClick={exchangeCode}
                  disabled={!authCode.trim() || exchanging}
                >
                  {exchanging && <Loader2 className='mr-2 size-3.5 animate-spin' />}
                  Refresh Token
                </Button>
              </div>
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
