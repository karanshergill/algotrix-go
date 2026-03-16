import { useMemo, useState } from 'react'
import {
  type ColumnDef,
  type PaginationState,
  type SortingState,
  type VisibilityState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { DataTableColumnHeader, DataTablePagination, DataTableViewOptions } from '@/components/data-table'
import { cn } from '@/lib/utils'
import type { StockScore } from './types'

type Props = {
  stocks: StockScore[]
  symbolLookup: Record<string, string>
  onRowClick: (isin: string) => void
}

function scoreColor(score: number): string {
  if (score >= 70) return 'text-emerald-400 font-bold'
  if (score >= 40) return 'text-amber-400 font-bold'
  return 'text-red-400 font-bold'
}

function PctCell({ value }: { value: number }) {
  const color = value >= 75 ? 'text-emerald-400' : value < 30 ? 'text-red-400' : 'text-muted-foreground'
  return <span className={cn('tabular-nums', color)}>{value.toFixed(1)}</span>
}

function buildColumns(symbolLookup: Record<string, string>): ColumnDef<StockScore>[] {
  return [
    {
      id: 'rank',
      header: '#',
      cell: ({ row }) => (
        <span className='text-muted-foreground tabular-nums text-xs'>{row.index + 1}</span>
      ),
      enableSorting: false,
    },
    {
      accessorKey: 'ISIN',
      header: ({ column }) => <DataTableColumnHeader column={column} title='Symbol' />,
      cell: ({ row }) => {
        const isin = row.getValue('ISIN') as string
        const sym = symbolLookup[isin] ?? '???'
        return (
          <div>
            <div className='font-semibold'>{sym}</div>
            <div className='text-[10px] text-muted-foreground/60'>{isin}</div>
          </div>
        )
      },
      sortingFn: (a, b) => {
        const symA = symbolLookup[a.original.ISIN] ?? ''
        const symB = symbolLookup[b.original.ISIN] ?? ''
        return symA.localeCompare(symB)
      },
    },
    {
      accessorKey: 'Composite',
      header: ({ column }) => <DataTableColumnHeader column={column} title='Score' />,
      cell: ({ row }) => {
        const score = row.getValue('Composite') as number
        return <span className={cn('tabular-nums', scoreColor(score))}>{score.toFixed(1)}</span>
      },
      sortingFn: 'basic',
      sortDescFirst: true,
    },
    {
      accessorKey: 'PctMADTV',
      header: ({ column }) => <DataTableColumnHeader column={column} title='MADTV%' />,
      cell: ({ row }) => <PctCell value={row.getValue('PctMADTV')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'PctAmihud',
      header: ({ column }) => <DataTableColumnHeader column={column} title='Amihud%' />,
      cell: ({ row }) => <PctCell value={row.getValue('PctAmihud')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'PctATRPct',
      header: ({ column }) => <DataTableColumnHeader column={column} title='ATR%P' />,
      cell: ({ row }) => <PctCell value={row.getValue('PctATRPct')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'PctParkinson',
      header: ({ column }) => <DataTableColumnHeader column={column} title='Park%' />,
      cell: ({ row }) => <PctCell value={row.getValue('PctParkinson')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'PctTradeSize',
      header: ({ column }) => <DataTableColumnHeader column={column} title='TrdSz%' />,
      cell: ({ row }) => <PctCell value={row.getValue('PctTradeSize')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'PctADRPct',
      header: ({ column }) => <DataTableColumnHeader column={column} title='ADR%P' />,
      cell: ({ row }) => <PctCell value={row.getValue('PctADRPct')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'PctRangeEff',
      header: ({ column }) => <DataTableColumnHeader column={column} title='RngEf%' />,
      cell: ({ row }) => <PctCell value={row.getValue('PctRangeEff')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'PctMomentum',
      header: ({ column }) => <DataTableColumnHeader column={column} title='Mom%' />,
      cell: ({ row }) => <PctCell value={row.getValue('PctMomentum')} />,
      sortingFn: 'basic',
    },
    {
      accessorKey: 'TradingDays',
      header: ({ column }) => <DataTableColumnHeader column={column} title='Days' />,
      cell: ({ row }) => (
        <span className='tabular-nums text-muted-foreground'>{row.getValue('TradingDays')}</span>
      ),
      sortingFn: 'basic',
    },
  ]
}

export function WatchlistTable({ stocks, symbolLookup, onRowClick }: Props) {
  const [sorting, setSorting] = useState<SortingState>([{ id: 'Composite', desc: true }])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 20 })

  const columns = useMemo(() => buildColumns(symbolLookup), [symbolLookup])

  const table = useReactTable({
    data: stocks,
    columns,
    state: { sorting, columnVisibility, pagination },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    enableSortingRemoval: false,
  })

  return (
    <div className='flex flex-col gap-4'>
      <div className='flex justify-end px-1'>
        <DataTableViewOptions table={table} />
      </div>
      <div className='overflow-auto max-h-[calc(100vh-26rem)]'>
        <Table>
          <TableHeader className='sticky top-0 z-10 bg-background'>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id}>
                {hg.headers.map((header) => (
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
            {table.getRowModel().rows.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  className='hover:bg-muted/20 cursor-pointer'
                  onClick={() => onRowClick(row.original.ISIN)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className='px-3 py-2.5'>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={columns.length} className='h-24 text-center text-muted-foreground'>
                  No qualified stocks
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <DataTablePagination table={table} />
    </div>
  )
}
