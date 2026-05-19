import { useMemo, useState } from 'react'

type JsonPreviewProps = {
  value: unknown
}

export default function JsonPreview({ value }: JsonPreviewProps) {
  const [expanded, setExpanded] = useState(false)
  const text = useMemo(() => JSON.stringify(value, null, 2), [value])
  const lines = useMemo(() => text.split('\n'), [text])
  const exceedsLimit = lines.length > 20
  const preview = exceedsLimit && !expanded ? `${lines.slice(0, 20).join('\n')}\n...` : text

  return (
    <div className="min-w-0 w-full space-y-3">
      <div className="max-w-full overflow-x-auto overscroll-x-contain">
        <pre className="sentinelflow-code-block">{preview}</pre>
      </div>
      {exceedsLimit ? (
        <button type="button" className="sentinelflow-ghost-button" onClick={() => setExpanded((current) => !current)}>
          {expanded ? '收起动作结果' : '展开更多'}
        </button>
      ) : null}
    </div>
  )
}
