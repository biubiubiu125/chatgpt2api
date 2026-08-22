import {
  formatLogDuration,
  isSystemLogFailed as isFailed,
  isSystemLogLimited as isLimited,
  isSystemLogSuccess as isSuccess,
  type ImageAttemptMonitor,
  type SystemLogRow,
} from '@/api/logs'

export type DetailField = {
  label: string
  value: string
  copyable?: boolean
  wide?: boolean
}

export type DetailTone = 'success' | 'danger' | 'warning' | 'info' | 'muted'
export type DetailTimelineCategory = 'entry' | 'prepare' | 'upstream' | 'resolve' | 'download'

type DetailTimelineStepConfig = {
  key: string
  label: string
  category: DetailTimelineCategory
  hint?: string
}

export type DetailTimelineStep = DetailTimelineStepConfig & {
  valueMs: number
  value: string
  tone: DetailTone
  statusLabel: string
  barStyle: Record<string, string>
  time: string
  note: string
}

export type DetailTimelineSegment = {
  key: string
  label: string
  valueMs: number
  value: string
  percent: string
  tone: DetailTone
  category: DetailTimelineCategory
  compact: boolean
  barStyle: Record<string, string>
  title: string
}

export type DetailTimelineLegendItem = {
  key: DetailTimelineCategory | DetailTone
  label: string
  category: DetailTimelineCategory | 'state'
  tone: DetailTone
}

export type DetailTimelineGroup = {
  name: string
  steps: DetailTimelineStep[]
}

type DetailTimelineSummary = {
  stepCount: number
  segmentTotalMs: number
  bottleneckStep: DetailTimelineStep | null
}

const detailTimelineSteps: DetailTimelineStepConfig[] = [
  { key: 'handler_queue_ms', label: '等待入口', category: 'entry', hint: 'run_in_threadpool' },
  { key: 'stream_first_queue_ms', label: '读取首包', category: 'entry', hint: '首个响应事件' },
  { key: 'account_wait_ms', label: '等待账号', category: 'entry', hint: '账号池筛选' },
  { key: 'egress_wait_ms', label: '等待出口', category: 'entry', hint: '代理出口准备' },
  { key: 'egress_acquire_ms', label: '出口租约', category: 'entry', hint: '代理节点并发' },
  { key: 'upload_ms', label: '上传输入图', category: 'prepare', hint: '参考图上传' },
  { key: 'bootstrap_ms', label: '预热页面', category: 'prepare', hint: 'ChatGPT 页面' },
  { key: 'requirements_ms', label: '获取请求令牌', category: 'prepare', hint: 'requirements / token' },
  { key: 'prepare_conversation_ms', label: '准备会话', category: 'prepare', hint: '图片会话上下文' },
  { key: 'http_dns_ms', label: 'HTTP DNS', category: 'prepare', hint: '域名解析' },
  { key: 'http_tcp_ms', label: 'HTTP TCP', category: 'prepare', hint: '代理 / TCP 建连' },
  { key: 'http_tls_ms', label: 'HTTP TLS', category: 'prepare', hint: 'TLS 握手' },
  { key: 'http_wait_ms', label: 'HTTP 等待', category: 'prepare', hint: '请求发出到首包' },
  { key: 'http_ttfb_ms', label: 'HTTP 首包', category: 'prepare', hint: '请求开始到首包' },
  { key: 'generation_start_ms', label: '启动生成', category: 'upstream', hint: '提交上游请求' },
  { key: 'sse_stream_ms', label: 'SSE 流耗时', category: 'upstream', hint: 'data 事件持续时间' },
  { key: 'sse_first_event_ms', label: 'SSE 首事件', category: 'upstream', hint: '首个 data 事件' },
  { key: 'sse_max_gap_ms', label: 'SSE 最大空窗', category: 'upstream', hint: '两次事件最大间隔' },
  { key: 'sse_last_gap_ms', label: 'SSE 收尾空窗', category: 'upstream', hint: '最后事件到关闭' },
  { key: 'conversation_stream_ms', label: '上游生成', category: 'upstream', hint: 'ChatGPT 会话流' },
  { key: 'stream_error_ms', label: '上游断流', category: 'upstream', hint: 'HTTP2 / SSE' },
  { key: 'poll_wait_ms', label: '等待结果', category: 'resolve', hint: '首次等待 / 轮询间隔 / 退避' },
  { key: 'poll_request_ms', label: '查询结果', category: 'resolve', hint: 'task / conversation' },
  { key: 'resolve_ms', label: '解析结果', category: 'resolve', hint: 'file ID / 下载地址' },
  { key: 'response_ms', label: '响应整理', category: 'resolve', hint: 'Codex 响应' },
  { key: 'download_ms', label: '下载图片', category: 'download', hint: '图片文件下载' },
]

