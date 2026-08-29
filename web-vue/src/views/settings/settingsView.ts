import { prepareSettingsForSave } from '@/api/settings'
import type { BackupState } from '@/api/settings'
import type { ProxyRuntimeStatus, Settings } from '@/types/api'

export type SettingsSelectOption = {
  label: string
  value: string
}

export type SettingsSummaryItem = {
  label: string
  value: string
}

export type SettingsApiDocItem = {
  title: string
  method: string
  path: string
  description: string
  example: string
}

export type SettingsBackupIncludeKey =
  | 'config'
  | 'register'
  | 'cpa'
  | 'sub2api'
  | 'logs'
  | 'dashboard_metrics'
  | 'image_tasks'
  | 'accounts_snapshot'
  | 'auth_keys_snapshot'
  | 'images'

export type SettingsBackupIncludeOption = {
  value: SettingsBackupIncludeKey
  label: string
}

export type NumberSettingOptions = {
  integer?: boolean
  min?: number
  max?: number
  fallback?: number
}

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

export const settingsTabs: SettingsSelectOption[] = [
  { value: 'basic', label: '基础配置' },
  { value: 'storage', label: '图片存储与审核' },
  { value: 'prompts', label: '提示词源' },
  { value: 'backup', label: 'R2 备份' },
  { value: 'keys', label: '用户密钥' },
  { value: 'api-docs', label: '接口接入' },
  { value: 'canvas', label: '画布入口' },
  { value: 'cpa', label: 'CPA' },
  { value: 'sub2api', label: 'Sub2API' },
]

export const logLevelOptions = ['debug', 'info', 'warning', 'error'] as const

export const imageUpscaleEngineOptions: SettingsSelectOption[] = [
  { label: 'Sharp / Lanczos3', value: 'sharp_lanczos3' },
  { label: 'Pillow / Lanczos', value: 'pillow_lanczos' },
]

export const backupIncludeOptions: SettingsBackupIncludeOption[] = [
  { value: 'config', label: '系统配置' },
  { value: 'register', label: '注册配置' },
  { value: 'cpa', label: 'CPA 配置' },
  { value: 'sub2api', label: 'Sub2API 配置' },
  { value: 'logs', label: '调度与调用日志' },
  { value: 'dashboard_metrics', label: '概览统计' },
  { value: 'image_tasks', label: '图片任务记录' },
  { value: 'accounts_snapshot', label: '账号快照' },
  { value: 'auth_keys_snapshot', label: '用户密钥快照' },
  { value: 'images', label: '图片文件目录' },
]

export const imageStorageModeOptions: SettingsSelectOption[] = [
  { label: '仅本地', value: 'local' },
  { label: '仅 WebDAV', value: 'webdav' },
  { label: '本地 + WebDAV', value: 'both' },
]

export const proxyRuntimeEgressOptions: SettingsSelectOption[] = [
  { label: '直连', value: 'direct' },
  { label: '单代理', value: 'single_proxy' },
]

export const proxyClearanceModeOptions: SettingsSelectOption[] = [
  { label: '关闭', value: 'none' },
  { label: 'FlareSolverr', value: 'flaresolverr' },
  { label: '手动 Cookie', value: 'manual' },
]

export function backupStatusText(state: BackupState | null | undefined) {
  if (!state) return '未加载'
  if (state.running) return '备份中'
  if (state.last_status === 'success') return '最近成功'
  if (state.last_status === 'error') return '最近失败'
  return state.last_status || '未执行'
}

export function buildProxyRuntimeSummaryItems(status: ProxyRuntimeStatus | null | undefined): SettingsSummaryItem[] {
  return [
    { label: '运行时', value: status ? (status.enabled ? '已启用' : '关闭') : '-' },
    { label: '出站方式', value: status ? (status.egress_mode === 'single_proxy' ? '单代理' : '直连') : '-' },
    { label: '代理', value: status ? (status.has_proxy ? '已配置' : '未配置') : '-' },
    { label: '清障', value: status ? (status.clearance_enabled ? `已启用 / ${status.clearance_mode}` : '关闭') : '-' },
    { label: '缓存', value: status ? (status.has_clearance_bundle ? '已有 clearance' : '暂无缓存') : '-' },
  ]
}

