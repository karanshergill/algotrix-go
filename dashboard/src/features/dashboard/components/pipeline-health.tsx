import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, AlertCircle, Clock, XCircle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

// ── Types ────────────────────────────────────────────────────────────────────

interface FeedHealth {
  name: string
  label: string
  lastStatus: 'success' | 'failed' | 'skipped' | null
  lastFetchTime: string | null
  lastFetchDate: string | null
  rowsOnLastFetch: number | null
  errorMessage: string | null
  latestMarketDate: string | null
  dateFrom: string | null
  dateTo: string | null
  totalRows: number
  tradingDays: number
  freshnessStatus: 'fresh' | 'stale' | 'empty'
}

interface PipelineHealthResponse {
  feeds: FeedHealth[]
  fetchedAt: string
}

// ── Fetch ────────────────────────────────────────────────────────────────────

async function fetchPipelineHealth(): Promise<PipelineHealthResponse> {
  const res = await fetch('/api/pipeline/health')
  if (!res.ok) throw new Error('Failed to fetch pipeline health')
  return res.json()
}

// ── Component ────────────────────────────────────────────────────────────────

export function PipelineHealth() {
  const { data, isLoading } = useQuery({
    queryKey: ['pipeline-health'],
    queryFn: fetchPipelineHealth,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000, // re-check every 5 min
  })

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className='h-4 w-40' />
        </CardHeader>
        <CardContent className='space-y-3'>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className='h-10 w-full' />
          ))}
        </CardContent>
      </Card>
    )
  }

  if (!data) return null

  return (
    <Card className='gap-2 card-accent-data'>
      <CardHeader className='pb-0'>
        <CardTitle className='text-sm font-medium'>NSE Data Status</CardTitle>
      </CardHeader>
      <CardContent className='space-y-2 pt-0'>
        {data.feeds.map((feed) => (
          <FeedRow key={feed.name} feed={feed} />
        ))}
      </CardContent>
    </Card>
  )
}

// ── Feed Row ─────────────────────────────────────────────────────────────────

function FeedRow({ feed }: { feed: FeedHealth }) {
  const { icon, color } = freshnessIndicator(feed.freshnessStatus, feed.lastStatus)

  return (
    <div className='flex items-center justify-between gap-2 rounded-md border px-3 py-2'>
      {/* Left: icon + label + coverage */}
      <div className='flex min-w-0 flex-1 items-center gap-2'>
        <span className={`shrink-0 ${color}`}>{icon}</span>
        <div className='min-w-0'>
          <div className='truncate text-sm font-medium'>{feed.label}</div>
          <div className='text-xs text-muted-foreground'>
            {feed.dateFrom && feed.dateTo
              ? `${formatDate(feed.dateFrom)} → ${formatDate(feed.dateTo)} · ${feed.tradingDays}d`
              : 'No data'}
          </div>

        </div>
      </div>

      {/* Right: last fetch date + freshness badge */}
      <div className='flex shrink-0 items-center gap-1.5'>
        {feed.lastFetchDate && (
          <Badge variant='secondary'>{formatDate(feed.lastFetchDate)}</Badge>
        )}
        <FreshnessBadge status={feed.freshnessStatus} lastStatus={feed.lastStatus} />
      </div>
    </div>
  )
}

// ── Freshness Badge ───────────────────────────────────────────────────────────

function FreshnessBadge({
  status,
  lastStatus,
}: {
  status: FeedHealth['freshnessStatus']
  lastStatus: FeedHealth['lastStatus']
}) {
  if (lastStatus === 'failed') {
    return <Badge className='bg-red-500 text-white border-transparent shrink-0'>FAILED</Badge>
  }
  if (status === 'fresh') {
    return <Badge className='bg-green-500 text-black border-transparent shrink-0'>FRESH</Badge>
  }
  if (status === 'stale') {
    return <Badge className='bg-yellow-500 text-white border-transparent shrink-0'>STALE</Badge>
  }
  return <Badge variant='outline' className='shrink-0'>EMPTY</Badge>
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function freshnessIndicator(
  freshness: FeedHealth['freshnessStatus'],
  lastStatus: FeedHealth['lastStatus']
) {
  if (lastStatus === 'failed') {
    return { icon: <XCircle className='h-4 w-4' />, color: 'text-red-500' }
  }
  if (freshness === 'fresh') {
    return { icon: <CheckCircle2 className='h-4 w-4' />, color: 'text-green-500' }
  }
  if (freshness === 'stale') {
    return { icon: <AlertCircle className='h-4 w-4' />, color: 'text-yellow-500' }
  }
  return { icon: <Clock className='h-4 w-4' />, color: 'text-muted-foreground' }
}

// Suppress unused import warnings — XCircle used in freshnessIndicator
void XCircle

// Convert YYYY-MM-DD → DD-MM-YYYY
function formatDate(iso: string): string {
  const [y, m, d] = iso.split('-')
  return `${d}-${m}-${y}`
}
