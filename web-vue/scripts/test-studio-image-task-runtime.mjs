import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const runtime = fs.readFileSync(path.join(root, 'src/views/studio/studioImageTaskRuntime.ts'), 'utf8')

function assertRegex(pattern, label) {
  if (!pattern.test(runtime)) throw new Error(`${label}: does not match ${pattern}`)
}

assertRegex(/IMAGE_POLL_TIMER_KEY = 'studio:image-poll'/, 'poll timer key')
assertRegex(/setInterval\(IMAGE_POLL_TIMER_KEY, 4000/, 'four-second task polling')
assertRegex(/IMAGE_ACK_RETRY_TIMER_KEY = 'studio:image-ack-retry'/, 'ACK retry timer key')
assertRegex(/setTimer\(IMAGE_ACK_RETRY_TIMER_KEY, delay/, 'ACK retry scheduling')
assertRegex(/getJsonPreference<unknown\[\]>\(preferenceKeys\.imageTaskLocalIds/, 'persistent local task ids')
assertRegex(/rememberPendingSubmission[\s\S]*scheduleRefresh\(1200, true\)/, 'submission reconciliation refresh')
assertRegex(/nextRequest\(IMAGE_TASKS_REQUEST_KEY\)/, 'request sequence allocation')
assertRegex(/isLatestRequest\(IMAGE_TASKS_REQUEST_KEY, requestSeq\)/, 'stale response rejection')
assertRegex(/task\.status === 'success'/, 'success state synchronization')
assertRegex(/task\.status === 'failed' \|\| task\.status === 'canceled'/, 'failure/cancel state synchronization')
assertRegex(/message\.status = 'running'/, 'running message state')
assertRegex(/message\.status = 'queued'/, 'queued message state')

console.log('[studio-image-task-runtime] all assertions passed')
