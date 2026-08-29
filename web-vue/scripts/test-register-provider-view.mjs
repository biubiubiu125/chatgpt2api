import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const sourcePath = path.resolve(__dirname, '../src/views/register/registerProviderView.ts')
const source = fs.readFileSync(sourcePath, 'utf8')
const settingsPath = path.resolve(__dirname, '../src/api/settings.ts')
const settingsSource = fs.readFileSync(settingsPath, 'utf8')
const gptMailRuntimePath = path.resolve(__dirname, '../src/views/register/registerGptMailRuntime.ts')
const gptMailRuntimeSource = fs.readFileSync(gptMailRuntimePath, 'utf8')

function assertIncludes(needle, label = needle) {
  if (!source.includes(needle)) {
    throw new Error('registerProviderView missing ' + label)
  }
}

function assertRegex(pattern, label = String(pattern)) {
  if (!pattern.test(source)) {
    throw new Error('registerProviderView does not match ' + label)
  }
}

function assertSettingsRegex(pattern, label = String(pattern)) {
  if (!pattern.test(settingsSource)) {
    throw new Error('settings.ts does not match ' + label)
  }
}

function assertGptMailRuntimeRegex(pattern, label = String(pattern)) {
  if (!pattern.test(gptMailRuntimeSource)) {
    throw new Error('registerGptMailRuntime.ts does not match ' + label)
  }
}

assertIncludes("{ value: 'icloud_api', label: 'iCloud 外部取码' }", 'iCloud provider option')
assertRegex(/icloud_api:\s*\['api_base',\s*'api_key'\]/, 'iCloud provider fields')
assertRegex(/case 'icloud_api':[\s\S]*?return \{ \.\.\.base, api_base: '', api_key: '' \}/, 'iCloud default provider')
assertRegex(/case 'icloud_api':[\s\S]*?requireValue\(provider\.api_base, '服务地址'\)[\s\S]*?requireValue\(provider\.api_key, 'API Key'\)/, 'iCloud requirement messages')
assertRegex(/providerUsesApiBase[\s\S]*?icloud_api/, 'iCloud api base UI')
assertRegex(/providerUsesApiKey[\s\S]*?icloud_api/, 'iCloud api key UI')
assertIncludes("Chrome/146.0.0.0", 'Chrome 146 default user agent')
assertRegex(/function chrome146UserAgent\(value: unknown\) \{\s*void value\s*return providerMailUserAgentChrome146\s*\}/s, 'Chrome 146 canonical provider helper')
assertRegex(/normalizeRegisterConfig[\s\S]*?mail\.user_agent = chrome146UserAgent\(mail\.user_agent\)/, 'Chrome 146 normalization')
assertRegex(/legacyRegisterPayload[\s\S]*?user_agent: chrome146UserAgent\(config\.mail\.user_agent\)/, 'Chrome 146 payload')
assertSettingsRegex(/function chrome146UserAgent\(value: unknown\): string \{\s*void value\s*return DEFAULT_PROXY_RUNTIME_USER_AGENT\s*\}/s, 'Chrome 146 canonical settings helper')
assertGptMailRuntimeRegex(
  /function gptMailProviderSignature\(provider: RegisterProvider \| undefined\)[\s\S]*?sanitizedProviderPayload\(provider\)/,
  'GPTMail provider stable signature',
)
assertGptMailRuntimeRegex(
  /function isCurrentGptMailProvider\(index: number, signature: string\)[\s\S]*?input\.providers\.value\[index\][\s\S]*?gptMailProviderSignature\(provider\) === signature/,
  'GPTMail stale async response guard',
)
const gptMailStaleGuards = (gptMailRuntimeSource.match(/isCurrentGptMailProvider\(index, signature\)/g) || []).length
if (gptMailStaleGuards < 4) {
  throw new Error('registerGptMailRuntime.ts missing stale response guards on GPTMail async status writes')
}

console.log('[register-provider-view] ok')
