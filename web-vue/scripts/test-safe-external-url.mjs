import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const safeUrl = fs.readFileSync(path.join(root, 'src/utils/safeExternalUrl.ts'), 'utf8')
const targets = fs.readFileSync(path.join(root, 'src/lib/downloadTargets.ts'), 'utf8')
const downloads = fs.readFileSync(path.join(root, 'src/lib/downloads.ts'), 'utf8')

function assertIncludes(source, value, label) {
  if (!source.includes(value)) throw new Error(`${label}: missing ${value}`)
}

function assertRegex(source, pattern, label) {
  if (!pattern.test(source)) throw new Error(`${label}: does not match ${pattern}`)
}

assertRegex(safeUrl, /parsed\.protocol !== 'http:' && parsed\.protocol !== 'https:'/, 'external URL protocol allowlist')
assertRegex(safeUrl, /parsed\.username = ''[\s\S]*parsed\.password = ''/, 'external URL credential stripping')
assertRegex(targets, /isTrustedApiDownloadUrl[\s\S]*pathname\.startsWith\('\/files\/'\)/, 'download path allowlist')
assertRegex(targets, /trustedOrigins = new Set\(\[new URL\(pageOrigin\)\.origin\]\)/, 'trusted origin allowlist')
assertRegex(targets, /shouldAuthorizeDownloadUrl[\s\S]*pathname\.startsWith\('\/files\/'\)/, 'download authorization path')
assertIncludes(downloads, 'isTrustedApiDownloadUrl', 'download URL trust check')
assertIncludes(downloads, 'shouldAuthorizeDownloadUrl', 'download authorization check')

console.log('[safe-external-url] all assertions passed')
