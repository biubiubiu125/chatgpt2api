import type { DebugEditableFileTask, DebugEditableKind } from '@/api/debug'

export const EDITABLE_FILE_TASK_REFRESH_DELAY_MS = 4000

export function shouldRefreshEditableFileTask(task: DebugEditableFileTask, kind: DebugEditableKind) {
  if (task.kind !== kind) return false
  const status = String(task.status || '').toLowerCase()
  return status === 'queued' || status === 'running'
}
