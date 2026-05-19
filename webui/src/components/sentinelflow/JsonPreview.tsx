import { useMemo, useState } from 'react'

type JsonPreviewProps = {
  value: unknown
}

function describeValue(value: unknown): { label: string; shouldStringifyCollapsed: boolean } {
  if (value === null) return { label: 'null', shouldStringifyCollapsed: true }
  if (Array.isArray(value)) {
    return {
      label: `Array(${value.length})`,
      shouldStringifyCollapsed: value.length <= 12,
    }
  }
  if (typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>)
    return {
      label: `Object(${keys.length})${keys.length ? ` · ${keys.slice(0, 8).join(', ')}${keys.length > 8 ? ', ...' : ''}` : ''}`,
      shouldStringifyCollapsed: keys.length <= 8,
    }
  }
  return { label: typeof value, shouldStringifyCollapsed: true }
}

export default function JsonPreview({ value }: JsonPreviewProps) {
  const [expanded, setExpanded] = useState(false)
  const descriptor = useMemo(() => describeValue(value), [value])
  const text = useMemo(() => {
    if (!expanded && !descriptor.shouldStringifyCollapsed) return ''
    return JSON.stringify(value, null, 2) ?? String(value)
  }, [descriptor.shouldStringifyCollapsed, expanded, value])
  const lines = useMemo(() => text.split('\n'), [text])
  const exceedsLimit = lines.length > 20
  const preview = exceedsLimit && !expanded ? `${lines.slice(0, 20).join('\n')}\n...` : text
  const collapsedSummaryOnly = !expanded && !descriptor.shouldStringifyCollapsed

  return (
    <div className="min-w-0 w-full space-y-3">
      <div className="max-w-full overflow-x-auto overscroll-x-contain">
        <pre className="sentinelflow-code-block">{collapsedSummaryOnly ? descriptor.label : preview}</pre>
      </div>
      {exceedsLimit || collapsedSummaryOnly ? (
        <button type="button" className="sentinelflow-ghost-button" onClick={() => setExpanded((current) => !current)}>
          {expanded ? '收起动作结果' : '展开 JSON'}
        </button>
      ) : null}
    </div>
  )
}
