import { useState } from 'react'
import { format, addDays, subDays } from 'date-fns'
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'

type DateNavigatorProps = {
  /** Date string in YYYY-MM-DD format */
  value: string
  /** Called with YYYY-MM-DD string */
  onChange: (date: string) => void
  /** Optional className for the container */
  className?: string
}

function toDate(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function toStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function DateNavigator({ value, onChange, className }: DateNavigatorProps) {
  const [open, setOpen] = useState(false)
  const current = toDate(value)

  const goPrev = () => onChange(toStr(subDays(current, 1)))
  const goNext = () => {
    const next = addDays(current, 1)
    if (next <= new Date()) onChange(toStr(next))
  }

  const handleSelect = (day: Date | undefined) => {
    if (day) {
      onChange(toStr(day))
      setOpen(false)
    }
  }

  const isToday = toStr(current) === toStr(new Date())

  return (
    <div className={`flex items-center gap-0.5 ${className ?? ''}`}>
      <Button
        variant='ghost'
        size='icon'
        className='h-7 w-7 shrink-0'
        onClick={goPrev}
        title='Previous day'
      >
        <ChevronLeft size={14} />
      </Button>

      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant='outline'
            className='h-7 px-2.5 text-xs font-medium gap-1.5 min-w-[120px] justify-center'
          >
            <CalendarIcon size={12} className='opacity-50' />
            {format(current, 'dd MMM yyyy')}
          </Button>
        </PopoverTrigger>
        <PopoverContent className='w-auto p-0' align='start'>
          <Calendar
            mode='single'
            selected={current}
            onSelect={handleSelect}
            disabled={(date: Date) =>
              date > new Date() || date < new Date('2020-01-01')
            }
            defaultMonth={current}
          />
        </PopoverContent>
      </Popover>

      <Button
        variant='ghost'
        size='icon'
        className='h-7 w-7 shrink-0'
        onClick={goNext}
        disabled={isToday}
        title='Next day'
      >
        <ChevronRight size={14} />
      </Button>
    </div>
  )
}
