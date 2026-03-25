import { useState } from 'react'
import { format } from 'date-fns'
import { ExternalLink, ChevronDown, ChevronRight } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { useNewsDetail } from '../use-news'
import type { FeedItem } from '../types'

const SOURCE_COLORS: Record<string, string> = {
  announcement: 'text-violet-500 border-violet-500/30 bg-violet-500/10',
  block_deal: 'text-amber-500 border-amber-500/30 bg-amber-500/10',
}

const CATEGORY_SEVERITY: Record<string, 'red' | 'amber'> = {
  'Outcome of Board Meeting': 'red',
  'Acquisitions': 'red',
  'Credit Rating': 'red',
  'Spurt in Volume': 'red',
  'SEBI - Loss of Securities': 'red',
  'Press Release': 'amber',
  'Allotment of Securities': 'amber',
  'News Verification': 'amber',
  'Bagging of Orders': 'amber',
}

const fmt = new Intl.NumberFormat('en-IN')

function formatBlockTitle(item: FeedItem) {
  if (item.traded_volume == null) return 'Block Deal'
  return `Block: ${fmt.format(item.traded_volume)} shares @ \u20B9${fmt.format(item.price!)} (\u20B9${fmt.format(item.traded_value!)})`
}

export function NewsCard({ item }: { item: FeedItem }) {
  const [expanded, setExpanded] = useState(false)
  const source = item.source === 'announcement' ? 'announcements' : 'block_deals'
  const detail = useNewsDetail(source, item.id)

  const handleExpand = () => {
    if (!expanded && !detail.data) detail.refetch()
    setExpanded(!expanded)
  }

  const severity = item.category ? CATEGORY_SEVERITY[item.category] : null
  const borderClass = item.is_market_moving
    ? 'border-l-2 border-l-red-500 bg-red-500/5'
    : severity === 'red'
      ? 'border-l-2 border-l-red-400'
      : severity === 'amber'
        ? 'border-l-2 border-l-amber-400'
        : ''

  const title = item.source === 'block_deal' ? formatBlockTitle(item) : item.title
  const timeStr = item.source === 'announcement'
    ? format(new Date(item.timestamp), 'HH:mm')
    : '\u2014'

  return (
    <tr
      className={`border-b border-border/50 hover:bg-muted/20 transition-colors cursor-pointer ${borderClass}`}
      onClick={handleExpand}
    >
      <td className='px-4 py-2.5' colSpan={5}>
        <div className='flex items-center gap-3'>
          <span className='text-muted-foreground'>
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </span>
          <span className='tabular-nums text-xs text-muted-foreground w-12 shrink-0'>
            {timeStr}
          </span>
          <span className='font-medium text-sm w-24 shrink-0'>{item.symbol}</span>
          <Badge variant='outline' className={`text-[10px] shrink-0 ${SOURCE_COLORS[item.source] ?? ''}`}>
            {item.source === 'block_deal' ? 'Block Deal' : 'Announcement'}
          </Badge>
          {item.is_market_moving && (
            <Badge variant='outline' className='text-[10px] text-red-500 border-red-500/30 bg-red-500/10'>
              Market Moving
            </Badge>
          )}
          {item.category && (
            <span className='text-[10px] text-muted-foreground'>{item.category}</span>
          )}
          <span className='text-sm truncate flex-1'>{title}</span>
          {item.attachment_url && (
            <a
              href={item.attachment_url}
              target='_blank'
              rel='noopener noreferrer'
              onClick={(e) => e.stopPropagation()}
              className='text-muted-foreground hover:text-foreground shrink-0'
            >
              <ExternalLink size={14} />
            </a>
          )}
        </div>
        {expanded && (
          <div className='mt-3 ml-8'>
            {detail.isLoading && <p className='text-xs text-muted-foreground'>Loading...</p>}
            {detail.data && (
              <pre className='text-xs bg-muted/30 rounded-md p-3 overflow-auto max-h-64'>
                {JSON.stringify(detail.data.raw_json, null, 2)}
              </pre>
            )}
          </div>
        )}
      </td>
    </tr>
  )
}