const detailTimelineCategories: ReadonlyArray<{
  category: DetailTimelineCategory
  label: string
}> = [
  { category: 'entry', label: '入口与账号' },
  { category: 'prepare', label: '上游准备' },
  { category: 'upstream', label: '上游生成' },
  { category: 'resolve', label: '结果处理' },
  { category: 'download', label: '图片下载' },
]

const detailTimelineSegments: ReadonlyArray<{
  key: string
  label: string
  category: DetailTimelineCategory
  aggregateKeys: readonly string[]
}> = [
  {
    key: 'entry_queue',
    label: '入口排队',
    category: 'entry',
    aggregateKeys: ['handler_queue_ms', 'stream_first_queue_ms'],
  },
  {
    key: 'account_egress',
    label: '账号与出口',
    category: 'entry',
    aggregateKeys: ['account_wait_ms', 'egress_wait_ms'],
  },
  {
    key: 'prepare',
    label: '上游准备',
    category: 'prepare',
    aggregateKeys: ['upload_ms', 'bootstrap_ms', 'requirements_ms', 'prepare_conversation_ms'],
  },
  {
    key: 'upstream',
    label: '上游生成',
    category: 'upstream',
    aggregateKeys: ['generation_start_ms', 'sse_stream_ms'],
  },
  {
    key: 'poll_wait',
    label: '等待结果',
    category: 'resolve',
    aggregateKeys: ['poll_wait_ms'],
  },
  {
    key: 'query_resolve',
    label: '查询与解析',
    category: 'resolve',
    aggregateKeys: ['poll_request_ms', 'resolve_ms', 'response_ms'],
  },
  {
    key: 'download',
    label: '图片下载',
    category: 'download',
    aggregateKeys: ['download_ms'],
  },
]
const defaultTimelineWarningThresholdMs = 60_000
const timelineWarningThresholdMs: Record<string, number> = {
  handler_queue_ms: 1_000,
  stream_first_queue_ms: 1_000,
  account_wait_ms: 10_000,
  egress_wait_ms: 10_000,
  egress_acquire_ms: 10_000,
  upload_ms: 60_000,
  bootstrap_ms: 60_000,
  requirements_ms: 60_000,
  prepare_conversation_ms: 60_000,
  generation_start_ms: 60_000,
  http_dns_ms: 1_000,
  http_tcp_ms: 3_000,
  http_tls_ms: 5_000,
  http_wait_ms: 30_000,
  http_ttfb_ms: 30_000,
  sse_first_event_ms: 30_000,
  sse_max_gap_ms: 60_000,
  sse_last_gap_ms: 30_000,
  poll_wait_ms: 60_000,
  poll_request_ms: 30_000,
  download_ms: 60_000,
  response_ms: 30_000,
}

function cleanString(value: unknown): string {
  return String(value || '').trim()
}

export function detailRecord(item: SystemLogRow): Record<string, any> {
  const detail = item.raw.detail
  return detail && typeof detail === 'object' ? detail : {}
}

function monitorRecord(item: SystemLogRow): Record<string, any> {
  const monitor = detailRecord(item).monitor
  return monitor && typeof monitor === 'object' ? monitor : {}
}

function rawDetailValue(item: SystemLogRow, key: string): string {
  return formatInlineValue(detailRecord(item)[key])
}

