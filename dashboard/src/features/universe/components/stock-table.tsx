import { useMemo, useState } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ArrowUpDown, Download } from 'lucide-react'
import type { FilteredStock, DepthTier } from '../types'

interface StockTableProps {
  stocks: FilteredStock[]
  highlightIsin?: string | null
}

const TIER_COLORS: Record<DepthTier, string> = {
  D50: 'bg-red-500/20 text-red-400 border-red-500/30',
  D30: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  D5: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  Eligible: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  none: 'bg-muted text-muted-foreground border-border',
}

function fmt(n: number): string {
  if (n >= 10_000_000) return `${(n / 10_000_000).toFixed(1)}Cr`
  if (n >= 100_000) return `${(n / 100_000).toFixed(1)}L`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return n.toLocaleString()
}

type ViewFilter = 'all' | 'pass' | 'fail'

export function StockTable({ stocks, highlightIsin }: StockTableProps) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'avgTurnover20d', desc: true },
  ])
  const [globalFilter, setGlobalFilter] = useState('')
  const [viewFilter, setViewFilter] = useState<ViewFilter>('all')

  const filteredData = useMemo(() => {
    if (viewFilter === 'all') return stocks
    if (viewFilter === 'pass') return stocks.filter((s) => s.pass)
    return stocks.filter((s) => !s.pass)
  }, [stocks, viewFilter])

  const columns = useMemo<ColumnDef<FilteredStock>[]>(
    () => [
      {
        accessorKey: 'symbol',
        header: () => <span className='text-foreground font-medium'>Symbol</span>,
        cell: ({ row }) => (
          <span className='font-medium text-foreground'>
            {row.original.symbol}
          </span>
        ),
        filterFn: 'includesString',
      },
      {
        accessorKey: 'lastPrice',
        header: ({ column }) => (
          <Button
            variant='ghost'
            size='sm'
            className='-ml-3 h-7 text-xs'
            onClick={() => column.toggleSorting()}
          >
            <span className='text-amber-400'>Price</span> <ArrowUpDown className='ml-1 size-3' />
          </Button>
        ),
        cell: ({ row }) => (
          <span className='tabular-nums text-amber-400/80'>
            ₹{row.original.lastPrice.toLocaleString()}
          </span>
        ),
      },
      {
        accessorKey: 'avgVolume20d',
        header: ({ column }) => (
          <Button
            variant='ghost'
            size='sm'
            className='-ml-3 h-7 text-xs'
            onClick={() => column.toggleSorting()}
          >
            <span className='text-cyan-400'>Avg Vol</span> <ArrowUpDown className='ml-1 size-3' />
          </Button>
        ),
        cell: ({ row }) => (
          <span className='tabular-nums text-cyan-400/80'>{fmt(row.original.avgVolume20d)}</span>
        ),
      },
      {
        accessorKey: 'avgTurnover20d',
        header: ({ column }) => (
          <Button
            variant='ghost'
            size='sm'
            className='-ml-3 h-7 text-xs'
            onClick={() => column.toggleSorting()}
          >
            <span className='text-violet-400'>Turnover</span> <ArrowUpDown className='ml-1 size-3' />
          </Button>
        ),
        cell: ({ row }) => (
          <span className='tabular-nums text-violet-400/80'>
            {fmt(row.original.avgTurnover20d)}
          </span>
        ),
      },
      {
        accessorKey: 'tradedDays',
        header: () => <span className='text-emerald-400 text-xs font-medium'>Days</span>,
        cell: ({ row }) => (
          <span className='tabular-nums text-emerald-400/80'>{row.original.tradedDays}/20</span>
        ),
      },
      {
        accessorKey: 'sector',
        header: () => <span className='text-rose-400 text-xs font-medium'>Sector</span>,
        cell: ({ row }) => (
          <span className='text-rose-400/70'>
            {row.original.sector ?? '—'}
          </span>
        ),
      },
      {
        accessorKey: 'tier',
        header: 'Tier',
        cell: ({ row }) => {
          const tier = row.original.tier
          return (
            <Badge variant='outline' className={TIER_COLORS[tier]}>
              {tier === 'none' ? '—' : tier === 'Eligible' ? 'Elig.' : tier}
            </Badge>
          )
        },
      },
      {
        id: 'status',
        header: 'Status',
        cell: ({ row }) => (
          <Badge
            variant={row.original.pass ? 'default' : 'secondary'}
            className={
              row.original.pass
                ? 'bg-emerald-500/20 text-emerald-400'
                : 'bg-muted text-muted-foreground'
            }
          >
            {row.original.pass ? 'Pass' : 'Fail'}
          </Badge>
        ),
      },
    ],
    []
  )

  const table = useReactTable({
    data: filteredData,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 50 } },
  })

  function exportCSV() {
    const headers = [
      'Symbol',
      'Price',
      'Avg Volume',
      'Avg Turnover',
      'Days',
      'Sector',
      'Tier',
      'Status',
    ]
    const rows = filteredData.map((s) => [
      s.symbol,
      s.lastPrice,
      s.avgVolume20d,
      s.avgTurnover20d,
      s.tradedDays,
      s.sector ?? '',
      s.tier,
      s.pass ? 'Pass' : 'Fail',
    ])
    const csv = [headers, ...rows].map((r) => r.join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'universe_stocks.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className='flex flex-col gap-3'>
      <div className='flex items-center gap-3'>
        <Input
          placeholder='Search symbol...'
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className='h-8 w-[200px]'
        />
        <Select
          value={viewFilter}
          onValueChange={(v) => setViewFilter(v as ViewFilter)}
        >
          <SelectTrigger className='h-8 w-[100px]'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>All</SelectItem>
            <SelectItem value='pass'>Pass</SelectItem>
            <SelectItem value='fail'>Fail</SelectItem>
          </SelectContent>
        </Select>
        <Button variant='outline' size='sm' className='h-8' onClick={exportCSV}>
          <Download className='mr-1.5 size-3' /> CSV
        </Button>
        <span className='ml-auto text-xs text-muted-foreground'>
          {table.getFilteredRowModel().rows.length} stocks
        </span>
      </div>

      <div className='overflow-auto rounded-md border border-border'>
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id}>
                {hg.headers.map((h) => (
                  <TableHead key={h.id} className='text-xs'>
                    {h.isPlaceholder
                      ? null
                      : flexRender(h.column.columnDef.header, h.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                className={
                  row.original.isin === highlightIsin
                    ? 'bg-primary/10'
                    : undefined
                }
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id} className='py-1.5 text-xs'>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className='flex items-center justify-between'>
        <span className='text-xs text-muted-foreground'>
          Page {table.getState().pagination.pageIndex + 1} of{' '}
          {table.getPageCount()}
        </span>
        <div className='flex gap-2'>
          <Button
            variant='outline'
            size='sm'
            className='h-7'
            onClick={() => table.previousPage()}
            disabled={!table.getCanPreviousPage()}
          >
            Prev
          </Button>
          <Button
            variant='outline'
            size='sm'
            className='h-7'
            onClick={() => table.nextPage()}
            disabled={!table.getCanNextPage()}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  )
}
