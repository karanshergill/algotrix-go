import { useState } from 'react'
import { Newspaper } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { HeaderToolbar } from '@/components/layout/header-toolbar'
import { DateNavigator } from '@/components/date-navigator'
import { useNewsSummary } from './use-news'
import { NewsFeed } from './components/news-feed'
import { UpcomingEvents } from './components/upcoming-events'
import { InsiderActivity } from './components/insider-activity'

const SOURCE_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'announcements', label: 'Announcements' },
  { value: 'block_deals', label: 'Block Deals' },
]

function todayIST() {
  return new Date().toLocaleString('en-CA', { timeZone: 'Asia/Kolkata' }).slice(0, 10)
}

export function NewsPage() {
  const [date, setDate] = useState(todayIST)
  const [symbolInput, setSymbolInput] = useState('')
  const [source, setSource] = useState('')
  const [marketMoving, setMarketMoving] = useState(false)
  const [tab, setTab] = useState('feed')

  const symbol = symbolInput.trim().toUpperCase() || undefined
  const { data: summary } = useNewsSummary(date)

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-6 py-3 border-b border-border shrink-0'>
        <div className='flex items-center gap-3'>
          <div className='p-1.5 rounded-lg bg-primary/10'>
            <Newspaper size={16} className='text-primary' />
          </div>
          <div>
            <h1 className='text-base font-semibold leading-tight'>News & Events</h1>
            <p className='text-[10px] text-muted-foreground'>
              Corporate announcements, deals, and insider activity
            </p>
          </div>
        </div>
        <HeaderToolbar />
      </div>

      {/* Filters */}
      <div className='flex items-center gap-3 px-6 py-2 border-b border-border/50 shrink-0 flex-wrap'>
        <DateNavigator value={date} onChange={setDate} />
        <Input
          type='text'
          placeholder='Symbol filter...'
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          className='h-7 w-32 text-xs'
        />
        {tab === 'feed' && (
          <>
            <div className='flex gap-1'>
              {SOURCE_OPTIONS.map((opt) => (
                <Badge
                  key={opt.value}
                  variant='outline'
                  className={`cursor-pointer text-xs ${source === opt.value ? 'bg-primary/10 text-primary border-primary/30' : ''}`}
                  onClick={() => setSource(opt.value)}
                >
                  {opt.label}
                </Badge>
              ))}
            </div>
            <label className='flex items-center gap-1.5 text-xs cursor-pointer'>
              <input
                type='checkbox'
                checked={marketMoving}
                onChange={(e) => setMarketMoving(e.target.checked)}
                className='rounded'
              />
              Market-moving only
            </label>
          </>
        )}

        {/* Summary counts */}
        {summary && (
          <div className='ml-auto flex items-center gap-3'>
            <span className='text-xs text-muted-foreground'>
              {summary.announcements} ann{summary.market_moving > 0 && (
                <span className='text-red-500'> ({summary.market_moving} MM)</span>
              )}
            </span>
            <span className='text-xs text-muted-foreground'>
              {summary.block_deals} blocks
            </span>
            <span className='text-xs text-muted-foreground'>
              {summary.upcoming_meetings} meetings
            </span>
            <span className='text-xs text-muted-foreground'>
              {summary.upcoming_actions} actions
            </span>
          </div>
        )}
      </div>

      {/* Summary cards */}
      {summary && (
        <div className='grid grid-cols-2 sm:grid-cols-5 gap-3 px-6 py-3 shrink-0'>
          <Card className='p-3'>
            <div className='text-[10px] text-muted-foreground'>Announcements</div>
            <div className='text-xl font-bold tabular-nums mt-0.5'>{summary.announcements}</div>
          </Card>
          <Card className='p-3'>
            <div className='text-[10px] text-red-500'>Market Moving</div>
            <div className='text-xl font-bold tabular-nums mt-0.5'>{summary.market_moving}</div>
          </Card>
          <Card className='p-3'>
            <div className='text-[10px] text-muted-foreground'>Block Deals</div>
            <div className='text-xl font-bold tabular-nums mt-0.5'>{summary.block_deals}</div>
          </Card>
          <Card className='p-3'>
            <div className='text-[10px] text-muted-foreground'>Upcoming Meetings</div>
            <div className='text-xl font-bold tabular-nums mt-0.5'>{summary.upcoming_meetings}</div>
          </Card>
          <Card className='p-3'>
            <div className='text-[10px] text-muted-foreground'>Upcoming Actions</div>
            <div className='text-xl font-bold tabular-nums mt-0.5'>{summary.upcoming_actions}</div>
          </Card>
        </div>
      )}

      {/* Tabs */}
      <div className='flex-1 overflow-auto'>
        <div className='px-6 py-4'>
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className='mb-4'>
              <TabsTrigger value='feed'>Feed</TabsTrigger>
              <TabsTrigger value='upcoming'>Upcoming</TabsTrigger>
              <TabsTrigger value='insider'>Insider Activity</TabsTrigger>
            </TabsList>

            <TabsContent value='feed'>
              <NewsFeed
                date={date}
                source={source || undefined}
                symbol={symbol}
                marketMoving={marketMoving}
              />
            </TabsContent>

            <TabsContent value='upcoming'>
              <UpcomingEvents symbol={symbol} />
            </TabsContent>

            <TabsContent value='insider'>
              <InsiderActivity symbol={symbol} />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  )
}
