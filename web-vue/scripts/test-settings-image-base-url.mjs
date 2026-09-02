import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8')
const settingsSource = read('src/api/settings.ts')
const typesSource = read('src/types/api.ts')
const panelSource = read('src/views/settings/SettingsBasicConfigPanel.vue')

function assertIncludes(source, value, label) {
  if (!source.includes(value)) throw new Error(`${label}: missing ${value}`)
}

assertIncludes(typesSource, 'image_base_url?: string', 'Settings image base URL type')
assertIncludes(settingsSource, "'image_base_url'", 'settings save key')
assertIncludes(settingsSource, 'image_base_url: cleanString(source.image_base_url)', 'settings normalization')
assertIncludes(settingsSource, 'image_base_url: cleanString(normalized.image_base_url)', 'settings backend payload')
assertIncludes(panelSource, 'v-model.trim="settings.base_url"', 'API public URL form binding')
assertIncludes(panelSource, 'v-model.trim="settings.image_base_url"', 'image URL form binding')

console.log('[settings-image-base-url] all assertions passed')
