import { useEffect, useMemo, useState } from 'react'
import { CalendarClock, Check, ChevronDown } from 'lucide-react'

export type AlertTimeRangeMode = 'today' | 'week' | 'custom'

export type AlertTimeRangeValue = {
  mode: AlertTimeRangeMode
  startDate: string
  startTime: string
  endDate: string
  endTime: string
}

const TIME_OPTIONS = Array.from({ length: 48 }, (_, index) => {
  const hours = Math.floor(index / 2)
  const minutes = index % 2 === 0 ? 0 : 30
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`
})

function padDatePart(value: number): string {
  return String(value).padStart(2, '0')
}

function toLocalDate(value: Date): string {
  return [
    value.getFullYear(),
    padDatePart(value.getMonth() + 1),
    padDatePart(value.getDate()),
  ].join('-')
}

function toNearestTime(value: Date): string {
  const minutes = value.getMinutes() >= 30 ? 30 : 0
  return `${padDatePart(value.getHours())}:${padDatePart(minutes)}`
}

function getTimezoneOffsetSuffix(value: Date): string {
  const offsetMinutes = -value.getTimezoneOffset()
  const sign = offsetMinutes >= 0 ? '+' : '-'
  const absolute = Math.abs(offsetMinutes)
  const hours = Math.floor(absolute / 60)
  const minutes = absolute % 60
  return `${sign}${padDatePart(hours)}:${padDatePart(minutes)}`
}

function toLocalIsoWithOffset(value: Date): string {
  return `${toLocalDate(value)}T${padDatePart(value.getHours())}:${padDatePart(value.getMinutes())}:${padDatePart(value.getSeconds())}${getTimezoneOffsetSuffix(value)}`
}

function getStartOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate())
}

function getStartOfWeek(value: Date): Date {
  const startOfDay = getStartOfDay(value)
  const day = startOfDay.getDay()
  const diff = day === 0 ? -6 : 1 - day
  startOfDay.setDate(startOfDay.getDate() + diff)
  return startOfDay
}

function toIsoFromLocalParts(date: string, time: string): string {
  const normalizedDate = String(date ?? '').trim()
  const normalizedTime = String(time ?? '').trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(normalizedDate) || !/^\d{2}:\d{2}$/.test(normalizedTime)) return ''
  const parsed = new Date(`${normalizedDate}T${normalizedTime}:00`)
  return Number.isNaN(parsed.getTime()) ? '' : toLocalIsoWithOffset(parsed)
}

function formatShortDate(value: string): string {
  const parts = value.split('-')
  if (parts.length !== 3) return value
  return `${parts[1]}/${parts[2]}`
}

export function createAlertTimeRangeValue(mode: AlertTimeRangeMode = 'today', now = new Date()): AlertTimeRangeValue {
  const start = mode === 'week' ? getStartOfWeek(now) : getStartOfDay(now)
  return {
    mode,
    startDate: toLocalDate(start),
    startTime: '00:00',
    endDate: toLocalDate(now),
    endTime: toNearestTime(now),
  }
}

export function alertTimeRangeToQuery(value: AlertTimeRangeValue, now = new Date()): { since: string; until: string } {
  if (value.mode === 'today') {
    return { since: toLocalIsoWithOffset(getStartOfDay(now)), until: '' }
  }
  if (value.mode === 'week') {
    return { since: toLocalIsoWithOffset(getStartOfWeek(now)), until: '' }
  }
  return {
    since: toIsoFromLocalParts(value.startDate, value.startTime),
    until: toIsoFromLocalParts(value.endDate, value.endTime),
  }
}

export function getAlertTimeRangeLabel(value: AlertTimeRangeValue): string {
  if (value.mode === 'today') return '今日告警'
  if (value.mode === 'week') return '本周告警'
  return `${formatShortDate(value.startDate)} ${value.startTime} - ${formatShortDate(value.endDate)} ${value.endTime}`
}

type Props = {
  value: AlertTimeRangeValue
  onChange: (next: AlertTimeRangeValue) => void
  align?: 'left' | 'right'
}

export default function AlertTimeRangeFilter({ value, onChange, align = 'left' }: Props) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(value)
  const label = useMemo(() => getAlertTimeRangeLabel(value), [value])

  useEffect(() => {
    setDraft(value)
  }, [value])

  const applyCustomRange = () => {
    onChange({ ...draft, mode: 'custom' })
    setOpen(false)
  }

  return (
    <div className={`relative ${align === 'right' ? 'text-right' : ''}`}>
      <button
        type="button"
        className="sentinelflow-ghost-button inline-flex items-center gap-2"
        onClick={() => setOpen((current) => !current)}
      >
        <CalendarClock className="h-4 w-4" />
        <span>按时间筛选</span>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">{label}</span>
        <ChevronDown className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open ? (
        <div className={`absolute z-20 mt-2 w-[min(92vw,520px)] rounded-xl border border-slate-200 bg-white p-4 text-left shadow-xl ${align === 'right' ? 'right-0' : 'left-0'}`}>
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="mb-3 text-xs font-semibold uppercase text-slate-500">自定义时间范围</div>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-slate-600">开始日期</span>
                <input
                  className="sentinelflow-settings-input rounded-lg px-3 py-2"
                  inputMode="numeric"
                  placeholder="YYYY-MM-DD"
                  value={draft.startDate}
                  onChange={(event) => setDraft((current) => ({ ...current, mode: 'custom', startDate: event.target.value }))}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-slate-600">开始时间</span>
                <select
                  className="sentinelflow-settings-input rounded-lg px-3 py-2"
                  value={draft.startTime}
                  onChange={(event) => setDraft((current) => ({ ...current, mode: 'custom', startTime: event.target.value }))}
                >
                  {TIME_OPTIONS.map((time) => <option key={time} value={time}>{time}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-slate-600">结束日期</span>
                <input
                  className="sentinelflow-settings-input rounded-lg px-3 py-2"
                  inputMode="numeric"
                  placeholder="YYYY-MM-DD"
                  value={draft.endDate}
                  onChange={(event) => setDraft((current) => ({ ...current, mode: 'custom', endDate: event.target.value }))}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-slate-600">结束时间</span>
                <select
                  className="sentinelflow-settings-input rounded-lg px-3 py-2"
                  value={draft.endTime}
                  onChange={(event) => setDraft((current) => ({ ...current, mode: 'custom', endTime: event.target.value }))}
                >
                  {TIME_OPTIONS.map((time) => <option key={time} value={time}>{time}</option>)}
                </select>
              </label>
            </div>
            <div className="mt-3 flex justify-end">
              <button type="button" className="sentinelflow-primary-button inline-flex items-center gap-2" onClick={applyCustomRange}>
                <Check className="h-4 w-4" />
                应用筛选
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
