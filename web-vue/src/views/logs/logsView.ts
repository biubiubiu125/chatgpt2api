import type { GalleryFile } from '@/api/gallery'
import type { RuntimeLog, SystemLogsResponse, SystemLogRow } from '@/api/logs'
import type { ActionMenuItem } from 'nanocat-ui'
import { actionMenuGroups } from '@/components/ai/menuItems'
import {
  formatLogDuration,
  imageFailureLabel,
  isSystemLogFailed as isFailed,
  isSystemLogLimited as isLimited,
  isSystemLogSuccess as isSuccess,
  isSystemLogTextReview as isTextReview,
} from '@/api/logs'

export type LogFilterOption = { label: string; value: string }
export type LogGroupedSelectOption = LogFilterOption & { disabled?: boolean }
export type LogGroupedSelectGroup = {
  label?: string
  options: LogGroupedSelectOption[]
}

export type SystemLogFilters = {
  search: string
  startDate: string
  endDate: string
  status: string
  endpoint: string
  model: string
  account: string
  conversationId: string
  type: string
}

export type RuntimeLogFilters = {
  level: string
  source: string
  search: string
}

export type AdvancedLogFilterKey = 'type' | 'status' | 'model' | 'account'

export type LogMetricItem = {
  label: string
  value: string | number
  class: string
}

export type LogStatusTone = 'success' | 'danger' | 'warning' | 'muted'

type RuntimeFilterKey = 'level' | 'source'

type AdvancedConditionGroup = {
  key: AdvancedLogFilterKey
  label: string
  options: LogFilterOption[]
}

export type LogPreviewImage = {
  url: string
  title?: string
  filename?: string
  alt?: string
  broken?: boolean
}

export type SystemLogRowSignatureInput = {
  selected: boolean
  firstImageBroken: boolean
}

type LogDurationDisplay = {
  total: string
  breakdown: string
}

export type LogCellDisplay = {
  primary: string
  secondary: string
}

export type LogRequestDisplay = LogCellDisplay & {
  kind: string
}

export const typeOptions = [
  { label: '调用日志', value: 'call' },
  { label: '账号日志', value: 'account' },
  { label: '全部类型', value: '' },
]

export const systemLogPageSizeOptions = [20, 50, 100, 200, 500]

export const runtimeLimitOptions = [
  { label: '100', value: '100' },
  { label: '300', value: '300' },
  { label: '500', value: '500' },
  { label: '1000', value: '1000' },
  { label: '2000', value: '2000' },
]

export const runtimeLevelOptions = [
  { label: '全部级别', value: '' },
  { label: 'debug', value: 'debug' },
  { label: 'info', value: 'info' },
  { label: 'warning', value: 'warning' },
  { label: 'error', value: 'error' },
]

export const runtimeSourceOptions = [
  { label: '全部来源', value: '' },
  { label: '内存日志', value: 'memory' },
  { label: '文件尾部', value: 'file' },
]

export const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '限流/受限', value: 'limited' },
]

export const systemQuickFilterOptions: LogGroupedSelectOption[] = [
  { label: '只看失败', value: 'quick:status:failed' },
  { label: '图生图', value: 'quick:endpoint:/v1/images/edits' },
  { label: '文生图', value: 'quick:endpoint:/v1/images/generations' },
]

export const quickEndpointValues = ['/v1/images/edits', '/v1/images/generations'] as const

export const systemQuickFilterGroups: LogGroupedSelectGroup[] = [
  { options: systemQuickFilterOptions },
]

export function cleanLogString(value: unknown): string {
  if (value === undefined || value === null) return ''
  return String(value).trim()
}

function signatureValue(value: unknown): string {
  return cleanLogString(value).replaceAll('|', '/')
}

function boundedSignatureText(value: unknown, limit = 180): string {
  const text = signatureValue(value)
  if (text.length <= limit) return text
  return `${text.length}:${text.slice(0, limit)}:${text.slice(-24)}`
}

export function currentMenuLabel(label: string, active: boolean): string {
  return active ? `${label}（当前）` : label
}

