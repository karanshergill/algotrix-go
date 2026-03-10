import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { useOhlcvFetchJob } from './use-ohlcv-fetch'
import { RESOLUTION_LABELS } from './constants'
import { getCoveragePercent } from './types'
import { useQueryClient } from '@tanstack/react-query'

interface OhlcvFetchProgressProps {
  jobId: string
  onDismiss: (jobId: string) => void
}

export function OhlcvFetchProgress({
  jobId,
  onDismiss,
}: OhlcvFetchProgressProps) {
  const queryClient = useQueryClient()
  const { data } = useOhlcvFetchJob(jobId)
  const hasAnnounced = useRef(false)

  useEffect(() => {
    if (!data || data.status === 'running' || hasAnnounced.current) {
      return
    }

    hasAnnounced.current = true
    void queryClient.invalidateQueries({ queryKey: ['ohlcv-status'] })
    void queryClient.invalidateQueries({ queryKey: ['ohlcv-symbols'] })

    if (data.status === 'completed') {
      toast.success(
        `${RESOLUTION_LABELS[data.resolution]} fetch completed for ${data.from}`
      )
    } else {
      toast.error(data.message || 'OHLCV fetch failed')
    }

    onDismiss(jobId)
  }, [data, jobId, onDismiss, queryClient])

  if (!data) {
    return null
  }

  const progressPercent = getCoveragePercent(data.done, data.total)

  return (
    <Card className='w-80 shadow-lg'>
      <CardHeader className='flex-row items-start justify-between space-y-0 pb-2'>
        <div>
          <CardTitle className='text-sm'>
            {RESOLUTION_LABELS[data.resolution]} fetch
          </CardTitle>
          <p className='text-xs text-muted-foreground'>
            {data.from} to {data.to}
          </p>
        </div>
        <Button
          variant='ghost'
          size='icon'
          className='size-7'
          onClick={() => onDismiss(jobId)}
        >
          <X className='size-4' />
        </Button>
      </CardHeader>
      <CardContent className='space-y-3'>
        <div className='h-2 overflow-hidden rounded-full bg-muted'>
          <div
            className='h-full rounded-full bg-blue-500 transition-[width]'
            style={{ width: `${progressPercent}%` }}
          />
        </div>
        <div className='flex items-center justify-between text-xs text-muted-foreground'>
          <span>
            {data.done.toLocaleString()} / {data.total.toLocaleString()} symbols
          </span>
          <span>{progressPercent}%</span>
        </div>
        <div className='text-xs text-muted-foreground'>
          Errors: {data.errors.toLocaleString()}
        </div>
      </CardContent>
    </Card>
  )
}
