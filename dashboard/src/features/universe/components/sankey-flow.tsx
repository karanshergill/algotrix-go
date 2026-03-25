import { useMemo, useRef, useEffect, useState } from 'react'
import {
  sankey,
  sankeyLinkHorizontal,
  type SankeyNode,
  type SankeyLink,
} from 'd3-sankey'
import type { FilterStageResult } from '../universe-filters'
import type { FilteredStock } from '../types'

interface SankeyFlowProps {
  stages: FilterStageResult[]
  filtered: FilteredStock[]
  totalStocks: number
}

interface SNode {
  name: string
  id: string
}

interface SLink {
  source: number
  target: number
  value: number
}

type SNodeExt = SankeyNode<SNode, SLink>
type SLinkExt = SankeyLink<SNode, SLink>

const TIER_COLORS = {
  D50: '#ef4444',
  D30: '#f59e0b',
  D5: '#3b82f6',
  Eligible: '#10b981',
}

export function SankeyFlow({ stages, filtered, totalStocks }: SankeyFlowProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState({ width: 500, height: 300 })
  const [hoveredLink, setHoveredLink] = useState<number | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) setDims({ width, height })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  const { nodes, links } = useMemo(() => {
    if (stages.length === 0 || totalStocks === 0)
      return { nodes: [] as SNodeExt[], links: [] as SLinkExt[] }

    const sNodes: SNode[] = [{ name: `Raw (${totalStocks})`, id: 'raw' }]
    const sLinks: SLink[] = []

    stages.forEach((stage, i) => {
      const passId = `pass_${i}`
      const failId = `fail_${i}`
      sNodes.push({ name: `${stage.stageName} Pass`, id: passId })
      sNodes.push({ name: `Fail: ${stage.stageName}`, id: failId })

      const sourceIdx = i === 0 ? 0 : sNodes.findIndex((n) => n.id === `pass_${i - 1}`)
      const passIdx = sNodes.findIndex((n) => n.id === passId)
      const failIdx = sNodes.findIndex((n) => n.id === failId)

      if (stage.passCount > 0)
        sLinks.push({ source: sourceIdx, target: passIdx, value: stage.passCount })
      if (stage.failCount > 0)
        sLinks.push({ source: sourceIdx, target: failIdx, value: stage.failCount })
    })

    // Tier nodes at the end
    const lastPassIdx = sNodes.findIndex(
      (n) => n.id === `pass_${stages.length - 1}`
    )
    const d50Count = filtered.filter((s) => s.tier === 'D50').length
    const d30Count = filtered.filter((s) => s.tier === 'D30').length
    const d5Count = filtered.filter((s) => s.tier === 'D5').length
    const eligibleCount = filtered.filter((s) => s.tier === 'Eligible').length

    if (d50Count > 0) {
      sNodes.push({ name: `D50 (${d50Count})`, id: 'D50' })
      sLinks.push({
        source: lastPassIdx,
        target: sNodes.length - 1,
        value: d50Count,
      })
    }
    if (d30Count > 0) {
      sNodes.push({ name: `D30 (${d30Count})`, id: 'D30' })
      sLinks.push({
        source: lastPassIdx,
        target: sNodes.length - 1,
        value: d30Count,
      })
    }
    if (d5Count > 0) {
      sNodes.push({ name: `D5 (${d5Count})`, id: 'D5' })
      sLinks.push({
        source: lastPassIdx,
        target: sNodes.length - 1,
        value: d5Count,
      })
    }
    if (eligibleCount > 0) {
      sNodes.push({ name: `Eligible (${eligibleCount})`, id: 'Eligible' })
      sLinks.push({
        source: lastPassIdx,
        target: sNodes.length - 1,
        value: eligibleCount,
      })
    }

    const layout = sankey<SNode, SLink>()
      .nodeWidth(14)
      .nodePadding(10)
      .extent([
        [16, 16],
        [dims.width - 16, dims.height - 16],
      ])

    const result = layout({
      nodes: sNodes.map((n) => ({ ...n })),
      links: sLinks.map((l) => ({ ...l })),
    })

    return { nodes: result.nodes, links: result.links }
  }, [stages, filtered, totalStocks, dims])

  const linkPath = sankeyLinkHorizontal()

  function getLinkColor(link: SLinkExt, _idx: number) {
    const target = link.target as SNodeExt
    if (target.id === 'D50') return TIER_COLORS.D50
    if (target.id === 'D30') return TIER_COLORS.D30
    if (target.id === 'D5') return TIER_COLORS.D5
    if (target.id === 'Eligible') return TIER_COLORS.Eligible
    if (target.id?.startsWith('fail')) return '#6b7280'
    return '#22c55e'
  }

  function getNodeColor(node: SNodeExt) {
    if (node.id === 'D50') return TIER_COLORS.D50
    if (node.id === 'D30') return TIER_COLORS.D30
    if (node.id === 'D5') return TIER_COLORS.D5
    if (node.id === 'Eligible') return TIER_COLORS.Eligible
    if (node.id?.startsWith('fail')) return '#374151'
    return '#10b981'
  }

  return (
    <div ref={containerRef} className='h-full w-full'>
      <svg ref={svgRef} width={dims.width} height={dims.height}>
        <defs>
          {links.map((link, i) => {
            const s = link.source as SNodeExt
            const t = link.target as SNodeExt
            return (
              <linearGradient
                key={i}
                id={`link-grad-${i}`}
                gradientUnits='userSpaceOnUse'
                x1={s.x1}
                x2={t.x0}
              >
                <stop offset='0%' stopColor={getNodeColor(s)} stopOpacity={0.5} />
                <stop offset='100%' stopColor={getLinkColor(link, i)} stopOpacity={0.5} />
              </linearGradient>
            )
          })}
        </defs>
        <g>
          {links.map((link, i) => (
            <path
              key={i}
              d={linkPath(link as never) ?? ''}
              fill='none'
              stroke={`url(#link-grad-${i})`}
              strokeWidth={Math.max((link as never as { width: number }).width, 1)}
              strokeOpacity={hoveredLink === null ? 0.6 : hoveredLink === i ? 0.9 : 0.15}
              onMouseEnter={() => setHoveredLink(i)}
              onMouseLeave={() => setHoveredLink(null)}
              className='transition-opacity duration-150'
            />
          ))}
        </g>
        <g>
          {nodes.map((node, i) => {
            const x0 = node.x0 ?? 0
            const y0 = node.y0 ?? 0
            const x1 = node.x1 ?? 0
            const y1 = node.y1 ?? 0
            const height = y1 - y0
            return (
              <g key={i}>
                <rect
                  x={x0}
                  y={y0}
                  width={x1 - x0}
                  height={Math.max(height, 1)}
                  fill={getNodeColor(node)}
                  rx={2}
                />
                <text
                  x={x0 < dims.width / 2 ? x1 + 6 : x0 - 6}
                  y={y0 + height / 2}
                  textAnchor={x0 < dims.width / 2 ? 'start' : 'end'}
                  dominantBaseline='central'
                  className='fill-muted-foreground text-[10px]'
                >
                  {node.name}
                </text>
              </g>
            )
          })}
        </g>
      </svg>
    </div>
  )
}