export function formatBytes(value: unknown) {
  const bytes = Number(value) || 0
  if (bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`
}

export function formatDateTime(value: unknown) {
  const raw = String(value || '').trim()
  if (!raw) return '-'
  const parsed = new Date(raw)
  if (Number.isNaN(parsed.getTime())) return raw
  return dateTimeFormatter.format(parsed)
}

export function normalizeNumberSetting(value: unknown, options: NumberSettingOptions = {}): number {
  const fallback = options.fallback ?? 0
  const min = options.min ?? 0
  const parsed = Number(value)
  const finite = Number.isFinite(parsed) ? parsed : fallback
  const bounded = Math.max(min, finite)
  const clamped = typeof options.max === 'number' ? Math.min(options.max, bounded) : bounded
  return options.integer ? Math.round(clamped) : clamped
}

export function settingsFingerprint(value: Settings | null | undefined): string {
  return value ? JSON.stringify(prepareSettingsForSave(value)) : ''
}

export function buildApiDocItems(serviceBaseUrl: string, currentApiKey: string): SettingsApiDocItem[] {
  const cleanServiceBaseUrl = String(serviceBaseUrl || '').replace(/\/$/, '')
  const openAIBaseUrl = `${cleanServiceBaseUrl}/v1`
  return [
    {
      title: '模型列表',
      method: 'GET',
      path: '/v1/models',
      description: '返回 OpenAI 兼容模型列表。',
      example: `curl ${openAIBaseUrl}/models \\\n  -H "Authorization: Bearer ${currentApiKey}"`,
    },
    {
      title: '聊天补全',
      method: 'POST',
      path: '/v1/chat/completions',
      description: 'OpenAI 兼容聊天补全接口，图片兼容场景也会解析 n 等参数。',
      example: `curl ${openAIBaseUrl}/chat/completions \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${currentApiKey}" \\\n  -d '{"model":"gpt-5-mini","messages":[{"role":"user","content":"你好"}]}'`,
    },
    {
      title: 'Responses',
      method: 'POST',
      path: '/v1/responses',
      description: '兼容 Responses 输入结构，支持文本与工具调用场景。',
      example: `curl ${openAIBaseUrl}/responses \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${currentApiKey}" \\\n  -d '{"model":"gpt-5-mini","input":"生成一张未来城市图片"}'`,
    },
    {
      title: 'Messages',
      method: 'POST',
      path: '/v1/messages',
      description: 'Anthropic Messages 兼容入口，支持 Authorization Bearer 或 x-api-key 鉴权。',
      example: `curl ${openAIBaseUrl}/messages \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${currentApiKey}" \\\n  -d '{"model":"gpt-5-mini","max_tokens":1024,"messages":[{"role":"user","content":"你好"}]}'`,
    },
    {
      title: '联网搜索',
      method: 'POST',
      path: '/v1/search',
      description: '本地搜索兼容入口，返回 answer 与 sources。',
      example: `curl ${openAIBaseUrl}/search \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${currentApiKey}" \\\n  -d '{"prompt":"今天的 OpenAI 新闻"}'`,
    },
    {
      title: '图片生成',
      method: 'POST',
      path: '/v1/images/generations',
      description: '图片生成接口，支持 prompt、model、n、size、quality 等参数；需要 Idempotency-Key 或 client_task_id。',
      example: `curl ${openAIBaseUrl}/images/generations \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${currentApiKey}" \\\n  -H "Idempotency-Key: image-generation-demo-1" \\\n  -d '{"model":"gpt-image-2","prompt":"一张极简产品海报","n":1}'`,
    },
    {
      title: '图片编辑',
      method: 'POST',
      path: '/v1/images/edits',
      description: '图片编辑接口，支持 multipart 上传参考图；需要 Idempotency-Key 或 client_task_id。',
      example: `curl ${openAIBaseUrl}/images/edits \\\n  -H "Authorization: Bearer ${currentApiKey}" \\\n  -H "Idempotency-Key: image-edit-demo-1" \\\n  -F "model=gpt-image-2" \\\n  -F "prompt=改成赛博朋克夜景" \\\n  -F "image=@./input.png"`,
    },
    {
      title: '创建可编辑文件任务',
      method: 'POST',
      path: '/v1/editable-file-tasks',
      description: '统一创建 PPT/PSD 文件任务，kind 可填 ppt 或 psd；需要 Idempotency-Key 或 client_task_id。',
      example: `curl ${openAIBaseUrl}/editable-file-tasks \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${currentApiKey}" \\\n  -H "Idempotency-Key: editable-file-demo-1" \\\n  -d '{"kind":"ppt","prompt":"做一份产品发布会 PPT"}'`,
    },
    {
      title: '查询可编辑文件任务',
      method: 'GET',
      path: '/v1/editable-file-tasks?ids={taskId1,taskId2}',
      description: '按任务 ID 查询 PPT/PSD 文件生成状态。',
      example: `curl "${openAIBaseUrl}/editable-file-tasks?ids=task_1,task_2" \\\n  -H "Authorization: Bearer ${currentApiKey}"`,
    },
    {
      title: 'PPT 生成任务',
      method: 'POST',
      path: '/v1/ppt/generations',
      description: '直接创建 PPT 生成任务，返回任务 ID 后再查询状态；需要 Idempotency-Key 或 client_task_id。',
      example: `curl ${openAIBaseUrl}/ppt/generations \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${currentApiKey}" \\\n  -H "Idempotency-Key: ppt-generation-demo-1" \\\n  -d '{"prompt":"生成一份市场分析 PPT"}'`,
    },
    {
      title: 'PSD 生成任务',
      method: 'POST',
      path: '/v1/psd/generations',
      description: '直接创建 PSD 生成任务，返回任务 ID 后再查询状态；需要 Idempotency-Key 或 client_task_id。',
      example: `curl ${openAIBaseUrl}/psd/generations \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${currentApiKey}" \\\n  -H "Idempotency-Key: psd-generation-demo-1" \\\n  -d '{"prompt":"生成一张电商海报 PSD"}'`,
    },
    {
      title: '文件下载',
      method: 'GET',
      path: '/files/{file_path}',
      description: '下载 PPT/PSD 任务生成的受保护文件，需要与任务所属账号一致的 API Key。',
      example: `curl ${cleanServiceBaseUrl}/files/{file_path} \\\n  -H "Authorization: Bearer ${currentApiKey}"`,
    },
  ]
}
