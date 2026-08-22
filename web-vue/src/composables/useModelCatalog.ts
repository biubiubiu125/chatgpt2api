import { computed, ref } from 'vue'
import { modelsApi } from '@/api/models'
import type { ModelCatalogResponse, ModelListResponse } from '@/api/models'
import type { Settings } from '@/types/api'
import {
  isImageModelId,
  PUBLIC_IMAGE_MODEL,
  resolveChatModels,
  resolveImageModels,
} from '@/config/modelCatalog'

type SettingsResolver = () => Settings | null | undefined

const sharedCatalog = ref<ModelCatalogResponse | null>(null)
const loadError = ref<Error | null>(null)
const isLoading = ref(false)

let hasLoaded = false
let inflight: Promise<ModelCatalogResponse | null> | null = null

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

function normalizeCatalog(payload: ModelCatalogResponse | null | undefined): ModelCatalogResponse | null {
  if (!payload) return null
  const chatModels = normalizeList(payload.chat_models)
  const imageModels = normalizeList(payload.image_models).filter(model => model === PUBLIC_IMAGE_MODEL)
  return {
    ...payload,
    chat_models: chatModels,
    image_models: imageModels,
    all_models: normalizeList(payload.all_models).length
      ? normalizeList(payload.all_models)
      : normalizeList([...chatModels, ...imageModels]),
    capabilities: {
      image_upscale: Boolean(payload.capabilities?.image_upscale),
    },
  }
}

function catalogFromOpenAIModels(response: ModelListResponse): ModelCatalogResponse | null {
  const ids = normalizeList((Array.isArray(response.data) ? response.data : []).map(item => item?.id))
  if (ids.length === 0) return null
  return {
    object: 'model_catalog',
    chat_models: ids.filter(model => !isImageModelId(model)),
     image_models: ids.filter(model => model === PUBLIC_IMAGE_MODEL),
    all_models: ids,
    capabilities: {
      image_upscale: false,
    },
    source: {
      chat: 'openai_models_endpoint',
      image: 'openai_models_endpoint',
    },
    openai_models_endpoint: '/v1/models',
  }
}

export function useModelCatalog(resolveSettings: SettingsResolver) {
  const chatModels = computed(() => {
    const fromCatalog = normalizeList(sharedCatalog.value?.chat_models)
    return fromCatalog.length > 0 ? fromCatalog : resolveChatModels(resolveSettings())
  })

  const imageModels = computed(() => {
    const fromCatalog = normalizeList(sharedCatalog.value?.image_models)
    return fromCatalog.length > 0 ? fromCatalog : resolveImageModels(resolveSettings())
  })

  async function loadModelCatalog(force = false) {
    if (!force && hasLoaded) return sharedCatalog.value
    if (inflight) return inflight

    isLoading.value = true
    inflight = (async () => {
      hasLoaded = true
      try {
        const catalog = normalizeCatalog(await modelsApi.catalog())
        sharedCatalog.value = catalog
        loadError.value = null
        return catalog
      } catch (catalogError) {
        try {
          const fallback = normalizeCatalog(catalogFromOpenAIModels(await modelsApi.list()))
          sharedCatalog.value = fallback
          loadError.value = null
          return fallback
        } catch (listError) {
          sharedCatalog.value = null
          loadError.value = listError instanceof Error ? listError : new Error('Failed to load model catalog')
          console.error('Failed to load model catalog:', catalogError, listError)
          return null
        }
      } finally {
        isLoading.value = false
        inflight = null
      }
    })()

    return inflight
  }

  return {
    catalog: sharedCatalog,
    chatModels,
    imageModels,
    isLoading,
    loadError,
    loadModelCatalog,
  }
}