function rawMonitorValue(item: SystemLogRow, key: string): string {
  return formatInlineValue(monitorRecord(item)[key])
}

function isErrorStatusCode(value: string): boolean {
  const statusCode = Number(cleanString(value))
  return Number.isFinite(statusCode) && statusCode >= 400
}

function diagnosticStageValue(item: SystemLogRow): string {
  const stage = item.stage || rawMonitorValue(item, 'stage_label') || rawMonitorValue(item, 'stage')
  const normalized = cleanString(stage).toLowerCase()
  if (isSuccess(item) && ['success', 'completed', 'complete', 'done', '完成'].includes(normalized)) return ''
  return stage
}

function hasDiagnosticSignal(item: SystemLogRow): boolean {
  return Boolean(
    isFailed(item)
      || isLimited(item)
      || isErrorStatusCode(item.statusCode)
      || item.errorCode
      || item.error
      || item.reason
      || item.upstreamErrorType
      || item.upstreamRequestId
      || item.blocked === '是'
      || item.toolInvoked === '是'
      || diagnosticStageValue(item),
  )
}

export function formatInlineValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return ''
  if (Array.isArray(value)) return value.map(formatInlineValue).filter(Boolean).join(' · ')
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).filter(([, item]) => item !== undefined && item !== null && item !== '')
    if (!entries.length) return ''
    const primitive = entries.every(([, item]) => !item || ['string', 'number', 'boolean'].includes(typeof item))
    if (primitive && entries.length <= 8) {
      return entries.map(([key, item]) => `${key}: ${formatInlineValue(item)}`).join(' · ')
    }
    try {
      return JSON.stringify(value, null, 2)
    } catch {
      return String(value)
    }
  }
  return String(value).trim()
}

