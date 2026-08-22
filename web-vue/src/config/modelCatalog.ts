import type { Settings } from '@/types/api'

export const FALLBACK_CHAT_MODELS = [
  'auto',
  'gpt-5',
  'gpt-5-1',
  'gpt-5-2',
  'gpt-5-3',
  'gpt-5-3-mini',
  'gpt-5-5',
  'gpt-5-mini',
]

export const FALLBACK_IMAGE_MODELS = [
  'gpt-image-2',
]
export const PUBLIC_IMAGE_MODEL = FALLBACK_IMAGE_MODELS[0]

function normalizeList(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  const result: string[] = []
  for (const item of raw) {
    const value = String(item || '').trim()
    if (!value || result.includes(value)) continue
    result.push(value)
  }
  return result
}

export function isImageModelId(model: string): boolean {
  const value = model.toLowerCase()
  return value.includes('image') || value.includes('dall-e') || value.includes('gpt-image')
}

export function normalizeImageModel(value: unknown): string {
  const candidate = String(value ?? '').trim()
  return candidate === PUBLIC_IMAGE_MODEL ? candidate : PUBLIC_IMAGE_MODEL
}

export function resolveChatModels(settings: Settings | null | undefined): string[] {
  const fromCatalog = normalizeList(settings?.model_catalog?.chat_models)
  if (fromCatalog.length > 0) return fromCatalog
  return [...FALLBACK_CHAT_MODELS]
}

export function resolveImageModels(settings: Settings | null | undefined): string[] {
  const fromImageConfig = normalizeList(settings?.image_generation?.model_options)
  const configuredImageModels = fromImageConfig.filter(model => model === PUBLIC_IMAGE_MODEL)
  if (configuredImageModels.length > 0) return configuredImageModels
  const fromCatalog = normalizeList(settings?.model_catalog?.image_api_models)
  const catalogImageModels = fromCatalog.filter(model => model === PUBLIC_IMAGE_MODEL)
  if (catalogImageModels.length > 0) return catalogImageModels
  return [...FALLBACK_IMAGE_MODELS]
}