export function buildRuntimeFilterMenuItems(filters: Pick<RuntimeLogFilters, 'level' | 'source'>): ActionMenuItem[] {
  return actionMenuGroups<ActionMenuItem>(
    runtimeLevelOptions
      .filter((item) => item.value)
      .map((item) => ({
        key: `runtime-level:${item.value}`,
        label: currentMenuLabel(`级别 ${item.label}`, filters.level === item.value),
      })),
    runtimeSourceOptions
      .filter((item) => item.value)
      .map((item) => ({
        key: `runtime-source:${item.value}`,
        label: currentMenuLabel(item.label, filters.source === item.value),
      })),
    [
      { key: 'runtime-clear:level', label: '清除级别筛选', disabled: !filters.level },
      { key: 'runtime-clear:source', label: '清除来源筛选', disabled: !filters.source },
    ],
  )
}

export function parseRuntimeFilterMenuKey(key: string): { key: RuntimeFilterKey; value: string } | null {
  if (key.startsWith('runtime-level:')) {
    return { key: 'level', value: key.slice('runtime-level:'.length) }
  }
  if (key.startsWith('runtime-source:')) {
    return { key: 'source', value: key.slice('runtime-source:'.length) }
  }
  if (key === 'runtime-clear:level') return { key: 'level', value: '' }
  if (key === 'runtime-clear:source') return { key: 'source', value: '' }
  return null
}

export function normalizeRuntimeLimit(value: unknown, fallback = 500): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.min(Math.max(Math.trunc(parsed), 1), 2000) : fallback
}

export function optionFromFacet(facet: Record<string, number>, allLabel: string): LogFilterOption[] {
  return [
    { label: allLabel, value: '' },
    ...Object.keys(facet)
      .map(cleanLogString)
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b))
      .map((value) => ({ label: `${value} (${facet[value] || 0})`, value })),
  ]
}

export function buildAdvancedConditionMenuGroups(
  modelFacet: Record<string, number>,
  accountFacet: Record<string, number>,
): LogGroupedSelectGroup[] {
  const groups: AdvancedConditionGroup[] = [
    {
      key: 'type',
      label: '类型',
      options: [
        { label: '调用日志', value: 'call' },
        { label: '账号日志', value: 'account' },
        { label: '全部类型', value: '' },
      ],
    },
    {
      key: 'status',
      label: '状态',
      options: statusOptions,
    },
    {
      key: 'model',
      label: '模型',
      options: optionFromFacet(modelFacet, '全部模型'),
    },
    {
      key: 'account',
      label: '账号',
      options: optionFromFacet(accountFacet, '全部账号'),
    },
  ]

  return groups.map((group) => ({
    label: group.label,
    options: group.options.map((option) => ({
      label: option.label,
      value: advancedConditionOptionValue(group.key, option.value),
    })),
  }))
}

export function typeLabel(type: string): string {
  if (type === 'call') return '调用日志'
  if (type === 'account') return '账号日志'
  return type || '日志'
}

function requestKind(item: SystemLogRow): string {
  const endpoint = item.endpoint.toLowerCase()
  const model = item.model.toLowerCase()
  let kind = item.type === 'account' ? '账号操作' : ''
  if (endpoint.includes('/images/edits')) kind = '图生图'
  else if (endpoint.includes('/images/generations')) kind = '文生图'
  else if (endpoint.includes('/chat/completions')) kind = model.includes('image') ? '对话生图' : '对话'
  else if (endpoint.includes('/search')) kind = '搜索'
  else if (endpoint.includes('/responses')) kind = '响应'
  else if (endpoint.includes('/messages')) kind = '消息'
  else if (!kind) kind = item.endpoint || typeLabel(item.type)
  return kind
}

export function requestDisplay(item: SystemLogRow): LogRequestDisplay {
  const kind = requestKind(item)
  return {
    kind: item.type === 'account' ? '' : kind,
    primary: item.model || typeLabel(item.type),
    secondary: item.endpoint,
  }
}

export function executionDisplay(item: SystemLogRow): LogCellDisplay {
  const taskCount = item.imageRequestedCount
  const attemptCount = item.imageAttempts.length
  const caller = item.keyName || item.keyId
  const primary = [
    taskCount > 0 ? `${taskCount} 个任务` : '',
    attemptCount > 0 ? `${attemptCount} 次尝试` : '',
  ].filter(Boolean).join(' · ') || caller || '-'
  const secondary = taskCount > 0 ? caller : ''
  return { primary, secondary }
}

