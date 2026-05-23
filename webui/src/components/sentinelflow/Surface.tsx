import { ChevronDown } from 'lucide-react'
import type { PropsWithChildren, ReactNode } from 'react'

type SurfacePreviewItem = {
  label: string
  value: string
}

type SurfaceProps = PropsWithChildren<{
  title: string
  subtitle?: string
  collapsible?: boolean
  defaultOpen?: boolean
  collapsedPreview?: ReactNode
}>

export function SurfacePreviewGrid({ items }: { items: SurfacePreviewItem[] }) {
  return (
    <div className="sentinelflow-surface-preview-grid">
      {items.map((item) => (
        <div key={item.label} className="sentinelflow-surface-preview-item">
          <span>{item.label}</span>
          <strong title={item.value}>{item.value}</strong>
        </div>
      ))}
    </div>
  )
}

function SurfaceHeader({ title, subtitle, collapsible }: Pick<SurfaceProps, 'title' | 'subtitle' | 'collapsible'>) {
  if (!title && !subtitle) return null

  return (
    <div className={`sentinelflow-surface-header${collapsible ? ' sentinelflow-surface-header-collapsible' : ''}`}>
      <div className="min-w-0 flex-1">
        {title ? <h2>{title}</h2> : null}
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {collapsible ? <ChevronDown className="sentinelflow-surface-chevron h-5 w-5 shrink-0 text-slate-400" aria-hidden="true" /> : null}
    </div>
  )
}

export default function Surface({
  title,
  subtitle,
  collapsible = false,
  defaultOpen = true,
  collapsedPreview,
  children,
}: SurfaceProps) {
  if (collapsible) {
    return (
      <section className="sentinelflow-surface">
        <details className="sentinelflow-surface-disclosure" {...(defaultOpen ? { open: true } : {})}>
          <summary className="sentinelflow-surface-summary">
            <SurfaceHeader title={title} subtitle={subtitle} collapsible />
            {collapsedPreview ? (
              <div className="sentinelflow-surface-collapsed-preview">
                {collapsedPreview}
                <p className="sentinelflow-surface-collapsed-hint">点击标题展开完整配置</p>
              </div>
            ) : null}
          </summary>
          <div className="sentinelflow-surface-body">{children}</div>
        </details>
      </section>
    )
  }

  return (
    <section className="sentinelflow-surface">
      <SurfaceHeader title={title} subtitle={subtitle} />
      <div className="sentinelflow-surface-body">{children}</div>
    </section>
  )
}
