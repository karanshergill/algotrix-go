import { useQuery, useInfiniteQuery } from '@tanstack/react-query'
import { isMarketOpen } from '@/lib/market-hours'
import type { FeedItem, NewsSummary, UpcomingMeeting, UpcomingAction, InsiderAggregate, InsiderTransaction } from './types'

const PAGE_SIZE = 50

async function fetchFeed(params: { date: string; source?: string; symbol?: string; marketMoving?: boolean; limit: number; offset: number }) {
  const sp = new URLSearchParams({ date: params.date, limit: String(params.limit), offset: String(params.offset) })
  if (params.source) sp.set('source', params.source)
  if (params.symbol) sp.set('symbol', params.symbol)
  if (params.marketMoving) sp.set('market_moving', 'true')
  const res = await fetch(`/api/news?${sp}`)
  if (!res.ok) throw new Error('Failed to fetch feed')
  return res.json() as Promise<{ items: FeedItem[]; has_more: boolean }>
}

async function fetchSummary(date: string) {
  const res = await fetch(`/api/news/summary?date=${date}`)
  if (!res.ok) throw new Error('Failed to fetch summary')
  return res.json() as Promise<NewsSummary>
}

async function fetchUpcoming(symbol?: string) {
  const sp = new URLSearchParams()
  if (symbol) sp.set('symbol', symbol)
  const res = await fetch(`/api/news/upcoming?${sp}`)
  if (!res.ok) throw new Error('Failed to fetch upcoming')
  return res.json() as Promise<{ meetings: UpcomingMeeting[]; actions: UpcomingAction[] }>
}

async function fetchInsider(params: { days: number; symbol?: string; limit: number; offset: number }) {
  const sp = new URLSearchParams({ days: String(params.days), limit: String(params.limit), offset: String(params.offset) })
  if (params.symbol) sp.set('symbol', params.symbol)
  const res = await fetch(`/api/news/insider-activity?${sp}`)
  if (!res.ok) throw new Error('Failed to fetch insider activity')
  return res.json()
}

async function fetchDetail(source: string, id: number) {
  const res = await fetch(`/api/news/${source}/${id}`)
  if (!res.ok) throw new Error('Failed to fetch detail')
  return res.json() as Promise<{ raw_json: Record<string, unknown> }>
}

export function useNewsFeed(date: string, source?: string, symbol?: string, marketMoving?: boolean) {
  return useInfiniteQuery({
    queryKey: ['news-feed', date, source, symbol, marketMoving],
    queryFn: ({ pageParam = 0 }) => fetchFeed({ date, source, symbol, marketMoving, limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, _allPages, lastPageParam) =>
      lastPage.has_more ? lastPageParam + PAGE_SIZE : undefined,
    refetchInterval: isMarketOpen() ? 120_000 : false,
  })
}

export function useNewsSummary(date: string) {
  return useQuery({
    queryKey: ['news-summary', date],
    queryFn: () => fetchSummary(date),
    refetchInterval: isMarketOpen() ? 120_000 : false,
  })
}

export function useUpcomingEvents(symbol?: string) {
  return useQuery({
    queryKey: ['news-upcoming', symbol],
    queryFn: () => fetchUpcoming(symbol),
  })
}

export function useInsiderActivity(days: number, symbol?: string) {
  return useQuery({
    queryKey: ['news-insider', days, symbol],
    queryFn: () => fetchInsider({ days, symbol, limit: 20, offset: 0 }),
  })
}

export function useInsiderDrilldown(days: number, symbol: string) {
  return useInfiniteQuery({
    queryKey: ['news-insider-drill', days, symbol],
    queryFn: ({ pageParam = 0 }) => fetchInsider({ days, symbol, limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage: { has_more: boolean }, _allPages: unknown[], lastPageParam: number) =>
      lastPage.has_more ? lastPageParam + PAGE_SIZE : undefined,
    enabled: !!symbol,
  })
}

export function useNewsDetail(source: string, id: number) {
  return useQuery({
    queryKey: ['news-detail', source, id],
    queryFn: () => fetchDetail(source, id),
    enabled: false,
    staleTime: Infinity,
  })
}