export function outcomeText(item: SystemLogRow): string {
  if (item.imageResultStatus === 'partial_success') {
    return `生成 ${item.imageSucceededCount}/${item.imageRequestedCount} 张图片`
  }
  if (isTextReview(item)) return '上游返回文本'

  const failureLabel = imageFailureLabel(item.errorCode)
  if (isFailed(item) || isLimited(item)) {
    return failureLabel || item.error || item.reason || item.summary || '调用失败'
  }

  const imageCount = Math.max(item.imageSucceededCount, item.imageUrls.length)
  if (imageCount > 0) {
    return item.imageRequestedCount > 1
      ? `生成 ${imageCount}/${item.imageRequestedCount} 张图片`
      : `生成 ${imageCount} 张图片`
  }
  if (isSuccess(item)) {
    const kind = requestKind(item)
    if (kind === '文生图' || kind === '图生图' || kind === '对话生图') return '图片生成完成'
    if (kind === '搜索') return '搜索完成'
    if (kind === '对话' || kind === '响应' || kind === '消息') return '文本响应完成'
    if (kind === '账号操作') return item.summary || '账号操作完成'
    return '调用完成'
  }
  return item.summary || item.preview || '调用完成'
}

export function resultDiagnostics(item: SystemLogRow): string {
  const showFailureDetails = isFailed(item) || isLimited(item)
  return [
    showFailureDetails && item.statusCode ? `HTTP ${item.statusCode}` : '',
    showFailureDetails ? item.errorCode : '',
  ].filter(Boolean).join(' · ')
}

export function summaryText(item: SystemLogRow): string {
  if (isTextReview(item) && item.summary) {
    return item.summary.replace('流式调用失败', '文本').replace('调用失败', '文本')
  }
  return item.summary || item.error || item.reason || item.preview
}

export function statusLabel(item: SystemLogRow): string {
  if (item.imageResultStatus === 'partial_success') return '部分成功'
  if (isTextReview(item)) return '文本'
  if (isSuccess(item)) return '成功'
  if (isFailed(item)) return '失败'
  if (isLimited(item)) return '受限'
  return item.status || '记录'
}

export function statusTone(item: SystemLogRow): LogStatusTone {
  if (item.imageResultStatus === 'partial_success') return 'warning'
  if (isTextReview(item)) return 'warning'
  if (isSuccess(item)) return 'success'
  if (isFailed(item)) return 'danger'
  if (isLimited(item)) return 'warning'
  return 'muted'
}

export function logDurationDisplay(item: SystemLogRow): LogDurationDisplay {
  const attemptsBySlot = new Map<number, number[]>()
  item.imageAttempts.forEach((attempt) => {
    if (attempt.durationMs <= 0) return
    const durations = attemptsBySlot.get(attempt.slot) || []
    durations.push(attempt.durationMs)
    attemptsBySlot.set(attempt.slot, durations)
  })

  const retryGroups = Array.from(attemptsBySlot, ([slot, durations]) => ({ slot, durations }))
    .filter((group) => group.durations.length > 1)
    .sort((left, right) => left.slot - right.slot)
  const uniqueSlotCount = new Set(item.imageAttempts.map((attempt) => attempt.slot)).size
  const fallbackTotalMs = retryGroups.length === 1 && uniqueSlotCount === 1
    ? retryGroups[0].durations.reduce((total, duration) => total + duration, 0)
    : 0
  const total = formatLogDuration(item.durationMs)
    || (fallbackTotalMs > 0 ? formatLogDuration(fallbackTotalMs) : '')
  if (!retryGroups.length) return { total, breakdown: '' }

  const hasMultipleSlots = uniqueSlotCount > 1
  const expressions = retryGroups.map(({ slot, durations }) => {
    const expression = durations.map((duration) => formatLogDuration(duration)).join(' + ')
    return hasMultipleSlots ? `图 ${slot}：${expression}` : expression
  })
  return { total, breakdown: `(${expressions.join('；')})` }
}

export function logDurationTone(item: SystemLogRow): LogStatusTone {
  if (isFailed(item)) return 'danger'
  const durationMs = Number(item.durationMs)
  if (!Number.isFinite(durationMs) || durationMs < 0) return 'muted'
  return durationMs >= 60_000 ? 'warning' : 'success'
}

