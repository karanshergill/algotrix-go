import { useQuery } from '@tanstack/react-query'
import {
  TrendingUp,
  TrendingDown,
  Minus,
  ArrowRight,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'

interface RegimeData {
  date: string
  scores: {
    volatility: number | null
    trend: number | null
    participation: number | null
    sentiment: number | null
    institutional_flow: number | null
  }
  composite_score: number
  regime_label: string
  predicted_next_label?: string
  predicted_confidence?: number
  availability_regime?: string
  source?: string
  prediction?: {
    next_day_label: string
    confidence: number
    leading_score: number
  }
}

async function fetchRegime(): Promise<RegimeData> {
  const res = await fetch('/api/regime/today')
  if (!res.ok) throw new Error('Failed to fetch regime')
  return res.json()
}

export function RegimeCard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['regime-today'],
    queryFn: fetchRegime,
    staleTime: 5 * 60_000,
    refetchInterval: 10 * 60_000,
  })

  if (isLoading) {
    return (
      <Card>
        <CardHeader className='pb-2'>
          <Skeleton className='h-4 w-32' />
        </CardHeader>
        <CardContent className='space-y-3'>
          <Skeleton className='h-10 w-full' />
          <Skeleton className='h-20 w-full' />
        </CardContent>
      </Card>
    )
  }

  if (error || !data) {
    return (
      <Card>
        <CardHeader className='pb-2'>
          <CardTitle className='text-sm font-medium'>Market Regime</CardTitle>
        </CardHeader>
        <CardContent>
          <p className='text-sm text-muted-foreground'>
            {error ? 'Failed to load regime data' : 'No data available'}
          </p>
        </CardContent>
      </Card>
    )
  }

  const prediction = data.prediction || {
    next_day_label: data.predicted_next_label,
    confidence: data.predicted_confidence,
  }

  return (
    <Card className='gap-2 card-accent-live'>
      <CardHeader className='pb-0'>
        <CardTitle className='text-sm font-medium'>Market Regime</CardTitle>
      </CardHeader>
      <CardContent className='space-y-3 pt-1'>
        {/* Current regime + composite */}
        <div className='flex items-center justify-between'>
          <div className='flex items-center gap-2'>
            <RegimeIcon label={data.regime_label} />
            <div>
              <div className='text-lg font-bold'>{data.regime_label}</div>
              <div className='text-xs text-muted-foreground'>
                Score: {data.composite_score?.toFixed(1)} / 100
              </div>
            </div>
          </div>
          <RegimeBadge label={data.regime_label} />
        </div>

        {/* 5-dimension bar chart */}
        <div className='space-y-1.5'>
          {data.scores && Object.entries(data.scores).map(([dim, score]) => (
            <DimensionBar key={dim} name={dim} score={score} />
          ))}
        </div>

        {/* Prediction */}
        {prediction?.next_day_label && (
          <div className='flex items-center gap-2 rounded-md border px-3 py-2'>
            <ArrowRight className='h-4 w-4 text-muted-foreground' />
            <div className='flex-1'>
              <div className='text-sm'>
                Next-day prediction:{' '}
                <span className='font-semibold'>{prediction.next_day_label}</span>
              </div>
              {prediction.confidence != null && (
                <div className='text-xs text-muted-foreground'>
                  Confidence: {(prediction.confidence * 100).toFixed(0)}%
                </div>
              )}
            </div>
            <RegimeBadge label={prediction.next_day_label} size='sm' />
          </div>
        )}

        {/* Date */}
        <div className='text-xs text-muted-foreground text-right'>
          {data.date}
        </div>
      </CardContent>
    </Card>
  )
}

function RegimeIcon({ label }: { label: string }) {
  if (label === 'Bullish') return <TrendingUp className='h-5 w-5 text-green-500' />
  if (label === 'Bearish') return <TrendingDown className='h-5 w-5 text-red-500' />
  return <Minus className='h-5 w-5 text-yellow-500' />
}

function RegimeBadge({ label, size = 'default' }: { label: string; size?: 'default' | 'sm' }) {
  const colors: Record<string, string> = {
    Bullish: 'bg-green-500 text-white border-transparent',
    Bearish: 'bg-red-500 text-white border-transparent',
    Neutral: 'bg-yellow-500 text-black border-transparent',
  }
  return (
    <Badge className={`${colors[label] || ''} ${size === 'sm' ? 'text-xs px-1.5 py-0' : ''} shrink-0`}>
      {label}
    </Badge>
  )
}

function DimensionBar({ name, score }: { name: string; score: number | null }) {
  const displayName = name
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())

  const pct = score != null ? Math.max(0, Math.min(100, score)) : 0
  const color = pct >= 60 ? 'bg-green-500' : pct <= 40 ? 'bg-red-500' : 'bg-yellow-500'

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className='flex items-center gap-2'>
            <span className='w-28 text-xs text-muted-foreground truncate'>
              {displayName}
            </span>
            <div className='flex-1 h-2 rounded-full bg-muted overflow-hidden'>
              <div
                className={`h-full rounded-full transition-all ${color}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className='w-8 text-xs text-right tabular-nums'>
              {score != null ? score.toFixed(0) : '—'}
            </span>
          </div>
        </TooltipTrigger>
        <TooltipContent>
          <p>{displayName}: {score != null ? score.toFixed(1) : 'N/A'}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
