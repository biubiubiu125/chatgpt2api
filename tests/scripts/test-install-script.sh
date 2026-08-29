#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALL_SCRIPT="${ROOT_DIR}/deploy/install.sh"
HARNESS="$(mktemp)"
TMP_DIR="$(mktemp -d)"
trap 'rm -f "${HARNESS}"; rm -rf "${TMP_DIR}"' EXIT

UI_IN="${TMP_DIR}/ui-input"
UI_OUT="${TMP_DIR}/ui-output"
: >"${UI_IN}"
: >"${UI_OUT}"
export UI_IN UI_OUT

awk '/^case "\$\{1:-\}"/ { exit } { print }' "${INSTALL_SCRIPT}" >"${HARNESS}"
# shellcheck disable=SC1090
source "${HARNESS}"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_command_fails() {
  local label="$1"
  shift
  if ("$@") >/dev/null 2>&1; then
    fail "${label} unexpectedly passed"
  fi
}

assert_command_passes() {
  local label="$1"
  shift
  if ! "$@" >/dev/null 2>&1; then
    fail "${label} unexpectedly failed"
  fi
}

validate_port() {
  PORT="$1"
  MODE="docker"
  STORAGE_BACKEND="json"
  THREAD_TOKENS="1"
  IMAGE_QUEUE_DATABASE_URL="postgresql://queue/chatgpt2api_image_queue"
  BASE_URL="http://127.0.0.1:3000"
  NODE_ROLE="standalone"
  validate_inputs
}

assert_command_fails 'port 0' validate_port 0
assert_command_fails 'port 65536' validate_port 65536
assert_command_passes 'port 1' validate_port 1
assert_command_passes 'port 65535' validate_port 65535

validate_missing_public_base_url() {
  PORT="3000"
  MODE="docker"
  STORAGE_BACKEND="json"
  THREAD_TOKENS="1"
  IMAGE_QUEUE_DATABASE_URL="postgresql://queue/chatgpt2api_image_queue"
  BASE_URL=""
  IMAGE_BASE_URL=""
  NODE_ROLE="standalone"
  validate_inputs
}

assert_command_fails 'standalone public base URL is required' validate_missing_public_base_url

FAKE_PYTHON="${TMP_DIR}/python312"
cat >"${FAKE_PYTHON}" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "-c" ]]; then
  exit 1
fi
exit 1
EOF
chmod +x "${FAKE_PYTHON}"
PYTHON_BIN="${FAKE_PYTHON}"
assert_command_fails 'Python version guard' ensure_python_version
FAKE_UV_DIR="${TMP_DIR}/uv-bin"
mkdir -p "${FAKE_UV_DIR}"
cat >"${FAKE_UV_DIR}/uv" <<'EOF'
#!/usr/bin/env bash
printf 'uv 0.8.17\n'
EOF
chmod +x "${FAKE_UV_DIR}/uv"
OLD_PATH="${PATH}"
PATH="${FAKE_UV_DIR}:${PATH}"
UV_VERSION="0.8.17"
assert_command_fails 'Python version guard with an existing matching uv' ensure_uv
PATH="${OLD_PATH}"
PYTHON_BIN="python3"

TRASH_INSTALL="${TMP_DIR}/trash-install"
mkdir -p "${TRASH_INSTALL}"
INSTALL_DIR="${TRASH_INSTALL}"
printf 'sensitive\n' >"${TRASH_INSTALL}/secret.tmp"
trash_path "${TRASH_INSTALL}/secret.tmp"
[[ ! -e "${TRASH_INSTALL}/secret.tmp" ]] || fail 'trash_path left the original file in place'
find "${TRASH_INSTALL}/.chatgpt2api-trash" -type f -name 'secret.tmp.*' -print -quit | grep -q . \
  || fail 'trash_path did not preserve the removed file in the install trash'

validate_warp_defaults() {
  WITH_WARP=1
  PORT="3000"
  MODE="docker"
  STORAGE_BACKEND="json"
  THREAD_TOKENS="1"
  IMAGE_QUEUE_DATABASE_URL="postgresql://queue/chatgpt2api_image_queue"
  BASE_URL="http://127.0.0.1:3000"
  NODE_ROLE="standalone"
  validate_inputs
}

assert_command_passes 'warp defaults' validate_warp_defaults

WIREGUARD_PORT='51820'
ufw() {
  if [[ "${1:-}" == 'status' ]]; then
    printf 'Status: active\n'
    return 0
  fi
  return 1
}
assert_command_fails 'active ufw rule failure' cluster_allow_wireguard_firewall
unset -f ufw

validate_unsupported_postgres_driver() {
  PORT="3000"
  MODE="docker"
  STORAGE_BACKEND="json"
  THREAD_TOKENS="1"
  IMAGE_QUEUE_DATABASE_URL="postgresql+asyncpg://queue/chatgpt2api_image_queue"
  BASE_URL="http://127.0.0.1:3000"
  NODE_ROLE="standalone"
  validate_inputs
}

assert_command_fails 'unsupported PostgreSQL driver' validate_unsupported_postgres_driver

MALICIOUS_MARKER="${TMP_DIR}/dotenv-command-executed"
INSTALL_DIR="${TMP_DIR}/malicious"
mkdir -p "${INSTALL_DIR}"
cat >"${INSTALL_DIR}/.env" <<'EOF'
CHATGPT2API_AUTH_KEY=$(touch /tmp/chatgpt2api-dotenv-command-executed)
IMAGE_QUEUE_DATABASE_URL='postgresql://queue/chatgpt2api_image_queue'
EOF
if cluster_load_env >/dev/null 2>&1; then
  fail 'malicious dotenv was accepted'
fi
[[ ! -e "${MALICIOUS_MARKER}" ]] || fail 'dotenv executed shell command'
[[ ! -e /tmp/chatgpt2api-dotenv-command-executed ]] || fail 'dotenv executed shell command'

INSTALL_DIR="${TMP_DIR}/valid"
mkdir -p "${INSTALL_DIR}"
cat >"${INSTALL_DIR}/.env" <<'EOF'
CHATGPT2API_AUTH_KEY='auth=value with spaces'
IMAGE_QUEUE_DATABASE_URL='postgresql://user:pass@db/chatgpt2api_image_queue?sslmode=require'
CHATGPT2API_THREAD_TOKENS='80'
CHATGPT2API_WARP_IMAGE='caomingjun/warp@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
CHATGPT2API_PRIVOXY_IMAGE='vimagick/privoxy@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
WARP_SOCKS_PORT='40000'
PRIVOXY_PORT='40080'
FLARESOLVERR_PORT='8191'
FLARESOLVERR_LOG_LEVEL='info'
TZ='Asia/Shanghai'
CHATGPT2API_CONFIG_FILE='/srv/chatgpt2api/config.json'
HOST='127.0.0.1'
LOG_LEVEL='warning'
UVICORN_WORKERS='2'
IMAGE_QUEUE_ARTIFACT_ROOT='/srv/chatgpt2api/data/images'
IMAGE_QUEUE_LEGACY_TASK_PATH='/srv/chatgpt2api/data/image_tasks.json'
IMAGE_QUEUE_LEASE_SECONDS='120'
IMAGE_QUEUE_DB_POOL_SIZE='20'
EDITABLE_FILE_WORKERS='2'
EDITABLE_FILE_MAX_BACKLOG='100'
PROMPT_LIBRARY_DEFAULT_URL='https://example.com/prompts.json'
EOF
cluster_load_env
[[ "${AUTH_KEY}" == 'auth=value with spaces' ]] || fail 'dotenv value containing equals was not preserved'
[[ "${IMAGE_QUEUE_DATABASE_URL}" == 'postgresql://user:pass@db/chatgpt2api_image_queue?sslmode=require' ]] || fail 'dotenv URL was not preserved'
[[ "${THREAD_TOKENS}" == '80' ]] || fail 'dotenv numeric value was not loaded'
[[ "${WARP_SOCKS_PORT}" == '40000' ]] || fail 'generated WARP port was not loaded'
[[ "${TZ}" == 'Asia/Shanghai' ]] || fail 'generated timezone was not loaded'
[[ "${IMAGE_QUEUE_LEASE_SECONDS}" == '120' ]] || fail 'runtime queue tuning was not loaded'
[[ "${CHATGPT2API_CONFIG_FILE}" == '/srv/chatgpt2api/config.json' ]] || fail 'config file override was not loaded'

INSTALL_DIR="${TMP_DIR}/single-quote"
mkdir -p "${INSTALL_DIR}"
cat >"${INSTALL_DIR}/.env" <<EOF
CHATGPT2API_AUTH_KEY=$(dotenv_escape "a'b")
IMAGE_QUEUE_DATABASE_URL=$(dotenv_escape 'postgresql://queue/chatgpt2api_image_queue')
EOF
cluster_load_env
[[ "${AUTH_KEY}" == "a'b" ]] || fail 'dotenv single quote value was not preserved'

