import { computed, ref, watch, type ComputedRef } from 'vue'
import {
  imageTasksApi,
  taskPrimaryMessage,
  type ImageTask,
  type ImageTaskAsset,
} from '@/api/imageTasks'
import {
  imageTaskAssetDeliveryKey,
  imageTaskLookupKeys,
  imageTaskMessageLookupId,
  shouldPreserveImageLoadError,
} from '@/lib/imageTaskDelivery'
import type { PageRuntime } from '@/composables/usePageRuntime'
import type {
  StudioConversation,
  StudioConversationBadge,
  StudioConversationBadgeState,
} from '@/components/studio/types'
import { getJsonPreference, preferenceKeys, setJsonPreference } from '@/lib/preferences'
import { isStudioImageMessageRunning, type StudioConversationLookup, type StudioConversationRuntimeIndex } from './studioConversationState'
import { cleanStudioText } from './studioSearchView'

const IMAGE_POLL_TIMER_KEY = 'studio:image-poll'
const IMAGE_REFRESH_TIMER_KEY = 'studio:image-refresh'
const IMAGE_ACK_RETRY_TIMER_KEY = 'studio:image-ack-retry'
const IMAGE_TASKS_REQUEST_KEY = 'studio:image-tasks'
const IMAGE_ACK_RETRY_DELAY_MS = 4000
const IMAGE_SUBMISSION_RECONCILE_TTL_MS = 5 * 60 * 1000

export type StudioImageTaskRuntimeHooks = {
  markConversationNotice: (conversationId: string, state: StudioConversationBadgeState) => void
  touchConversation: (conversation: StudioConversation) => void
  onRefreshError: (message: string) => void
  onRefreshSuccess?: () => void
  formatError: (error: unknown, fallback: string) => string
}

export type StudioImageTaskRuntimeInput = {
  pageRuntime: PageRuntime
  activeConversation: ComputedRef<StudioConversation | null>
  conversationNotices: ComputedRef<Record<string, StudioConversationBadgeState>>
  conversationLookup: ComputedRef<StudioConversationLookup>
  conversationRuntimeIndex: ComputedRef<StudioConversationRuntimeIndex>
  hooks: StudioImageTaskRuntimeHooks
}

