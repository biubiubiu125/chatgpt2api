import { ref } from 'vue'

import { getAuthToken, notifyAuthFailure } from '@/api/client'
import { settingsApi, type BackupDetail, type BackupItem, type BackupState, type BackupTestResult } from '@/api/settings'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import type { usePageRuntime } from '@/composables/usePageRuntime'
import { usePageQuery } from '@/composables/usePageQuery'
import { useToast } from '@/composables/useToast'
import { errorMessage } from '@/lib/errorMessage'
import { saveBlob } from '@/lib/downloads'

type SettingsBackupRuntimeOptions = {
  runtime: ReturnType<typeof usePageRuntime>
  requestKey: string
  requireSavedSettings: (actionLabel: string) => boolean
  afterRestore?: () => void | Promise<void>
}

export function useSettingsBackupRuntime(options: SettingsBackupRuntimeOptions) {
  const backupsLoaded = ref(false)
  const backupBusy = ref('')
  const backupLoading = ref(false)
  const backupState = ref<BackupState | null>(null)
  const backupItems = ref<BackupItem[]>([])
  const backupTestResult = ref<BackupTestResult | null>(null)
  const backupDetail = ref<BackupDetail | null>(null)
  const backupRestoreTarget = ref<BackupItem | null>(null)
  const backupRestorePassphrase = ref('')
  const toast = useToast()
  const confirmDialog = useConfirmDialog()

  function backupItemName(item: BackupItem) {
    return String(item.name || item.key || 'backup.bin')
  }

  async function backupDownloadBlob(item: BackupItem) {
    const headers: Record<string, string> = {}
    const token = getAuthToken()
    if (token) headers.Authorization = `Bearer ${token}`
    const request: RequestInit = { headers }
    let url = settingsApi.backupDownloadUrl(item.key)
    let filename = backupItemName(item)
    if (item.encrypted) {
      const passphrase = window.prompt('请输入该加密备份的解密口令', '')
      if (passphrase === null) throw new Error('已取消下载')
      headers['Content-Type'] = 'application/json'
      request.method = 'POST'
      request.body = JSON.stringify({ key: item.key, passphrase })
      url = settingsApi.backupDownloadPath()
    }
    const response = await fetch(url, request)
    if (!response.ok) {
      notifyAuthFailure(response.status)
      const text = await response.text().catch(() => '')
      throw new Error(text || `HTTP ${response.status}`)
    }
    const blob = await response.blob()
    if (!blob.size) throw new Error('备份文件为空')
    const contentDisposition = response.headers.get('content-disposition') || ''
    const encodedName = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
    const plainName = contentDisposition.match(/filename="([^"]+)"/i)?.[1]
    if (encodedName) {
      try {
        filename = decodeURIComponent(encodedName)
      } catch {
        filename = encodedName
      }
    } else if (plainName) {
      filename = plainName
    }
    return { blob, filename }
  }

  const backupsQuery = usePageQuery({
    runtime: options.runtime,
    key: options.requestKey,
    loading: backupLoading,
    errorMessage: '加载备份历史失败',
  })

  async function loadBackups() {
    await backupsQuery.run(
      () => settingsApi.listBackups(),
      {
        apply: (response) => {
          backupItems.value = Array.isArray(response.items) ? response.items : []
          if (backupDetail.value && !backupItems.value.some((item) => item.key === backupDetail.value?.key)) {
            backupDetail.value = null
          }
          backupState.value = response.state || null
          backupsLoaded.value = true
        },
        onError: (message) => {
          backupItems.value = []
          backupState.value = null
          toast.error(message)
        },
      },
    )
  }

  async function testBackupConnection() {
    if (!options.requireSavedSettings('测试备份连接')) return
    const confirmed = await confirmDialog.ask({
      title: '确认测试备份连接',
      message: '即将使用已保存的备份配置发起 R2/备份存储连接测试，可能访问外部存储服务。是否继续？',
      confirmText: '开始测试',
      cancelText: '取消',
    })
    if (!confirmed) return

    backupBusy.value = 'test'
    backupTestResult.value = null
    try {
      const response = await settingsApi.testBackup()
      backupTestResult.value = response.result
      if (response.result.ok) toast.success('备份连接测试通过')
      else toast.warning(response.result.error || '备份连接测试失败')
    } catch (error) {
      const message = errorMessage(error, '备份连接测试失败')
      backupTestResult.value = { ok: false, error: message }
      toast.error(message)
    } finally {
      backupBusy.value = ''
    }
  }

  async function runBackupNow() {
    if (!options.requireSavedSettings('执行立即备份')) return
    const confirmed = await confirmDialog.ask({
      title: '确认立即备份',
      message: '即将把当前配置和运行数据写入备份存储，可能产生外部上传流量。是否继续？',
      confirmText: '开始备份',
      cancelText: '取消',
    })
    if (!confirmed) return

    backupBusy.value = 'run'
    try {
      const response = await settingsApi.runBackup()
      toast.success(`备份已完成：${response.result.key}`)
      await loadBackups()
    } catch (error) {
      toast.error(errorMessage(error, '执行备份失败'))
    } finally {
      backupBusy.value = ''
    }
  }

  async function showBackupDetail(item: BackupItem) {
    backupBusy.value = `detail:${item.key}`
    try {
      let passphrase = ''
      if (item.encrypted) {
        const entered = window.prompt('请输入该加密备份的解密口令', '')
        if (entered === null) throw new Error('已取消读取详情')
        passphrase = entered
      }
      const response = await settingsApi.getBackupDetail(item.key, passphrase)
      backupDetail.value = response.item
      toast.success('备份详情已读取')
    } catch (error) {
      toast.error(errorMessage(error, '读取备份详情失败'))
    } finally {
      backupBusy.value = ''
    }
  }

  async function downloadBackupItem(item: BackupItem) {
    backupBusy.value = `download:${item.key}`
    try {
      const { blob, filename } = await backupDownloadBlob(item)
      saveBlob(blob, filename, settingsApi.backupDownloadUrl(item.key))
      toast.success('备份下载已开始')
    } catch (error) {
      toast.error(errorMessage(error, '下载备份失败'))
    } finally {
      backupBusy.value = ''
    }
  }

  async function deleteBackupItem(item: BackupItem) {
    const confirmed = await confirmDialog.ask({
      title: '删除备份',
      message: `确定删除备份 ${item.name || item.key}？`,
      confirmText: '删除',
      cancelText: '取消',
    })
    if (!confirmed) return

    backupBusy.value = item.key
    try {
      await settingsApi.deleteBackup(item.key)
      if (backupDetail.value?.key === item.key) backupDetail.value = null
      toast.success('备份已删除')
      await loadBackups()
    } catch (error) {
      toast.error(errorMessage(error, '删除备份失败'))
    } finally {
      backupBusy.value = ''
    }
  }

  function openBackupRestore(item: BackupItem) {
    if (!options.requireSavedSettings('恢复图片任务备份')) return
    backupRestoreTarget.value = item
    backupRestorePassphrase.value = ''
  }

  function closeBackupRestore() {
    if (backupBusy.value.startsWith('restore:')) return
    backupRestoreTarget.value = null
    backupRestorePassphrase.value = ''
  }

  async function restoreBackupItem() {
    const item = backupRestoreTarget.value
    if (!item) return
    backupBusy.value = `restore:${item.key}`
    try {
      const response = await settingsApi.restoreBackup(item.key, backupRestorePassphrase.value)
      const restoredImages = Number(response.result.restored_images || 0)
      toast.success(`恢复完成：图片文件 ${restoredImages} 个`)
      backupRestoreTarget.value = null
      backupRestorePassphrase.value = ''
      await options.afterRestore?.()
      await loadBackups()
    } catch (error) {
      toast.error(errorMessage(error, '恢复备份失败'))
    } finally {
      backupBusy.value = ''
    }
  }

  function invalidate() {
    backupsQuery.invalidate()
    backupsLoaded.value = false
  }

  return {
    backupsLoaded,
    backupBusy,
    backupLoading,
    backupState,
    backupItems,
    backupTestResult,
    backupDetail,
    backupRestoreTarget,
    backupRestorePassphrase,
    loadBackups,
    testBackupConnection,
    runBackupNow,
    showBackupDetail,
    downloadBackupItem,
    deleteBackupItem,
    openBackupRestore,
    closeBackupRestore,
    restoreBackupItem,
    invalidate,
  }
}
