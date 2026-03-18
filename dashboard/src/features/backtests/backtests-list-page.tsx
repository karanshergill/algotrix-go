import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { FlaskConical, Plus, Trash2, RefreshCw, ChevronDown } from 'lucide-react'
import { format } from 'date-fns'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { HeaderToolbar } from '@/components/layout/header-toolbar'
import { Badge } from '@/components/ui/badge'
import { useBacktests, useRunBacktest, useDeleteBacktest } from './use-backtests'
import { WatchlistWeightSliders } from '../watchlist/watchlist-weight-sliders'
import { DEFAULT_WEIGHTS } from '../watchlist/types'
import type { MetricWeights } from '../watchlist/types'
import type { BacktestRun } from './types'

function statusBadge(status: BacktestRun['status']) {
  switch (status) {
    case 'running':
      return <Badge variant='outline' className='text-yellow-500 border-yellow-500/30'>Running</Badge>
    case 'completed':
      return <Badge variant='outline' className='text-emerald-500 border-emerald-500/30'>Completed</Badge>
    case 'failed':
      return <Badge variant='outline' className='text-red-500 border-red-500/30'>Failed</Badge>
  }
}

function edgeSummary(run: BacktestRun): string {
  if (!run.summary) return '—'
  const t1 = run.summary['T+1']
  if (!t1) return '—'
  const sign = t1.edge_max_opp >= 0 ? '+' : ''
  return `${sign}${t1.edge_max_opp.toFixed(2)}%`
}

function winRate(run: BacktestRun): string {
  if (!run.summary) return '—'
  const t1 = run.summary['T+1']
  if (!t1) return '—'
  return `${t1.win_count}/${t1.total_count}`
}