export function useStudioImageTaskRuntime(input: StudioImageTaskRuntimeInput) {
  const imageTasks = ref<ImageTask[]>([])
  const isFetchingTasks = ref(false)
  const taskById = computed(() => {
    const map = new Map<string, ImageTask>()
    imageTasks.value.forEach((task) => {
      imageTaskLookupKeys(task).forEach((key) => map.set(key, task))
    })
    return map
  })
  const activeImageTaskIds = computed(() => {
    const ids = input.activeConversation.value?.messages
      .map((message) => imageTaskMessageLookupId(message))
      .filter((id): id is string => Boolean(id)) || []
    return Array.from(new Set(ids)).slice(0, 80)
  })
  const pendingImageTaskIds = computed(() => input.conversationRuntimeIndex.value.pendingImageTaskIds)
  const requestedImageTaskIds = computed(() => Array.from(new Set([
    ...activeImageTaskIds.value,
    ...pendingImageTaskIds.value,
    ...storedImageTaskIds(),
  ])).slice(0, 180))
  const activeRunningTaskCount = computed(() => {
    const conversation = input.activeConversation.value
    if (!conversation) return 0
    return conversation.messages.reduce((total, message) => {
      if (isStudioImageMessageRunning(message)) return total + 1
      if (message.status === 'sending' || message.status === 'streaming') return total + 1
      return total
    }, 0)
  })
  const conversationBadges = computed<Record<string, StudioConversationBadge>>(() => {
    const badges: Record<string, StudioConversationBadge> = {}
    const runtime = input.conversationRuntimeIndex.value
    const validIds = input.conversationLookup.value.validIds
    Object.entries(runtime.runningCounts).forEach(([conversationId, running]) => {
      if (running > 0) {
        badges[conversationId] = {
          state: 'running',
          label: `处理中 ${running}`,
          count: running,
        }
      }
    })
    Object.entries(input.conversationNotices.value).forEach(([conversationId, notice]) => {
      if (!validIds.has(conversationId) || badges[conversationId]) return
      if (notice === 'done') {
        badges[conversationId] = { state: 'done', label: '已完成' }
      } else if (notice === 'error') {
        badges[conversationId] = { state: 'error', label: '失败' }
      }
    })
    return badges
  })

  let imageRefreshQueued = false
  let imageRefreshQueuedForce = false
  let lastSuccessfulImageRefreshSignature = ''
  const renderedImageAssetsByTask = new Map<string, Set<string>>()
  const acknowledgingImageTaskIds = new Set<string>()
  const ackRetryTaskIds = new Set<string>()
  const pendingSubmissionTaskIds = new Map<string, number>()

  function storedImageTaskIds() {
    const ids = getJsonPreference<unknown[]>(preferenceKeys.imageTaskLocalIds, [])
    return Array.isArray(ids) ? ids.map((id) => cleanStudioText(id)).filter(Boolean) : []
  }

  function rememberTask(taskId: string) {
    if (!taskId) return
    const ids = Array.from(new Set([taskId, ...storedImageTaskIds()])).slice(0, 160)
    setJsonPreference(preferenceKeys.imageTaskLocalIds, ids)
  }

  function isPendingSubmission(taskId: string) {
    const cleanTaskId = cleanStudioText(taskId)
    if (!cleanTaskId) return false
    const expiresAt = pendingSubmissionTaskIds.get(cleanTaskId) || 0
    if (!expiresAt) return false
    if (Date.now() <= expiresAt) return true
    pendingSubmissionTaskIds.delete(cleanTaskId)
    return false
  }

  function rememberPendingSubmission(taskId: string) {
    const cleanTaskId = cleanStudioText(taskId)
    if (!cleanTaskId) return
    pendingSubmissionTaskIds.set(cleanTaskId, Date.now() + IMAGE_SUBMISSION_RECONCILE_TTL_MS)
    rememberTask(cleanTaskId)
    schedulePoll()
    scheduleRefresh(1200, true)
  }

  function pruneStoredTasks(taskIds: string[]) {
    const missing = new Set(taskIds.map((id) => cleanStudioText(id)).filter(Boolean))
    if (!missing.size) return
    const ids = storedImageTaskIds().filter((id) => !missing.has(id) || isPendingSubmission(id))
    setJsonPreference(preferenceKeys.imageTaskLocalIds, ids)
    if (taskIds.some((id) => isPendingSubmission(id))) scheduleRefresh(4000, true)
  }

  async function refresh(force = false) {
    if (!input.pageRuntime.canRun.value) return
    if (isFetchingTasks.value) {
      imageRefreshQueued = true
      imageRefreshQueuedForce = imageRefreshQueuedForce || force
      return
    }

    const requestSeq = input.pageRuntime.nextRequest(IMAGE_TASKS_REQUEST_KEY)
    const ids = requestedImageTaskIds.value
    const signature = ids.join('\u0000')
    if (!force && signature && signature === lastSuccessfulImageRefreshSignature) {
      if (ackRetryTaskIds.size) scheduleAckRetry()
      return
    }
    if (!ids.length) {
      imageTasks.value = []
      lastSuccessfulImageRefreshSignature = ''
      if (ackRetryTaskIds.size) scheduleAckRetry()
      return
    }

    isFetchingTasks.value = true
    try {
      const response = await imageTasksApi.list(ids)
      if (!input.pageRuntime.isLatestRequest(IMAGE_TASKS_REQUEST_KEY, requestSeq)) return
      merge(response.items)
      markMissing(response.missing_ids)
      pruneStoredTasks(response.missing_ids)
      syncMessageStatuses()
      input.hooks.onRefreshSuccess?.()
      lastSuccessfulImageRefreshSignature = signature
    } catch (error) {
      if (!input.pageRuntime.isLatestRequest(IMAGE_TASKS_REQUEST_KEY, requestSeq)) return
      input.hooks.onRefreshError(input.hooks.formatError(error, '刷新图片任务失败'))
      lastSuccessfulImageRefreshSignature = ''
    } finally {
      if (!input.pageRuntime.isLatestRequest(IMAGE_TASKS_REQUEST_KEY, requestSeq)) return
      isFetchingTasks.value = false
      if (imageRefreshQueued) {
        const queuedForce = imageRefreshQueuedForce
        imageRefreshQueued = false
        imageRefreshQueuedForce = false
        scheduleRefresh(0, queuedForce)
      }
      if (ackRetryTaskIds.size) scheduleAckRetry()
    }
  }

  function merge(items: ImageTask[]) {
    const map = new Map(imageTasks.value.map((task) => [task.id, task]))
    items.filter((task) => task.id).forEach((task) => {
      map.set(task.id, task)
      imageTaskLookupKeys(task).forEach((key) => pendingSubmissionTaskIds.delete(key))
      if (task.delivery_status === 'acknowledged') ackRetryTaskIds.delete(task.id)
    })
    imageTasks.value = Array.from(map.values())
    lastSuccessfulImageRefreshSignature = ''
  }

  function reset() {
    imageTasks.value = []
    lastSuccessfulImageRefreshSignature = ''
    renderedImageAssetsByTask.clear()
    acknowledgingImageTaskIds.clear()
    ackRetryTaskIds.clear()
    pendingSubmissionTaskIds.clear()
    input.pageRuntime.clearTimer(IMAGE_ACK_RETRY_TIMER_KEY)
  }

  function imageAssetDeliveryKey(asset: ImageTaskAsset | { path?: string; url?: string; b64_json?: string } | null | undefined) {
    return imageTaskAssetDeliveryKey(asset)
  }

  function renderableImageAssetKeys(task: ImageTask) {
    return new Set((task.data || []).map(imageAssetDeliveryKey).filter(Boolean))
  }

  async function acknowledgeRenderedImageTask(taskId: string, assetKey = '') {
    const cleanTaskId = cleanStudioText(taskId)
    if (!cleanTaskId) return
    const task = taskById.value.get(cleanTaskId)
    if (!task || task.delivery_status === 'acknowledged') {
      ackRetryTaskIds.delete(cleanTaskId)
      return
    }
    const expected = renderableImageAssetKeys(task)
    const deliverable = task.status === 'success'
      || ((task.status === 'failed' || task.status === 'canceled') && expected.size > 0)
    if (!deliverable) return
    if (!expected.size) return
    const loaded = renderedImageAssetsByTask.get(cleanTaskId) || new Set<string>()
    const cleanAssetKey = cleanStudioText(assetKey)
    if (cleanAssetKey) loaded.add(cleanAssetKey)
    renderedImageAssetsByTask.set(cleanTaskId, loaded)
    if (Array.from(expected).some((key) => !loaded.has(key))) return
    await acknowledgeLoadedImageTask(cleanTaskId)
  }

  async function acknowledgeLoadedImageTask(taskId: string) {
    const task = taskById.value.get(taskId)
    if (!task || task.delivery_status === 'acknowledged') {
      ackRetryTaskIds.delete(taskId)
      return
    }
    if (acknowledgingImageTaskIds.has(taskId)) return
    acknowledgingImageTaskIds.add(taskId)
    try {
      const acknowledged = await imageTasksApi.acknowledge(taskId)
      ackRetryTaskIds.delete(taskId)
      merge([acknowledged])
    } catch (error) {
      ackRetryTaskIds.add(taskId)
      input.hooks.onRefreshError(input.hooks.formatError(error, '确认图片送达失败'))
    } finally {
      acknowledgingImageTaskIds.delete(taskId)
      if (ackRetryTaskIds.size) scheduleAckRetry()
    }
  }

  function scheduleAckRetry(delay = IMAGE_ACK_RETRY_DELAY_MS) {
    if (!input.pageRuntime.canRun.value || !ackRetryTaskIds.size) return
    input.pageRuntime.setTimer(IMAGE_ACK_RETRY_TIMER_KEY, delay, () => {
      void retryPendingAcknowledgements()
    })
  }

  async function retryPendingAcknowledgements() {
    const taskIds = Array.from(ackRetryTaskIds)
    for (const taskId of taskIds) {
      const task = taskById.value.get(taskId)
      if (!task || task.delivery_status === 'acknowledged') {
        ackRetryTaskIds.delete(taskId)
        continue
      }
      const expected = renderableImageAssetKeys(task)
      const deliverable = task.status === 'success'
        || ((task.status === 'failed' || task.status === 'canceled') && expected.size > 0)
      const loaded = renderedImageAssetsByTask.get(taskId)
      if (!deliverable || !expected.size || !loaded || Array.from(expected).some((key) => !loaded.has(key))) {
        ackRetryTaskIds.delete(taskId)
        continue
      }
      await acknowledgeLoadedImageTask(taskId)
    }
    if (ackRetryTaskIds.size) scheduleAckRetry()
  }

  async function resumeImageTask(taskId: string) {
    const cleanTaskId = cleanStudioText(taskId)
    if (!cleanTaskId) return
    try {
      const task = await imageTasksApi.resumePoll(cleanTaskId)
      rememberTask(task.id || cleanTaskId)
      merge([task])
      syncMessageStatuses()
      schedulePoll()
      scheduleRefresh(0, true)
    } catch (error) {
      input.hooks.onRefreshError(input.hooks.formatError(error, '恢复图片轮询失败'))
    }
  }

  function markMissing(taskIds: string[]) {
    const missing = new Set(taskIds.filter(Boolean))
    if (!missing.size) return
    const changedConversations = new Set<StudioConversation>()
    input.conversationRuntimeIndex.value.imageTaskMessageEntries.forEach(({ conversation, message }) => {
      const lookupId = imageTaskMessageLookupId(message)
      if (!lookupId || !missing.has(lookupId)) return
      if (isPendingSubmission(lookupId)) {
        scheduleRefresh(4000, true)
        return
      }
      if (message.status === 'error') return
      message.status = 'error'
      message.error = '图片任务已过期或不存在'
      changedConversations.add(conversation)
      input.hooks.markConversationNotice(conversation.id, 'error')
    })
    changedConversations.forEach(input.hooks.touchConversation)
  }

  function syncMessageStatuses() {
    const changedConversations = new Set<StudioConversation>()
    input.conversationRuntimeIndex.value.imageTaskMessageEntries.forEach(({ conversation, message }) => {
      const lookupId = imageTaskMessageLookupId(message)
      if (!lookupId) return
      const task = taskById.value.get(lookupId)
      if (!task) return
      const previousStatus = message.status
      if (!message.taskId && task.id) {
        message.taskId = task.id
      }
      if (task.status === 'success') {
        if (shouldPreserveImageLoadError(message, task)) {
          changedConversations.add(conversation)
          return
        }
        message.status = 'done'
        if (previousStatus !== 'done') input.hooks.markConversationNotice(conversation.id, 'done')
      } else if (task.status === 'failed' || task.status === 'canceled') {
        message.status = 'error'
        message.error = taskPrimaryMessage(task)
        if (previousStatus !== 'error') input.hooks.markConversationNotice(conversation.id, 'error')
      } else if (task.status === 'queued' || task.status === 'retrying') {
        message.status = 'queued'
      } else if (task.status === 'running' || task.status === 'saving') {
        message.status = 'running'
      } else {
        message.status = 'queued'
      }
      if (message.status !== previousStatus) changedConversations.add(conversation)
    })
    changedConversations.forEach(input.hooks.touchConversation)
  }

  function schedulePoll() {
    input.pageRuntime.clearInterval(IMAGE_POLL_TIMER_KEY)
    if (!input.pageRuntime.canRun.value) return
    if (!pendingImageTaskIds.value.length) return
    input.pageRuntime.setInterval(IMAGE_POLL_TIMER_KEY, 4000, () => {
      void refresh(true)
    })
  }

  function scheduleRefresh(delay = 120, force = false) {
    if (!input.pageRuntime.canRun.value) return
    input.pageRuntime.setTimer(IMAGE_REFRESH_TIMER_KEY, delay, () => {
      void refresh(force)
    })
  }

  function deactivate() {
    input.pageRuntime.invalidateRequest(IMAGE_TASKS_REQUEST_KEY)
    isFetchingTasks.value = false
    imageRefreshQueued = false
    imageRefreshQueuedForce = false
    input.pageRuntime.clearInterval(IMAGE_POLL_TIMER_KEY)
    input.pageRuntime.clearTimer(IMAGE_REFRESH_TIMER_KEY)
    input.pageRuntime.clearTimer(IMAGE_ACK_RETRY_TIMER_KEY)
  }

  const stopRequestedImageTaskWatch = watch(requestedImageTaskIds, () => scheduleRefresh())
  const stopPendingImageTaskWatch = watch(pendingImageTaskIds, schedulePoll)

  function dispose() {
    deactivate()
    stopRequestedImageTaskWatch()
    stopPendingImageTaskWatch()
  }

  return {
    imageTasks,
    isFetchingTasks,
    taskById,
    activeImageTaskIds,
    pendingImageTaskIds,
    requestedImageTaskIds,
    activeRunningTaskCount,
    conversationBadges,
    rememberTask,
    rememberPendingSubmission,
    refresh,
    merge,
    reset,
    acknowledgeRenderedImageTask,
    scheduleAckRetry,
    resumeImageTask,
    schedulePoll,
    scheduleRefresh,
    deactivate,
    dispose,
  }
}
