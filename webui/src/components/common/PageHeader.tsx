import type { ReactNode } from 'react'

type PageHeaderProps = {
  title: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
}

export default function PageHeader({ title, description, action, icon }: PageHeaderProps) {
  return (
    <header className="mb-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          {icon ? (
            <div className="mt-0.5 shrink-0 text-sky-700" aria-hidden="true">
              {icon}
            </div>
          ) : null}
          <div className="min-w-0">
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">{title}</h1>
            {description ? <p className="mt-1 text-sm leading-6 text-slate-500">{description}</p> : null}
          </div>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </header>
  )
}