export function systemLogRowSignature(item: SystemLogRow, input: SystemLogRowSignatureInput): string {
  const durationDisplay = logDurationDisplay(item)
  const request = requestDisplay(item)
  const execution = executionDisplay(item)
  return [
    item.id,
    input.selected ? 1 : 0,
    input.firstImageBroken ? 1 : 0,
    boundedSignatureText(item.time),
    boundedSignatureText(request.primary, 96),
    boundedSignatureText(request.kind, 64),
    boundedSignatureText(request.secondary, 128),
    boundedSignatureText(execution.primary, 96),
    boundedSignatureText(execution.secondary, 96),
    boundedSignatureText(item.durationMs, 64),
    boundedSignatureText(durationDisplay.breakdown, 160),
    boundedSignatureText(statusLabel(item), 64),
    statusTone(item),
    boundedSignatureText(outcomeText(item)),
    boundedSignatureText(resultDiagnostics(item), 160),
    boundedSignatureText(item.statusCode, 32),
    boundedSignatureText(item.errorCode, 64),
    boundedSignatureText(item.imageResultStatus, 32),
    item.imageRequestedCount,
    item.imageSucceededCount,
    item.imageUrls.length,
    item.imageAttempts.length,
    item.accountSwitchCount,
    item.imageUrls.slice(0, 4).map((url) => boundedSignatureText(url, 96)).join(','),
    boundedSignatureText(item.preview),
    isFailed(item) ? 1 : 0,
  ].map(signatureValue).join('|')
}

