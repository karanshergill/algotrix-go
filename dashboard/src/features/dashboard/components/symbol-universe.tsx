import { useQuery } from '@tanstack/react-query'
import { Info } from 'lucide-react'
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'

interface SymbolStats {
  total: number
  active: number
  skipped: number
  enriched: number
  fno: number
  bySkipReason: {
    nonEquity: number
    sme: number
    tradToTrade: number
  }
}

async function fetchSymbolStats(): Promise<SymbolStats> {
  const res = await fetch('/api/symbols/stats')
  if (!res.ok) throw new Error('Failed to fetch symbol stats')
  return res.json()
}

export function SymbolUniverse() {
  const { data, isLoading } = useQuery({
    queryKey: ['symbol-stats'],
    queryFn: fetchSymbolStats,
    staleTime: 60_000,
  })

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className='h-4 w-32' />
        </CardHeader>
        <CardContent>
          <Skeleton className='h-8 w-24' />
        </CardContent>
      </Card>
    )
  }

  if (!data) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle className='text-sm font-medium'>Symbol Universe</CardTitle>
        <CardAction>
          <Popover>
            <PopoverTrigger asChild>
              <button className='inline-flex h-5 w-5 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground'>
                <Info className='h-4 w-4' />
              </button>
            </PopoverTrigger>
            <PopoverContent className='w-64' align='end'>
              <div className='space-y-3'>
                <div className='space-y-1.5'>
                  <p className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
                    Overview
                  </p>
                  <Row label='Total Scrips' value={data.total} />
                  <Row
                    label='Active (EQ)'
                    value={data.active}
                    className='text-green-500'
                  />
                  <Row
                    label='Enriched'
                    value={data.enriched}
                    className='text-blue-500'
                  />
                  <Row label='FnO' value={data.fno} />
                </div>

                <Separator />

                <div className='space-y-1.5'>
                  <p className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
                    Skipped ({data.skipped.toLocaleString()})
                  </p>
                  <Row label='Non-Equity' value={data.bySkipReason.nonEquity} dim />
                  <Row label='SME' value={data.bySkipReason.sme} dim />
                  <Row
                    label='Trade-to-Trade'
                    value={data.bySkipReason.tradToTrade}
                    dim
                  />
                </div>
              </div>
            </PopoverContent>
          </Popover>
        </CardAction>
      </CardHeader>
      <CardContent>
        <div className='text-2xl font-bold'>{data.active.toLocaleString()}</div>
        <p className='text-xs text-muted-foreground'>
          of {data.total.toLocaleString()} total scrips
        </p>
      </CardContent>
    </Card>
  )
}

function Row({
  label,
  value,
  className,
  dim,
}: {
  label: string
  value: number
  className?: string
  dim?: boolean
}) {
  return (
    <div className='flex items-center justify-between text-sm'>
      <span className={dim ? 'text-muted-foreground' : ''}>{label}</span>
      <span
        className={`font-semibold tabular-nums ${className ?? ''} ${dim ? 'text-muted-foreground' : ''}`}
      >
        {value.toLocaleString()}
      </span>
    </div>
  )
}
