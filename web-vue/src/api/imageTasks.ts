import apiClient from './client'
import { normalizeImageModel } from '@/config/modelCatalog'
import { resolveImageTaskAssetUrl } from '@/lib/imageTaskDelivery'

export type ImageTaskStatus = 'queued' | 'running' | 'saving' | 'retrying' | 'success' | 'failed' | 'canceled'
export type ImageTaskMode = 'generate' | 'edit'

export interface ImageTaskAsset {
  url?: string
  path?: string
  relative_path?: string
  b64_json?: string
  revised_prompt?: string
  width?: number
  height?: number
  [key: string]: unknown
}

export interface ImageTask {
  id: string
  task_id?: string
  client_task_id?: string
  status: ImageTaskStatus
  mode: ImageTaskMode
  model: string
  n?: number
  size?: string
  quality?: string
  required_jobs?: number
  succeeded_jobs?: number
  failed_jobs?: number
  queue_position?: number
  estimated_wait_seconds?: number
  estimated_start_at?: string
  wait_reason?: string
  delivery_status?: 'pending' | 'response_attempted' | 'acknowledged' | string
  stage?: string
  progress?: string
  created_at?: string
  updated_at?: string
  elapsed_secs?: number
  duration_ms?: number
  conversation_id?: string
  data?: ImageTaskAsset[]
  usage?: Record<string, unknown>
  error?: string
  error_code?: string
  can_resume_poll?: boolean
}

export interface ImageTasksResponse {
  items: ImageTask[]
  missing_ids: string[]
  quota_summary?: ImageQuotaSummary
}

export interface ImageQuotaSummary {
  total_quota: number
  unlimited_quota_count: number
  unknown_quota_count: number
  active_accounts: number
  limited_accounts: number
  abnormal_accounts: number
  disabled_accounts: number
  available: boolean
}

export interface CreateGenerationTaskInput {
  prompt: string
  model?: string
  n?: number
  size?: string
  quality?: string
  clientTaskId?: string
}

export interface CreateEditTaskInput extends CreateGenerationTaskInput {
  files?: File[]
  imageUrls?: string[]
  mask?: File
}

export const DEFAULT_IMAGE_MODEL = 'gpt-image-2'
export const DEFAULT_IMAGE_QUALITY = 'auto'
export const DEFAULT_IMAGE_SIZE = 'auto'

export interface ImageSizeOption {
  label: string
  value: string
}

export type ImageSizeResolution = 'auto' | '1K' | '2K' | '4K'

export interface ImageSizePreset extends ImageSizeOption {
  ratio: string
  resolution: ImageSizeResolution
  width?: number
  height?: number
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b)
}

export function parseImageSize(value: string) {
  if (!value || value === DEFAULT_IMAGE_SIZE) return null
  const match = value.match(/^(\d+)\s*x\s*(\d+)$/i)
  if (!match) return null
  const width = Number(match[1])
  const height = Number(match[2])
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null
  return { width, height }
}

function imageResolutionTier(width: number, height: number): ImageSizeResolution {
  const maxEdge = Math.max(width, height)
  if (maxEdge >= 3840) return '4K'
  if (maxEdge > 1920) return '2K'
  return '1K'
}

export function formatImageSizeLabel(value: string, autoLabel = '自动') {
  if (!value || value === DEFAULT_IMAGE_SIZE) return autoLabel
  const parsed = parseImageSize(value)
  if (!parsed) return value
  const { width, height } = parsed
  const divisor = gcd(width, height)
  const ratio = `${width / divisor}:${height / divisor}`
  return [ratio, imageResolutionTier(width, height), `${width}x${height}`].join(' · ')
}

function createSizePreset(value: string, ratio: string, resolution: ImageSizeResolution): ImageSizePreset {
  const parsed = parseImageSize(value)
  return {
    label: formatImageSizeLabel(value),
    value,
    ratio,
    resolution,
    width: parsed?.width,
    height: parsed?.height,
  }
}

