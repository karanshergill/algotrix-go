export type FeedSource = 'announcement' | 'block_deal' | 'board_meeting' | 'corporate_action' | 'insider_trading'

export type FeedItem = {
  id: number
  source: FeedSource
  symbol: string
  timestamp: string
  title: string | null
  category: string | null
  is_market_moving: boolean
  attachment_url: string | null
  traded_volume: number | null
  price: number | null
  traded_value: number | null
}

export type UpcomingMeeting = {
  id: number
  symbol: string
  meeting_date: string
  purpose: string
  description: string | null
}

export type UpcomingAction = {
  id: number
  symbol: string
  subject: string
  ex_date: string
  record_date: string | null
}

export type InsiderAggregate = {
  symbol: string
  net_value: number
  buy_value: number
  sell_value: number
  txn_count: number
}

export type InsiderTransaction = {
  id: number
  acquirer_name: string
  acquisition_mode: string
  shares_acquired: number
  value: number
  transaction_date: string
}

export type NewsSummary = {
  announcements: number
  market_moving: number
  block_deals: number
  upcoming_meetings: number
  upcoming_actions: number
}