function metricFromRecord(record: unknown, key: string): number {
  if (!record || typeof record !== 'object') return 0
  const raw = (record as Record<string, unknown>)[key]
  const parsed = Number(raw || 0)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

export function metricValueFromLog(item: SystemLogRow, key: string): number {
  const detail = detailRecord(item)
  const monitor = monitorRecord(item)
  const values = [
    metricFromRecord(detail.perf, key),
    metricFromRecord(detail.metrics, key),
    metricFromRecord(monitor.metrics, key),
  ]
  const images = monitor.images
  if (images && typeof images === 'object') {
    Object.values(images as Record<string, any>).forEach((image) => {
      if (image && typeof image === 'object') values.push(metricFromRecord(image.metrics, key))
    })
  }
  return Math.max(...values, 0)
}

function timelineStepTone(key: string, valueMs: number): DetailTone {
  if (key === 'stream_error_ms') return 'danger'
  const threshold = timelineWarningThresholdMs[key] ?? defaultTimelineWarningThresholdMs
  if (valueMs >= threshold) return 'warning'
  return 'info'
}

function timelineStatusLabel(tone: DetailTone): string {
  if (tone === 'danger') return '异常'
  if (tone === 'warning') return '慢'
  if (tone === 'success') return '完成'
  if (tone === 'info') return '记录'
  return '记录'
}

function eventTimeFromEvents(events: unknown, metricKey: string): string {
  if (!Array.isArray(events)) return ''
  const matched = events.find((event) => event && typeof event === 'object' && Number((event as Record<string, unknown>)[metricKey] || 0) > 0)
  return formatInlineValue((matched as Record<string, unknown> | undefined)?.time)
}

function eventTimeForMetric(item: SystemLogRow, metricKey: string): string {
  return eventTimeFromEvents(monitorRecord(item).events, metricKey)
}

function requestShapeImageSummary(item: SystemLogRow): string {
  const shape = detailRecord(item).request_shape
  if (!shape || typeof shape !== 'object') return ''
  const record = shape as Record<string, unknown>
  const pairs: Array<[string, string]> = [
    ['input_image_parts', '输入图'],
    ['image_url_parts', '图链'],
    ['image_parts', '图片块'],
    ['data_url_images', 'base64'],
    ['remote_image_urls', '远程图'],
    ['literal_image_placeholders', '占位图'],
  ]
  const parts = pairs
    .map(([key, label]) => [label, Number(record[key] || 0)] as const)
    .filter(([, count]) => Number.isFinite(count) && count > 0)
    .map(([label, count]) => `${label} ${count}`)
  return parts.join(' · ')
}

function timelineStepNote(item: SystemLogRow, step: DetailTimelineStepConfig): string {
  const parts = [step.hint || '']
  if (step.key === 'upload_ms') parts.push(requestShapeImageSummary(item))
  if (step.key === 'resolve_ms' && item.imageUrls.length) parts.push(`结果图 ${item.imageUrls.length}`)
  if (step.key === 'download_ms' && item.imageUrls.length) parts.push(`下载 ${item.imageUrls.length} 张`)
  return parts.filter(Boolean).join(' · ')
}

function attemptTimelineStepNote(step: DetailTimelineStepConfig): string {
  return step.hint || ''
}

function maskKeyLabel(value: string): string {
  return cleanString(value).replace(/sk-[A-Za-z0-9_-]{6,}/g, (token) => `${token.slice(0, 5)}***${token.slice(-4)}`)
}

function proxySourceLabel(value: unknown): string {
  const source = cleanString(value)
  if (!source) return ''
  if (source.includes('account_group')) return '账号组'
  if (source.includes('account')) return '账号'
  if (source.includes('default')) return '默认'
  if (source.includes('global')) return '默认'
  if (source.includes('runtime_resource')) return '资源代理'
  if (source.includes('runtime')) return 'Runtime'
  if (source.includes('explicit')) return '指定'
  if (source.includes('direct')) return '直连'
  return source
}

function egressDetailValue(item: SystemLogRow): string {
  const source = item.proxySource || rawMonitorValue(item, 'proxy_source')
  const hash = item.proxyHash || rawMonitorValue(item, 'proxy_hash')
  const groupId = item.proxyGroupId || rawMonitorValue(item, 'proxy_group_id')
  const nodeName = item.proxyNodeName || rawMonitorValue(item, 'proxy_node_name')
  const nodeId = item.proxyNodeId || rawMonitorValue(item, 'proxy_node_id')
  const egressLabel = item.egressLabel || rawMonitorValue(item, 'egress_label')
  const label = proxySourceLabel(source)
  if (!label) return ''
  const nodeLabel = [groupId, nodeName || nodeId].filter(Boolean).join('/')
  if (nodeLabel) return `${label} ${nodeLabel}`
  if (egressLabel && egressLabel !== 'direct' && !egressLabel.startsWith('proxy:')) return `${label} ${egressLabel}`
  if (hash && hash !== 'direct') return `${label} ${hash}`
  return label
}

function hasDetailValue(value: string): boolean {
  const clean = cleanString(value)
  return Boolean(clean && clean !== '-' && clean.toLowerCase() !== 'null' && clean.toLowerCase() !== 'undefined')
}

function compactDetailFields(fields: Array<DetailField | null>, options: { keepStatus?: boolean } = {}): DetailField[] {
  return fields.filter((field): field is DetailField => {
    if (!field) return false
    if (options.keepStatus && field.label === '状态') return true
    return hasDetailValue(field.value)
  })
}

function timeRangeDetailValue(item: SystemLogRow): string {
  const start = cleanString(item.startedAt || item.time)
  const end = cleanString(item.endedAt)
  if (!start) return end
  if (!end || end === start) return start
  return `${start} → ${end}`
}

function buildTimelineSegmentsFromMetric(
  metricValue: (key: string) => number,
): DetailTimelineSegment[] {
  const segments = detailTimelineSegments
    .map((segment) => {
      const primaryValueMs = sumTimelineMetrics(metricValue, segment.aggregateKeys)
      const valueMs = segment.key === 'upstream'
        ? Math.max(primaryValueMs, upstreamEnvelopeValue(metricValue))
        : primaryValueMs
      const toneKeys = segment.key === 'upstream'
        ? [...segment.aggregateKeys, 'stream_error_ms']
        : segment.aggregateKeys
      const tones = toneKeys
        .map((key) => ({ key, valueMs: metricValue(key) }))
        .filter((metric) => metric.valueMs > 0)
        .map((metric) => timelineStepTone(metric.key, metric.valueMs))
      const tone: DetailTone = tones.includes('danger')
        ? 'danger'
        : tones.includes('warning') ? 'warning' : 'info'
      return {
        key: segment.key,
        label: segment.label,
        valueMs,
        tone,
        category: segment.category,
      }
    })
    .filter((stage) => stage.valueMs > 0)

  const totalMs = segments.reduce((total, segment) => total + segment.valueMs, 0)
  if (totalMs <= 0) return []
  return segments.map((segment) => {
    const percent = (segment.valueMs / totalMs) * 100
    const value = formatLogDuration(segment.valueMs)
    return {
      ...segment,
      value,
      percent: `${percent.toFixed(percent >= 10 ? 0 : 1)}%`,
      compact: percent < 12,
      barStyle: { flexGrow: String(Math.max(segment.valueMs, 1)) },
      title: `${segment.label} ${value} · ${percent.toFixed(1)}%`,
    }
  })
}

function sumTimelineMetrics(
  metricValue: (key: string) => number,
  keys: readonly string[],
): number {
  return keys.reduce((total, key) => total + metricValue(key), 0)
}

function upstreamEnvelopeValue(metricValue: (key: string) => number): number {
  const envelopeMs = metricValue('conversation_stream_ms') || metricValue('stream_error_ms')
  if (envelopeMs <= 0) return 0
  const prepareStage = detailTimelineSegments.find((segment) => segment.key === 'prepare')
  const prepareMs = sumTimelineMetrics(metricValue, prepareStage?.aggregateKeys || [])
  return Math.max(0, envelopeMs - prepareMs)
}

export function buildTimelineSegments(item: SystemLogRow | null): DetailTimelineSegment[] {
  if (!item || hasImageAttemptBreakdown(item)) return []
  return buildTimelineSegmentsFromMetric((key) => metricValueFromLog(item, key))
}

export function buildAttemptTimelineSegments(monitor: ImageAttemptMonitor): DetailTimelineSegment[] {
  return buildTimelineSegmentsFromMetric((key) => metricFromRecord(monitor.metrics, key))
}

export function buildTimelineLegendItems(segments: DetailTimelineSegment[]): DetailTimelineLegendItem[] {
  if (!segments.length) return []
  const items: DetailTimelineLegendItem[] = detailTimelineCategories
    .map(({ category, label }) => ({
      key: category,
      label,
      category,
      tone: 'info',
    }))
  if (segments.some((segment) => segment.tone === 'warning')) {
    items.push({ key: 'warning', label: '超过阈值', category: 'state', tone: 'warning' })
  }
  if (segments.some((segment) => segment.tone === 'danger')) {
    items.push({ key: 'danger', label: '异常中断', category: 'state', tone: 'danger' })
  }
  return items
}

function buildTimelineGroupsFromMetric(
  metricValue: (key: string) => number,
  eventTime: (key: string) => string,
  stepNote: (step: DetailTimelineStepConfig) => string,
): DetailTimelineGroup[] {
  const maxMs = Math.max(...detailTimelineSteps.map((step) => metricValue(step.key)), 0)
  if (maxMs <= 0) return []
  const groups = new Map<DetailTimelineCategory, DetailTimelineStep[]>()
  detailTimelineSteps.forEach((step) => {
    const valueMs = metricValue(step.key)
    if (valueMs <= 0) return
    const width = Math.max(3, Math.round((valueMs / maxMs) * 100))
    const tone = timelineStepTone(step.key, valueMs)
    const groupSteps = groups.get(step.category) || []
    groupSteps.push({
      ...step,
      valueMs,
      value: formatLogDuration(valueMs),
      tone,
      statusLabel: timelineStatusLabel(tone),
      barStyle: { width: `${width}%` },
      time: eventTime(step.key),
      note: stepNote(step),
    })
    groups.set(step.category, groupSteps)
  })
  return detailTimelineCategories
    .map(({ category, label }) => ({ name: label, steps: groups.get(category) || [] }))
    .filter((group) => group.steps.length > 0)
}

export function buildTimelineGroups(item: SystemLogRow | null): DetailTimelineGroup[] {
  if (!item || hasImageAttemptBreakdown(item)) return []
  return buildTimelineGroupsFromMetric(
    (key) => metricValueFromLog(item, key),
    (key) => eventTimeForMetric(item, key),
    (step) => timelineStepNote(item, step),
  )
}

export function buildAttemptTimelineGroups(monitor: ImageAttemptMonitor): DetailTimelineGroup[] {
  return buildTimelineGroupsFromMetric(
    (key) => metricFromRecord(monitor.metrics, key),
    (key) => eventTimeFromEvents(monitor.events, key),
    attemptTimelineStepNote,
  )
}

export function summarizeTimeline(
  segments: DetailTimelineSegment[],
  groups: DetailTimelineGroup[],
): DetailTimelineSummary {
  const steps = groups.flatMap((group) => group.steps)
  const bottleneckStep = steps.reduce<DetailTimelineStep | null>((current, step) => {
    if (!current || step.valueMs > current.valueMs) return step
    return current
  }, null)
  return {
    stepCount: steps.length,
    segmentTotalMs: segments.reduce((total, segment) => total + segment.valueMs, 0),
    bottleneckStep,
  }
}

export function shouldAutoExpandTimeline(item: SystemLogRow | null, bottleneckStep: DetailTimelineStep | null): boolean {
  if (!item) return false
  if (isFailed(item)) return true
  if (Number(item.durationMs || 0) >= 180_000) return true
  if (metricValueFromLog(item, 'stream_error_ms') > 0) return true
  return bottleneckStep?.tone === 'danger'
}

export function buildPrimaryDetailFields(item: SystemLogRow | null): DetailField[] {
  if (!item) return []
  const accountDetailsLiveInAttemptTimeline = hasImageAttemptBreakdown(item)
  return compactDetailFields([
    { label: '请求 ID', value: rawDetailValue(item, 'call_id') || item.id, copyable: true },
    { label: '接口', value: item.endpoint, copyable: true },
    { label: '模型', value: item.model, copyable: true },
    accountDetailsLiveInAttemptTimeline ? null : { label: '账号', value: item.accountEmail, copyable: true },
    { label: '密钥', value: maskKeyLabel([item.keyName, item.keyId].filter(Boolean).join(' / ')) },
    { label: '出口', value: egressDetailValue(item) },
    accountDetailsLiveInAttemptTimeline ? null : { label: '会话 ID', value: item.conversationId, copyable: true },
    { label: '时间', value: timeRangeDetailValue(item), wide: true },
  ])
}

export function hasImageAttemptBreakdown(item: SystemLogRow): boolean {
  return item.imageAttempts.length > 0
    && (item.imageAttempts.length > 1 || item.imageRequestedCount > 1)
}

export function buildDiagnosticDetailFields(item: SystemLogRow | null): DetailField[] {
  if (!item) return []
  if (!hasDiagnosticSignal(item)) return []
  const shouldShowBooleans = isFailed(item) || isLimited(item) || Boolean(item.errorCode || item.error || item.reason)
  return compactDetailFields([
    { label: '状态码', value: item.statusCode },
    { label: '错误码', value: item.errorCode, copyable: true },
    { label: '阶段', value: diagnosticStageValue(item), copyable: true },
    { label: '原因', value: item.reason, copyable: true },
    { label: '上游错误', value: item.upstreamErrorType, copyable: true },
    { label: '上游请求 ID', value: item.upstreamRequestId, copyable: true },
    { label: '请求形状', value: item.requestShape, copyable: true },
    shouldShowBooleans ? { label: '工具调用', value: item.toolInvoked } : null,
    shouldShowBooleans ? { label: '阻断', value: item.blocked } : null,
    { label: '上游文本长度', value: item.upstreamMessageLen },
  ])
}