export const STANDARD_IMAGE_SIZE_PRESETS: ImageSizePreset[] = [
  { label: '自动', value: 'auto', ratio: 'auto', resolution: 'auto' },
  createSizePreset('1024x1024', '1:1', '1K'),
  createSizePreset('1024x1536', '2:3', '1K'),
  createSizePreset('1536x1024', '3:2', '1K'),
  createSizePreset('1024x1365', '3:4', '1K'),
  createSizePreset('1365x1024', '4:3', '1K'),
  createSizePreset('1088x1920', '9:16', '1K'),
  createSizePreset('1920x1088', '16:9', '1K'),
]

export const HIGH_RES_IMAGE_SIZE_PRESETS: ImageSizePreset[] = [
  createSizePreset('2048x2048', '1:1', '2K'),
  createSizePreset('2560x1440', '16:9', '2K'),
  createSizePreset('1440x2560', '9:16', '2K'),
  createSizePreset('3840x2160', '16:9', '4K'),
  createSizePreset('2160x3840', '9:16', '4K'),
]

export const IMAGE_SIZE_PRESETS: ImageSizePreset[] = [
  ...STANDARD_IMAGE_SIZE_PRESETS,
  ...HIGH_RES_IMAGE_SIZE_PRESETS,
]

export const STANDARD_IMAGE_SIZE_OPTIONS: ImageSizeOption[] = STANDARD_IMAGE_SIZE_PRESETS
export const HIGH_RES_IMAGE_SIZE_OPTIONS: ImageSizeOption[] = HIGH_RES_IMAGE_SIZE_PRESETS
export const IMAGE_SIZE_OPTIONS: ImageSizeOption[] = IMAGE_SIZE_PRESETS

export function supportsHighResolutionImageSizes(model: string, upscaleEnabled = false) {
  if (upscaleEnabled) return true
  const value = String(model || '').toLowerCase()
  return value.includes('codex-gpt-image-2') || value.includes('gpt-image-2-codex')
}

export function resolveImageSizePresets(model: string, upscaleEnabled = false): ImageSizePreset[] {
  return supportsHighResolutionImageSizes(model, upscaleEnabled)
    ? IMAGE_SIZE_PRESETS
    : STANDARD_IMAGE_SIZE_PRESETS
}

export function resolveImageSizeOptions(model: string, upscaleEnabled = false): ImageSizeOption[] {
  return resolveImageSizePresets(model, upscaleEnabled)
}

export function isImageSizeSupportedByModel(size: string, model: string, upscaleEnabled = false) {
  if (!size || size === DEFAULT_IMAGE_SIZE) return true
  return resolveImageSizeOptions(model, upscaleEnabled).some((option) => option.value === size)
}

export const IMAGE_QUALITY_OPTIONS = [
  { label: '自动', value: 'auto' },
  { label: '低', value: 'low' },
  { label: '中', value: 'medium' },
  { label: '高', value: 'high' },
]

export const IMAGE_COUNT_OPTIONS = [
  { label: '1 张', value: 1 },
  { label: '2 张', value: 2 },
  { label: '3 张', value: 3 },
  { label: '4 张', value: 4 },
]

function cleanString(value: unknown, fallback = '') {
  const text = String(value ?? '').trim()
  return text || fallback
}

export function normalizeImageCount(value: unknown) {
  const count = Number.isFinite(Number(value)) ? Math.trunc(Number(value)) : 1
  return Math.min(4, Math.max(1, count))
}

function positiveImageDimension(value: unknown) {
  const dimension = Number(value)
  return Number.isFinite(dimension) && dimension > 0 ? Math.trunc(dimension) : undefined
}

