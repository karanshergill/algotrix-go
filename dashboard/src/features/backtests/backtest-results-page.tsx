import { useState } from 'react'
import { useParams } from '@tanstack/react-router'
import { FlaskConical, ArrowLeft, ChevronDown } from 'lucide-react'
import { format } from 'date-fns'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { Link } from '@tanstack/react-router'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { HeaderToolbar } from '@/components/layout/header-toolbar'
import { useBacktestDetail } from './use-backtests'
import type { DateResult, Pick } from './types'

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

function pnlColor(val: number): string {
  return val >= 0 ? 'text-emerald-400' : 'text-red-400'
}

function PicksTable({ picks }: { picks: Pick[] }) {
  const winners = picks.filter((p) => p.max_opp > 0.5)
  const avgMaxOpp = picks.reduce((s, p) => s + p.max_opp, 0) / picks.length
  const best = picks.reduce((a, b) => (a.max_opp > b.max_opp ? a : b))
  const worst = picks.reduce((a, b) => (a.max_opp < b.max_opp ? a : b))

  return (
    <div className='space-y-3'>
      <table className='w-full text-sm'>
        <thead>
          <tr className='border-b border-border bg-muted/30'>
            <th className='text-left px-3 py-1.5 text-xs font-medium text-muted-foreground'>#</th>
            <th className='text-left px-3 py-1.5 text-xs font-medium text-muted-foreground'>Symbol</th>
            <th className='text-right px-3 py-1.5 text-xs font-medium text-muted-foreground'>Score</th>
            <th className='text-right px-3 py-1.5 text-xs font-medium text-muted-foreground'>Open</th>
            <th className='text-right px-3 py-1.5 text-xs font-medium text-muted-foreground'>High</th>
            <th className='text-right px-3 py-1.5 text-xs font-medium text-muted-foreground'>Low</th>
            <th className='text-right px-3 py-1.5 text-xs font-medium text-muted-foreground'>Close</th>
            <th className='text-right px-3 py-1.5 text-xs font-medium text-muted-foreground'>Max Opp</th>
            <th className='text-right px-3 py-1.5 text-xs font-medium text-muted-foreground'>O→C Return</th>
          </tr>
        </thead>
        <tbody>
          {picks.map((p) => (
            <tr key={p.isin} className='border-b border-border/20 hover:bg-muted/10'>
              <td className='px-3 py-1.5 tabular-nums text-muted-foreground'>{p.rank}</td>
              <td className='px-3 py-1.5 font-medium'>{p.symbol}</td>
              <td className='px-3 py-1.5 text-right tabular-nums'>{p.score.toFixed(2)}</td>
              <td className='px-3 py-1.5 text-right tabular-nums'>{p.open_price.toFixed(2)}</td>
              <td className='px-3 py-1.5 text-right tabular-nums'>{p.high_price.toFixed(2)}</td>
              <td className='px-3 py-1.5 text-right tabular-nums'>{p.low_price.toFixed(2)}</td>
              <td className='px-3 py-1.5 text-right tabular-nums'>{p.close_price.toFixed(2)}</td>
              <td className={`px-3 py-1.5 text-right tabular-nums font-medium ${pnlColor(p.max_opp)}`}>
                {p.max_opp >= 0 ? '+' : ''}{p.max_opp.toFixed(2)}%
              </td>
              <td className={`px-3 py-1.5 text-right tabular-nums font-medium ${pnlColor(p.oc_return)}`}>
                {p.oc_return >= 0 ? '+' : ''}{p.oc_return.toFixed(2)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Pick summary footer */}
      <div className='flex flex-wrap gap-4 px-3 text-xs text-muted-foreground'>
        <span>Winners (MO &gt; 0.5%): <span className='text-foreground font-medium'>{winners.length}/{picks.length}</span></span>
        <span>Avg MaxOpp: <span className='text-foreground font-medium'>{avgMaxOpp.toFixed(2)}%</span></span>
        <span>Best: <span className='text-emerald-400 font-medium'>{best.symbol} +{best.max_opp.toFixed(2)}%</span></span>
        <span>Worst: <span className='text-red-400 font-medium'>{worst.symbol} {worst.max_opp.toFixed(2)}%</span></span>
      </div>
    </div>
  )
}

function DateRow({ result }: { result: DateResult }) {
  const [open, setOpen] = useState(false)
  const avgPicksMaxOpp = result.picks.length > 0
    ? result.picks.reduce((s, p) => s + p.max_opp, 0) / result.picks.length
    : result.metrics.max_opp
  const edge = avgPicksMaxOpp - result.benchmark.nifty_max_opp

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <button className='w-full flex items-center gap-4 px-4 py-2.5 text-sm hover:bg-muted/10 transition-colors border-b border-border/30'>
          <ChevronDown size={14} className={`text-muted-foreground transition-transform shrink-0 ${open ? 'rotate-0' : '-rotate-90'}`} />
          <span className='font-medium tabular-nums w-28 text-left'>
            {format(new Date(result.build_date), 'MMM dd, yyyy')}
          </span>
          <span className='text-muted-foreground text-xs w-20 text-right tabular-nums'>
            {result.picks.length} picks
          </span>
          <span className='text-right tabular-nums w-24'>
            {avgPicksMaxOpp.toFixed(2)}%
          </span>
          <span className='text-right tabular-nums text-muted-foreground w-24'>
            {result.benchmark.nifty_max_opp.toFixed(2)}%
          </span>
          <span className={`inline-block px-2 py-0.5 rounded text-xs tabular-nums font-medium ${edgeColor(edge)}`}>
            {edge >= 0 ? '+' : ''}{edge.toFixed(2)}%
          </span>
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        {result.picks.length > 0 ? (
          <div className='px-4 py-3 bg-muted/5 border-b border-border/30'>
            <PicksTable picks={result.picks} />
          </div>
        ) : (
          <div className='px-4 py-3 text-sm text-muted-foreground border-b border-border/30'>
            No picks data available
          </div>
        )}
      </CollapsibleContent>
    </Collapsible>
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

export function BacktestResultsPage() {
  const { id } = useParams({ from: '/_authenticated/backtests/$id' })
  const { data: run, isLoading } = useBacktestDetail(id)

  if (isLoading) {
    return (
      <div className='p-6 space-y-4'>
        <Skeleton className='h-8 w-64' />
        <div className='grid grid-cols-3 gap-3'>
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className='h-20' />)}
        </div>
        <Skeleton className='h-64 w-full' />
      </div>
    )
  }

  if (!run) {
    return <div className='p-6 text-muted-foreground'>Backtest not found</div>
  }

  // T+1 only — filter to horizon 1
  const dateResults = run.date_results.filter((r) => r.horizon === 1)
  const summary = run.summary?.['T+1']

  const duration = run.completed_at
    ? `${Math.round((new Date(run.completed_at).getTime() - new Date(run.started_at).getTime()) / 1000)}s`
    : 'running…'

  // Compute summary metrics from picks
  const allPicks = dateResults.flatMap((r) => r.picks)
  const avgPicksMaxOpp = allPicks.length > 0
    ? allPicks.reduce((s, p) => s + p.max_opp, 0) / allPicks.length
    : (summary?.avg_max_opp ?? 0)

  const avgEdge = summary
    ? summary.edge_max_opp
    : (dateResults.length > 0
      ? dateResults.reduce((s, r) => s + r.metrics.max_opp - r.benchmark.nifty_max_opp, 0) / dateResults.length
      : 0)

  const winRate = summary
    ? { wins: summary.win_count, total: summary.total_count }
    : {
        wins: dateResults.filter((r) => r.metrics.max_opp > r.benchmark.nifty_max_opp).length,
        total: dateResults.length,
      }

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
              <Badge variant='secondary' className='text-[10px]'>T+1</Badge>
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
          {/* Summary cards — 3 only */}
          <div className='grid grid-cols-3 gap-3'>
            <SummaryCard
              label='Avg Edge vs Nifty'
              value={`${avgEdge >= 0 ? '+' : ''}${avgEdge.toFixed(2)}%`}
              positive={avgEdge > 0}
            />
            <SummaryCard
              label='Win Rate'
              value={`${winRate.wins}/${winRate.total}`}
              subtitle={`${((winRate.wins / Math.max(winRate.total, 1)) * 100).toFixed(0)}% of dates beat Nifty`}
              positive={winRate.wins > winRate.total / 2}
            />
            <SummaryCard
              label='Avg MaxOpp (Picks)'
              value={`${avgPicksMaxOpp.toFixed(2)}%`}
            />
          </div>

          {/* Edge chart */}
          <EdgeChart results={dateResults} />

          {/* Per-date accordion */}
          <Card className='overflow-hidden'>
            {/* Header row */}
            <div className='flex items-center gap-4 px-4 py-2 text-xs font-medium text-muted-foreground border-b border-border bg-muted/30'>
              <span className='w-[14px] shrink-0' />
              <span className='w-28 text-left'>Date</span>
              <span className='w-20 text-right'>Picks</span>
              <span className='w-24 text-right'>Picks MaxOpp</span>
              <span className='w-24 text-right'>Nifty MaxOpp</span>
              <span>Edge</span>
            </div>
            {dateResults.map((r) => (
              <DateRow key={r.id} result={r} />
            ))}
          </Card>
        </div>
      </div>
    </div>
  )
}
