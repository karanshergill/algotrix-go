import { useMemo } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { Card } from '@/components/ui/card'
import type { StockScore } from './types'

type Props = {
  stocks: StockScore[]
}

const BUCKETS = [
  { label: '90-100', min: 90, max: 100 },
  { label: '80-89', min: 80, max: 89.99 },
  { label: '70-79', min: 70, max: 79.99 },
  { label: '60-69', min: 60, max: 69.99 },
  { label: '50-59', min: 50, max: 59.99 },
  { label: '40-49', min: 40, max: 49.99 },
  { label: '30-39', min: 30, max: 39.99 },
  { label: '20-29', min: 20, max: 29.99 },
  { label: '10-19', min: 10, max: 19.99 },
  { label: '0-9', min: 0, max: 9.99 },
]

function bucketColor(label: string): string {
  const min = parseInt(label)
  if (min >= 70) return 'hsl(142, 71%, 45%)'
  if (min >= 40) return 'hsl(38, 92%, 50%)'
  return 'hsl(0, 84%, 60%)'
}

export function WatchlistScoreChart({ stocks }: Props) {
  const data = useMemo(() => {
    return BUCKETS.map((b) => ({
      bucket: b.label,
      count: stocks.filter((s) => s.Composite >= b.min && s.Composite <= b.max).length,
    })).reverse()
  }, [stocks])

  return (
    <Card className='p-4'>
      <h3 className='text-sm font-medium mb-3'>Score Distribution</h3>
      <ResponsiveContainer width='100%' height={200}>
        <BarChart data={data} layout='vertical' margin={{ left: 10, right: 10 }}>
          <XAxis type='number' hide />
          <YAxis
            type='category'
            dataKey='bucket'
            width={50}
            tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: 'hsl(var(--card))',
              border: '1px solid hsl(var(--border))',
              borderRadius: '6px',
              fontSize: 12,
            }}
            labelStyle={{ color: 'hsl(var(--foreground))' }}
          />
          <Bar dataKey='count' radius={[0, 4, 4, 0]}>
            {data.map((entry) => (
              <Cell key={entry.bucket} fill={bucketColor(entry.bucket)} fillOpacity={0.7} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}
