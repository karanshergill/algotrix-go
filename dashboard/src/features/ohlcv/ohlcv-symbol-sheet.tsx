import { useMemo, useState } from 'react'
import {
  type ColumnDef,
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
  flexRender,
  getCoreRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { Badge } from '@/components/ui/badge'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  DataTableColumnHeader,
  DataTablePagination,
  DataTableToolbar,
} from '@/components/data-table'
import { cn } from '@/lib/utils'
import { RESOLUTION_LABELS, type Resolution } from './constants'
import { useOhlcvSymbols } from './use-ohlcv-symbols'

interface OhlcvSymbolSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  date: string | null
  resolution: Resolution | null
}

type OhlcvSymbolTableRow = {
  symbol: string
  isin: string
  status: 'full' | 'missing'
  rowCount: number
}

const columns: ColumnDef<OhlcvSymbolTableRow>[] = [
  {
    accessorKey: 'symbol',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Symbol' />
    ),
    cell: ({ row }) => (
      <div className='font-medium'>{row.getValue('symbol')}</div>
    ),
  },
  {
    accessorKey: 'isin',
    header: ({ column }) => <DataTableColumnHeader column={column} title='ISIN' />,
    cell: ({ row }) => (
      <div className='font-mono text-xs'>{row.getValue('isin')}</div>
    ),
  },
  {
    accessorKey: 'status',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Status' />
    ),
    cell: ({ row }) => {
      const value = row.getValue('status') as OhlcvSymbolTableRow['status']
      return (
        <Badge
          variant={value === 'full' ? 'outline' : 'secondary'}
          className={cn(
            value === 'full' && 'border-emerald-500/30 text-emerald-500'
          )}
        >
          {value === 'full' ? 'Full' : 'Missing'}
        </Badge>
      )
    },
  },
  {
    accessorKey: 'rowCount',
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title='Row Count' />
    ),
    cell: ({ row }) => (
      <div className='text-right tabular-nums'>
        {Number(row.getValue('rowCount')).toLocaleString()}
      </div>
    ),
    meta: {
      className: 'w-32 text-right',
    },
  },
]

export function OhlcvSymbolSheet({
  open,
  onOpenChange,
  date,
  resolution,
}: OhlcvSymbolSheetProps) {
  const { data, isLoading, isError, error } = useOhlcvSymbols({
    date: date ?? '',
    resolution: resolution ?? '1d',
    enabled: open && date !== null && resolution !== null,
  })

  const rows = useMemo<OhlcvSymbolTableRow[]>(
    () =>
      data?.symbols.map((symbol) => ({
        symbol: symbol.symbol,
        isin: symbol.isin,
        status: symbol.hasData ? 'full' : 'missing',
        rowCount: symbol.rowCount,
      })) ?? [],
    [data]
  )

  const [sorting, setSorting] = useState<SortingState>([
    { id: 'symbol', desc: false },
  ])
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})

  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns,
    state: {
      sorting,
      columnFilters,
      columnVisibility,
    },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFacetedRowModel: getFacetedRowModel(),
    getFacetedUniqueValues: getFacetedUniqueValues(),
  })

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side='right' className='flex w-full flex-col sm:max-w-3xl'>
        <SheetHeader className='text-start'>
          <SheetTitle>
            {resolution ? RESOLUTION_LABELS[resolution] : 'OHLCV'} symbols
          </SheetTitle>
          <SheetDescription>
            {date
              ? `Coverage details for ${date}`
              : 'Select a day to inspect symbol coverage.'}
          </SheetDescription>
        </SheetHeader>

        <div className='mt-6 flex min-h-0 flex-1 flex-col gap-4'>
          {isLoading ? (
            <div className='rounded-lg border p-6 text-sm text-muted-foreground'>
              Loading symbols...
            </div>
          ) : isError ? (
            <div className='rounded-lg border border-destructive/20 p-6 text-sm text-destructive'>
              {error instanceof Error
                ? error.message
                : 'Failed to load symbol coverage'}
            </div>
          ) : (
            <>
              <DataTableToolbar
                table={table}
                searchPlaceholder='Search symbol...'
                searchKey='symbol'
                filters={[
                  {
                    columnId: 'status',
                    title: 'Status',
                    options: [
                      { label: 'Full', value: 'full' },
                      { label: 'Missing', value: 'missing' },
                    ],
                  },
                ]}
              />

              <div className='min-h-0 overflow-hidden rounded-md border'>
                <Table>
                  <TableHeader>
                    {table.getHeaderGroups().map((headerGroup) => (
                      <TableRow key={headerGroup.id}>
                        {headerGroup.headers.map((header) => (
                          <TableHead
                            key={header.id}
                            colSpan={header.colSpan}
                            className={cn(
                              header.column.columnDef.meta?.className,
                              header.column.columnDef.meta?.thClassName
                            )}
                          >
                            {header.isPlaceholder
                              ? null
                              : flexRender(
                                  header.column.columnDef.header,
                                  header.getContext()
                                )}
                          </TableHead>
                        ))}
                      </TableRow>
                    ))}
                  </TableHeader>
                  <TableBody>
                    {table.getRowModel().rows.length > 0 ? (
                      table.getRowModel().rows.map((row) => (
                        <TableRow key={row.id}>
                          {row.getVisibleCells().map((cell) => (
                            <TableCell
                              key={cell.id}
                              className={cn(
                                cell.column.columnDef.meta?.className,
                                cell.column.columnDef.meta?.tdClassName
                              )}
                            >
                              {flexRender(
                                cell.column.columnDef.cell,
                                cell.getContext()
                              )}
                            </TableCell>
                          ))}
                        </TableRow>
                      ))
                    ) : (
                      <TableRow>
                        <TableCell
                          colSpan={columns.length}
                          className='h-24 text-center'
                        >
                          No symbols found.
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>

              <DataTablePagination table={table} className='mt-auto' />
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
