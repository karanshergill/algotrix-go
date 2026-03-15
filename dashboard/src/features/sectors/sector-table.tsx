import { useMemo, useRef, useState } from 'react'
import {
  type ColumnDef,
  type SortingState,
  type VisibilityState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { DataTableColumnHeader } from '@/components/data-table'
import { DataTableViewOptions } from '@/components/data-table/view-options'
import { cn } from '@/lib/utils'
import type { SectorGroup, SectorLevel } from './types'

/* ── Cell renderers ────────────────────────────────────── */

export function ReturnCell({ value }: { value: number | null }) {
  if (value == null) return <span className='text-muted-foreground'>—</span>
  const isPos = value > 0
  const isNeg = value < 0
  return (
    <span
      className={
        isPos ? 'text-emerald-400 font-medium' : isNeg ? 'text-red-400 font-medium' : 'text-muted-foreground'
      }
    >
      {isPos ? '+' : ''}{value.toFixed(2)}%
    </span>
  )
}

export function ScoreBar({ score }: { score: number | null }) {
  if (score == null) return <span className='text-muted-foreground'>—</span>
  const pct = Math.max(0, Math.min(100, Math.round(score)))
  const filledSegments = Math.round(pct / 5)
  const color =
    pct >= 60 ? 'bg-emerald-500' : pct <= 40 ? 'bg-red-500' : 'bg-amber-500'

  return (
    <div className='flex min-w-[92px] items-center gap-2'>
      <div className='grid h-1.5 flex-1 grid-cols-[repeat(20,minmax(0,1fr))] gap-0.5'>
        {Array.from({ length: 20 }).map((_, index) => (
          <div
            key={index}
            className={cn(
              'h-full rounded-full bg-border/80 transition-colors',
              index < filledSegments && color
            )}
          />
        ))}
      </div>
      <span className='text-xs tabular-nums w-8 text-right'>{pct}</span>
    </div>
  )
}

export function RvolCell({ value }: { value: unknown }) {
  if (value == null) return <span className='text-muted-foreground'>—</span>
  const num = Number(value)
  if (Number.isNaN(num)) return <span className='text-muted-foreground'>—</span>
  const isHigh = num >= 1.2
  const isLow = num <= 0.8
  return (
    <span
      className={cn(
        'font-medium tabular-nums',
        isHigh ? 'text-emerald-400' : isLow ? 'text-red-400' : 'text-muted-foreground'
      )}
    >
      {num.toFixed(2)}×
    </span>
  )
}

export function StatusBadge({ score }: { score: number | null }) {
  if (score == null) return null
  if (score >= 60)
    return <Badge className='bg-emerald-500/15 text-emerald-400 border-emerald-500/30 text-xs'>Outperforming</Badge>
  if (score <= 40)
    return <Badge className='bg-red-500/15 text-red-400 border-red-500/30 text-xs'>Underperforming</Badge>
  return <Badge className='bg-amber-500/15 text-amber-400 border-amber-500/30 text-xs'>Neutral</Badge>
}

/* ── Column definitions ────────────────────────────────── */

function buildColumns(_level: SectorLevel): ColumnDef<SectorGroup>[] {
  return [
    {
      accessorKey: 'group_name',
      header: ({ column }) => <DataTableColumnHeader column={column} title='Name' />,
      cell: ({ row }) => (
        <div className='font-medium max-w-[200px] truncate' title={row.getValue('group_name')}>
          {row.getValue('group_name')}
        </div>
      ),
      enableHiding: false,
    },
    {
      accessorKey: 'stock_count',
      header: ({ column }) => <DataTableColumnHeader column={column} title='Stocks' />,
      cell: ({ row }) => (
        <span className='text-muted-foreground tabular-nums'>{row.getValue('stock_count')}</span>
      ),
    },
    {
      accessorKey: 'score',
      header: ({ column }) => <DataTableColumnHeader column={column} title='Score' />,
      cell: ({ row }) => <ScoreBar score={row.getValue('score')} />,
      sortingFn: 'basic',
      sortDescFirst: true,
      enableHiding: false,
    },
    {
      accessorKey: 'ret_1d',
      header: ({ column }) => <DataTableColumnHeader column={column} title='1D %' />,
      cell: ({ row }) => <ReturnCell value={row.getValue('ret_1d')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'ret_1w',
      header: ({ column }) => <DataTableColumnHeader column={column} title='1W %' />,
      cell: ({ row }) => <ReturnCell value={row.getValue('ret_1w')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'ret_1m',
      header: ({ column }) => <DataTableColumnHeader column={column} title='1M %' />,
      cell: ({ row }) => <ReturnCell value={row.getValue('ret_1m')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'ret_3m',
      header: ({ column }) => <DataTableColumnHeader column={column} title='3M %' />,
      cell: ({ row }) => <ReturnCell value={row.getValue('ret_3m')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'ret_6m',
      header: ({ column }) => <DataTableColumnHeader column={column} title='6M %' />,
      cell: ({ row }) => <ReturnCell value={row.getValue('ret_6m')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'ret_1y',
      header: ({ column }) => <DataTableColumnHeader column={column} title='1Y %' />,
      cell: ({ row }) => <ReturnCell value={row.getValue('ret_1y')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'vol_ratio',
      header: ({ column }) => <DataTableColumnHeader column={column} title='RVOL' />,
      cell: ({ row }) => <RvolCell value={row.getValue('vol_ratio')} />,
      sortingFn: 'basic',
    },
    {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => <StatusBadge score={row.original.score} />,
      enableSorting: false,
    },
    {
      id: 'ad',
      header: 'A/D',
      cell: ({ row }) => (
        <div className='whitespace-nowrap'>
          <span className='text-xs text-emerald-400'>{row.original.adv_count}↑</span>
          <span className='text-xs text-muted-foreground mx-1'>/</span>
          <span className='text-xs text-red-400'>{row.original.dec_count}↓</span>
        </div>
      ),
      enableSorting: false,
    },

  ]
}

/* Default: show 1D, 1W, 1M — hide 3M, 6M, 1Y */
const DEFAULT_VISIBILITY: VisibilityState = {
  ret_3m: false,
  ret_6m: false,
  ret_1y: false,
}

/* ── Virtualized table body ─────────────────────────────── */

function VirtualizedTableBody({
  table,
  columns,
}: {
  table: ReturnType<typeof useReactTable<SectorGroup>>
  columns: ColumnDef<SectorGroup>[]
}) {
  const parentRef = useRef<HTMLDivElement>(null)
  const { rows } = table.getRowModel()

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 40,
    overscan: 10,
  })

  return (
    <div ref={parentRef} className='overflow-auto max-h-[calc(100vh-16rem)]'>
      <Table>
        <TableHeader className='sticky top-0 z-10 bg-background'>
          {table.getHeaderGroups().map(headerGroup => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map(header => (
                <TableHead key={header.id} className='whitespace-nowrap'>
                  {header.isPlaceholder
                    ? null
                    : flexRender(header.column.columnDef.header, header.getContext())}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {rows.length ? (
            <>
              {virtualizer.getVirtualItems().length > 0 && (
                <tr style={{ height: virtualizer.getVirtualItems()[0]?.start ?? 0 }} />
              )}
              {virtualizer.getVirtualItems().map(virtualRow => {
                const row = rows[virtualRow.index]
                return (
                  <TableRow
                    key={row.id}
                    data-index={virtualRow.index}
                    ref={virtualizer.measureElement}
                    className='hover:bg-muted/20'
                  >
                    {row.getVisibleCells().map(cell => (
                      <TableCell key={cell.id} className='px-3 py-2.5'>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                )
              })}
              {virtualizer.getVirtualItems().length > 0 && (
                <tr
                  style={{
                    height:
                      virtualizer.getTotalSize() -
                      ((items) => items.length > 0 ? items[items.length - 1].end : 0)(virtualizer.getVirtualItems()),
                  }}
                />
              )}
            </>
          ) : (
            <TableRow>
              <TableCell colSpan={columns.length} className='h-24 text-center text-muted-foreground'>
                No data available
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
}

/* ── Table component ───────────────────────────────────── */

type Props = {
  level: SectorLevel
  groups: SectorGroup[]
  loading?: boolean
}

export function SectorTable({ level, groups, loading }: Props) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'score', desc: true },
  ])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(DEFAULT_VISIBILITY)

  const columns = useMemo(() => buildColumns(level), [level])

  const table = useReactTable({
    data: groups,
    columns,
    state: { sorting, columnVisibility },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    enableSortingRemoval: false,
  })

  if (loading) {
    return (
      <div className='space-y-2 p-4'>
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className='h-10 bg-muted/40 rounded animate-pulse' />
        ))}
      </div>
    )
  }

  return (
    <div className='flex flex-col'>
      {/* Toolbar with column toggle */}
      <div className='flex items-center justify-end px-3 py-2 border-b border-border/50'>
        <DataTableViewOptions table={table} />
      </div>

      {/* Virtualized Table */}
      <VirtualizedTableBody table={table} columns={columns} />
    </div>
  )
}
