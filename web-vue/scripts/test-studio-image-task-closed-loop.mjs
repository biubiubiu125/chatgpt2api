import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8')
const studio = read('src/views/Studio.vue')
const runtime = read('src/views/studio/studioImageTaskRuntime.ts')
const api = read('src/api/imageTasks.ts')
const delivery = read('src/lib/imageTaskDelivery.ts')

function assertIncludes(source, value, label) {
  if (!source.includes(value)) throw new Error(`${label}: missing ${value}`)
}

function assertRegex(source, pattern, label) {
  if (!pattern.test(source)) throw new Error(`${label}: does not match ${pattern}`)
}

assertIncludes(api, "'/api/image-tasks/generations'", 'generation submission endpoint')
assertIncludes(api, "'/api/image-tasks/edits'", 'edit submission endpoint')
assertIncludes(api, "`/api/image-tasks/${encodeURIComponent(taskId)}/ack`", 'ACK endpoint')
assertIncludes(api, "`/api/image-tasks/${encodeURIComponent(taskId)}/resume-poll`", 'resume endpoint')
assertRegex(studio, /handleImageAssetLoad[\s\S]*acknowledgeRenderedImageTask/, 'rendered asset ACK wiring')
assertRegex(studio, /handleImageAssetError[\s\S]*IMAGE_LOAD_ERROR_PREFIX/, 'image load error state')
assertRegex(studio, /sendImageEditRequest\([\s\S]*files: \[sourceFile, payload\.markedImage\]/, 'inpaint edit submits both source files')
assertRegex(runtime, /expected = renderableImageAssetKeys\(task\)/, 'all renderable assets required before ACK')
assertRegex(runtime, /Array\.from\(expected\)\.some\(\(key\) => !loaded\.has\(key\)\)/, 'partial render does not ACK')
assertRegex(runtime, /imageTasksApi\.acknowledge\(taskId\)/, 'runtime sends ACK')
assertRegex(runtime, /imageTasksApi\.resumePoll\(cleanTaskId\)/, 'runtime resumes polling')
assertIncludes(delivery, "const apiBaseUrl = String(import.meta.env.VITE_API_URL || '').replace(/\\/+$/, '')", 'image task asset API base')
assertIncludes(delivery, 'return apiBaseUrl ? `${apiBaseUrl}${path}` : path', 'image task relative asset uses API base')
assertRegex(delivery, /delivery_status !== 'acknowledged'/, 'unacknowledged delivery is preserved')

console.log('[studio-image-task-closed-loop] all assertions passed')
