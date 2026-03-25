import { format, startOfWeek, isSameWeek, addWeeks } from 'date-fns'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { useUpcomingEvents } from '../use-news'
import type { UpcomingMeeting, UpcomingAction } from '../types'

const PURPOSE_COLORS: Record<string, string> = {
  'Financial Results': 'text-red-500 border-red-500/30 bg-red-500/10',
  'Dividend': 'text-emerald-500 border-emerald-500/30 bg-emerald-500/10',
}

function purposeBadge(purpose: string) {
  const lower = purpose.toLowerCase()
  const cls = lower.includes('financial')
    ? PURPOSE_COLORS['Financial Results']
    : lower.includes('dividend')
      ? PURPOSE_COLORS['Dividend']
      : 'text-muted-foreground border-border'
  return (
    <Badge variant='outline' className={`text-[10px] ${cls}`}>
      {purpose}
    </Badge>
  )
}

const SUBJECT_COLORS: Record<string, string> = {
  dividend: 'text-emerald-500 border-emerald-500/30 bg-emerald-500/10',
  bonus: 'text-blue-500 border-blue-500/30 bg-blue-500/10',
  split: 'text-amber-500 border-amber-500/30 bg-amber-500/10',
  rights: 'text-violet-500 border-violet-500/30 bg-violet-500/10',
}

function subjectBadge(subject: string) {
  const lower = subject.toLowerCase()
  const key = Object.keys(SUBJECT_COLORS).find((k) => lower.includes(k))
  const cls = key ? SUBJECT_COLORS[key] : 'text-muted-foreground border-border'
  return (
    <Badge variant='outline' className={`text-[10px] ${cls}`}>
      {subject.length > 60 ? subject.slice(0, 57) + '...' : subject}
    </Badge>
  )
}

type WeekGroup<T> = { label: string; items: T[] }

function groupByWeek<T>(items: T[], dateKey: keyof T): WeekGroup<T>[] {
  const now = new Date()
  const thisWeekStart = startOfWeek(now, { weekStartsOn: 1 })
  const nextWeekStart = addWeeks(thisWeekStart, 1)

  const groups: WeekGroup<T>[] = [
    { label: 'This Week', items: [] },
    { label: 'Next Week', items: [] },
    { label: 'Later', items: [] },
  ]

  for (const item of items) {
    const d = new Date(item[dateKey] as string)
    if (isSameWeek(d, now, { weekStartsOn: 1 })) {
      groups[0].items.push(item)
    } else if (isSameWeek(d, nextWeekStart, { weekStartsOn: 1 })) {
      groups[1].items.push(item)
    } else {
      groups[2].items.push(item)
    }
  }

  return groups.filter((g) => g.items.length > 0)
}

type Props = { symbol?: string }

export function UpcomingEvents({ symbol }: Props) {
  const { data, isLoading, isError, refetch } = useUpcomingEvents(symbol)

  if (isLoading) {
    return (
      <div className='space-y-2'>
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className='h-10 w-full rounded-lg' />
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <div className='flex items-center justify-center h-32 text-muted-foreground text-sm gap-2'>
        Failed to load upcoming events.
        <Button variant='ghost' size='sm' onClick={() => refetch()}>
          Retry
        </Button>
      </div>
    )
  }

  const meetings = data?.meetings ?? []
  const actions = data?.actions ?? []

  if (meetings.length === 0 && actions.length === 0) {
    return (
      <div className='flex items-center justify-center h-32 text-muted-foreground text-sm'>
        No upcoming events
      </div>
    )
  }

  const meetingGroups = groupByWeek<UpcomingMeeting>(meetings, 'meeting_date')
  const actionGroups = groupByWeek<UpcomingAction>(actions, 'ex_date')

  return (
    <div className='space-y-6'>
      {/* Board Meetings */}
      {meetings.length > 0 && (
        <div>
          <h3 className='text-sm font-semibold mb-3'>Board Meetings</h3>
          {meetingGroups.map((group) => (
            <div key={group.label} className='mb-4'>
              <p className='text-xs text-muted-foreground font-medium mb-2'>{group.label}</p>
              <Card className='overflow-hidden'>
                <table className='w-full text-sm'>
                  <thead>
                    <tr className='border-b border-border bg-muted/30'>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Symbol</th>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Date</th>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Purpose</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.items.map((m) => (
                      <tr key={m.id} className='border-b border-border/50 hover:bg-muted/20 transition-colors'>
                        <td className='px-4 py-2.5 font-medium'>{m.symbol}</td>
                        <td className='px-4 py-2.5 tabular-nums text-xs'>{format(new Date(m.meeting_date), 'dd MMM yyyy')}</td>
                        <td className='px-4 py-2.5'>{purposeBadge(m.purpose)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            </div>
          ))}
        </div>
      )}

      {/* Corporate Actions */}
      {actions.length > 0 && (
        <div>
          <h3 className='text-sm font-semibold mb-3'>Corporate Actions</h3>
          {actionGroups.map((group) => (
            <div key={group.label} className='mb-4'>
              <p className='text-xs text-muted-foreground font-medium mb-2'>{group.label}</p>
              <Card className='overflow-hidden'>
                <table className='w-full text-sm'>
                  <thead>
                    <tr className='border-b border-border bg-muted/30'>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Symbol</th>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Subject</th>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Ex-Date</th>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Record Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.items.map((a) => (
                      <tr key={a.id} className='border-b border-border/50 hover:bg-muted/20 transition-colors'>
                        <td className='px-4 py-2.5 font-medium'>{a.symbol}</td>
                        <td className='px-4 py-2.5'>{subjectBadge(a.subject)}</td>
                        <td className='px-4 py-2.5 tabular-nums text-xs'>{format(new Date(a.ex_date), 'dd MMM yyyy')}</td>
                        <td className='px-4 py-2.5 tabular-nums text-xs'>
                          {a.record_date ? format(new Date(a.record_date), 'dd MMM yyyy') : '\u2014'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