INSTALL_DIR="${TMP_DIR}/generated"
mkdir -p "${INSTALL_DIR}"
AUTH_KEY="generated-auth"
PORT="3000"
THREAD_TOKENS="80"
NODE_ROLE="standalone"
STORAGE_BACKEND="json"
IMAGE_QUEUE_DATABASE_URL="postgresql://queue/chatgpt2api_image_queue"
CHATGPT2API_WARP_IMAGE='caomingjun/warp@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
CHATGPT2API_PRIVOXY_IMAGE='vimagick/privoxy@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
write_env_file
cluster_load_env
[[ "${AUTH_KEY}" == 'generated-auth' ]] || fail 'generated dotenv auth key was not reloaded'
[[ "${CHATGPT2API_WARP_IMAGE}" == 'caomingjun/warp@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ]] || fail 'generated dotenv WARP image was not reloaded'
[[ "${PRIVOXY_PORT}" == '40080' ]] || fail 'generated dotenv Privoxy port was not reloaded'

RUN_PYTHON_INSTALL="${TMP_DIR}/run-python-env"
RUN_PYTHON_CAPTURE="${TMP_DIR}/run-python-env.capture"
RUN_PYTHON_BIN="${TMP_DIR}/run-python-uv"
RUN_PYTHON_NPM_DIR="${TMP_DIR}/run-python-npm"
mkdir -p "${RUN_PYTHON_INSTALL}/data" "${RUN_PYTHON_INSTALL}/scripts/image_upscale"
printf '{"name":"image-upscale","private":true,"dependencies":{"sharp":"0.35.3"}}\n' \
  >"${RUN_PYTHON_INSTALL}/scripts/image_upscale/package.json"
printf '{"name":"image-upscale","lockfileVersion":3,"packages":{}}\n' \
  >"${RUN_PYTHON_INSTALL}/scripts/image_upscale/package-lock.json"
mkdir -p "${RUN_PYTHON_NPM_DIR}"
cat >"${RUN_PYTHON_NPM_DIR}/npm" <<'EOF'
#!/usr/bin/env bash
if [[ "${PWD:-}" == */scripts/image_upscale ]]; then
  mkdir -p node_modules/sharp
fi
exit 0
EOF
chmod +x "${RUN_PYTHON_NPM_DIR}/npm"
export RUN_PYTHON_CAPTURE
cat >"${RUN_PYTHON_BIN}" <<'EOF'
#!/usr/bin/env bash
if [[ " $* " == *" scripts.bootstrap_database_roles "* ]]; then
  printf '%s\n' "${IMAGE_QUEUE_DATABASE_URL-}" >"${RUN_PYTHON_CAPTURE}"
  printf '%s\n' "${APP_DATABASE_URL-}" >>"${RUN_PYTHON_CAPTURE}"
fi
if [[ "${PWD:-}" == */scripts/image_upscale ]]; then
  mkdir -p node_modules/sharp
fi
exit 0
EOF
chmod +x "${RUN_PYTHON_BIN}"
(
  ensure_uv() { UV_BIN="${RUN_PYTHON_BIN}"; }
  build_frontend() { :; }
  wait_python_runtime_health() { :; }
  PATH="${RUN_PYTHON_NPM_DIR}:${PATH}"
  INSTALL_DIR="${RUN_PYTHON_INSTALL}"
  MODE="python"
  AUTH_KEY="run-python-auth"
  PORT="31999"
  THREAD_TOKENS="80"
  BASE_URL="http://127.0.0.1:31999"
  IMAGE_BASE_URL=""
  PYTHON_BIN="python3"
  IMAGE_PORT="3000"
  NODE_ROLE="standalone"
  RUN_API=""
  RUN_WORKER=""
  WORKER_ID=""
  WIREGUARD_IP=""
  WORKER_JOINED_MARKER_FILE=""
  STORAGE_BACKEND="postgres"
  APP_DATABASE_URL="postgresql://user:password@db/chatgpt2api_app"
  DATABASE_URL="${APP_DATABASE_URL}"
  IMAGE_QUEUE_DATABASE_URL="postgresql://user:password@db/chatgpt2api_image_queue"
  IMAGE_QUEUE_INSTANCE_ID=""
  CHATGPT2API_PYTHON_PID_FILE="${RUN_PYTHON_INSTALL}/data/chatgpt2api.pid"
  run_python
)
[[ "$(sed -n '1p' "${RUN_PYTHON_CAPTURE}")" == 'postgresql://user:password@db/chatgpt2api_image_queue' ]] \
  || fail 'python mode bootstrap did not receive IMAGE_QUEUE_DATABASE_URL from the loaded environment'
[[ "$(sed -n '2p' "${RUN_PYTHON_CAPTURE}")" == 'postgresql://user:password@db/chatgpt2api_app' ]] \
  || fail 'python mode bootstrap did not receive APP_DATABASE_URL from the loaded environment'

INSTALL_DIR="${TMP_DIR}/rerun"
mkdir -p "${INSTALL_DIR}"
cat >"${INSTALL_DIR}/.env" <<'EOF'
CHATGPT2API_AUTH_KEY='existing-auth'
CHATGPT2API_PORT='3100'
CHATGPT2API_THREAD_TOKENS='91'
IMAGE_QUEUE_DATABASE_URL='postgresql://existing-queue/chatgpt2api_image_queue'
IMAGE_PROMPT_SUFFIX_ENABLED='false'
IMAGE_PROMPT_SUFFIX='existing suffix'
IMAGE_QUEUE_MAX_BACKLOG='77'
CHATGPT2API_PROXY_RUNTIME_ENABLED='true'
CHATGPT2API_PROXY_RUNTIME_PROXY_URL='http://privoxy:8118'
WARP_LICENSE_KEY='existing-license'
MODE='docker'
WITH_WARP='1'
EOF
AUTH_KEY=""
PORT="3000"
THREAD_TOKENS="80"
IMAGE_QUEUE_DATABASE_URL=""
IMAGE_PROMPT_SUFFIX_ENABLED="true"
IMAGE_PROMPT_SUFFIX=""
IMAGE_QUEUE_MAX_BACKLOG="50"
CHATGPT2API_PROXY_RUNTIME_ENABLED=""
CHATGPT2API_PROXY_RUNTIME_PROXY_URL=""
WARP_LICENSE_KEY=""
MODE=""
WITH_WARP="0"
load_existing_install_env
[[ "${AUTH_KEY}" == 'existing-auth' ]] || fail 'rerun did not preserve existing auth key'
[[ "${PORT}" == '3100' ]] || fail 'rerun did not preserve existing port'
[[ "${THREAD_TOKENS}" == '91' ]] || fail 'rerun did not preserve existing thread tokens'
[[ "${IMAGE_QUEUE_DATABASE_URL}" == 'postgresql://existing-queue/chatgpt2api_image_queue' ]] || fail 'rerun did not preserve queue URL'
[[ "${IMAGE_PROMPT_SUFFIX_ENABLED}" == 'false' ]] || fail 'rerun did not preserve prompt suffix toggle'
[[ "${IMAGE_PROMPT_SUFFIX}" == 'existing suffix' ]] || fail 'rerun did not preserve prompt suffix'
[[ "${IMAGE_QUEUE_MAX_BACKLOG}" == '77' ]] || fail 'rerun did not preserve queue backlog'
[[ "${CHATGPT2API_PROXY_RUNTIME_ENABLED}" == 'true' ]] || fail 'rerun did not preserve proxy runtime toggle'
[[ "${CHATGPT2API_PROXY_RUNTIME_PROXY_URL}" == 'http://privoxy:8118' ]] || fail 'rerun did not preserve proxy runtime URL'
[[ "${WARP_LICENSE_KEY}" == 'existing-license' ]] || fail 'rerun did not preserve WARP license'
[[ "${MODE}" == 'docker' ]] || fail 'rerun did not preserve run mode'
[[ "${WITH_WARP}" == '1' ]] || fail 'rerun did not preserve WARP compose selection'

INSTALL_DIR="${TMP_DIR}/manifest"
mkdir -p "${INSTALL_DIR}/deploy"
cat >"${INSTALL_DIR}/deploy/release-manifest.env" <<'EOF'
CHATGPT2API_RELEASE_REF=7777777777777777777777777777777777777777
CHATGPT2API_IMAGE=ghcr.io/example/chatgpt2api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
UV_VERSION=0.8.18
CHATGPT2API_WARP_IMAGE=caomingjun/warp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CHATGPT2API_PRIVOXY_IMAGE=vimagick/privoxy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
CHATGPT2API_FLARESOLVERR_IMAGE=flaresolverr/flaresolverr@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
EOF
validate_existing_deployment_dir \
  || fail 'release manifest was not recognized as a resumable deployment'
CHATGPT2API_RELEASE_REF=""
CHATGPT2API_IMAGE=""
UV_VERSION=""
CHATGPT2API_WARP_IMAGE=""
CHATGPT2API_PRIVOXY_IMAGE=""
CHATGPT2API_FLARESOLVERR_IMAGE=""
load_release_manifest
[[ "${CHATGPT2API_RELEASE_REF}" == '7777777777777777777777777777777777777777' ]] || fail 'release manifest ref was not loaded'
[[ "${BRANCH}" == '7777777777777777777777777777777777777777' ]] || fail 'release manifest ref was not applied to download branch'
[[ "${CHATGPT2API_IMAGE}" == 'ghcr.io/example/chatgpt2api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ]] || fail 'release manifest image was not loaded'
[[ "${UV_VERSION}" == '0.8.18' ]] || fail 'release manifest uv version was not loaded'
[[ "${CHATGPT2API_WARP_IMAGE}" == 'caomingjun/warp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' ]] || fail 'release manifest warp image was not loaded'

INSTALL_DIR="${TMP_DIR}/remote-manifest"
mkdir -p "${TMP_DIR}/remote-source"
cp "${ROOT_DIR}/deploy/release-manifest.env" "${TMP_DIR}/remote-source/release-manifest.env"
curl() {
  local output=""
  local previous=""
  local argument=""
  for argument in "$@"; do
    if [[ "${previous}" == '-o' ]]; then
      output="${argument}"
    fi
    previous="${argument}"
  done
  [[ -n "${output}" ]] || return 22
  cp "${REMOTE_MANIFEST_SOURCE:-${TMP_DIR}/remote-source/release-manifest.env}" "${output}"
}
BRANCH="${DEFAULT_RELEASE_REF}"
CHATGPT2API_RELEASE_REF=""
CHATGPT2API_IMAGE=""
CHATGPT2API_IMAGE_DIGEST='sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
load_release_manifest
[[ -f "${INSTALL_DIR}/deploy/release-manifest.env" ]] \
  || fail 'remote release manifest was not persisted for the bundle validator'
[[ "${CHATGPT2API_IMAGE_DIGEST}" == 'sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a' ]] \
  || fail 'release manifest loading did not replace a stale optional image digest'
validate_existing_deployment_dir \
  || fail 'remote release manifest did not make the fresh install resumable'

INSTALL_DIR="${TMP_DIR}/prompt-release-refresh"
mkdir -p "${INSTALL_DIR}/deploy" "${TMP_DIR}/prompt-release-source"
PROMPT_OLD_REF='1111111111111111111111111111111111111111'
PROMPT_NEW_REF='2222222222222222222222222222222222222222'
cat >"${INSTALL_DIR}/deploy/release-manifest.env" <<'EOF'
CHATGPT2API_RELEASE_REF=1111111111111111111111111111111111111111
CHATGPT2API_IMAGE=ghcr.io/example/chatgpt2api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
UV_VERSION=0.8.17
CHATGPT2API_WARP_IMAGE=caomingjun/warp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CHATGPT2API_PRIVOXY_IMAGE=vimagick/privoxy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
CHATGPT2API_FLARESOLVERR_IMAGE=flaresolverr/flaresolverr@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
EOF
cat >"${TMP_DIR}/prompt-release-source/release-manifest.env" <<'EOF'
CHATGPT2API_RELEASE_REF=2222222222222222222222222222222222222222
CHATGPT2API_IMAGE=ghcr.io/example/chatgpt2api@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
UV_VERSION=0.8.17
CHATGPT2API_WARP_IMAGE=caomingjun/warp@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
CHATGPT2API_PRIVOXY_IMAGE=vimagick/privoxy@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
CHATGPT2API_FLARESOLVERR_IMAGE=flaresolverr/flaresolverr@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
EOF
REMOTE_MANIFEST_SOURCE="${TMP_DIR}/prompt-release-source/release-manifest.env"
BRANCH="${PROMPT_OLD_REF}"
CHATGPT2API_RELEASE_REF=''
CHATGPT2API_IMAGE=''
CHATGPT2API_IMAGE_DIGEST=''
CLI_BRANCH_SET='0'
ENV_RELEASE_REF_SET='0'
RELEASE_REF_SELECTED='0'
load_release_manifest
BRANCH="${PROMPT_NEW_REF}"
RELEASE_REF_SELECTED='1'
load_release_manifest
[[ "${BRANCH}" == "${PROMPT_NEW_REF}" ]] \
  || fail 'release manifest refresh overwrote the release selected in the interactive prompt'
[[ "${CHATGPT2API_IMAGE}" == 'ghcr.io/example/chatgpt2api@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' ]] \
  || fail 'release manifest refresh reused the image from before the interactive release selection'
REMOTE_MANIFEST_SOURCE="${TMP_DIR}/remote-source/release-manifest.env"
RELEASE_REF_SELECTED='0'
unset -f curl

INSTALL_DIR="${TMP_DIR}/release-asset"
mkdir -p "${INSTALL_DIR}"
cat >"${TMP_DIR}/release-asset-manifest.env" <<'EOF'
CHATGPT2API_RELEASE_REF=3333333333333333333333333333333333333333
CHATGPT2API_IMAGE=ghcr.io/example/chatgpt2api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
UV_VERSION=0.8.17
CHATGPT2API_WARP_IMAGE=caomingjun/warp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CHATGPT2API_PRIVOXY_IMAGE=vimagick/privoxy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
CHATGPT2API_FLARESOLVERR_IMAGE=flaresolverr/flaresolverr@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
EOF
BRANCH='3333333333333333333333333333333333333333'
CHATGPT2API_RELEASE_REF=''
CHATGPT2API_IMAGE=''
CHATGPT2API_IMAGE_DIGEST=''
UV_VERSION=''
curl() {
  local output=''
  local url=''
  local previous=''
  local argument=''
  for argument in "$@"; do
    if [[ "${previous}" == '-o' ]]; then
      output="${argument}"
    fi
    if [[ "${argument}" == http* ]]; then
      url="${argument}"
    fi
    previous="${argument}"
  done
  if [[ "${url}" == *"/releases/download/chatgpt2api-${BRANCH}/release-manifest.env" ]]; then
    cp "${TMP_DIR}/release-asset-manifest.env" "${output}"
    return 0
  fi
  return 22
}
load_release_manifest
[[ "${CHATGPT2API_RELEASE_REF}" == "${BRANCH}" ]] || fail 'GitHub release asset manifest was not loaded'
[[ "${CHATGPT2API_IMAGE}" == ghcr.io/example/chatgpt2api@sha256:* ]] || fail 'GitHub release asset image was not loaded'
unset -f curl

INSTALL_DIR="${TMP_DIR}/stale-remote-manifest"
mkdir -p "${INSTALL_DIR}"
BRANCH='4444444444444444444444444444444444444444'
CHATGPT2API_RELEASE_REF=''
CHATGPT2API_IMAGE=''
curl() {
  local output=''
  local previous=''
  local argument=''
  for argument in "$@"; do
    if [[ "${previous}" == '-o' ]]; then
      output="${argument}"
    fi
    previous="${argument}"
  done
  cat >"${output}" <<'EOF'
CHATGPT2API_RELEASE_REF=5555555555555555555555555555555555555555
CHATGPT2API_IMAGE=ghcr.io/example/chatgpt2api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
UV_VERSION=0.8.17
CHATGPT2API_WARP_IMAGE=caomingjun/warp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CHATGPT2API_PRIVOXY_IMAGE=vimagick/privoxy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
CHATGPT2API_FLARESOLVERR_IMAGE=flaresolverr/flaresolverr@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
EOF
}
assert_command_fails 'stale remote release manifest' load_release_manifest
unset -f curl

INSTALL_DIR="${TMP_DIR}/incomplete-remote-manifest"
mkdir -p "${INSTALL_DIR}"
BRANCH='6666666666666666666666666666666666666666'
CHATGPT2API_RELEASE_REF=''
CHATGPT2API_IMAGE=''
curl() {
  local output=''
  local previous=''
  local argument=''
  for argument in "$@"; do
    if [[ "${previous}" == '-o' ]]; then
      output="${argument}"
    fi
    previous="${argument}"
  done
  cat >"${output}" <<'EOF'
CHATGPT2API_RELEASE_REF=6666666666666666666666666666666666666666
UV_VERSION=0.8.17
CHATGPT2API_WARP_IMAGE=caomingjun/warp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CHATGPT2API_PRIVOXY_IMAGE=vimagick/privoxy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
CHATGPT2API_FLARESOLVERR_IMAGE=flaresolverr/flaresolverr@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
EOF
}
assert_command_fails 'incomplete remote release manifest' load_release_manifest
unset -f curl

INSTALL_DIR="${TMP_DIR}/manifest-precedence"
mkdir -p "${INSTALL_DIR}/deploy"
cat >"${INSTALL_DIR}/.env" <<'EOF'
CHATGPT2API_RELEASE_REF='8888888888888888888888888888888888888888'
EOF
cat >"${INSTALL_DIR}/deploy/release-manifest.env" <<'EOF'
CHATGPT2API_RELEASE_REF=9999999999999999999999999999999999999999
CHATGPT2API_IMAGE=ghcr.io/example/chatgpt2api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
UV_VERSION=0.8.17
CHATGPT2API_WARP_IMAGE=caomingjun/warp@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CHATGPT2API_PRIVOXY_IMAGE=vimagick/privoxy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
CHATGPT2API_FLARESOLVERR_IMAGE=flaresolverr/flaresolverr@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
EOF
BRANCH="${DEFAULT_RELEASE_REF}"
CHATGPT2API_RELEASE_REF=""
load_existing_install_env
load_release_manifest
load_existing_install_env
[[ "${BRANCH}" == '8888888888888888888888888888888888888888' ]] || fail 'existing release ref did not override manifest'
parse_args --branch cli-release
[[ "${BRANCH}" == 'cli-release' ]] || fail 'CLI release ref did not override existing env'
[[ "${RELEASE_REF_SELECTED}" == '1' ]] || fail 'CLI release ref did not force a release manifest refresh'

INSTALL_DIR="${TMP_DIR}/manifest-cli-precedence"
mkdir -p "${INSTALL_DIR}/deploy"
cat >"${INSTALL_DIR}/.env" <<'EOF'
CHATGPT2API_RELEASE_REF='release-from-existing-env'
CHATGPT2API_IMAGE='ghcr.io/example/chatgpt2api@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
UV_VERSION='0.8.19'
EOF
cat >"${INSTALL_DIR}/deploy/release-manifest.env" <<'EOF'
CHATGPT2API_RELEASE_REF=release-from-manifest
CHATGPT2API_IMAGE=ghcr.io/example/chatgpt2api@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
UV_VERSION=0.8.20
EOF
BRANCH="${DEFAULT_RELEASE_REF}"
CHATGPT2API_RELEASE_REF=""
CHATGPT2API_IMAGE=""
CHATGPT2API_IMAGE_DIGEST=""
UV_VERSION=""
CLI_BRANCH_SET="0"
CLI_BRANCH_VALUE=""
export CHATGPT2API_RELEASE_REF='exported-old-release'
parse_args --branch cli-release
load_existing_install_env
curl() { return 22; }
assert_command_fails 'explicit release without manifest' load_release_manifest
unset -f curl
load_existing_install_env
parse_args --branch cli-release
[[ "${BRANCH}" == 'cli-release' ]] || fail 'CLI branch was overwritten by stale local release metadata'
[[ -z "${CHATGPT2API_IMAGE}" ]] || fail 'stale local image was reused for an explicit CLI branch'
[[ "${CHATGPT2API_RELEASE_REF}" == 'cli-release' ]] || fail 'exported release ref overrode an explicit CLI branch'
unset CHATGPT2API_RELEASE_REF

INSTALL_DIR="${TMP_DIR}/fresh-manifest-fallback"
BRANCH="${DEFAULT_RELEASE_REF}"
REPO_OWNER="biubiubiu125"
REPO_NAME="chatgpt2api"
CHATGPT2API_RELEASE_REF=""
unset -f curl 2>/dev/null || true
curl() { return 22; }
load_release_manifest
validate_existing_deployment_dir \
  || fail 'fresh install became an unrecognized deployment after release manifest fallback'
[[ "${CHATGPT2API_IMAGE_DIGEST}" == "${DEFAULT_CHATGPT2API_IMAGE_DIGEST}" ]] \
  || fail 'built-in default release fallback did not preserve the immutable image digest'
unset -f curl

INSTALL_DIR="${TMP_DIR}/persisted"
mkdir -p "${INSTALL_DIR}"
AUTH_KEY="persisted-auth"
CHATGPT2API_CONFIG_FILE="/srv/chatgpt2api/custom-config.json"
PORT="3000"
THREAD_TOKENS="80"
BRANCH="${DEFAULT_RELEASE_REF}"
CHATGPT2API_RELEASE_REF=""
CHATGPT2API_IMAGE=""
CHATGPT2API_IMAGE_DIGEST=""
UV_VERSION="0.8.17"
NODE_ROLE="standalone"
STORAGE_BACKEND="json"
IMAGE_QUEUE_DATABASE_URL="postgresql://queue/chatgpt2api_image_queue"
IMAGE_PROMPT_SUFFIX_ENABLED="false"
IMAGE_PROMPT_SUFFIX="persisted suffix"
IMAGE_QUEUE_MAX_BACKLOG="77"
IMAGE_QUEUE_LEASE_SECONDS="120"
IMAGE_QUEUE_DB_POOL_SIZE="20"
EDITABLE_FILE_WORKERS="2"
PROMPT_LIBRARY_DEFAULT_URL="https://example.com/prompts.json"
CHATGPT2API_MONITOR_COMPLETED_LIMIT="321"
HOST="127.0.0.1"
LOG_LEVEL="warning"
UVICORN_WORKERS="2"
WARP_SOCKS_PORT="40100"
PRIVOXY_PORT="40180"
FLARESOLVERR_PORT="8192"
FLARESOLVERR_LOG_LEVEL="warning"
TZ="UTC"
CHATGPT2API_PROXY_RUNTIME_ENABLED="true"
CHATGPT2API_PROXY_RUNTIME_PROXY_URL="http://privoxy:8118"
WARP_LICENSE_KEY="license"
write_env_file
for key in \
  IMAGE_PROMPT_SUFFIX_ENABLED IMAGE_PROMPT_SUFFIX IMAGE_QUEUE_MAX_BACKLOG \
  IMAGE_QUEUE_LEASE_SECONDS IMAGE_QUEUE_DB_POOL_SIZE EDITABLE_FILE_WORKERS \
  PROMPT_LIBRARY_DEFAULT_URL CHATGPT2API_MONITOR_COMPLETED_LIMIT HOST LOG_LEVEL UVICORN_WORKERS \
  CHATGPT2API_CONFIG_FILE WARP_SOCKS_PORT PRIVOXY_PORT FLARESOLVERR_PORT FLARESOLVERR_LOG_LEVEL TZ \
  CHATGPT2API_PROXY_RUNTIME_ENABLED CHATGPT2API_PROXY_RUNTIME_PROXY_URL WARP_LICENSE_KEY; do
  grep -q "^${key}=" "${INSTALL_DIR}/.env" || fail "write_env_file omitted ${key}"
done

APP_DATABASE_URL="postgresql://queue/chatgpt2api_image_queue"
assert_command_fails 'invalid dedicated app database URL' validate_inputs
APP_DATABASE_URL=""

MODE="docker"
CHATGPT2API_IMAGE="ghcr.io/example/chatgpt2api:latest"
assert_command_fails 'mutable custom image' validate_inputs
CHATGPT2API_IMAGE="ghcr.io/example/chatgpt2api @sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
assert_command_fails 'image reference containing whitespace' validate_inputs
CHATGPT2API_IMAGE=""

BRANCH="v2.7.1"
assert_command_fails 'version tag without immutable image' validate_inputs
CHATGPT2API_IMAGE='ghcr.io/example/chatgpt2api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
assert_command_fails 'Docker version tag source ref' validate_inputs
BRANCH="${DEFAULT_RELEASE_REF}"
CHATGPT2API_IMAGE=""

FRONTEND_BIN="${TMP_DIR}/frontend-bin"
FRONTEND_INSTALL="${TMP_DIR}/frontend-install"
mkdir -p "${FRONTEND_BIN}" "${FRONTEND_INSTALL}/web-vue/dist"
cat >"${FRONTEND_BIN}/npm" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "${FRONTEND_BIN}/npm"
printf 'frontend\n' >"${FRONTEND_INSTALL}/web-vue/dist/index.html"
OLD_PATH="${PATH}"
PATH="${FRONTEND_BIN}:${PATH}"
INSTALL_DIR="${FRONTEND_INSTALL}"
for _ in 1 2 3; do
  build_frontend
done
[[ -d "${FRONTEND_INSTALL}/web_dist.previous" ]] || fail 'frontend publish did not keep the controlled previous build'
previous_count="$(find "${FRONTEND_INSTALL}" -mindepth 1 -maxdepth 1 -type d -name 'web_dist.previous*' | wc -l | tr -d '[:space:]')"
[[ "${previous_count}" == '1' ]] || fail 'frontend publish left more than one previous build'
staging_count="$(find "${FRONTEND_INSTALL}" -mindepth 1 -maxdepth 1 -type d -name '.web_dist.staging*' | wc -l | tr -d '[:space:]')"
[[ "${staging_count}" == '0' ]] || fail 'frontend publish left a staging directory after success'
FRONTEND_FAIL_INSTALL="${TMP_DIR}/frontend-fail-install"
mkdir -p "${FRONTEND_FAIL_INSTALL}/web-vue/dist"
printf 'frontend\n' >"${FRONTEND_FAIL_INSTALL}/web-vue/dist/index.html"
INSTALL_DIR="${FRONTEND_FAIL_INSTALL}"
cp() { return 1; }
assert_command_fails 'frontend staging copy failure' build_frontend
unset -f cp
staging_count="$(find "${FRONTEND_FAIL_INSTALL}" -mindepth 1 -maxdepth 1 -type d -name '.web_dist.staging*' | wc -l | tr -d '[:space:]')"
[[ "${staging_count}" == '0' ]] || fail 'frontend copy failure left a staging directory'
PATH="${OLD_PATH}"

[[ "$(default_image)" != *':latest' ]] || fail 'default image still uses latest'
[[ "$(raw_url docker-compose.yml)" != */main/* ]] || fail 'raw URL still uses mutable main ref'

for path in \
  scripts/bootstrap_database_roles.py \
  deploy/release-manifest.env \
  web-vue/scripts/test-debug-center-editable-tasks.mjs \
  web-vue/scripts/test-studio-image-task-closed-loop.mjs \
  web-vue/scripts/test-studio-image-task-runtime.mjs \
  web-vue/scripts/test-register-provider-view.mjs \
  web-vue/scripts/test-third-party-links.mjs \
  web-vue/scripts/test-safe-external-url.mjs; do
  [[ -f "${ROOT_DIR}/${path}" ]] || fail "missing frontend regression script: ${path}"
done

if grep -En 'curl[^|]*\|[[:space:]]*(sh|bash)' \
  "${ROOT_DIR}/Dockerfile" "${INSTALL_SCRIPT}" >/dev/null; then
  fail 'unbounded installer command remains'
fi
if grep -En '^[[:space:]]*(source|\.)[[:space:]]+.*\.env' "${INSTALL_SCRIPT}" >/dev/null; then
  fail 'Python launcher still executes .env as shell code'
fi
if grep -En 'rm[[:space:]]+-rf' "${INSTALL_SCRIPT}" >/dev/null; then
  fail 'installer still permanently removes deployment paths'
fi
grep -q 'docker compose.*config --quiet' "${INSTALL_SCRIPT}" \
  || fail 'installer does not preflight rendered Compose configuration'
help_output="$(cat "${INSTALL_SCRIPT}" | bash -s -- --help 2>&1)" \
  || fail 'installer did not execute when read from stdin'
grep -q 'bash deploy/install.sh' <<<"${help_output}" \
  || fail 'stdin installer invocation did not print usage'

if grep -En 'ghcr\.io/[^[:space:]]+:latest|caomingjun/warp:latest|vimagick/privoxy:latest|flaresolverr/flaresolverr:latest' \
  "${ROOT_DIR}/docker-compose.yml" "${ROOT_DIR}/docker-compose.cluster-main.yml" \
  "${ROOT_DIR}/docker-compose.cluster-worker.yml" "${ROOT_DIR}/docker-compose.warp.yml" >/dev/null; then
  fail 'mutable production image tag remains'
fi
if grep -En 'postgres:17-alpine|postgres:latest' \
  "${ROOT_DIR}/docker-compose.local.yml" "${ROOT_DIR}/docker-compose.cluster-main.yml" >/dev/null; then
  fail 'mutable PostgreSQL image tag remains'
fi
for compose_file in \
  "${ROOT_DIR}/docker-compose.local.yml" \
  "${ROOT_DIR}/docker-compose.cluster-main.yml"; do
  grep -Eq 'image: postgres@sha256:[0-9a-f]{64}$' "${compose_file}" \
    || fail "PostgreSQL digest is missing or malformed in ${compose_file}"
done
grep -Eq '/health/live\?format=json' "${ROOT_DIR}/docker-compose.cluster-worker.yml" \
  || fail 'worker activation healthcheck does not expose process liveness'
if grep -En '/health\?format=json&scope=runtime' "${ROOT_DIR}/docker-compose.cluster-worker.yml" >/dev/null; then
  fail 'worker activation healthcheck waits for runtime readiness before join activation'
fi
grep -q 'download_file "deploy/nginx-worker-images.example.conf"' "${INSTALL_SCRIPT}" \
  || fail 'worker Nginx template is not a required download'
grep -q 'download_file "scripts/env_loader.py"' "${INSTALL_SCRIPT}" \
  || fail 'Docker database bootstrap bundle is missing the safe dotenv loader'
INSTALL_DIR="${TMP_DIR}/unsafe-install"
mkdir -p "${INSTALL_DIR}"
printf 'do not overwrite\n' >"${INSTALL_DIR}/unrelated.txt"
assert_command_fails 'unrecognized non-empty install directory' validate_existing_deployment_dir
INSTALL_DIR="${TMP_DIR}/safe-install"
mkdir -p "${INSTALL_DIR}"
printf 'CHATGPT2API_PORT=3000\n' >"${INSTALL_DIR}/.env"
assert_command_passes 'recognized existing install directory' validate_existing_deployment_dir

if grep -En '^FROM .*((node|python):|python:[^@[:space:]]|node:[^@[:space:]])' \
  "${ROOT_DIR}/Dockerfile" >/dev/null; then
  fail 'mutable Dockerfile base image tag remains'
fi

if grep -En 'main/deploy/install\.sh|ghcr\.io/biubiubiu125/chatgpt2api:latest|caomingjun/warp:latest|vimagick/privoxy:latest|flaresolverr/flaresolverr:latest' \
  "${ROOT_DIR}/README.md" "${ROOT_DIR}/docs/deployment.md" "${ROOT_DIR}/.env.example" >/dev/null; then
  fail 'deployment documentation still points at mutable release'
fi

if grep -En 'uses:.*@(v[0-9]+|main|master|latest)([[:space:]]|$)' \
  "${ROOT_DIR}/.github/workflows"/*.yml >/dev/null; then
  fail 'mutable GitHub Action ref remains'
fi

if ! grep -q 'database-init' "${ROOT_DIR}/docker-compose.yml"; then
  fail 'standard compose has no image queue bootstrap service'
fi
if ! grep -q 'service_completed_successfully' "${ROOT_DIR}/docker-compose.yml"; then
  fail 'standard compose app does not wait for image queue bootstrap'
fi
grep -Fq 'command: ["/app/.venv/bin/python", "-m", "scripts.bootstrap_database_roles"]' \
  "${ROOT_DIR}/docker-compose.yml" \
  || fail 'standard database bootstrap does not run as a module from the app root'
grep -Fq 'command: ["/app/.venv/bin/python", "-m", "scripts.bootstrap_database_roles"]' \
  "${ROOT_DIR}/docker-compose.warp.yml" \
  || fail 'WARP database bootstrap does not run as a module from the app root'
grep -Fq 'test: ["CMD", "/healthcheck/index.sh"]' "${ROOT_DIR}/docker-compose.warp.yml" \
  || fail 'WARP healthcheck does not use the image-provided healthcheck'
if grep -Fq 'python3 -c' "${ROOT_DIR}/docker-compose.warp.yml"; then
  fail 'WARP healthcheck depends on an unbundled Python runtime'
fi
grep -Fq 'STORAGE_BACKEND: ${STORAGE_BACKEND:-postgres}' "${ROOT_DIR}/docker-compose.yml" \
  || fail 'standard database bootstrap does not receive STORAGE_BACKEND'
grep -Fq 'STORAGE_BACKEND: ${STORAGE_BACKEND:-postgres}' "${ROOT_DIR}/docker-compose.warp.yml" \
  || fail 'WARP database bootstrap does not receive STORAGE_BACKEND'

INSTALL_DIR="${TMP_DIR}/optional-download"
mkdir -p "${INSTALL_DIR}"
printf 'stale\n' >"${INSTALL_DIR}/config.example.yaml"
curl() {
  printf '404'
}
assert_command_passes 'optional 404' download_optional_file 'config.example.yaml'
[[ ! -e "${INSTALL_DIR}/config.example.yaml" ]] || fail 'optional 404 preserved a stale downloaded file'
curl() {
  return 7
}
assert_command_fails 'optional network failure' download_optional_file 'config.example.yaml'
curl() {
  printf '503'
}
assert_command_fails 'optional server failure' download_optional_file 'config.example.yaml'
unset -f curl

INSTALL_DIR="${TMP_DIR}/persist-release"
mkdir -p "${INSTALL_DIR}"
AUTH_KEY="persisted-auth"
PORT="3000"
THREAD_TOKENS="80"
BRANCH="release-test"
UV_VERSION="0.8.19"
CHATGPT2API_IMAGE="ghcr.io/example/chatgpt2api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NODE_ROLE="standalone"
STORAGE_BACKEND="json"
IMAGE_QUEUE_DATABASE_URL="postgresql://queue/chatgpt2api_image_queue"
write_env_file
grep -q "^CHATGPT2API_RELEASE_REF='release-test'$" "${INSTALL_DIR}/.env" \
  || fail 'write_env_file omitted release ref'
grep -q "^UV_VERSION='0.8.19'$" "${INSTALL_DIR}/.env" \
  || fail 'write_env_file omitted uv version'

grep -q 'systemd/system/chatgpt2api.service' "${INSTALL_SCRIPT}" \
  || fail 'python mode has no systemd service path'
grep -q 'systemctl enable "chatgpt2api.service"' "${INSTALL_SCRIPT}" \
  || fail 'python mode does not enable the systemd service'
grep -q 'systemctl restart "chatgpt2api.service"' "${INSTALL_SCRIPT}" \
  || fail 'python mode does not restart the systemd service on rerun'
grep -q 'nohup' "${INSTALL_SCRIPT}" \
  || fail 'python mode has no non-systemd fallback'
grep -q 'nohup "\${launcher_file}"' "${INSTALL_SCRIPT}" \
  || fail 'python mode fallback does not load the generated dotenv launcher'
grep -q 'build_image_upscale_runtime' "${INSTALL_SCRIPT}" \
  || fail 'python mode does not install the Sharp image-upscale runtime'
grep -q 'kill -KILL' "${INSTALL_SCRIPT}" \
  || fail 'python mode fallback does not terminate a stale managed process'
grep -q 'up -d --remove-orphans' "${INSTALL_SCRIPT}" \
  || fail 'Docker rerun does not remove orphaned services when switching compose variants'
grep -q 'docker compose -f "\${compose_file}" down --remove-orphans' "${INSTALL_SCRIPT}" \
  || fail 'cluster failure cleanup does not remove orphaned services'
if ! python3 - "${INSTALL_SCRIPT}" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index("cluster_fail_worker_activation()")
end = text.index("\n}\n", start) + 3
body = text[start:end]
if "if ! cluster_down_compose" not in body:
    raise SystemExit("cluster activation failure does not report cleanup failures")
PY
then
  fail 'cluster activation failure cleanup errors are still silently swallowed'
fi
grep -q 'wait_python_runtime_health' "${INSTALL_SCRIPT}" \
  || fail 'python mode does not wait for runtime readiness'
grep -q 'systemd_escape_path_value' "${INSTALL_SCRIPT}" \
  || fail 'python systemd unit does not escape paths'
grep -q 'python_process_matches_install' "${INSTALL_SCRIPT}" \
  || fail 'python mode does not verify stale PID ownership'
grep -q 'stop_python_runtime' "${INSTALL_SCRIPT}" \
  || fail 'Docker mode does not stop the previous managed Python runtime'
grep -q 'stop_docker_runtime' "${INSTALL_SCRIPT}" \
  || fail 'Python mode does not stop the previous managed Docker runtime'
for compose_file in docker-compose.yml docker-compose.warp.yml docker-compose.local.yml docker-compose.cluster-main.yml docker-compose.cluster-worker.yml; do
  grep -q "${compose_file}" "${INSTALL_SCRIPT}" \
    || fail "mode switch cleanup does not cover ${compose_file}"
done
DOCKER_SWITCH_BIN="${TMP_DIR}/docker-switch-bin"
DOCKER_SWITCH_INSTALL="${TMP_DIR}/docker-switch-install"
DOCKER_SWITCH_LOG="${TMP_DIR}/docker-switch.log"
mkdir -p "${DOCKER_SWITCH_BIN}" "${DOCKER_SWITCH_INSTALL}"
for compose_file in docker-compose.yml docker-compose.warp.yml docker-compose.local.yml docker-compose.cluster-main.yml docker-compose.cluster-worker.yml; do
  : >"${DOCKER_SWITCH_INSTALL}/${compose_file}"
done
cat >"${DOCKER_SWITCH_BIN}/docker" <<'EOF'
#!/usr/bin/env bash
printf 'args=%s cluster=%s app=%s queue=%s worker=%s wireguard=%s image=%s admin=%s admin_encoded=%s\n' \
  "$*" "${CHATGPT2API_CLUSTER_ID-}" "${APP_DATABASE_URL-}" "${IMAGE_QUEUE_DATABASE_URL-}" \
  "${CHATGPT2API_WORKER_ID-}" "${CHATGPT2API_WIREGUARD_IP-}" "${CHATGPT2API_IMAGE_BASE_URL-}" \
  "${POSTGRES_ADMIN_PASSWORD-}" "${POSTGRES_PASSWORD_URLENCODED-}" >>"${DOCKER_SWITCH_LOG}"
if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then exit 0; fi
if [[ "${1:-}" == "info" ]]; then exit 0; fi
if [[ "${1:-}" == "compose" ]]; then exit 0; fi
exit 0
EOF
chmod +x "${DOCKER_SWITCH_BIN}/docker"
(
  INSTALL_DIR="${DOCKER_SWITCH_INSTALL}"
  CHATGPT2API_CLUSTER_ID=""
  APP_DATABASE_URL=""
  IMAGE_QUEUE_DATABASE_URL=""
  CHATGPT2API_WORKER_ID=""
  CHATGPT2API_WIREGUARD_IP=""
  IMAGE_BASE_URL=""
  POSTGRES_ADMIN_PASSWORD=""
  POSTGRES_PASSWORD_URLENCODED=""
  PATH="${DOCKER_SWITCH_BIN}:${PATH}"
  export DOCKER_SWITCH_LOG INSTALL_DIR CHATGPT2API_CLUSTER_ID APP_DATABASE_URL IMAGE_QUEUE_DATABASE_URL
  export CHATGPT2API_WORKER_ID CHATGPT2API_WIREGUARD_IP IMAGE_BASE_URL POSTGRES_ADMIN_PASSWORD POSTGRES_PASSWORD_URLENCODED PATH
  stop_docker_runtime
)
[[ "$(grep -c 'down --remove-orphans' "${DOCKER_SWITCH_LOG}")" == '5' ]] \
  || fail 'mode switch cleanup did not stop every managed Compose variant'
grep -q 'cluster=cluster-placeholder' "${DOCKER_SWITCH_LOG}" \
  || fail 'cluster Compose cleanup did not provide a parseable cluster placeholder'
grep -q 'app=postgresql://placeholder:placeholder@127.0.0.1:5432/chatgpt2api_app' "${DOCKER_SWITCH_LOG}" \
  || fail 'cluster Compose cleanup did not provide a parseable app database placeholder'
grep -q 'systemctl disable "chatgpt2api.service"' "${INSTALL_SCRIPT}" \
  || fail 'Docker mode does not disable the previous managed Python service'
grep -q 'cluster_main_cmd' "${INSTALL_SCRIPT}" \
  || fail 'cluster main command is missing'
grep -q 'cluster_worker_cmd' "${INSTALL_SCRIPT}" \
  || fail 'cluster worker command is missing'
if ! python3 - "${INSTALL_SCRIPT}" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
for function, compose_marker in (
    ("cluster_main_cmd", 'cluster_up_compose "docker-compose.cluster-main.yml" postgres'),
    ("cluster_worker_cmd", 'cluster_up_compose "docker-compose.cluster-worker.yml"'),
):
    match = re.search(rf"(?ms)^{function}\(\)\s*\{{\n.*?^\}}\n", text)
    if not match:
        raise SystemExit(f"{function} not found")
    body = match.group(0)
    if body.find("stop_python_runtime") < 0:
        raise SystemExit(f"{function} does not stop the previous managed Python runtime")
    if body.find("stop_python_runtime") > body.find(compose_marker):
        raise SystemExit(f"{function} stops Python after the cluster stack starts")
    if body.find("stop_docker_runtime") < 0:
        raise SystemExit(f"{function} does not stop the previous managed Docker stack")
    if body.find("stop_docker_runtime") > body.find(compose_marker):
        raise SystemExit(f"{function} stops Docker after the cluster stack starts")
PY
then
  fail 'cluster mode does not stop previous managed runtimes before Compose startup'
fi
if grep -Fq 'export PORT="\${PORT:-\${CHATGPT2API_PORT:-3000}}"' "${INSTALL_SCRIPT}"; then
  fail 'generated Python launcher overrides the persisted CHATGPT2API_PORT before env loading'
fi

MODE='python'
BRANCH='v2.7.1'
CHATGPT2API_IMAGE=''
CHATGPT2API_IMAGE_DIGEST=''
STORAGE_BACKEND='json'
IMAGE_QUEUE_DATABASE_URL='postgresql://queue/chatgpt2api_image_queue'
BASE_URL='http://127.0.0.1:3000'
validate_inputs
INSTALL_DIR="${TMP_DIR}/python-version-tag"
mkdir -p "${INSTALL_DIR}"
write_env_file
grep -q "^CHATGPT2API_IMAGE=$" "${INSTALL_DIR}/.env" \
  || fail 'python mode unexpectedly required or wrote a Docker image'

NONINTERACTIVE=1
INSTALL_LANG=zh
UI_IN="${TMP_DIR}/noninteractive-input"
UI_OUT="${TMP_DIR}/noninteractive-output"
: >"${UI_IN}"
: >"${UI_OUT}"
prompt_value="$(prompt_input 'noninteractive prompt' 'default-value')"
[[ "${prompt_value}" == 'default-value' ]] || fail 'noninteractive prompt did not return its default'
[[ ! -s "${UI_OUT}" ]] || fail 'noninteractive prompt wrote interactive output'
confirm 'noninteractive confirmation' 'Y' || fail 'noninteractive affirmative confirmation did not use its default'
[[ ! -s "${UI_OUT}" ]] || fail 'noninteractive confirmation wrote interactive output'

test_cli_install_target_precedence() {
  INSTALL_TARGET='api-main'
  NODE_ROLE='standalone'
  INSTALL_EXISTING='0'
  CHATGPT2API_INSTALL_TARGET=''
  CLI_INSTALL_TARGET_SET='1'
  prompt_install_target() {
    printf 'standalone'
  }
  [[ "$(resolve_install_target 0)" == 'api-main' ]] \
    || fail 'explicit --install-target was replaced by the interactive target prompt'
  unset -f prompt_install_target
}
test_cli_install_target_precedence

test_first_worker_defaults_are_safe() {
  INSTALL_EXISTING='0'
  CREATE_FIRST_WORKER=''
  NONINTERACTIVE='1'
  assert_command_fails 'noninteractive first Worker creation default' resolve_create_first_worker

  INSTALL_EXISTING='1'
  NONINTERACTIVE='0'
  assert_command_fails 'rerun first Worker creation default' resolve_create_first_worker

  INSTALL_EXISTING='0'
  NONINTERACTIVE='1'
  CREATE_FIRST_WORKER='1'
  assert_command_passes 'explicit noninteractive first Worker creation' resolve_create_first_worker
  CREATE_FIRST_WORKER=''
}
test_first_worker_defaults_are_safe

test_legacy_storage_backend_inference() {
  INSTALL_DIR="${TMP_DIR}/legacy-sqlite"
  mkdir -p "${INSTALL_DIR}/data"
  DATABASE_URL='sqlite:////app/data/accounts.db'
  STORAGE_BACKEND=''
  [[ "$(infer_existing_storage_backend)" == 'sqlite' ]] \
    || fail 'legacy SQLite install was not inferred as sqlite'

  INSTALL_DIR="${TMP_DIR}/legacy-json"
  mkdir -p "${INSTALL_DIR}/data"
  : >"${INSTALL_DIR}/data/accounts.json"
  DATABASE_URL=''
  STORAGE_BACKEND=''
  [[ "$(infer_existing_storage_backend)" == 'json' ]] \
    || fail 'legacy JSON install was not inferred as json'
}
test_legacy_storage_backend_inference

REPO_OWNER='custom-owner'
REPO_NAME='custom-name'
BRANCH="${DEFAULT_RELEASE_REF}"
CHATGPT2API_IMAGE=''
CHATGPT2API_IMAGE_DIGEST=''
assert_command_fails 'custom repository implicit digest' default_image
REPO_OWNER='biubiubiu125'
REPO_NAME='chatgpt2api'

DATABASE_FAIL_BIN="${TMP_DIR}/database-fail-bin"
mkdir -p "${DATABASE_FAIL_BIN}"
cat >"${DATABASE_FAIL_BIN}/docker" <<'EOF'
#!/usr/bin/env bash
exit 42
EOF
chmod +x "${DATABASE_FAIL_BIN}/docker"
OLD_PATH="${PATH}"
PATH="${DATABASE_FAIL_BIN}:${PATH}"
POSTGRES_PASSWORD='runtime-password'
assert_command_fails 'database worker preflight query failure' cluster_check_worker_database_record worker-1
PATH="${OLD_PATH}"

grep -q 'cluster_resolve_worker_join_file' "${INSTALL_SCRIPT}" \
  || fail 'worker join file resolver is missing'
grep -q 'JOIN_RELEASE_REF' "${INSTALL_SCRIPT}" \
  || fail 'worker join file does not carry release metadata'
grep -q 'public_key_tmp=' "${INSTALL_SCRIPT}" \
  || fail 'join signing public key is not regenerated from the private key'
grep -q 'port 5432 proto tcp' "${INSTALL_SCRIPT}" \
  || fail 'cluster firewall does not handle PostgreSQL TCP'
grep -q 'different listen port' "${INSTALL_SCRIPT}" \
  || fail 'managed WireGuard config does not validate listen port'
grep -q "trap 'cluster_join_transaction_cleanup; exit 1' INT TERM ERR" "${INSTALL_SCRIPT}" \
  || fail 'cluster join transaction has no signal cleanup trap'
grep -q 'cluster_worker_activation_cleanup_on_signal' "${INSTALL_SCRIPT}" \
  || fail 'worker activation has no signal cleanup trap'
grep -q 'WORKER_JOIN_CONSUMED' "${INSTALL_SCRIPT}" \
  || fail 'worker activation does not track whether the join token was consumed'
grep -q 'cluster_worker_check_cmd --skip-public-delivery' "${INSTALL_SCRIPT}" \
  || fail 'worker activation does not defer public delivery verification until after join activation'
grep -q 'From this point the database token is joined' "${INSTALL_SCRIPT}" \
  || fail 'worker activation does not track the joined database transition before marker writes'
grep -q 'CLUSTER_JOIN_TRANSACTION_PEER_ADDED="1"' "${INSTALL_SCRIPT}" \
  || fail 'cluster join transaction does not guard peer creation during interruption'
grep -q 'relying on WireGuard handshake and database checks' "${INSTALL_SCRIPT}" \
  || fail 'worker-check still treats ICMP ping as a mandatory dependency'
grep -q 'cluster_run_join_payload_python' "${INSTALL_SCRIPT}" \
  || fail 'cluster join payload does not use the stdin-only execution helper'
[[ "$(grep -c -- '-e CHATGPT2API_JOIN_PAYLOAD_JSON' "${INSTALL_SCRIPT}")" -eq 0 ]] \
  || fail 'cluster join payload is exposed through docker compose environment'
grep -q 'printf.*payload_json' "${INSTALL_SCRIPT}" \
  || fail 'cluster join payload is not streamed through stdin'
grep -q 'JOIN_VERSION' "${INSTALL_SCRIPT}" \
  || fail 'join file version is not parsed and validated'
grep -q -- '--create-first-worker' "${INSTALL_SCRIPT}" \
  || fail 'explicit first Worker creation flag is missing'
grep -q -- '--no-first-worker' "${INSTALL_SCRIPT}" \
  || fail 'explicit first Worker suppression flag is missing'
grep -q 'API 存活.*Worker' "${INSTALL_SCRIPT}" \
  || fail 'cluster main summary does not distinguish API liveness from Worker runtime readiness'
grep -q '^# DATABASE_URL=' "${ROOT_DIR}/.env.example" \
  || fail 'PostgreSQL example DATABASE_URL is active instead of being an explicit example'
grep -q '^# IMAGE_QUEUE_DATABASE_URL=' "${ROOT_DIR}/.env.example" \
  || fail 'PostgreSQL queue example URL is active instead of being an explicit example'

test_join_payload_helper_streams_stdin() {
  INSTALL_DIR="${TMP_DIR}/join-helper"
  mkdir -p "${INSTALL_DIR}"
  local fake_bin="${TMP_DIR}/fake-docker-bin"
  local args_file="${TMP_DIR}/docker-args.txt"
  local stdin_file="${TMP_DIR}/docker-stdin.txt"
  mkdir -p "${fake_bin}"
  cat >"${fake_bin}/docker" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$@" > "${args_file}"
cat > "${stdin_file}"
EOF
  chmod +x "${fake_bin}/docker"
  OLD_PATH="${PATH}"
  PATH="${fake_bin}:${PATH}"
  APP_DATABASE_URL='postgresql://runtime:password@db/chatgpt2api_app'
  if ! cluster_run_join_payload_python docker-compose.cluster-main.yml exec issue '{"token":"abc","worker_id":"worker-1"}'; then
    PATH="${OLD_PATH}"
    fail 'join payload helper did not complete with the fake docker binary'
  fi
  PATH="${OLD_PATH}"
  grep -q 'CHATGPT2API_JOIN_PAYLOAD_JSON' "${args_file}" \
    && fail 'join payload helper still exposes the payload through an environment variable'
  [[ "$(cat "${stdin_file}")" == '{"token":"abc","worker_id":"worker-1"}' ]] \
    || fail 'join payload helper did not stream the payload through stdin'
}
test_join_payload_helper_streams_stdin
if ! python3 - "${INSTALL_SCRIPT}" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text(encoding='utf-8')
match = re.search(r'(?ms)^cluster_worker_cmd\(\)\s*\{\n.*?^\}\n', text)
if not match:
    raise SystemExit('cluster_worker_cmd not found')
worker_cmd = match.group(0)
skip_check = worker_cmd.find('cluster_worker_check_cmd --skip-public-delivery')
activate = worker_cmd.find('cluster_activate_join_token')
final_check = worker_cmd.rfind('cluster_worker_check_cmd')
if skip_check < 0 or activate < 0 or final_check < 0:
    raise SystemExit('worker activation check ordering markers are missing')
if not skip_check < activate < final_check:
    raise SystemExit('worker public delivery check is not deferred until after join activation')

match = re.search(r'(?ms)^cluster_worker_preflight_cmd\(\)\s*\{\n.*?^\}\n', text)
if not match:
    raise SystemExit('cluster_worker_preflight_cmd not found')
if 'CHATGPT2API_JOIN_PAYLOAD_JSON' in match.group(0):
    raise SystemExit('preflight still references join payload env')
PY
then
  fail 'worker database preflight still depends on the join payload env'
fi

IMAGE_BASE_URL='http://198.51.100.10:3000/images'
IMAGE_PORT='3000'
CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE='direct'
assert_command_passes 'direct worker public entry' cluster_confirm_worker_image_proxy
[[ "${CHATGPT2API_WORKER_BIND_HOST}" == '0.0.0.0' ]] \
  || fail 'direct worker public entry did not bind the public host'

IMAGE_BASE_URL='http://198.51.100.10:3000'
IMAGE_PORT='3000'
CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE='direct'
assert_command_passes 'direct worker root entry' cluster_confirm_worker_image_proxy
[[ "${CHATGPT2API_WORKER_BIND_HOST}" == '0.0.0.0' ]] \
  || fail 'direct worker root entry did not bind the public host'

IMAGE_BASE_URL='https://img.example.com/images'
IMAGE_PORT='3000'
CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE='direct'
assert_command_fails 'direct worker HTTPS entry' cluster_confirm_worker_image_proxy

IMAGE_BASE_URL='https://img.example.com/foo/images'
IMAGE_PORT='3000'
CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE='proxy'
assert_command_fails 'proxy worker custom path' cluster_confirm_worker_image_proxy

IMAGE_BASE_URL='https://img.example.com/images'
IMAGE_PORT='3000'
CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE='proxy'
assert_command_passes 'proxy worker public entry' cluster_confirm_worker_image_proxy
[[ "${CHATGPT2API_WORKER_BIND_HOST}" == '127.0.0.1' ]] \
  || fail 'proxy worker public entry did not keep the container private'

[[ "$(normalize_repo_remote 'git@github.com:biubiubiu125/chatgpt2api.git')" == 'biubiubiu125/chatgpt2api' ]] \
  || fail 'Git SSH origin normalization is incorrect'
[[ "$(normalize_repo_remote 'https://github.com/biubiubiu125/chatgpt2api.git')" == 'biubiubiu125/chatgpt2api' ]] \
  || fail 'Git HTTPS origin normalization is incorrect'

JOIN_FIXTURE="${TMP_DIR}/worker-missing-port.join"
cat >"${JOIN_FIXTURE}" <<'EOF'
VERSION=1
WORKER_ID=worker-1
WORKER_NO=1
WIREGUARD_IP=10.77.0.11
WIREGUARD_SERVER_IP=10.77.0.1
WIREGUARD_SERVER_ENDPOINT=main.example.com
WIREGUARD_SERVER_PUBLIC_KEY=server-public
WIREGUARD_WORKER_PRIVATE_KEY=worker-private
WIREGUARD_WORKER_PUBLIC_KEY=worker-public
APP_DATABASE_URL=postgresql://runtime:password@10.77.0.1:5432/chatgpt2api_app
IMAGE_QUEUE_DATABASE_URL=postgresql://runtime:password@10.77.0.1:5432/chatgpt2api_image_queue
TOKEN=token
CLUSTER_ID=cluster-1
JOIN_NONCE=nonce
EXPIRES_AT=9999999999
SIGNING_PUBLIC_KEY_B64=public-key
CHATGPT2API_RELEASE_REF=d887be015b77abfcfc210814a4ed125b8a3cb8b0
CHATGPT2API_IMAGE=ghcr.io/biubiubiu125/chatgpt2api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
CHATGPT2API_IMAGE_DIGEST=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
UV_VERSION=0.8.17
EOF
assert_command_fails 'join file missing WireGuard port' cluster_read_join_file "${JOIN_FIXTURE}"

sed 's/^VERSION=1$/VERSION=2/' "${JOIN_FIXTURE}" >"${JOIN_FIXTURE}.v2"
assert_command_fails 'join file invalid version' cluster_read_join_file "${JOIN_FIXTURE}.v2"

test_normalize_install_target() {
  [[ "$(normalize_install_target '1')" == 'standalone' ]] \
    || fail 'install target 1 was not normalized to standalone'
  [[ "$(normalize_install_target 'main')" == 'api-main' ]] \
    || fail 'main install target was not normalized to api-main'
  [[ "$(normalize_install_target '3')" == 'worker' ]] \
    || fail 'install target 3 was not normalized to worker'
}

assert_command_passes 'standalone deployment target normalization' test_normalize_install_target

test_new_install_defaults_to_postgres() {
  INSTALL_EXISTING='0'
  STORAGE_BACKEND='json'
  apply_install_storage_defaults
  [[ "${STORAGE_BACKEND}" == 'postgres' ]] || fail 'new install did not force PostgreSQL storage'
}

assert_command_passes 'new install PostgreSQL storage default' test_new_install_defaults_to_postgres

test_manual_auth_key_is_required() {
  PORT='3000'
  MODE='docker'
  STORAGE_BACKEND='postgres'
  THREAD_TOKENS='1'
  AUTH_KEY=''
  DATABASE_URL='postgresql://user:password@db/chatgpt2api_app'
  IMAGE_QUEUE_DATABASE_URL='postgresql://user:password@db/chatgpt2api_image_queue'
  BASE_URL='http://127.0.0.1:3000'
  IMAGE_BASE_URL=''
  NODE_ROLE='standalone'
  INSTALL_TARGET='standalone'
  validate_inputs
}

assert_command_fails 'manual administrator auth key is required' test_manual_auth_key_is_required

test_install_summary_contains_credentials() {
  INSTALL_TARGET='standalone'
  NODE_ROLE='standalone'
  PORT='3000'
  AUTH_KEY='manual-admin-key'
  DATABASE_URL='postgresql://user:password@db/chatgpt2api_app'
  APP_DATABASE_URL="${DATABASE_URL}"
  IMAGE_QUEUE_DATABASE_URL='postgresql://user:password@db/chatgpt2api_image_queue'
  BASE_URL='https://api.example.com'
  INSTALL_DIR='/opt/chatgpt2api'
  local output=''
  output="$(print_install_summary)"
  [[ "${output}" == *'manual-admin-key'* ]] || fail 'install summary omitted administrator auth key'
  [[ "${output}" == *'postgresql://user:password@db/chatgpt2api_app'* ]] \
    || fail 'install summary omitted PostgreSQL DATABASE_URL'
  [[ "${output}" == *'postgresql://user:password@db/chatgpt2api_image_queue'* ]] \
    || fail 'install summary omitted image queue DATABASE_URL'
}

assert_command_passes 'install summary contains credentials' test_install_summary_contains_credentials

grep -Fq 'STORAGE_BACKEND="${STORAGE_BACKEND:-postgres}"' "${INSTALL_SCRIPT}" \
  || fail 'installer default storage backend is not PostgreSQL'
grep -q 'prompt_install_target' "${INSTALL_SCRIPT}" \
  || fail 'interactive installer has no deployment target prompt'

printf '[install-script] all assertions passed\n'
