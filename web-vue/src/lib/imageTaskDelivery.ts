export const IMAGE_LOAD_ERROR_PREFIX = '图片加载失败，请检查图片访问路径后重试。'

const apiBaseUrl = String(import.meta.env.VITE_API_URL || '').replace(/\/+$/, '')

type ImageTaskLikeMessage = {
  mode?: string
  status?: string
  taskId?: string
  clientTaskId?: string
  error?: string
}

type ImageTaskLikeTask = {
  id?: string
  task_id?: string
  client_task_id?: string
  status?: string
  delivery_status?: string
  succeeded_jobs?: number
}

type ImageTaskLikeAsset = {
  url?: string
  b64_json?: string
  path?: string
  relative_path?: string
}

export function cleanImageTaskText(value: unknown) {
  return String(value ?? '').trim()
}

function publicImageTaskAssetUrl(path: string) {
  return apiBaseUrl ? `${apiBaseUrl}${path}` : path
}

export function imageTaskMessageLookupId(message: ImageTaskLikeMessage | null | undefined) {
  if (!message) return ''
  return cleanImageTaskText(message.taskId) || cleanImageTaskText(message.clientTaskId)
}

export function imageTaskLookupKeys(task: ImageTaskLikeTask | null | undefined) {
  if (!task) return []
  return Array.from(new Set([
    cleanImageTaskText(task.id),
    cleanImageTaskText(task.task_id),
    cleanImageTaskText(task.client_task_id),
  ].filter(Boolean)))
}

function relativeImagePathUrl(value: unknown) {
  const cleaned = cleanImageTaskText(value)
  if (!cleaned) return ''
  if (/^https?:\/\//i.test(cleaned)) return cleaned
  if (/^(?:data|javascript|vbscript):/i.test(cleaned)) return ''
  const rel = cleaned.replace(/^\/+/, '')
  if (!rel || rel.includes('..')) return ''
  if (rel.startsWith('images/') || rel.startsWith('image-thumbnails/')) {
    return publicImageTaskAssetUrl(`/${rel}`)
  }
  return publicImageTaskAssetUrl(`/images/${rel}`)
}

export function resolveImageTaskAssetUrl(asset: ImageTaskLikeAsset | null | undefined) {
  if (!asset) return ''
  const url = cleanImageTaskText(asset.url)
  if (/^(?:https?:|data:|blob:)/i.test(url)) return url
  if (url) return relativeImagePathUrl(url)
  const base64 = cleanImageTaskText(asset.b64_json)
  if (base64) return `data:image/png;base64,${base64}`
  return relativeImagePathUrl(asset.relative_path || asset.path)
}

export function resolveImageTaskAssetFetchSource(asset: ImageTaskLikeAsset | null | undefined) {
  if (!asset) return ''
  const url = cleanImageTaskText(asset.url)
  if (/^(?:https?:|data:|blob:)/i.test(url)) return url
  const base64 = cleanImageTaskText(asset.b64_json)
  if (base64) return `data:image/png;base64,${base64}`
  return relativeImagePathUrl(asset.path || asset.relative_path) || relativeImagePathUrl(url)
}

export function imageTaskAssetDeliveryKey(asset: ImageTaskLikeAsset | null | undefined) {
  if (!asset) return ''
  const path = cleanImageTaskText(asset.path) || cleanImageTaskText(asset.relative_path)
  if (path) return path
  const url = cleanImageTaskText(asset.url)
  if (/^(?:https?:|data:|blob:)/i.test(url)) return url
  return relativeImagePathUrl(url) || resolveImageTaskAssetUrl(asset)
}

export function isAmbiguousImageTaskSubmissionError(error: unknown) {
  if (!error || typeof error !== 'object') return false
  const value = error as Record<string, unknown>
  const response = value.response
  const responseStatus = response && typeof response === 'object'
    ? Number((response as Record<string, unknown>).status)
    : 0
  const status = Number(value.status) || responseStatus
  if (status) return status >= 500 || status === 408 || status === 425 || status === 429

  const code = cleanImageTaskText(value.code).toUpperCase()
  if ([
    'ECONNABORTED',
    'ECONNRESET',
    'EAI_AGAIN',
    'ECONNREFUSED',
    'ERR_NETWORK',
    'ETIMEDOUT',
  ].includes(code)) {
    return true
  }
  if (value.request) return true
  return error instanceof TypeError && /\b(network|fetch|timeout)\b/i.test(error.message)
}

export function isImageLoadDeliveryError(message: ImageTaskLikeMessage | null | undefined) {
  return message?.mode === 'image'
    && message.status === 'error'
    && cleanImageTaskText(message.error).startsWith(IMAGE_LOAD_ERROR_PREFIX)
}

export function imageTaskVisiblePrimaryMessage(
  message: ImageTaskLikeMessage | null | undefined,
  taskOrMessage: ImageTaskLikeTask | unknown,
  maybeTaskMessage?: unknown,
) {
  const hasTask = Boolean(taskOrMessage && typeof taskOrMessage === 'object')
  const taskMessage = hasTask ? maybeTaskMessage : taskOrMessage
  const cleanedTaskMessage = cleanImageTaskText(taskMessage)
  if (isImageLoadDeliveryError(message)) {
    return cleanImageTaskText(message?.error) || cleanedTaskMessage
  }
  const messageError = cleanImageTaskText(message?.error)
  if (!hasTask && message?.mode === 'image' && message.status === 'error' && messageError) {
    return messageError
  }
  return cleanedTaskMessage || messageError
}

export function shouldPreserveImageLoadError(
  message: ImageTaskLikeMessage | null | undefined,
  task: ImageTaskLikeTask | null | undefined,
) {
  return isImageLoadDeliveryError(message)
    && (
      task?.status === 'success'
      || ((task?.status === 'failed' || task?.status === 'canceled') && Number(task.succeeded_jobs || 0) > 0)
    )
    && task.delivery_status !== 'acknowledged'
}
