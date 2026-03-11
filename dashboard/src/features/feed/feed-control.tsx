import { Radio, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { useFeedStatus, useFeedStart, useFeedStop } from './use-feed-status'

export function FeedControl() {
  const { data, isLoading } = useFeedStatus()
  const start = useFeedStart()
  const stop = useFeedStop()

  const status = data?.status ?? 'disconnected'
  const busy = start.isPending || stop.isPending

  async function handleConnect() {
    try {
      await start.mutateAsync()
      toast.success('Feed connecting...')
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  async function handleDisconnect() {
    try {
      await stop.mutateAsync()
      toast.success('Feed disconnected')
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant='ghost' size='icon' className='size-9'>
          <Radio
            className={cn(
              'size-[1.2rem] transition-colors',
              isLoading && 'text-muted-foreground',
              status === 'connected' && 'text-green-500',
              status === 'connecting' && 'text-yellow-500 animate-pulse',
              status === 'error' && 'text-red-500',
              status === 'disconnected' && 'text-muted-foreground'
            )}
          />
          <span className='sr-only'>Feed status</span>
        </Button>
      </PopoverTrigger>

      <PopoverContent className='w-64' align='end'>
        <div className='space-y-3'>
          <p className='text-sm font-medium'>Live Feed</p>

          <div className='space-y-1 text-sm'>
            <div className='flex items-center justify-between'>
              <span className='text-muted-foreground'>Status</span>
              <span
                className={cn(
                  'font-medium capitalize',
                  status === 'connected' && 'text-green-500',
                  status === 'connecting' && 'text-yellow-500',
                  status === 'error' && 'text-red-500',
                  status === 'disconnected' && 'text-muted-foreground'
                )}
              >
                {status}
              </span>
            </div>

            {data?.symbolCount ? (
              <div className='flex items-center justify-between'>
                <span className='text-muted-foreground'>Symbols</span>
                <span>{data.symbolCount.toLocaleString()}</span>
              </div>
            ) : null}

            {status === 'connected' && (
              <div className='flex items-center justify-between'>
                <span className='text-muted-foreground'>Ticks / min</span>
                <span>{data?.ticksLastMinute ?? 0}</span>
              </div>
            )}

            {data?.lastError && status === 'error' && (
              <p className='text-xs text-red-500 break-all'>{data.lastError}</p>
            )}
          </div>

          <div className='border-t pt-3'>
            {status === 'connected' || status === 'connecting' ? (
              <Button
                size='sm'
                variant='destructive'
                className='w-full'
                onClick={handleDisconnect}
                disabled={busy}
              >
                {busy && <Loader2 className='mr-2 size-3.5 animate-spin' />}
                Disconnect
              </Button>
            ) : (
              <Button
                size='sm'
                className='w-full'
                onClick={handleConnect}
                disabled={busy}
              >
                {busy && <Loader2 className='mr-2 size-3.5 animate-spin' />}
                Connect
              </Button>
            )}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