export function filenameFromUrl(url: string): string {
  const value = cleanLogString(url)
  if (!value) return '-'
  try {
    const parsed = new URL(value, 'https://local.invalid')
    return decodeURIComponent(parsed.pathname.split('/').pop() || value)
  } catch {
    return decodeURIComponent(value.split(/[/?#]/)[0]?.split('/').pop() || value)
  }
}

export function buildLogPreviewImages(
  item: SystemLogRow | null | undefined,
  isPreviewBroken: (url: string) => boolean,
): LogPreviewImage[] {
  if (!item) return []
  return item.imageUrls.map((url, index) => {
    const sourceUrl = item.urls[index] || url
    return {
      url,
      title: sourceUrl,
      filename: filenameFromUrl(sourceUrl),
      alt: `日志结果图片 ${index + 1}`,
      broken: isPreviewBroken(url),
    }
  })
}

export function buildLogPreviewGalleryFile(image: LogPreviewImage | null | undefined): GalleryFile | null {
  if (!image) return null
  const filename = image.filename || filenameFromUrl(image.title || image.url) || 'log-preview-image'
  return {
    filename,
    path: image.title || image.url,
    url: image.url,
    thumbnail_url: image.url,
    size: 0,
    created_at: '',
    mtime: 0,
    date: '',
    type: 'image',
    expired: false,
    expires_in_seconds: null,
    tags: [],
    storage: 'log',
    local: false,
    webdav: false,
    width: null,
    height: null,
  }
}

export function formatRuntimeLogLine(item: RuntimeLog): string {
  const time = cleanLogString(item.time)
  const level = cleanLogString(item.level).toUpperCase()
  const source = cleanLogString(item.source)
  const message = cleanLogString(item.message) || '-'
  const path = cleanLogString(item.path)
  return [
    time,
    level,
    source ? `[${source}]` : '',
    message,
    path,
  ].filter(Boolean).join(' ')
}

export function runtimeStats(items: readonly RuntimeLog[]) {
  const counts = { total: items.length, warning: 0, error: 0, memory: 0, file: 0 }
  items.forEach((item) => {
    const level = cleanLogString(item.level).toLowerCase()
    const source = cleanLogString(item.source).toLowerCase()
    if (level === 'warning') counts.warning += 1
    if (level === 'error' || level === 'critical') counts.error += 1
    if (source === 'memory') counts.memory += 1
    if (source === 'file') counts.file += 1
  })
  return counts
}

export function systemMetricItems(logMeta: Pick<SystemLogsResponse, 'stats' | 'stats_scope'>): LogMetricItem[] {
  const stats = logMeta.stats
  const pageScope = logMeta.stats_scope === 'page'
  return [
    { label: '总数', value: stats.total, class: 'text-foreground' },
    { label: pageScope ? '本页成功' : '成功', value: stats.success, class: 'text-emerald-600' },
    { label: pageScope ? '本页文本' : '文本', value: stats.text_review, class: 'text-violet-600' },
    { label: pageScope ? '本页失败' : '失败', value: stats.failed, class: 'text-rose-600' },
    { label: pageScope ? '本页限流' : '限流', value: stats.limited, class: 'text-amber-600' },
    { label: pageScope ? '本页图片' : '图片接口', value: stats.image, class: 'text-cyan-600' },
  ]
}

export function runtimeMetricItems(items: readonly RuntimeLog[]): LogMetricItem[] {
  const counts = runtimeStats(items)
  return [
    { label: '运行日志', value: counts.total, class: 'text-foreground' },
    { label: 'Warning', value: counts.warning, class: 'text-amber-600' },
    { label: 'Error', value: counts.error, class: 'text-rose-600' },
    { label: '内存', value: counts.memory, class: 'text-cyan-600' },
    { label: '文件', value: counts.file, class: 'text-violet-600' },
  ]
}

export function activeSystemFilterCount(filters: SystemLogFilters): number {
  return [
    filters.search,
    filters.startDate,
    filters.endDate,
    filters.status,
    filters.endpoint,
    filters.model,
    filters.account,
    filters.conversationId,
    filters.type !== 'call' ? filters.type || 'all' : '',
  ].filter(Boolean).length
}

export function activeRuntimeFilterCount(filters: RuntimeLogFilters): number {
  return [
    filters.level,
    filters.source,
    filters.search,
  ].filter(Boolean).length
}

export function advancedConditionCount(filters: Pick<SystemLogFilters, 'type' | 'status' | 'model' | 'account'>): number {
  return [
    filters.type !== 'call' ? filters.type || 'all' : '',
    filters.status,
    filters.model,
    filters.account,
  ].filter(Boolean).length
}

export function advancedConditionOptionValue(key: AdvancedLogFilterKey, value: string): string {
  return `advanced:${key}:${encodeURIComponent(value)}`
}

export function parseAdvancedConditionOptionValue(key: string): { conditionKey: AdvancedLogFilterKey; value: string } | null {
  const match = key.match(/^advanced:(type|status|model|account):(.*)$/)
  if (!match) return null
  return {
    conditionKey: match[1] as AdvancedLogFilterKey,
    value: decodeURIComponent(match[2] || ''),
  }
}

export function latestAdvancedConditionValue(values: readonly string[], key: AdvancedLogFilterKey): string | null {
  const matched = values
    .map(parseAdvancedConditionOptionValue)
    .filter((item): item is { conditionKey: AdvancedLogFilterKey; value: string } => Boolean(item && item.conditionKey === key))
  if (matched.length === 0) return null
  return matched[matched.length - 1].value
}

export function buildAdvancedConditionSelection(filters: Pick<SystemLogFilters, 'type' | 'status' | 'model' | 'account'>): string[] {
  const values: string[] = []
  if (filters.type !== 'call') values.push(advancedConditionOptionValue('type', filters.type))
  if (filters.status) values.push(advancedConditionOptionValue('status', filters.status))
  if (filters.model) values.push(advancedConditionOptionValue('model', filters.model))
  if (filters.account) values.push(advancedConditionOptionValue('account', filters.account))
  return values
}

export function buildSystemQuickFilterSelection(filters: Pick<SystemLogFilters, 'status' | 'endpoint'>): string[] {
  const values: string[] = []
  if (filters.status === 'failed') values.push('quick:status:failed')
  if (filters.endpoint === '/v1/images/edits') values.push('quick:endpoint:/v1/images/edits')
  if (filters.endpoint === '/v1/images/generations') values.push('quick:endpoint:/v1/images/generations')
  return values
}

export function latestQuickEndpointValue(values: readonly string[]): string | null {
  const matched = values
    .filter((item) => item.startsWith('quick:endpoint:'))
    .map((item) => item.slice('quick:endpoint:'.length))
  return matched.length ? matched[matched.length - 1] : null
}

export function isQuickEndpointValue(value: string): boolean {
  return (quickEndpointValues as readonly string[]).includes(value)
}
