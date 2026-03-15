import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
} from 'recharts'

type Props = {
  percentiles: {
    pctMADTV: number
    pctAmihud: number
    pctATRPct: number
    pctParkinson: number
    pctTradeSize: number
  }
}

export function WatchlistRadarChart({ percentiles }: Props) {
  const data = [
    { metric: 'MADTV', value: percentiles.pctMADTV },
    { metric: 'Amihud', value: percentiles.pctAmihud },
    { metric: 'ATR%', value: percentiles.pctATRPct },
    { metric: 'Parkinson', value: percentiles.pctParkinson },
    { metric: 'TradeSize', value: percentiles.pctTradeSize },
  ]

  return (
    <ResponsiveContainer width='100%' height={260}>
      <RadarChart data={data} cx='50%' cy='50%' outerRadius='75%'>
        <PolarGrid stroke='hsl(var(--border))' />
        <PolarAngleAxis
          dataKey='metric'
          tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
        />
        <PolarRadiusAxis
          domain={[0, 100]}
          tick={{ fontSize: 9, fill: 'hsl(var(--muted-foreground))' }}
          tickCount={5}
        />
        <Radar
          name='Percentile'
          dataKey='value'
          stroke='hsl(142, 71%, 45%)'
          fill='hsl(142, 71%, 45%)'
          fillOpacity={0.25}
          strokeWidth={2}
        />
      </RadarChart>
    </ResponsiveContainer>
  )
}
