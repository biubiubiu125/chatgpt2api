import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const settings = fs.readFileSync(path.join(root, 'src/api/settings.ts'), 'utf8')
const panel = fs.readFileSync(path.join(root, 'src/views/settings/SettingsIntegrationsPanel.vue'), 'utf8')

function assertIncludes(source, value, label) {
  if (!source.includes(value)) throw new Error(`${label}: missing ${value}`)
}

function assertRegex(source, pattern, label) {
  if (!pattern.test(source)) throw new Error(`${label}: does not match ${pattern}`)
}

assertIncludes(settings, 'export function normalizeThirdPartyApps', 'third-party normalization export')
assertRegex(settings, /enabled: boolValue\(infiniteCanvas\.enabled, false\)/, 'enabled boolean normalization')
assertRegex(settings, /url: cleanString\(infiniteCanvas\.url\) \|\| 'https:\/\/canvas\.best'/, 'default canvas URL')
assertRegex(settings, /third_party_apps:[\s\S]*infinite_canvas:/, 'settings payload includes integrations')
assertIncludes(settings, "'/api/third-party-apps'", 'third-party API endpoint')
assertRegex(panel, /settings\.third_party_apps\.infinite_canvas\.enabled/, 'integration toggle binding')
assertRegex(panel, /settings\.third_party_apps\.infinite_canvas\.url/, 'integration URL binding')

console.log('[third-party-links] all assertions passed')