export function BacktestsListPage() {
  const navigate = useNavigate()
  const { data: runs, isLoading } = useBacktests()
  const runMutation = useRunBacktest()
  const deleteMutation = useDeleteBacktest()

  const [showConfig, setShowConfig] = useState(false)
  const [name, setName] = useState('')
  const [topN, setTopN] = useState(25)
  const [step, setStep] = useState(1)
  const [minMcap, setMinMcap] = useState(0)
  const [maxMcap, setMaxMcap] = useState(0)
  const [lookback, setLookback] = useState(30)
  const [madtvFloor, setMadtvFloor] = useState(100)
  const [minScore, setMinScore] = useState(0)
  const [weights, setWeights] = useState<MetricWeights>({ ...DEFAULT_WEIGHTS })

  const openConfig = () => {
    setName(`Builder Backtest ${format(new Date(), 'yyyy-MM-dd HH:mm')}`)
    setTopN(25)
    setStep(1)
    setMinMcap(0)
    setMaxMcap(0)
    setLookback(30)
    setMadtvFloor(100)
    setMinScore(0)
    setWeights({ ...DEFAULT_WEIGHTS })
    setShowConfig(true)
  }

  const handleRun = () => {
    runMutation.mutate(
      {
        type: 'builder',
        name,
        config: {
          top_n: topN,
          step,
          min_mcap: minMcap || undefined,
          max_mcap: maxMcap || undefined,
          lookback,
          madtv_floor: madtvFloor * 1e7,
          min_score: minScore || undefined,
          weights,
        },
      },
      {
        onSuccess: () => setShowConfig(false),
      },
    )
  }

  const handleDelete = (e: React.MouseEvent, id: number) => {
    e.stopPropagation()
    deleteMutation.mutate(id)
  }

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-6 py-3 border-b border-border shrink-0'>
        <div className='flex items-center gap-3'>
          <div className='p-1.5 rounded-lg bg-primary/10'>
            <FlaskConical size={16} className='text-primary' />
          </div>
          <div>
            <h1 className='text-base font-semibold leading-tight'>Backtests</h1>
            <p className='text-[10px] text-muted-foreground'>
              Rolling historical backtests of watchlist builder picks
            </p>
          </div>
        </div>
        <HeaderToolbar />
      </div>

      {/* Action bar */}
      <div className='flex items-center justify-between px-6 py-2 border-b border-border/50 shrink-0'>
        <span className='text-xs text-muted-foreground'>
          {runs?.length ?? 0} run{(runs?.length ?? 0) !== 1 ? 's' : ''}
        </span>
        {!showConfig && (
          <Button onClick={openConfig} size='sm' className='h-7 px-4'>
            <Plus size={12} className='mr-1.5' />
            New Backtest
          </Button>
        )}
      </div>

      {/* Config panel */}
      {showConfig && (
        <div className='px-6 py-3 border-b border-border/50 shrink-0'>
          <Card className='p-4'>
            <div className='flex items-center gap-2 mb-3'>
              <ChevronDown size={14} className='text-muted-foreground' />
              <span className='text-sm font-medium'>Backtest Configuration</span>
            </div>

            <div className='grid grid-cols-2 md:grid-cols-3 gap-3'>
              <div className='col-span-2 md:col-span-3'>
                <Label htmlFor='bt-name' className='text-xs text-muted-foreground mb-1'>Name</Label>
                <Input
                  id='bt-name'
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  disabled={runMutation.isPending}
                  className='h-8 text-sm'
                />
              </div>

              <div>
                <Label htmlFor='bt-topn' className='text-xs text-muted-foreground mb-1'>Top N</Label>
                <Input
                  id='bt-topn'
                  type='number'
                  min={5}
                  max={100}
                  value={topN}
                  onChange={(e) => setTopN(Number(e.target.value))}
                  disabled={runMutation.isPending}
                  className='h-8 text-sm'
                />
              </div>

              <div>
                <Label htmlFor='bt-step' className='text-xs text-muted-foreground mb-1'>Step (trading days)</Label>
                <Input
                  id='bt-step'
                  type='number'
                  min={1}
                  max={10}
                  value={step}
                  onChange={(e) => setStep(Number(e.target.value))}
                  disabled={runMutation.isPending}
                  className='h-8 text-sm'
                />
              </div>

              <div>
                <Label htmlFor='bt-minmcap' className='text-xs text-muted-foreground mb-1'>Min Market Cap (Cr)</Label>
                <Input
                  id='bt-minmcap'
                  type='number'
                  min={0}
                  placeholder='₹ Crores'
                  value={minMcap || ''}
                  onChange={(e) => setMinMcap(Number(e.target.value))}
                  disabled={runMutation.isPending}
                  className='h-8 text-sm'
                />
              </div>

              <div>
                <Label htmlFor='bt-maxmcap' className='text-xs text-muted-foreground mb-1'>Max Market Cap (Cr)</Label>
                <Input
                  id='bt-maxmcap'
                  type='number'
                  min={0}
                  placeholder='₹ Crores'
                  value={maxMcap || ''}
                  onChange={(e) => setMaxMcap(Number(e.target.value))}
                  disabled={runMutation.isPending}
                  className='h-8 text-sm'
                />
              </div>
            </div>

            {/* Scoring section */}
            <div className='flex items-center gap-2 mt-4 mb-3'>
              <ChevronDown size={14} className='text-muted-foreground' />
              <span className='text-sm font-medium'>Scoring</span>
            </div>

            <div className='grid grid-cols-2 md:grid-cols-3 gap-3 mb-3'>
              <div>
                <Label htmlFor='bt-lookback' className='text-xs text-muted-foreground mb-1'>Lookback Days</Label>
                <Input
                  id='bt-lookback'
                  type='number'
                  min={10}
                  max={120}
                  value={lookback}
                  onChange={(e) => setLookback(Number(e.target.value))}
                  disabled={runMutation.isPending}
                  className='h-8 text-sm'
                />
              </div>

              <div>
                <Label htmlFor='bt-madtv' className='text-xs text-muted-foreground mb-1'>Min MADTV (₹Cr)</Label>
                <Input
                  id='bt-madtv'
                  type='number'
                  min={0}
                  value={madtvFloor || ''}
                  onChange={(e) => setMadtvFloor(Number(e.target.value))}
                  disabled={runMutation.isPending}
                  className='h-8 text-sm'
                />
              </div>

              <div>
                <Label htmlFor='bt-minscore' className='text-xs text-muted-foreground mb-1'>Min Score (0-100)</Label>
                <Input
                  id='bt-minscore'
                  type='number'
                  min={0}
                  max={80}
                  value={minScore || ''}
                  onChange={(e) => setMinScore(Number(e.target.value))}
                  disabled={runMutation.isPending}
                  className='h-8 text-sm'
                />
              </div>
            </div>

            <WatchlistWeightSliders
              weights={weights}
              onChange={setWeights}
            />

            <div className='flex items-center gap-2 mt-4'>
              <Button onClick={handleRun} disabled={runMutation.isPending} size='sm' className='h-8 px-5'>
                {runMutation.isPending ? (
                  <>
                    <RefreshCw size={12} className='mr-1.5 animate-spin' />
                    Running…
                  </>
                ) : (
                  'Run Backtest'
                )}
              </Button>
              <Button
                variant='ghost'
                size='sm'
                className='h-8 px-4'
                onClick={() => setShowConfig(false)}
                disabled={runMutation.isPending}
              >
                Cancel
              </Button>
            </div>
          </Card>
        </div>
      )}

      {/* Content */}
      <div className='flex-1 overflow-auto'>
        <div className='px-6 py-4'>
          {isLoading && (
            <div className='space-y-2'>
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className='h-14 w-full rounded-lg' />
              ))}
            </div>
          )}

          {runs && runs.length === 0 && (
            <div className='flex items-center justify-center h-32 text-muted-foreground text-sm'>
              No backtest runs yet. Click "New Backtest" to start.
            </div>
          )}

          {runs && runs.length > 0 && (
            <Card className='overflow-hidden'>
              <table className='w-full text-sm'>
                <thead>
                  <tr className='border-b border-border bg-muted/30'>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Name</th>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Type</th>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Status</th>
                    <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Dates</th>
                    <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Edge (T+1)</th>
                    <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Win Rate</th>
                    <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Created</th>
                    <th className='w-10'></th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr
                      key={run.id}
                      className='border-b border-border/50 hover:bg-muted/20 cursor-pointer transition-colors'
                      onClick={() => navigate({ to: '/backtests/$id', params: { id: String(run.id) } })}
                    >
                      <td className='px-4 py-2.5 font-medium'>{run.name ?? `Run #${run.id}`}</td>
                      <td className='px-4 py-2.5'>
                        <Badge variant='secondary' className='text-[10px]'>{run.type}</Badge>
                      </td>
                      <td className='px-4 py-2.5'>{statusBadge(run.status)}</td>
                      <td className='px-4 py-2.5 text-right tabular-nums'>{run.build_dates_tested ?? '—'}</td>
                      <td className='px-4 py-2.5 text-right tabular-nums'>
                        <span className={run.summary?.['T+1']?.edge_max_opp && run.summary['T+1'].edge_max_opp > 0 ? 'text-emerald-500' : 'text-red-400'}>
                          {edgeSummary(run)}
                        </span>
                      </td>
                      <td className='px-4 py-2.5 text-right tabular-nums'>{winRate(run)}</td>
                      <td className='px-4 py-2.5 text-right text-xs text-muted-foreground'>
                        {format(new Date(run.started_at), 'MMM d, HH:mm')}
                      </td>
                      <td className='px-2 py-2.5'>
                        <Button
                          variant='ghost'
                          size='icon'
                          className='h-6 w-6'
                          onClick={(e) => handleDelete(e, run.id)}
                        >
                          <Trash2 size={12} className='text-muted-foreground' />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
