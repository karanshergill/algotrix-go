import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useNewsFeed } from '../use-news'
import { NewsCard } from './news-card'

type Props = {
  date: string
  source?: string
  symbol?: string
  marketMoving: boolean
}

export function NewsFeed({ date, source, symbol, marketMoving }: Props) {
  const {
    data,
    isLoading,
    isError,
    refetch,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useNewsFeed(date, source, symbol, marketMoving)

  const items = data?.pages.flatMap((p) => p.items) ?? []

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
        Failed to load feed.
        <Button variant='ghost' size='sm' onClick={() => refetch()}>
          Retry
        </Button>
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className='flex items-center justify-center h-32 text-muted-foreground text-sm'>
        No news for {date}
      </div>
    )
  }

  return (
    <>
      <Card className='overflow-hidden'>
        <table className='w-full text-sm'>
          <tbody>
            {items.map((item) => (
              <NewsCard key={`${item.source}-${item.id}`} item={item} />
            ))}
          </tbody>
        </table>
      </Card>
      {hasNextPage && (
        <div className='flex justify-center mt-4'>
          <Button
            variant='outline'
            size='sm'
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
          >
            {isFetchingNextPage ? 'Loading...' : 'Load more'}
          </Button>
        </div>
      )}
    </>
  )
}
