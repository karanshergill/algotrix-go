import { useState } from 'react'
import { useParams } from '@tanstack/react-router'
import { FlaskConical, ArrowLeft } from 'lucide-react'
import { format } from 'date-fns'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { Link } from '@tanstack/react-router'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { HeaderToolbar } from '@/components/layout/header-toolbar'
import { useBacktestDetail } from './use-backtests'
import type { DateResult, HorizonSummary } from './types'

function SummaryCard({ label, value, subtitle, positive }: { label: string; value: string; subtitle?: string; positive?: boolean }) {
  return (
    <Card className='px-4 py-3'>
      <div className='text-[10px] uppercase tracking-wider text-muted-foreground'>{label}</div>
      <div className={`text-xl font-bold tabular-nums ${positive === true ? 'text-emerald-500' : positive === false ? 'text-red-400' : ''}`}>
        {value}
      </div>
      {subtitle && <div className='text-[10px] text-muted-foreground'>{subtitle}</div>}
    </Card>
  )
}

function edgeColor(edge: number): string {
  if (edge >= 1) return 'bg-emerald-500/20 text-emerald-400'
  if (edge > 0) return 'bg-emerald-500/10 text-emerald-400'
  if (edge > -0.5) return 'bg-red-500/10 text-red-400'
  return 'bg-red-500/20 text-red-400'
}

