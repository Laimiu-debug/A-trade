import { useEffect, useMemo } from 'react'

interface EllipsisMeasureProps {
  enableMeasure?: boolean
  text?: unknown
  children: (nodes: unknown[], canEllipsis: boolean) => React.ReactNode
  onEllipsis?: (isEllipsis: boolean) => void
}

function toNodeArray(input: unknown): unknown[] {
  if (input === null || input === undefined || input === false) return []
  return Array.isArray(input) ? input : [input]
}

// Runtime guard: fallback to plain render and never run measurement loops.
// This avoids rare infinite update cycles in antd Typography EllipsisMeasure.
export default function EllipsisMeasure(props: EllipsisMeasureProps) {
  const { text, children, onEllipsis } = props
  const nodeList = useMemo(() => toNodeArray(text), [text])

  useEffect(() => {
    onEllipsis?.(false)
  }, [onEllipsis, text])

  return <>{children(nodeList as unknown[], false)}</>
}
