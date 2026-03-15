import { useState } from 'react'
import { Activity } from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { HeaderToolbar } from '@/components/layout/header-toolbar'
import { StockSearch } from './stock-search'
import { useSectorStrength } from './use-sector-strength'
import { SectorTable } from './sector-table'
import type { SectorLevel } from './types'

const LEVELS: { value: SectorLevel; label: string; description: string }[] = [
  { value: 'macro',        label: 'Macro Sectors',    description: '12 groups' },
  { value: 'sector',       label: 'Micro Sectors',    description: '22 groups' },
  { value: 'industry',     label: 'Broad Industries', description: '58 groups' },
  { value: 'sub_industry', label: 'Sub Industries',   description: '182 groups' },
]

function LevelTab({ level }: { level: SectorLevel }) {
  const { data, isLoading } = useSectorStrength(level)

  return (
    <SectorTable
      level={level}
      groups={data?.groups ?? []}
      loading={isLoading}
    />
  )
}

export function SectorPulsePage() {
  const [activeLevel, setActiveLevel] = useState<SectorLevel>('sector')
  const { data: activeData } = useSectorStrength(activeLevel)

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-6 py-4 border-b border-border shrink-0'>
        <div className='flex items-center gap-3'>
          <div className='p-2 rounded-lg bg-primary/10'>
            <Activity size={18} className='text-primary' />
          </div>
          <div>
            <h1 className='text-lg font-semibold'>Industry &amp; Sector Pulse</h1>
            {activeData?.date && (
              <p className='text-xs text-muted-foreground'>
                As of{' '}
                {new Date(activeData.date).toLocaleDateString('en-IN', {
                  day: 'numeric',
                  month: 'short',
                  year: 'numeric',
                })}
              </p>
            )}
          </div>
        </div>
        <HeaderToolbar />
      </div>

      {/* Tabs */}
      <div className='flex-1 overflow-hidden flex flex-col'>
        <Tabs
          value={activeLevel}
          onValueChange={(v) => setActiveLevel(v as SectorLevel)}
          className='flex-1 flex flex-col overflow-hidden'
        >
          <div className='flex items-center justify-between gap-4 px-6 pt-4 shrink-0'>
            <TabsList className='bg-muted/50'>
              {LEVELS.map(l => (
                <TabsTrigger key={l.value} value={l.value} className='text-sm'>
                  {l.label}
                  <span className='ml-1.5 text-xs text-muted-foreground hidden sm:inline'>
                    ({l.description})
                  </span>
                </TabsTrigger>
              ))}
            </TabsList>
            <StockSearch />
          </div>

          {LEVELS.map(l => (
            <TabsContent
              key={l.value}
              value={l.value}
              className='flex-1 overflow-auto mt-0 border-0 px-6 pb-6'
            >
              <div className='rounded-lg border border-border bg-card overflow-hidden mt-4'>
                <LevelTab level={l.value} />
              </div>
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </div>
  )
}
