import { nextTick, type Ref } from 'vue'
import { createClientTaskId, normalizeImageCount } from '@/api/imageTasks'
import { isAmbiguousImageTaskSubmissionError } from '@/lib/imageTaskDelivery'
import type {
  StudioComposeMode,
  StudioConversation,
  StudioImageCompareSource,
  StudioImageForm,
  StudioMessage,
  StudioReferenceImage,
} from '@/components/studio/types'
import type { useToast } from '@/composables/useToast'
import { createStudioImageTask, runStudioSearchRequest, studioErrorMessage, studioModeRequestErrorFallback, studioModeRetryErrorFallback } from './studioRequestView'
import type { useStudioChatStreamRuntime } from './studioChatStreamRuntime'
import type { useStudioComposerRuntime } from './studioComposerRuntime'
import type { useStudioImageTaskRuntime } from './studioImageTaskRuntime'
import type { useStudioMessageRuntime } from './studioMessageRuntime'
import { filesFromStudioReferenceImages } from './studioReferenceRuntime'
import type { useStudioReferenceRuntime } from './studioReferenceRuntime'

export type StudioSendRuntimeHooks = {
  activeConversationId: Ref<string>
  ensureConversation: (content?: string) => StudioConversation
  markConversationNotice: (conversationId: string, state: 'done' | 'error' | 'running') => void
  clearConversationNotice: (conversationId: string) => void
  touchConversation: (conversation: StudioConversation) => void
  scheduleScrollToBottom: () => void
}

export type StudioSendRuntimeInput = {
  composerRuntime: ReturnType<typeof useStudioComposerRuntime>
  referenceRuntime: ReturnType<typeof useStudioReferenceRuntime>
  messageRuntime: ReturnType<typeof useStudioMessageRuntime>
  chatStreamRuntime: ReturnType<typeof useStudioChatStreamRuntime>
  imageTaskRuntime: Pick<ReturnType<typeof useStudioImageTaskRuntime>, 'rememberTask' | 'rememberPendingSubmission' | 'merge' | 'schedulePoll' | 'scheduleRefresh'>
  chatModel: Ref<string>
  chatReasoningEffort: Ref<string>
  imageForm: StudioImageForm
  toast: Pick<ReturnType<typeof useToast>, 'success'>
  hooks: StudioSendRuntimeHooks
}

export type StudioImageEditRequest = {
  prompt: string
  files: File[]
  userContent?: string
  referenceImages?: StudioReferenceImage[]
  assistantContent?: string
  inpaintSource?: StudioImageCompareSource
  imageCount?: number
}