export function createClientTaskId(prefix = 'img') {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function normalizeTask(raw: Partial<ImageTask>): ImageTask {
  return {
    id: cleanString(raw.id),
    task_id: cleanString(raw.task_id),
    client_task_id: cleanString(raw.client_task_id),
    status: (cleanString(raw.status, 'queued') as ImageTaskStatus),
    mode: (cleanString(raw.mode, 'generate') as ImageTaskMode),
    model: cleanString(raw.model, DEFAULT_IMAGE_MODEL),
    n: normalizeImageCount(raw.n),
    size: cleanString(raw.size),
    quality: cleanString(raw.quality, DEFAULT_IMAGE_QUALITY),
    required_jobs: numberValue(raw.required_jobs),
    succeeded_jobs: numberValue(raw.succeeded_jobs),
    failed_jobs: numberValue(raw.failed_jobs),
    queue_position: numberValue(raw.queue_position),
    estimated_wait_seconds: numberValue(raw.estimated_wait_seconds),
    estimated_start_at: cleanString(raw.estimated_start_at),
    wait_reason: cleanString(raw.wait_reason),
    delivery_status: cleanString(raw.delivery_status),
    stage: cleanString(raw.stage),
    progress: cleanString(raw.progress),
    created_at: cleanString(raw.created_at),
    updated_at: cleanString(raw.updated_at),
    elapsed_secs: Number.isFinite(Number(raw.elapsed_secs)) ? Number(raw.elapsed_secs) : undefined,
    duration_ms: Number.isFinite(Number(raw.duration_ms)) ? Number(raw.duration_ms) : undefined,
    conversation_id: cleanString(raw.conversation_id),
    data: Array.isArray(raw.data) ? raw.data.map((asset) => normalizeTaskAsset(asset)) : [],
    usage: raw.usage && typeof raw.usage === 'object' ? raw.usage : undefined,
    error: cleanString(raw.error),
    error_code: cleanString(raw.error_code),
    can_resume_poll: Boolean(raw.can_resume_poll),
  }
}

function normalizeTaskAsset(raw: unknown): ImageTaskAsset {
  const asset = (raw && typeof raw === 'object' ? raw : {}) as ImageTaskAsset
  const relativePath = cleanString(asset.relative_path)
  return {
    ...asset,
    url: cleanString(asset.url),
    path: cleanString(asset.path, relativePath),
    relative_path: relativePath || cleanString(asset.path),
    b64_json: cleanString(asset.b64_json),
    revised_prompt: cleanString(asset.revised_prompt),
    width: positiveImageDimension(asset.width),
    height: positiveImageDimension(asset.height),
  }
}

function normalizeResponse(response: Partial<ImageTasksResponse>): ImageTasksResponse {
  return {
    items: (response.items || []).map((item) => normalizeTask(item)),
    missing_ids: Array.isArray(response.missing_ids) ? response.missing_ids.map((id) => String(id)) : [],
    quota_summary: response.quota_summary ? normalizeQuotaSummary(response.quota_summary) : undefined,
  }
}

function numberValue(value: unknown) {
  return Number.isFinite(Number(value)) ? Math.max(0, Math.trunc(Number(value))) : 0
}

function normalizeQuotaSummary(response: Partial<ImageQuotaSummary>): ImageQuotaSummary {
  if (!response || typeof response !== 'object') {
    throw new Error('Invalid image quota response')
  }
  const totalQuota = numberValue(response.total_quota)
  const unlimited = numberValue(response.unlimited_quota_count)
  const unknown = numberValue(response.unknown_quota_count)
  return {
    total_quota: totalQuota,
    unlimited_quota_count: unlimited,
    unknown_quota_count: unknown,
    active_accounts: numberValue(response.active_accounts),
    limited_accounts: numberValue(response.limited_accounts),
    abnormal_accounts: numberValue(response.abnormal_accounts),
    disabled_accounts: numberValue(response.disabled_accounts),
    available: typeof response.available === 'boolean' ? response.available : totalQuota > 0 || unlimited > 0 || unknown > 0,
  }
}

function requestSize(size?: string) {
  const value = cleanString(size, DEFAULT_IMAGE_SIZE)
  return value === DEFAULT_IMAGE_SIZE ? undefined : value
}

function normalizeUrlList(value: string[] | undefined) {
  return (value || []).map((item) => item.trim()).filter(Boolean)
}

function createEditForm(input: CreateEditTaskInput) {
  const form = new FormData()
  form.append('client_task_id', input.clientTaskId || createClientTaskId('edit'))
  form.append('prompt', input.prompt)
  form.append('model', normalizeImageModel(input.model))
  form.append('n', String(normalizeImageCount(input.n)))
  form.append('quality', input.quality || DEFAULT_IMAGE_QUALITY)
  const size = requestSize(input.size)
  if (size) form.append('size', size)

  const imageUrls = normalizeUrlList(input.imageUrls)
  if (imageUrls.length === 1) {
    form.append('image_url', imageUrls[0])
  } else if (imageUrls.length > 1) {
    form.append('images', JSON.stringify(imageUrls))
  }

  for (const file of input.files || []) {
    form.append('image', file, file.name)
  }
  if (input.mask) {
    form.append('mask', input.mask, input.mask.name || 'mask.png')
  }
  return form
}

export function isImageTaskTerminal(task: ImageTask) {
  return task.status === 'success' || task.status === 'failed' || task.status === 'canceled'
}

export const IMAGE_TASK_PROGRESS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '生成中',
  saving: '保存中',
  retrying: '重试中',
  failed: '失败',
  canceled: '已取消',
  getting_account: '等待账号',
  image_egress_waiting: '等待出口',
  image_egress_ready: '出口就绪',
  uploading: '上传中',
  bootstrapping: '初始化',
  getting_token: '获取令牌',
  preparing_conversation: '准备会话',
  starting_generation: '启动生成',
  generating: '上游生成中',
  image_stream_resolve_start: '解析结果',
  receiving_image: '接收图片',
}

