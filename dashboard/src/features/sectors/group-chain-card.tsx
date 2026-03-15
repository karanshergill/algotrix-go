import { X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { ReturnCell, RvolCell, ScoreBar, StatusBadge } from './sector-table'
import type { GroupChainNode, GroupChainResponse, SectorLevel } from './types'

const LEVEL_LABELS: Record<SectorLevel, string> = {
  macro: 'Macro',
  sector: 'Sector',
  industry: 'Industry',
  sub_industry: 'Sub-Industry',
}

type GroupChainCardProps = {
  data: GroupChainResponse
  onClose: () => void
}

export function GroupChainCard({ data, onClose }: GroupChainCardProps) {
  const peers = data.peers.filter((peer) => peer.isin !== data.stock.isin)
  const focusNode =
    data.chain.find((node) => node.level === 'sub_industry') ??
    [...data.chain].reverse().find((node) => node.score != null) ??
    data.chain[data.chain.length - 1]

  return (
    <Card className='border-border/60 bg-card shadow-lg'>
      <CardHeader className='gap-3 border-b border-border/60 pb-5'>
        <div className='space-y-2'>
          <div className='flex flex-wrap items-center gap-2'>
            <CardTitle className='text-base sm:text-lg'>
              {data.stock.symbol} — {data.stock.name}
            </CardTitle>
            {focusNode?.score != null ? <StatusBadge score={focusNode.score} /> : null}
          </div>
          <CardDescription className='flex flex-wrap items-center gap-2 text-xs sm:text-sm'>
            <span>ISIN: {data.stock.isin}</span>
            {data.stock.industry_basic ? (
              <>
                <span className='text-border'>•</span>
                <span>{data.stock.industry_basic}</span>
              </>
            ) : null}
          </CardDescription>
        </div>
        <CardAction>
          <Button
            type='button'
            variant='ghost'
            size='icon'
            className='text-muted-foreground hover:text-foreground'
            onClick={onClose}
            aria-label='Close stock group chain'
          >
            <X className='size-4' />
          </Button>
        </CardAction>
      </CardHeader>

      <CardContent className='space-y-4'>
        {/* Chain rows */}
        <div className='space-y-3'>
          {data.chain.map((node) => (
            <ChainRow key={node.level} node={node} />
          ))}
        </div>

        {/* Peers */}
        <div className='rounded-lg border border-border/60 bg-muted/15 p-4'>
          <div className='mb-3 flex items-center justify-between gap-3'>
            <div>
              <h3 className='text-sm font-semibold'>Peers</h3>
              <p className='text-xs text-muted-foreground'>
                Active stocks in{' '}
                <span className='text-foreground'>
                  {focusNode?.group_name ?? data.stock.industry_basic ?? 'this group'}
                </span>
              </p>
            </div>
            <Badge
              variant='outline'
              className='border-primary/25 bg-primary/5 text-primary'
            >
              {peers.length}
            </Badge>
          </div>

          {peers.length > 0 ? (
            <div className='flex flex-wrap gap-2'>
              {peers.map((peer) => (
                <div
                  key={peer.isin}
                  className='rounded-md border border-border/50 bg-background/40 px-3 py-1.5'
                >
                  <span className='text-sm font-medium'>{peer.symbol}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className='text-sm text-muted-foreground'>
              No other active peers found in this sub-industry.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function ChainRow({ node }: { node: GroupChainNode }) {
  return (
    <div className='rounded-lg border border-border/60 bg-muted/20 p-4'>
      {/* Row 1: Level badge + Group name */}
      <div className='mb-3 flex items-center gap-2'>
        <Badge
          variant='outline'
          className='shrink-0 border-primary/25 bg-primary/5 text-[11px] font-medium uppercase tracking-wide text-primary'
        >
          {LEVEL_LABELS[node.level]}
        </Badge>
        <span className='text-sm font-semibold'>
          {node.group_name ?? 'Unclassified'}
        </span>
      </div>

      {/* Row 2: Metrics in a row */}
      <div className='flex items-center gap-6'>
        <Metric label='Score'>
          <ScoreBar score={node.score} />
        </Metric>

        <Metric label='1D %'>
          <ReturnCell value={node.ret_1d} />
        </Metric>

        <Metric label='RVOL'>
          <RvolCell value={node.vol_ratio} />
        </Metric>

        <Metric label='Stocks'>
          <span className='text-sm font-medium tabular-nums'>{node.stock_count}</span>
        </Metric>

        <Metric label='A / D'>
          <div className='whitespace-nowrap text-sm'>
            <span className='font-medium text-emerald-400'>{node.adv_count}</span>
            <span className='mx-1 text-muted-foreground'>/</span>
            <span className='font-medium text-red-400'>{node.dec_count}</span>
          </div>
        </Metric>
      </div>
    </div>
  )
}

function Metric({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className='space-y-1'>
      <p className='text-[11px] font-medium uppercase tracking-wide text-muted-foreground'>
        {label}
      </p>
      <div>{children}</div>
    </div>
  )
}