function DateResultsTable({ results }: { results: DateResult[] }) {
  return (
    <Card className='overflow-hidden'>
      <table className='w-full text-sm'>
        <thead>
          <tr className='border-b border-border bg-muted/30'>
            <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Build Date</th>
            <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>MaxOpp</th>
            <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>OC Ret</th>
            <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Range</th>
            <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Hit%</th>
            <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Nifty MO</th>
            <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Nifty Rng</th>
            <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Edge</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => {
            const edge = r.metrics.max_opp - r.benchmark.nifty_max_opp
            return (
              <tr key={`${r.build_date}-${r.horizon}`} className='border-b border-border/30 hover:bg-muted/10'>
                <td className='px-4 py-2 font-medium tabular-nums'>
                  {format(new Date(r.build_date), 'MMM dd, yyyy')}
                </td>
                <td className='px-4 py-2 text-right tabular-nums'>{r.metrics.max_opp.toFixed(2)}%</td>
                <td className='px-4 py-2 text-right tabular-nums'>
                  <span className={r.metrics.oc_ret >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                    {r.metrics.oc_ret >= 0 ? '+' : ''}{r.metrics.oc_ret.toFixed(2)}%
                  </span>
                </td>
                <td className='px-4 py-2 text-right tabular-nums'>{r.metrics.range.toFixed(2)}%</td>
                <td className='px-4 py-2 text-right tabular-nums'>{(r.metrics.hit_rate * 100).toFixed(0)}%</td>
                <td className='px-4 py-2 text-right tabular-nums text-muted-foreground'>{r.benchmark.nifty_max_opp.toFixed(2)}%</td>
                <td className='px-4 py-2 text-right tabular-nums text-muted-foreground'>{r.benchmark.nifty_range.toFixed(2)}%</td>
                <td className='px-4 py-2 text-right'>
                  <span className={`inline-block px-2 py-0.5 rounded text-xs tabular-nums font-medium ${edgeColor(edge)}`}>
                    {edge >= 0 ? '+' : ''}{edge.toFixed(2)}%
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </Card>
  )
}

function EdgeChart({ results }: { results: DateResult[] }) {
  const data = results.map((r) => ({
    date: format(new Date(r.build_date), 'MMM dd'),
    edge: +(r.metrics.max_opp - r.benchmark.nifty_max_opp).toFixed(2),
    maxOpp: +r.metrics.max_opp.toFixed(2),
    niftyMaxOpp: +r.benchmark.nifty_max_opp.toFixed(2),
  }))

  return (
    <Card className='p-4'>
      <h3 className='text-sm font-medium mb-3'>Edge Over Time (MaxOpp vs Nifty)</h3>
      <ResponsiveContainer width='100%' height={250}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray='3 3' stroke='hsl(var(--border))' />
          <XAxis dataKey='date' tick={{ fontSize: 10 }} stroke='hsl(var(--muted-foreground))' />
          <YAxis tick={{ fontSize: 10 }} stroke='hsl(var(--muted-foreground))' tickFormatter={(v) => `${v}%`} />
          <Tooltip
            contentStyle={{
              backgroundColor: 'hsl(var(--card))',
              border: '1px solid hsl(var(--border))',
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value: number | undefined) => value != null ? [`${value.toFixed(2)}%`] : ['—']}
          />
          <ReferenceLine y={0} stroke='hsl(var(--muted-foreground))' strokeDasharray='3 3' />
          <Line type='monotone' dataKey='edge' stroke='hsl(var(--chart-1))' strokeWidth={2} dot={{ r: 2 }} name='Edge' />
          <Line type='monotone' dataKey='maxOpp' stroke='hsl(var(--chart-2))' strokeWidth={1} dot={false} name='Picks MaxOpp' />
          <Line type='monotone' dataKey='niftyMaxOpp' stroke='hsl(var(--chart-3))' strokeWidth={1} dot={false} name='Nifty MaxOpp' />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  )
}

function HorizonView({ summary, dateResults }: { summary: HorizonSummary | undefined; dateResults: DateResult[] }) {
  if (!summary || dateResults.length === 0) {
    return <div className='text-muted-foreground text-sm py-8 text-center'>No data for this horizon</div>
  }

  return (
    <div className='space-y-4'>
      {/* Summary cards */}
      <div className='grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3'>
        <SummaryCard
          label='Edge vs Nifty (MaxOpp)'
          value={`${summary.edge_max_opp >= 0 ? '+' : ''}${summary.edge_max_opp.toFixed(2)}%`}
          positive={summary.edge_max_opp > 0}
        />
        <SummaryCard
          label='Win Rate'
          value={`${summary.win_count}/${summary.total_count}`}
          subtitle={`${((summary.win_count / Math.max(summary.total_count, 1)) * 100).toFixed(0)}% of dates`}
          positive={summary.win_count > summary.total_count / 2}
        />
        <SummaryCard label='Avg MaxOpp' value={`${summary.avg_max_opp.toFixed(2)}%`} />
        <SummaryCard label='Avg Range' value={`${summary.avg_range.toFixed(2)}%`} />
        <SummaryCard label='Avg Hit Rate' value={`${(summary.avg_hit_rate * 100).toFixed(0)}%`} />
      </div>

      {/* Chart */}
      <EdgeChart results={dateResults} />

      {/* Date results table */}
      <DateResultsTable results={dateResults} />
    </div>
  )
}

export function BacktestResultsPage() {
  const { id } = useParams({ from: '/_authenticated/backtests/$id' })
  const { data: run, isLoading } = useBacktestDetail(id)
  const [horizon, setHorizon] = useState('1')

  if (isLoading) {
    return (
      <div className='p-6 space-y-4'>
        <Skeleton className='h-8 w-64' />
        <div className='grid grid-cols-5 gap-3'>
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className='h-20' />)}
        </div>
        <Skeleton className='h-64 w-full' />
      </div>
    )
  }

  if (!run) {
    return <div className='p-6 text-muted-foreground'>Backtest not found</div>
  }

  const horizons = [...new Set(run.date_results.map((r) => r.horizon))].sort()

  const duration = run.completed_at
    ? `${Math.round((new Date(run.completed_at).getTime() - new Date(run.started_at).getTime()) / 1000)}s`
    : 'running…'

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-6 py-3 border-b border-border shrink-0'>
        <div className='flex items-center gap-3'>
          <Link to='/backtests'>
            <Button variant='ghost' size='icon' className='h-7 w-7'>
              <ArrowLeft size={14} />
            </Button>
          </Link>
          <div className='p-1.5 rounded-lg bg-primary/10'>
            <FlaskConical size={16} className='text-primary' />
          </div>
          <div>
            <div className='flex items-center gap-2'>
              <h1 className='text-base font-semibold leading-tight'>{run.name ?? `Run #${run.id}`}</h1>
              <Badge variant='secondary' className='text-[10px]'>{run.type}</Badge>
            </div>
            <p className='text-[10px] text-muted-foreground'>
              Top-{run.config.top_n} · Step {run.config.step} · {run.build_dates_tested ?? 0} dates · {duration}
            </p>
          </div>
        </div>
        <HeaderToolbar />
      </div>

      {/* Content */}
      <div className='flex-1 overflow-auto'>
        <div className='px-6 py-4 space-y-4'>
          <Tabs value={horizon} onValueChange={setHorizon}>
            <TabsList>
              {horizons.map((h) => (
                <TabsTrigger key={h} value={String(h)}>T+{h}</TabsTrigger>
              ))}
            </TabsList>
            {horizons.map((h) => (
              <TabsContent key={h} value={String(h)}>
                <HorizonView
                  summary={run.summary?.[`T+${h}`]}
                  dateResults={run.date_results.filter((r) => r.horizon === h)}
                />
              </TabsContent>
            ))}
          </Tabs>
        </div>
      </div>
    </div>
  )
}
