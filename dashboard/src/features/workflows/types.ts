import type { Node, Edge } from '@xyflow/react'

export type NodeCategory = 'source' | 'filter' | 'enrich' | 'score' | 'logic' | 'output'

export interface WorkflowNodeData {
  label: string
  description: string
  category: NodeCategory
  icon: string
  [key: string]: unknown
}

export type WorkflowNode = Node<WorkflowNodeData>
export type WorkflowEdge = Edge

export interface PaletteItem {
  type: string
  label: string
  description: string
  category: NodeCategory
  icon: string
}

export const CATEGORY_COLORS: Record<NodeCategory, string> = {
  source: '#22c55e',
  filter: '#3b82f6',
  enrich: '#eab308',
  score: '#f97316',
  logic: '#a855f7',
  output: '#ef4444',
}

export const PALETTE_ITEMS: PaletteItem[] = [
  // SOURCE
  { type: 'source', label: 'Stock Universe', description: 'All NSE listed stocks', category: 'source', icon: '📊' },
  { type: 'source', label: 'FnO Universe', description: 'F&O eligible stocks', category: 'source', icon: '📈' },
  { type: 'source', label: 'Index Membership', description: 'Nifty 50, Next 50, etc.', category: 'source', icon: '🏛️' },
  { type: 'source', label: 'Sector/Industry', description: 'Filter by sector', category: 'source', icon: '🏭' },
  { type: 'source', label: 'Manual List', description: 'Custom stock list', category: 'source', icon: '📝' },
  // FILTER
  { type: 'filter', label: 'Liquidity Filter', description: 'ADTV threshold', category: 'filter', icon: '💧' },
  { type: 'filter', label: 'Volatility Filter', description: 'ATR range filter', category: 'filter', icon: '🌊' },
  { type: 'filter', label: 'Market Cap Filter', description: 'Market cap range', category: 'filter', icon: '💰' },
  { type: 'filter', label: 'Price Filter', description: 'Price range filter', category: 'filter', icon: '🏷️' },
  { type: 'filter', label: 'Sector Filter', description: 'Include/exclude sectors', category: 'filter', icon: '🔍' },
  // ENRICH
  { type: 'enrich', label: 'Volume Profile', description: 'POC, VAH, VAL levels', category: 'enrich', icon: '📊' },
  { type: 'enrich', label: 'ATR Calculator', description: 'Multi-timeframe ATR', category: 'enrich', icon: '📐' },
  { type: 'enrich', label: 'Range Detector', description: 'Detect price ranges', category: 'enrich', icon: '↔️' },
  { type: 'enrich', label: 'Hurst Exponent', description: 'Mean-reversion score', category: 'enrich', icon: '🔬' },
  { type: 'enrich', label: 'Live Feed Data', description: 'Current LTP, volume, OI', category: 'enrich', icon: '⚡' },
  { type: 'enrich', label: 'Correlation', description: 'Top correlated peers', category: 'enrich', icon: '🔗' },
  // SCORE
  { type: 'score', label: 'Top N', description: 'Select top N stocks', category: 'score', icon: '🏆' },
  { type: 'score', label: 'Rank', description: 'Rank by metric', category: 'score', icon: '📏' },
  { type: 'score', label: 'Weighted Score', description: 'Multi-factor scoring', category: 'score', icon: '⚖️' },
  // LOGIC
  { type: 'logic', label: 'AND (Intersection)', description: 'Stocks in ALL inputs', category: 'logic', icon: '∩' },
  { type: 'logic', label: 'OR (Union)', description: 'Stocks in ANY input', category: 'logic', icon: '∪' },
  { type: 'logic', label: 'NOT (Exclude)', description: 'Remove from second input', category: 'logic', icon: '⊘' },
  { type: 'logic', label: 'Condition', description: 'If/else branching', category: 'logic', icon: '🔀' },
  { type: 'logic', label: 'Time Gate', description: 'Execute during hours', category: 'logic', icon: '⏰' },
  // OUTPUT
  { type: 'output', label: 'Save to Watchlist', description: 'Save as watchlist', category: 'output', icon: '💾' },
  { type: 'output', label: 'Discord Alert', description: 'Send to Discord', category: 'output', icon: '🔔' },
  { type: 'output', label: 'Feed Subscribe', description: 'Subscribe to depth feed', category: 'output', icon: '📡' },
  { type: 'output', label: 'Dashboard Widget', description: 'Show on dashboard', category: 'output', icon: '🖥️' },
]
