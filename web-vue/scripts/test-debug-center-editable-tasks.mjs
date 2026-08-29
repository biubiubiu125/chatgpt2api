import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8')
const debugView = read('src/views/DebugCenter.vue')
const debugApi = read('src/api/debug.ts')
const polling = read('src/lib/editableFileTaskPolling.ts')

function assertIncludes(source, value, label) {
  if (!source.includes(value)) throw new Error(`${label}: missing ${value}`)
}

function assertRegex(source, pattern, label) {
  if (!pattern.test(source)) throw new Error(`${label}: does not match ${pattern}`)
}

assertIncludes(debugApi, "'/v1/ppt/generations'", 'PPT endpoint')
assertIncludes(debugApi, "'/v1/psd/generations'", 'PSD endpoint')
assertIncludes(debugApi, "'/v1/editable-file-tasks'", 'editable task list endpoint')
assertIncludes(debugApi, 'client_task_id', 'idempotent client task id')
assertIncludes(debugView, "activeTab.value === 'psd' ? 'psd' : 'ppt'", 'kind selection')
assertIncludes(debugView, 'filter((task) => task.kind === requestKind)', 'kind-filtered task refresh')
assertIncludes(debugView, 'requestId !== editableRefreshRequestId || requestKind !== currentEditableKind.value', 'stale request guard')
assertIncludes(debugView, 'shouldRefreshEditableFileTask(task, currentEditableKind.value)', 'queued/running polling')
assertIncludes(debugView, 'downloadUrlAsFile(url, filenameFromDownloadUrl(url), { authorization: true })', 'authorized result download')
assertRegex(polling, /status === 'queued' \|\| status === 'running'/, 'editable task polling statuses')

console.log('[debug-center-editable-tasks] all assertions passed')