export function useStudioSendRuntime(input: StudioSendRuntimeInput) {
  const {
    chatModel,
    chatReasoningEffort,
    chatStreamRuntime,
    composerRuntime,
    hooks,
    imageForm,
    imageTaskRuntime,
    messageRuntime,
    referenceRuntime,
    toast,
  } = input

  async function sendMessage() {
    const content = composerRuntime.composerText.value.trim()
    if (!content || composerRuntime.isSending.value || chatStreamRuntime.isStreaming.value) return

    if (composerRuntime.editingMessageId.value) {
      await sendEditedMessage(content)
      return
    }

    const conversation = hooks.ensureConversation(content)
    const mode = composerRuntime.composeMode.value
    const withReferences = mode === 'image' || mode === 'chat'
    const files = referenceRuntime.selectedFiles()
    const attachments = referenceRuntime.attachmentNames()
    const referenceImages = withReferences ? referenceRuntime.messageReferenceImages() : []
    const clearReferencesImmediately = mode === 'chat' && referenceImages.length > 0

    messageRuntime.addMessage(conversation, {
      role: 'user',
      mode,
      content,
      status: 'done',
      attachments: withReferences && attachments.length ? attachments : undefined,
      referenceImages: buildMessageReferenceImages(referenceImages),
      hadReferenceImages: mode === 'image' && referenceImages.length > 0 || undefined,
    })
    composerRuntime.composerText.value = ''
    if (clearReferencesImmediately) referenceRuntime.clear()
    await runRequestWithComposerState({
      mode,
      conversation,
      prompt: content,
      files,
      requestErrorFallback: studioModeRequestErrorFallback(mode),
      clearReferencesOnSuccess: withReferences && !clearReferencesImmediately,
    })
  }

  async function sendEditedMessage(content: string) {
    const target = messageRuntime.findMessage(composerRuntime.editingMessageId.value)
    if (!target || target.message.role !== 'user') {
      composerRuntime.editingMessageId.value = ''
      return
    }

    const { conversation, message } = target
    const originalMessages = conversation.messages.slice()
    const originalTitle = conversation.title
    const mode = composerRuntime.composeMode.value
    const withReferences = mode === 'image' || mode === 'chat'
    const files = referenceRuntime.selectedFiles()
    const attachments = referenceRuntime.attachmentNames()
    const referenceImages = withReferences ? referenceRuntime.messageReferenceImages() : []
    const clearReferencesImmediately = mode === 'chat' && referenceImages.length > 0
    const editedMessage: StudioMessage = {
      ...message,
      mode,
      content,
      status: 'done',
      error: undefined,
      attachments: withReferences && attachments.length ? attachments : undefined,
      referenceImages: buildMessageReferenceImages(referenceImages),
      hadReferenceImages: mode === 'image' && referenceImages.length > 0 || undefined,
    }

    hooks.activeConversationId.value = conversation.id
    messageRuntime.replaceFromTarget(target, editedMessage)
    composerRuntime.editingMessageId.value = ''
    composerRuntime.composerText.value = ''
    if (clearReferencesImmediately) referenceRuntime.clear()
    hooks.clearConversationNotice(conversation.id)

    const success = await runRequestWithComposerState({
      mode,
      conversation,
      prompt: content,
      files,
      requestErrorFallback: studioModeRequestErrorFallback(mode),
      clearReferencesOnSuccess: withReferences && !clearReferencesImmediately,
    })
    if (!success) {
      conversation.messages = originalMessages
      conversation.title = originalTitle
      hooks.touchConversation(conversation)
      composerRuntime.startEdit(editedMessage)
      referenceRuntime.restoreFromMessageReferenceImages(referenceImages)
    }
  }

  async function sendImageEditRequest(request: StudioImageEditRequest) {
    const prompt = request.prompt.trim()
    if (!prompt || composerRuntime.isSending.value || chatStreamRuntime.isStreaming.value) return false

    const userContent = request.userContent?.trim() || prompt
    const conversation = hooks.ensureConversation(userContent)
    hooks.activeConversationId.value = conversation.id
    composerRuntime.cancelMessageEdit(false)
    composerRuntime.activateImageMode()

    messageRuntime.addMessage(conversation, {
      role: 'user',
      mode: 'image',
      content: userContent,
      status: 'done',
      attachments: request.files.length ? request.files.map((file) => file.name).filter(Boolean) : undefined,
      referenceImages: buildMessageReferenceImages(request.referenceImages || []),
      hadReferenceImages: true,
    })

    return runRequestWithComposerState({
      mode: 'image',
      conversation,
      prompt,
      files: request.files,
      requestErrorFallback: '图片编辑任务提交失败',
      assistantContent: request.assistantContent,
      inpaintSource: request.inpaintSource,
      imageCount: request.imageCount,
      requiresImageEdit: true,
    })
  }

  function fillComposerFromMessage(message: StudioMessage) {
    composerRuntime.cancelMessageEdit(false)
    composerRuntime.fillFromMessage(message)
    referenceRuntime.restoreFromMessageReferenceImages(message.referenceImages)
  }

  function editMessage(message: StudioMessage) {
    const target = messageRuntime.findMessage(message.id)
    if (!target || target.message.role !== 'user') return
    hooks.activeConversationId.value = target.conversation.id
    composerRuntime.startEdit(target.message)
    referenceRuntime.restoreFromMessageReferenceImages(target.message.referenceImages)
    hooks.scheduleScrollToBottom()
  }

  async function resendMessage(message: StudioMessage) {
    if (composerRuntime.isSending.value || chatStreamRuntime.isStreaming.value) return
    fillComposerFromMessage(message)
    await nextTick()
    await sendMessage()
  }

  async function retryAssistantMessage(message: StudioMessage) {
    if (composerRuntime.isSending.value || chatStreamRuntime.isStreaming.value) return
    const target = messageRuntime.findMessage(message.id)
    if (!target) return
    const previousUserMessage = messageRuntime.findPreviousUserMessage(target)
    if (!previousUserMessage) return
    const retryFiles = previousUserMessage.mode === 'image'
      ? filesFromStudioReferenceImages(previousUserMessage.referenceImages)
      : []
    const hadImageReferences = Boolean(previousUserMessage.hadReferenceImages || previousUserMessage.referenceImages?.length)
    const requiresImageEdit = previousUserMessage.mode === 'image' && hadImageReferences
    const retryClientTaskId = previousUserMessage.mode === 'image' && !target.message.taskId ? target.message.clientTaskId : undefined

    hooks.activeConversationId.value = target.conversation.id
    messageRuntime.pruneAfterTarget(target)
    hooks.clearConversationNotice(target.conversation.id)

    await runRequestWithComposerState({
      mode: previousUserMessage.mode,
      conversation: target.conversation,
      prompt: previousUserMessage.content,
      files: retryFiles,
      requestErrorFallback: studioModeRetryErrorFallback(previousUserMessage.mode),
      requiresImageEdit,
      clientTaskId: retryClientTaskId,
    })
  }

  async function runRequestWithComposerState(input: {
    mode: StudioComposeMode
    conversation: StudioConversation
    prompt: string
    files: File[]
    requestErrorFallback: string
    clearReferencesOnSuccess?: boolean
    assistantContent?: string
    inpaintSource?: StudioImageCompareSource
    imageCount?: number
    requiresImageEdit?: boolean
    clientTaskId?: string
  }) {
    composerRuntime.setSending(true)
    try {
      const success = await sendByMode(input.conversation, input.mode, input.prompt, input.files, {
        assistantContent: input.assistantContent,
        inpaintSource: input.inpaintSource,
        imageCount: input.imageCount,
        requiresImageEdit: input.requiresImageEdit,
        clientTaskId: input.clientTaskId,
      })
      if (success && input.clearReferencesOnSuccess) {
        referenceRuntime.clear()
      }
      return success
    } catch (error) {
      const message = studioErrorMessage(error, input.requestErrorFallback)
      hooks.markConversationNotice(input.conversation.id, 'error')
      messageRuntime.addMessage(input.conversation, {
        role: 'assistant',
        mode: input.mode,
        content: message,
        status: 'error',
        error: message,
      })
      return false
    } finally {
      composerRuntime.setSending(false)
      hooks.scheduleScrollToBottom()
    }
  }

  async function sendByMode(
    conversation: StudioConversation,
    mode: StudioComposeMode,
    prompt: string,
    files: File[],
    imageOptions: {
      assistantContent?: string
      inpaintSource?: StudioImageCompareSource
      imageCount?: number
      requiresImageEdit?: boolean
      clientTaskId?: string
    } = {},
  ) {
    if (mode === 'chat') {
      await sendTextMessage(conversation)
      return true
    } else if (mode === 'search') {
      return sendSearchMessage(conversation, prompt)
    } else {
      return sendImageMessage(conversation, prompt, files, imageOptions)
    }
  }

  async function sendTextMessage(conversation: StudioConversation) {
    const assistantMessage = messageRuntime.addMessage(conversation, {
      role: 'assistant',
      mode: 'chat',
      content: '',
      status: 'streaming',
      model: chatModel.value,
    })

    await chatStreamRuntime.stream({
      conversation,
      assistantMessage,
      model: chatModel.value,
      reasoningEffort: chatReasoningEffort.value,
    })
  }

  async function sendSearchMessage(conversation: StudioConversation, prompt: string) {
    const assistantMessage = messageRuntime.addMessage(conversation, {
      role: 'assistant',
      mode: 'search',
      content: '正在搜索...',
      status: 'sending',
      model: 'search',
    })

    try {
      const result = await runStudioSearchRequest(prompt, assistantMessage.id)
      assistantMessage.searchSources = result.sources
      assistantMessage.searchImageGroups = result.imageGroups
      assistantMessage.content = result.content
      assistantMessage.status = 'done'
      hooks.markConversationNotice(conversation.id, 'done')
      return true
    } catch (error) {
      const message = studioErrorMessage(error, '搜索请求失败')
      assistantMessage.status = 'error'
      assistantMessage.content = message
      assistantMessage.error = message
      hooks.markConversationNotice(conversation.id, 'error')
      return false
    } finally {
      hooks.touchConversation(conversation)
      hooks.scheduleScrollToBottom()
    }
  }

  async function sendImageMessage(
    conversation: StudioConversation,
    prompt: string,
    files: File[],
    options: {
      assistantContent?: string
      inpaintSource?: StudioImageCompareSource
      imageCount?: number
      requiresImageEdit?: boolean
      clientTaskId?: string
    } = {},
  ) {
    const imageCount = normalizeImageCount(options.imageCount ?? imageForm.n)
    const clientTaskId = options.clientTaskId || createClientTaskId(files.length ? 'edit' : 'gen')
    const assistantMessage = messageRuntime.addMessage(conversation, {
      role: 'assistant',
      mode: 'image',
      content: options.assistantContent || (files.length ? '图像编辑任务已提交' : '图片任务已提交'),
      status: 'queued',
      model: imageForm.model,
      imageSize: imageForm.size,
      imageCount,
      clientTaskId,
      inpaintSource: options.inpaintSource,
    })

    try {
      const task = await createStudioImageTask({
        prompt,
        files,
        imageForm: {
          ...imageForm,
          n: imageCount,
        },
        clientTaskId,
        requiresEdit: options.requiresImageEdit,
      })
      assistantMessage.taskId = task.id
      assistantMessage.status = mapTaskStatusToMessageStatus(task.status)
      hooks.touchConversation(conversation)
      imageTaskRuntime.rememberTask(task.id)
      imageTaskRuntime.merge([task])
      toast.success('图片任务已提交')
      imageTaskRuntime.schedulePoll()
      return true
    } catch (error) {
      const message = studioErrorMessage(error, '图片任务提交失败')
      if (isAmbiguousImageTaskSubmissionError(error)) {
        assistantMessage.status = 'queued'
        assistantMessage.content = '图片任务提交结果确认中，正在自动对账'
        assistantMessage.error = undefined
        hooks.touchConversation(conversation)
        hooks.markConversationNotice(conversation.id, 'running')
        imageTaskRuntime.rememberPendingSubmission(clientTaskId)
        return true
      }
      assistantMessage.status = 'error'
      assistantMessage.content = message
      assistantMessage.error = message
      hooks.touchConversation(conversation)
      hooks.markConversationNotice(conversation.id, 'error')
      return false
    }
  }

  function buildMessageReferenceImages(referenceImages: StudioReferenceImage[]) {
    return referenceImages.length ? referenceImages : undefined
  }

  function mapTaskStatusToMessageStatus(status: string) {
    const value = String(status || '').toLowerCase()
    if (value === 'success') return 'done'
    if (value === 'failed' || value === 'canceled') return 'error'
    if (value === 'queued' || value === 'retrying') return 'queued'
    if (value === 'running' || value === 'saving') return 'running'
    return 'queued'
  }

  return {
    editMessage,
    fillComposerFromMessage,
    resendMessage,
    retryAssistantMessage,
    sendImageEditRequest,
    sendMessage,
  }
}
