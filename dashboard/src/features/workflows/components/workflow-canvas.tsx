import { useCallback, useRef, type DragEvent } from 'react'
import {
  ReactFlow,
  MiniMap,
  Background,
  BackgroundVariant,
  Controls,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Connection,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { SourceNode } from './node-types/source-node'
import { FilterNode } from './node-types/filter-node'
import { EnrichNode } from './node-types/enrich-node'
import { ScoreNode } from './node-types/score-node'
import { LogicNode } from './node-types/logic-node'
import { OutputNode } from './node-types/output-node'
import { CATEGORY_COLORS, type PaletteItem, type WorkflowNode, type WorkflowEdge } from '../types'

const nodeTypes: NodeTypes = {
  source: SourceNode,
  filter: FilterNode,
  enrich: EnrichNode,
  score: ScoreNode,
  logic: LogicNode,
  output: OutputNode,
}

// Sample workflow: Two branches merging via AND
const initialNodes: WorkflowNode[] = [
  // Branch 1: FnO → Liquidity → Volatility
  {
    id: '1',
    type: 'source',
    position: { x: 50, y: 100 },
    data: { label: 'FnO Universe', description: 'F&O eligible stocks (~185)', category: 'source', icon: '📈' },
  },
  {
    id: '2',
    type: 'filter',
    position: { x: 300, y: 100 },
    data: { label: 'Liquidity Filter', description: 'ADTV ≥ 50 Cr', category: 'filter', icon: '💧' },
  },
  {
    id: '3',
    type: 'filter',
    position: { x: 550, y: 100 },
    data: { label: 'Volatility Filter', description: 'ATR 1.5% – 5%', category: 'filter', icon: '🌊' },
  },
  // Branch 2: Nifty 500 → Sector exclude IT
  {
    id: '6',
    type: 'source',
    position: { x: 50, y: 320 },
    data: { label: 'Index Membership', description: 'Nifty 500', category: 'source', icon: '🏛️' },
  },
  {
    id: '7',
    type: 'filter',
    position: { x: 300, y: 320 },
    data: { label: 'Sector Filter', description: 'Exclude: IT', category: 'filter', icon: '🔍' },
  },
  // Merge: AND intersection
  {
    id: '8',
    type: 'logic',
    position: { x: 800, y: 200 },
    data: { label: 'AND (Intersection)', description: 'Stocks in ALL inputs', category: 'logic', icon: '∩' },
  },
  // Enrich with range detection
  {
    id: '9',
    type: 'enrich',
    position: { x: 1050, y: 200 },
    data: { label: 'Range Detector', description: 'Confidence ≥ 70', category: 'enrich', icon: '↔️' },
  },
  // Score and output
  {
    id: '4',
    type: 'score',
    position: { x: 1300, y: 120 },
    data: { label: 'Top N', description: 'Top 10 by confidence', category: 'score', icon: '🏆' },
  },
  {
    id: '5',
    type: 'output',
    position: { x: 1550, y: 120 },
    data: { label: 'Save to Watchlist', description: 'Range Candidates', category: 'output', icon: '💾' },
  },
  {
    id: '10',
    type: 'output',
    position: { x: 1300, y: 320 },
    data: { label: 'Feed Subscribe', description: 'Fyers TBT 50-level', category: 'output', icon: '📡' },
  },
  {
    id: '11',
    type: 'output',
    position: { x: 1550, y: 320 },
    data: { label: 'Discord Alert', description: '#system channel', category: 'output', icon: '🔔' },
  },
]

const initialEdges: WorkflowEdge[] = [
  // Branch 1
  { id: 'e1-2', source: '1', target: '2', animated: true, style: { stroke: CATEGORY_COLORS.source } },
  { id: 'e2-3', source: '2', target: '3', animated: true, style: { stroke: CATEGORY_COLORS.filter } },
  { id: 'e3-8', source: '3', target: '8', targetHandle: 'a', animated: true, style: { stroke: CATEGORY_COLORS.filter } },
  // Branch 2
  { id: 'e6-7', source: '6', target: '7', animated: true, style: { stroke: CATEGORY_COLORS.source } },
  { id: 'e7-8', source: '7', target: '8', targetHandle: 'b', animated: true, style: { stroke: CATEGORY_COLORS.filter } },
  // Merge → Enrich → Split to outputs
  { id: 'e8-9', source: '8', target: '9', animated: true, style: { stroke: CATEGORY_COLORS.logic } },
  { id: 'e9-4', source: '9', target: '4', animated: true, style: { stroke: CATEGORY_COLORS.enrich } },
  { id: 'e4-5', source: '4', target: '5', animated: true, style: { stroke: CATEGORY_COLORS.score } },
  { id: 'e9-10', source: '9', target: '10', animated: true, style: { stroke: CATEGORY_COLORS.enrich } },
  { id: 'e9-11', source: '9', target: '11', animated: true, style: { stroke: CATEGORY_COLORS.enrich } },
]

let nodeId = 100

export function WorkflowCanvas() {
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const { screenToFlowPosition } = useReactFlow()
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges)

  const onConnect = useCallback(
    (params: Connection) => {
      const sourceNode = nodes.find((n) => n.id === params.source)
      const color = sourceNode ? CATEGORY_COLORS[sourceNode.data.category] : '#888'
      setEdges((eds) =>
        addEdge({ ...params, animated: true, style: { stroke: color } }, eds)
      )
    },
    [nodes, setEdges]
  )

  const onDragOver = useCallback((event: DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: DragEvent) => {
      event.preventDefault()

      const data = event.dataTransfer.getData('application/reactflow')
      if (!data) return

      const item: PaletteItem = JSON.parse(data)

      const position = screenToFlowPosition({
        x: event.clientX - 90,
        y: event.clientY - 30,
      })

      const newNode: WorkflowNode = {
        id: `node-${++nodeId}`,
        type: item.category,
        position,
        data: {
          label: item.label,
          description: item.description,
          category: item.category,
          icon: item.icon,
        },
      }

      setNodes((nds) => [...nds, newNode])
    },
    [setNodes]
  )

  return (
    <div ref={reactFlowWrapper} className='h-full w-full'>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onDragOver={onDragOver}
        onDrop={onDrop}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        deleteKeyCode='Backspace'
        className='bg-background'
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} className='!bg-background' />
        <Controls className='!bg-card !border !shadow-md [&>button]:!bg-card [&>button]:!border-border [&>button]:!text-foreground' />
        <MiniMap
          className='!bg-card !border !shadow-md'
          nodeColor={(node) => {
            const data = node.data as { category?: string }
            return CATEGORY_COLORS[data.category as keyof typeof CATEGORY_COLORS] ?? '#888'
          }}
          maskColor='hsl(var(--background) / 0.7)'
        />
      </ReactFlow>
    </div>
  )
}
