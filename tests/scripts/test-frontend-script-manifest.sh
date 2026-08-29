#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PACKAGE_JSON="${ROOT_DIR}/web-vue/package.json"

node --input-type=module - "${PACKAGE_JSON}" <<'NODE'
import fs from 'node:fs'
import path from 'node:path'

const packagePath = process.argv[2]
const packageJson = JSON.parse(fs.readFileSync(packagePath, 'utf8'))
const scriptsDir = path.join(path.dirname(packagePath), 'scripts')
const scripts = packageJson.scripts || {}
const commandFiles = [
  'test-debug-center-editable-tasks.mjs',
  'test-register-provider-view.mjs',
  'test-studio-image-task-closed-loop.mjs',
  'test-studio-image-task-runtime.mjs',
  'test-third-party-links.mjs',
  'test-safe-external-url.mjs',
]

for (const file of commandFiles) {
  if (!Object.values(scripts).some((command) => command.includes(file))) {
    throw new Error(`package.json does not expose ${file}`)
  }
  if (!fs.existsSync(path.join(scriptsDir, file))) throw new Error(`missing ${file}`)
}

console.log('[frontend-script-manifest] all assertions passed')
NODE