export function imageTaskProgressLabel(task?: ImageTask | null) {
  const key = cleanString(task?.progress || task?.stage || task?.status)
  return IMAGE_TASK_PROGRESS_LABELS[key] || '生成中'
}

export function taskPrimaryMessage(task?: ImageTask | null) {
  return cleanString(task?.error, task?.status === 'canceled' ? '图片任务已取消' : '图片生成失败')
}

export function imageAssetUrl(asset: ImageTaskAsset) {
  return resolveImageTaskAssetUrl(asset)
}

export const imageTasksApi = {
  list: async (ids?: string[]) => {
    const params = ids?.length ? { ids: ids.join(',') } : undefined
    const response = await apiClient.get<never, ImageTasksResponse>('/api/image-tasks', { params })
    return normalizeResponse(response)
  },

  quota: async () => {
    const response = await apiClient.get<never, ImageQuotaSummary>('/api/image-tasks/quota')
    return normalizeQuotaSummary(response)
  },

  createGeneration: async (input: CreateGenerationTaskInput) => {
    const response = await apiClient.post<Record<string, unknown>, ImageTask>('/api/image-tasks/generations', {
      client_task_id: input.clientTaskId || createClientTaskId('gen'),
      prompt: input.prompt,
      model: normalizeImageModel(input.model),
      n: normalizeImageCount(input.n),
      size: requestSize(input.size),
      quality: input.quality || DEFAULT_IMAGE_QUALITY,
    })
    return normalizeTask(response)
  },

  createEdit: async (input: CreateEditTaskInput) => {
    const response = await apiClient.post<FormData, ImageTask>('/api/image-tasks/edits', createEditForm(input))
    return normalizeTask(response)
  },

  cancel: async (taskId: string) => {
    const response = await apiClient.post<never, ImageTask>(
      `/api/image-tasks/${encodeURIComponent(taskId)}/cancel`,
    )
    return normalizeTask(response)
  },

  resumePoll: async (taskId: string, extraTimeoutSecs = 30) => {
    const response = await apiClient.post<{ extra_timeout_secs: number }, ImageTask>(
      `/api/image-tasks/${encodeURIComponent(taskId)}/resume-poll`,
      { extra_timeout_secs: extraTimeoutSecs },
    )
    return normalizeTask(response)
  },

  acknowledge: async (taskId: string) => {
    const response = await apiClient.post<never, ImageTask>(
      `/api/image-tasks/${encodeURIComponent(taskId)}/ack`,
    )
    return normalizeTask(response)
  },
}
