import { ref } from 'vue'

import { accountsApi, type AccountSourceType } from '@/api/accounts'
import { accountImportsApi } from '@/api/accountImports'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { useToast } from '@/composables/useToast'
import type { useAccountBulkProgressRuntime } from './accountBulkProgressRuntime'
import {
  accountTokens,
  normalizeAccountImports,
  parseCPAJsonAccounts,
  parseSessionJsonAccounts,
  parseTokenLines,
  uniqueTokens,
  type ParsedAccountImport,
} from './accountImportParsing'

export type AccountImportMode = 'oauth_login' | 'access_token' | 'session_json' | 'cpa_json' | 'remote_cpa' | 'sub2api'

const IMPORT_BATCH_SIZE = 20

type AccountImportRuntimeOptions = {
  bulkProgress: ReturnType<typeof useAccountBulkProgressRuntime>
  normalizeErrorMessage: (error: unknown) => string
  setError: (prefix: string, error: unknown, notify?: boolean) => void
  loadData: (options?: { silentErrorToast?: boolean }) => Promise<void>
}

export function useAccountImportRuntime(options: AccountImportRuntimeOptions) {
  const importBusy = ref(false)
  const showImportModal = ref(false)
  const importMode = ref<AccountImportMode>('access_token')
  const oauthEmailHint = ref('')
  const oauthCallbackText = ref('')
  const oauthSessionId = ref('')
  const oauthAuthorizeUrl = ref('')
  const oauthRedirectUriPrefix = ref('')
  const manualTokenText = ref('')
  const sessionJsonText = ref('')
  const toast = useToast()
  const confirmDialog = useConfirmDialog()

  const importModeOptions = [
    { label: 'OAuth 登录已有账号', value: 'oauth_login' },
    { label: '导入 Access Token', value: 'access_token' },
    { label: '导入 Session JSON', value: 'session_json' },
    { label: '导入 CPA JSON 文件', value: 'cpa_json' },
    { label: '从远程 CPA 服务器导入', value: 'remote_cpa' },
    { label: '从 Sub2API 服务器导入', value: 'sub2api' },
  ] as const

  function setImportMode(mode: AccountImportMode) {
    importMode.value = mode
  }

  async function openImportModal(mode: AccountImportMode = 'access_token') {
    showImportModal.value = true
    setImportMode(mode)
  }

  function closeImportModal() {
    if (importBusy.value) return
    showImportModal.value = false
  }

  async function promptRemoveImportedAbnormalAccounts(importedTokens: string[], errorCount: number) {
    if (errorCount <= 0 || options.bulkProgress.bulkStopRequested.value) return

    let preview: Awaited<ReturnType<typeof accountsApi.cleanupImportedAbnormalAccounts>>
    try {
      preview = await accountsApi.cleanupImportedAbnormalAccounts(importedTokens, false)
    } catch (error) {
      options.setError('检查本次确认失效账号失败，已先保留', error)
      return
    }

    if (!preview.abnormal) {
      toast.info('本次导入有刷新失败，但没有确认失效账号；暂时检测失败的账号会保留')
      return
    }

    const confirmed = await confirmDialog.ask({
      title: '移除本次确认失效账号？',
      message: `本次导入刷新失败 ${errorCount} 个。\n后端确认其中 ${preview.abnormal} 个账号鉴权已经失效，是否直接删除？\n\n只会删除本次导入且已确认失效的账号；正常、限流、暂时检测失败和历史账号都会保留。`,
      confirmText: `删除 ${preview.abnormal} 个`,
      cancelText: '先保留',
    })

    if (!confirmed) return

    try {
      const result = await accountsApi.cleanupImportedAbnormalAccounts(importedTokens, true)
      toast.success(`已移除 ${result.removed || 0} 个本次确认失效账号`)
    } catch (error) {
      options.setError('移除本次确认失效账号失败', error)
    } finally {
      await options.loadData({ silentErrorToast: true })
    }
  }

  async function importAccountBatch(
    accountPayloads: ParsedAccountImport[],
    sourceType: AccountSourceType,
    title: string,
  ) {
    const normalizedAccounts = normalizeAccountImports(accountPayloads)
    const importedTokens = accountTokens(normalizedAccounts)
    if (!normalizedAccounts.length) {
      toast.warning('\u6ca1\u6709\u53ef\u5bfc\u5165\u7684 access token')
      return
    }

    const confirmed = await confirmDialog.ask({
      title,
      message: `\u5373\u5c06\u5bfc\u5165 ${normalizedAccounts.length} \u4e2a\u8d26\u53f7\u3002\u5df2\u5b58\u5728\u8d26\u53f7\u4f1a\u5237\u65b0\u8fdc\u7aef\u4fe1\u606f\u3002\u662f\u5426\u7ee7\u7eed\uff1f`,
      confirmText: '\u786e\u8ba4\u5bfc\u5165',
      cancelText: '\u53d6\u6d88',
    })
    if (!confirmed) return

    importBusy.value = true
    options.bulkProgress.start(title, normalizedAccounts.length, 'mutation')
    let addedCount = 0
    let skippedCount = 0
    let refreshedCount = 0
    let processed = 0
    const errors: string[] = []
    try {
      for (let index = 0; index < normalizedAccounts.length; index += IMPORT_BATCH_SIZE) {
        if (options.bulkProgress.bulkStopRequested.value) break
        const batch = normalizedAccounts.slice(index, index + IMPORT_BATCH_SIZE)
        try {
          const result = await accountsApi.importAccounts(
            batch,
            sourceType,
            { refresh: true, returnItems: false },
          )
          addedCount += Number(result.added || 0)
          skippedCount += Number(result.skipped || 0)
          refreshedCount += Number(result.refreshed || 0)
          errors.push(...(Array.isArray(result.errors) ? result.errors.filter(Boolean) : []))
        } catch (error) {
          errors.push(`${String(batch[0]?.access_token || '').slice(0, 6) || '-'}... \u7b49 ${batch.length} \u4e2a\u8d26\u53f7\uff1a${options.normalizeErrorMessage(error)}`)
        } finally {
          processed = Math.min(normalizedAccounts.length, processed + batch.length)
          options.bulkProgress.update({
            total: normalizedAccounts.length,
            processed,
            done: processed >= normalizedAccounts.length,
            total_quota: 0,
          })
        }
      }

      await options.loadData({ silentErrorToast: true })
      const stopped = options.bulkProgress.bulkStopRequested.value && processed < normalizedAccounts.length
      options.bulkProgress.finish({
        total: normalizedAccounts.length,
        processed,
        total_quota: 0,
      })
      if (stopped) {
        toast.warning(`${title}\u5df2\u505c\u6b62\uff1a\u5df2\u5904\u7406 ${processed}/${normalizedAccounts.length} \u4e2a`)
      } else if (errors.length > 0) {
        toast.warning(`${title}\u5b8c\u6210\uff1a\u65b0\u589e ${addedCount}\uff0c\u8df3\u8fc7 ${skippedCount}\uff0c\u5237\u65b0 ${refreshedCount}\uff0c\u5931\u8d25 ${errors.length}`)
      } else {
        toast.success(`${title}\u5b8c\u6210\uff1a\u65b0\u589e ${addedCount}\uff0c\u8df3\u8fc7 ${skippedCount}\uff0c\u5237\u65b0 ${refreshedCount}`)
      }
      if (addedCount + skippedCount + refreshedCount > 0) {
        manualTokenText.value = ''
        sessionJsonText.value = ''
      }
      if (!stopped && errors.length > 0) {
        await promptRemoveImportedAbnormalAccounts(importedTokens, errors.length)
      }
    } catch (error) {
      options.bulkProgress.finish({
        total: normalizedAccounts.length,
        processed,
        error: options.normalizeErrorMessage(error),
        total_quota: 0,
      })
      options.setError(`${title}\u5931\u8d25`, error)
    } finally {
      importBusy.value = false
      options.bulkProgress.end()
    }
  }

  async function importTokenBatch(tokens: string[], sourceType: AccountSourceType, title: string) {
    const accounts = uniqueTokens(tokens).map((accessToken) => ({
      access_token: accessToken,
      source_type: sourceType,
    }))
    await importAccountBatch(accounts, sourceType, title)
  }
  async function importManualTokenText() {
    await importTokenBatch(parseTokenLines(manualTokenText.value), 'web', '导入 Access Token')
  }

  async function importTokenTextFile(file: File | null | undefined) {
    if (!file) return
    const text = await file.text()
    manualTokenText.value = text
    await importManualTokenText()
  }

  async function importSessionJson() {
    await importAccountBatch(parseSessionJsonAccounts(sessionJsonText.value), 'web', '导入 Session JSON')
  }

  async function startOAuthLogin() {
    importBusy.value = true
    try {
      const result = await accountImportsApi.startOAuthLogin(oauthEmailHint.value)
      oauthSessionId.value = String(result.session_id || '')
      oauthAuthorizeUrl.value = String(result.authorize_url || '')
      oauthRedirectUriPrefix.value = String(result.redirect_uri_prefix || '')
      oauthCallbackText.value = ''
      if (!oauthSessionId.value || !oauthAuthorizeUrl.value) {
        throw new Error('后端没有返回完整的 OAuth 授权会话')
      }
      window.open(oauthAuthorizeUrl.value, '_blank', 'noopener,noreferrer')
      toast.success('OAuth 授权链接已生成')
    } catch (error) {
      options.setError('生成 OAuth 授权链接失败', error)
    } finally {
      importBusy.value = false
    }
  }

  function openOAuthAuthorizeUrl() {
    if (!oauthAuthorizeUrl.value) {
      void startOAuthLogin()
      return
    }
    window.open(oauthAuthorizeUrl.value, '_blank', 'noopener,noreferrer')
  }

  async function copyOAuthAuthorizeUrl() {
    const value = oauthAuthorizeUrl.value.trim()
    if (!value) {
      toast.warning('请先生成 OAuth 授权链接')
      return
    }
    try {
      await navigator.clipboard.writeText(value)
      toast.success('授权链接已复制')
    } catch (error) {
      options.setError('复制 OAuth 授权链接失败', error)
    }
  }

  async function finishOAuthLogin() {
    const sessionId = oauthSessionId.value.trim()
    const callback = oauthCallbackText.value.trim()
    if (!sessionId) {
      toast.warning('请先生成 OAuth 授权链接')
      return
    }
    if (!callback) {
      toast.warning('请先粘贴 callback URL 或 code')
      return
    }

    importBusy.value = true
    try {
      const result = await accountImportsApi.finishOAuthLogin(sessionId, callback)
      await options.loadData({ silentErrorToast: true })
      const added = Number(result.added || 0)
      const skipped = Number(result.skipped || 0)
      const refreshed = Number(result.refreshed || 0)
      const errors = Array.isArray(result.errors) ? result.errors.length : 0
      if (errors > 0) {
        toast.warning(`OAuth 登录导入完成：新增 ${added}，跳过 ${skipped}，刷新 ${refreshed}，异常 ${errors}`)
      } else {
        toast.success(`OAuth 登录导入完成：新增 ${added}，跳过 ${skipped}，刷新 ${refreshed}`)
      }
      oauthEmailHint.value = ''
      oauthCallbackText.value = ''
      oauthSessionId.value = ''
      oauthAuthorizeUrl.value = ''
      oauthRedirectUriPrefix.value = ''
    } catch (error) {
      options.setError('OAuth 登录导入失败', error)
    } finally {
      importBusy.value = false
    }
  }

  async function importLocalCPAFiles(files: FileList | File[] | null | undefined) {
    const fileList = Array.from(files || [])
    if (!fileList.length) return
    importBusy.value = true
    try {
      const accounts: ParsedAccountImport[] = []
      for (const file of fileList) {
        const text = await file.text()
        accounts.push(...parseCPAJsonAccounts(text, file.name))
      }
      importBusy.value = false
      await importAccountBatch(accounts, 'codex', '导入 CPA JSON 文件')
    } catch (error) {
      options.setError('导入 CPA JSON 文件失败', error)
    } finally {
      importBusy.value = false
    }
  }

  return {
    importBusy,
    showImportModal,
    importMode,
    importModeOptions,
    oauthEmailHint,
    oauthCallbackText,
    oauthSessionId,
    oauthAuthorizeUrl,
    oauthRedirectUriPrefix,
    manualTokenText,
    sessionJsonText,
    setImportMode,
    openImportModal,
    closeImportModal,
    importManualTokenText,
    importTokenTextFile,
    importSessionJson,
    startOAuthLogin,
    openOAuthAuthorizeUrl,
    copyOAuthAuthorizeUrl,
    finishOAuthLogin,
    importLocalCPAFiles,
  }
}
