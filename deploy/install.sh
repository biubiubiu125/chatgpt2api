#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="${REPO_OWNER:-biubiubiu125}"
REPO_NAME="${REPO_NAME:-chatgpt2api}"
DEFAULT_RELEASE_REF="d887be015b77abfcfc210814a4ed125b8a3cb8b0"
DEFAULT_CHATGPT2API_IMAGE_DIGEST="sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a"
DEFAULT_CHATGPT2API_WARP_IMAGE="caomingjun/warp@sha256:da12ba946c7e44665ef25de1fc7d22ef432a9fa8b71fa32dc7790e1b5f27cd7f"
DEFAULT_CHATGPT2API_PRIVOXY_IMAGE="vimagick/privoxy@sha256:8db03d3e5a36800e2c7e32f17b47e21e18f476bf492f0a50e2fc43073f6bb21f"
DEFAULT_CHATGPT2API_FLARESOLVERR_IMAGE="flaresolverr/flaresolverr@sha256:139dfee1c6f89249c8d665d1333a42e8ec74ec0a86bc6bb1c8461e10d3a66a47"
INITIAL_BRANCH_VALUE="${BRANCH-}"
INITIAL_RELEASE_REF_VALUE="${CHATGPT2API_RELEASE_REF-}"
ENV_RELEASE_REF_SET="0"
ENV_RELEASE_REF_VALUE=""
if [[ -n "${INITIAL_BRANCH_VALUE}" ]]; then
  ENV_RELEASE_REF_SET="1"
  ENV_RELEASE_REF_VALUE="${INITIAL_BRANCH_VALUE}"
elif [[ -n "${INITIAL_RELEASE_REF_VALUE}" ]]; then
  ENV_RELEASE_REF_SET="1"
  ENV_RELEASE_REF_VALUE="${INITIAL_RELEASE_REF_VALUE}"
fi
BRANCH="${BRANCH:-${CHATGPT2API_RELEASE_REF:-${DEFAULT_RELEASE_REF}}}"
INSTALL_DIR="${INSTALL_DIR:-/opt/chatgpt2api}"
PORT="${CHATGPT2API_PORT:-${PORT:-3000}}"
THREAD_TOKENS="${CHATGPT2API_THREAD_TOKENS:-${THREAD_TOKENS:-80}}"
BASE_URL="${CHATGPT2API_BASE_URL:-${BASE_URL:-}}"
IMAGE_BASE_URL="${CHATGPT2API_IMAGE_BASE_URL:-${IMAGE_BASE_URL:-}}"
PYTHON_BIN="${CHATGPT2API_PYTHON_BIN:-${PYTHON_BIN:-python3}}"
IMAGE_PORT="${CHATGPT2API_IMAGE_PORT:-${IMAGE_PORT:-3000}}"
NODE_ROLE="${CHATGPT2API_NODE_ROLE:-${NODE_ROLE:-standalone}}"
INSTALL_TARGET="${CHATGPT2API_INSTALL_TARGET:-${INSTALL_TARGET:-}}"
INSTALL_EXISTING="${INSTALL_EXISTING:-0}"
CREATE_FIRST_WORKER="${CHATGPT2API_CREATE_FIRST_WORKER:-${CREATE_FIRST_WORKER:-}}"
RUN_API="${CHATGPT2API_RUN_API:-${RUN_API:-}}"
RUN_WORKER="${CHATGPT2API_RUN_WORKER:-${RUN_WORKER:-}}"
WORKER_ID="${CHATGPT2API_WORKER_ID:-${WORKER_ID:-}}"
CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE="${CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE:-direct}"
CHATGPT2API_WORKER_BIND_HOST="${CHATGPT2API_WORKER_BIND_HOST:-127.0.0.1}"
WIREGUARD_IP="${CHATGPT2API_WIREGUARD_IP:-${WIREGUARD_IP:-}}"
WIREGUARD_INTERFACE="${CHATGPT2API_WIREGUARD_INTERFACE:-${WIREGUARD_INTERFACE:-wg-chatgpt2api}}"
WIREGUARD_SERVER_IP="${WIREGUARD_SERVER_IP:-10.77.0.1}"
WIREGUARD_SERVER_ENDPOINT="${WIREGUARD_SERVER_ENDPOINT:-}"
WIREGUARD_PORT="${WIREGUARD_PORT:-51820}"
CLUSTER_ID="${CHATGPT2API_CLUSTER_ID:-${CLUSTER_ID:-}}"
MODE="${MODE:-}"
WITH_WARP="${WITH_WARP:-0}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"
AUTH_KEY="${CHATGPT2API_AUTH_KEY:-${AUTH_KEY:-}}"
CHATGPT2API_CONFIG_FILE="${CHATGPT2API_CONFIG_FILE:-}"
CHATGPT2API_BACKUP_PASSPHRASE="${CHATGPT2API_BACKUP_PASSPHRASE:-}"
CHATGPT2API_MONITOR_COMPLETED_LIMIT="${CHATGPT2API_MONITOR_COMPLETED_LIMIT:-}"
CHATGPT2API_MONITOR_EVENT_LIMIT="${CHATGPT2API_MONITOR_EVENT_LIMIT:-}"
CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS="${CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS:-}"
CHATGPT2API_RUNTIME_LOG_FILE="${CHATGPT2API_RUNTIME_LOG_FILE:-}"
HOST="${HOST:-0.0.0.0}"
LOG_LEVEL="${LOG_LEVEL:-info}"
UVICORN_WORKERS="${UVICORN_WORKERS:-}"
STORAGE_BACKEND="${STORAGE_BACKEND:-postgres}"
APP_DATABASE_URL="${APP_DATABASE_URL:-}"
DATABASE_URL="${DATABASE_URL:-}"
IMAGE_QUEUE_DATABASE_URL="${IMAGE_QUEUE_DATABASE_URL:-}"
IMAGE_QUEUE_INSTANCE_ID="${IMAGE_QUEUE_INSTANCE_ID:-}"
IMAGE_QUEUE_VERIFY_RETURNED_URL="${IMAGE_QUEUE_VERIFY_RETURNED_URL:-true}"
IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS:-5}"
IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS:-3}"
IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES="${IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES:-65536}"
IMAGE_PROMPT_SUFFIX_ENABLED="${IMAGE_PROMPT_SUFFIX_ENABLED:-true}"
IMAGE_PROMPT_SUFFIX="${IMAGE_PROMPT_SUFFIX:-}"
IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS="${IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS:-900}"
IMAGE_QUEUE_DELIVERY_GRACE_SECONDS="${IMAGE_QUEUE_DELIVERY_GRACE_SECONDS:-604800}"
IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS="${IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS:-2592000}"
IMAGE_QUEUE_STARTUP_RETRY_SECONDS="${IMAGE_QUEUE_STARTUP_RETRY_SECONDS:-5}"
IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS="${IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS:-300}"
IMAGE_QUEUE_GENERATION_CONCURRENCY="${IMAGE_QUEUE_GENERATION_CONCURRENCY:-}"
IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP="${IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP:-99999}"
IMAGE_QUEUE_ABSOLUTE_GUARD="${IMAGE_QUEUE_ABSOLUTE_GUARD:-}"
IMAGE_QUEUE_MAX_BACKLOG="${IMAGE_QUEUE_MAX_BACKLOG:-50}"
IMAGE_QUEUE_PENDING_TTL_SECONDS="${IMAGE_QUEUE_PENDING_TTL_SECONDS:-1800}"
IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS="${IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS:-1800}"
IMAGE_QUEUE_LEGACY_TASK_PATH="${IMAGE_QUEUE_LEGACY_TASK_PATH:-}"
IMAGE_QUEUE_LEASE_SECONDS="${IMAGE_QUEUE_LEASE_SECONDS:-}"
IMAGE_QUEUE_HEARTBEAT_SECONDS="${IMAGE_QUEUE_HEARTBEAT_SECONDS:-}"
IMAGE_QUEUE_POLL_INTERVAL_SECONDS="${IMAGE_QUEUE_POLL_INTERVAL_SECONDS:-}"
IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS="${IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS:-}"
IMAGE_QUEUE_GENERATION_ATTEMPTS="${IMAGE_QUEUE_GENERATION_ATTEMPTS:-}"
IMAGE_QUEUE_DOWNLOAD_ATTEMPTS="${IMAGE_QUEUE_DOWNLOAD_ATTEMPTS:-}"
IMAGE_QUEUE_SAVE_ATTEMPTS="${IMAGE_QUEUE_SAVE_ATTEMPTS:-}"
IMAGE_QUEUE_CPU_THROTTLE_PERCENT="${IMAGE_QUEUE_CPU_THROTTLE_PERCENT:-}"
IMAGE_QUEUE_CPU_PAUSE_PERCENT="${IMAGE_QUEUE_CPU_PAUSE_PERCENT:-}"
IMAGE_QUEUE_CPU_RESUME_PERCENT="${IMAGE_QUEUE_CPU_RESUME_PERCENT:-}"
IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT="${IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT:-}"
IMAGE_QUEUE_MEMORY_PAUSE_PERCENT="${IMAGE_QUEUE_MEMORY_PAUSE_PERCENT:-}"
IMAGE_QUEUE_MEMORY_REJECT_PERCENT="${IMAGE_QUEUE_MEMORY_REJECT_PERCENT:-}"
IMAGE_QUEUE_DB_POOL_SIZE="${IMAGE_QUEUE_DB_POOL_SIZE:-}"
IMAGE_QUEUE_DB_MAX_OVERFLOW="${IMAGE_QUEUE_DB_MAX_OVERFLOW:-}"
EDITABLE_FILE_WORKERS="${EDITABLE_FILE_WORKERS:-}"
EDITABLE_FILE_MAX_BACKLOG="${EDITABLE_FILE_MAX_BACKLOG:-}"
PROMPT_LIBRARY_DEFAULT_URL="${PROMPT_LIBRARY_DEFAULT_URL:-}"
PROMPT_LIBRARY_REMOTE_URL="${PROMPT_LIBRARY_REMOTE_URL:-}"
CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS="${CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS:-30}"
CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS="${CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS:-2}"
CHATGPT2API_PROXY_RUNTIME_ENABLED="${CHATGPT2API_PROXY_RUNTIME_ENABLED:-}"
CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE="${CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE:-}"
CHATGPT2API_PROXY_RUNTIME_PROXY_URL="${CHATGPT2API_PROXY_RUNTIME_PROXY_URL:-}"
CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL="${CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL:-}"
CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY="${CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY:-}"
CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES="${CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES:-}"
CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED:-}"
CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE:-}"
CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC:-}"
CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL:-}"
CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START="${CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START:-}"
CHATGPT2API_PROXY_RUNTIME_BROWSER="${CHATGPT2API_PROXY_RUNTIME_BROWSER:-}"
CHATGPT2API_PROXY_RUNTIME_USER_AGENT="${CHATGPT2API_PROXY_RUNTIME_USER_AGENT:-}"
CHATGPT2API_FLARESOLVERR_URL="${CHATGPT2API_FLARESOLVERR_URL:-}"
WARP_LICENSE_KEY="${WARP_LICENSE_KEY:-}"
WARP_SOCKS_PORT="${WARP_SOCKS_PORT:-40000}"
PRIVOXY_PORT="${PRIVOXY_PORT:-40080}"
FLARESOLVERR_PORT="${FLARESOLVERR_PORT:-8191}"
FLARESOLVERR_LOG_LEVEL="${FLARESOLVERR_LOG_LEVEL:-info}"
TZ="${TZ:-Asia/Shanghai}"
CHATGPT2API_PYTHON_PID_FILE="${CHATGPT2API_PYTHON_PID_FILE:-}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
POSTGRES_ADMIN_USER="${POSTGRES_ADMIN_USER:-chatgpt2api_admin}"
POSTGRES_ADMIN_PASSWORD="${POSTGRES_ADMIN_PASSWORD:-}"
INSTALL_LANG="${INSTALL_LANG:-}"
CHATGPT2API_IMAGE="${CHATGPT2API_IMAGE:-}"
CHATGPT2API_IMAGE_DIGEST="${CHATGPT2API_IMAGE_DIGEST:-}"
CHATGPT2API_WARP_IMAGE="${CHATGPT2API_WARP_IMAGE:-${DEFAULT_CHATGPT2API_WARP_IMAGE}}"
CHATGPT2API_PRIVOXY_IMAGE="${CHATGPT2API_PRIVOXY_IMAGE:-${DEFAULT_CHATGPT2API_PRIVOXY_IMAGE}}"
CHATGPT2API_FLARESOLVERR_IMAGE="${CHATGPT2API_FLARESOLVERR_IMAGE:-${DEFAULT_CHATGPT2API_FLARESOLVERR_IMAGE}}"
UV_VERSION="${UV_VERSION:-0.8.17}"
GIT_REPO_URL="${GIT_REPO_URL:-}"
GIT_TOKEN="${GIT_TOKEN:-}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_FILE_PATH="${GIT_FILE_PATH:-accounts.json}"
GIT_AUTH_KEYS_FILE_PATH="${GIT_AUTH_KEYS_FILE_PATH:-auth_keys.json}"
CLI_BRANCH_SET="${CLI_BRANCH_SET:-0}"
CLI_BRANCH_VALUE="${CLI_BRANCH_VALUE:-}"
CLI_INSTALL_TARGET_SET="${CLI_INSTALL_TARGET_SET:-0}"
RELEASE_REF_SELECTED="${RELEASE_REF_SELECTED:-0}"

UI_IN="${UI_IN:-/dev/tty}"
UI_OUT="${UI_OUT:-/dev/tty}"
if [[ ! -r "${UI_IN}" ]]; then
  UI_IN="/dev/stdin"
fi
if [[ ! -w "${UI_OUT}" ]]; then
  UI_OUT="/dev/stderr"
fi

usage() {
  printf '%s\n\n' "$(text usage_title)"
  printf '%s\n' "$(text usage_usage)"
  cat <<'EOF'
  bash deploy/install.sh
  bash deploy/install.sh main
  bash deploy/install.sh worker
  bash deploy/install.sh create-worker 2
  bash deploy/install.sh rotate-worker 2
  bash deploy/install.sh status
  bash deploy/install.sh worker-check
  curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/d887be015b77abfcfc210814a4ed125b8a3cb8b0/deploy/install.sh -o /tmp/chatgpt2api-install.sh
  sudo bash /tmp/chatgpt2api-install.sh
EOF

  printf '\n%s\n' "$(text usage_env)"
  cat <<'EOF'
  INSTALL_DIR=/opt/chatgpt2api
  PORT=3000
  CHATGPT2API_THREAD_TOKENS=80
  CHATGPT2API_BASE_URL=https://api.example.com
  CHATGPT2API_IMAGE_BASE_URL=https://img-1.example.com/images
  CHATGPT2API_IMAGE_PORT=3000
  CHATGPT2API_NODE_ROLE=standalone|api-main|worker
  CHATGPT2API_INSTALL_TARGET=standalone|api-main|worker
  CHATGPT2API_CREATE_FIRST_WORKER=0|1
  CHATGPT2API_RUN_API=true|false
  CHATGPT2API_RUN_WORKER=true|false
  CHATGPT2API_WORKER_ID=worker-1
  CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE=direct|proxy
  CHATGPT2API_WORKER_BIND_HOST=0.0.0.0|127.0.0.1
  CHATGPT2API_WIREGUARD_IP=10.77.0.11
  WIREGUARD_SERVER_ENDPOINT=main.example.com
  WIREGUARD_SERVER_IP=10.77.0.1
  WIREGUARD_PORT=51820
  WIREGUARD_INTERFACE=wg-chatgpt2api
  APP_DATABASE_URL=postgresql://...
  MODE=docker|python
  WITH_WARP=0|1
  NONINTERACTIVE=0|1
  AUTH_KEY=your-auth-key
  STORAGE_BACKEND=postgres
  DATABASE_URL=postgresql://...
  IMAGE_QUEUE_DATABASE_URL=postgresql+psycopg2://...
  IMAGE_QUEUE_INSTANCE_ID=worker-1-join-nonce
  IMAGE_QUEUE_VERIFY_RETURNED_URL=true
  IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS=5
  IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS=3
  IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES=65536
  POSTGRES_PASSWORD=strong-password-for-compose-postgres
  POSTGRES_ADMIN_USER=chatgpt2api_admin
  POSTGRES_ADMIN_PASSWORD=main-node-only-admin-password
  INSTALL_LANG=zh|en
  CHATGPT2API_RELEASE_REF=d887be015b77abfcfc210814a4ed125b8a3cb8b0
  CHATGPT2API_IMAGE=ghcr.io/biubiubiu125/chatgpt2api@sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a
  CHATGPT2API_IMAGE_DIGEST=sha256:c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a
  CHATGPT2API_WARP_IMAGE=caomingjun/warp@sha256:da12ba946c7e44665ef25de1fc7d22ef432a9fa8b71fa32dc7790e1b5f27cd7f
  CHATGPT2API_PRIVOXY_IMAGE=vimagick/privoxy@sha256:8db03d3e5a36800e2c7e32f17b47e21e18f476bf492f0a50e2fc43073f6bb21f
  CHATGPT2API_FLARESOLVERR_IMAGE=flaresolverr/flaresolverr@sha256:139dfee1c6f89249c8d665d1333a42e8ec74ec0a86bc6bb1c8461e10d3a66a47
  GIT_REPO_URL=https://github.com/your/private-storage.git
  GIT_TOKEN=ghp_xxx
  GIT_BRANCH=main
  GIT_FILE_PATH=accounts.json
  GIT_AUTH_KEYS_FILE_PATH=auth_keys.json
EOF

  printf '\n%s\n' "$(text usage_flags)"
  cat <<'EOF'
  --mode docker|python
  --port 3000
  --thread-tokens 80
  --base-url https://api.example.com
  --install-dir /opt/chatgpt2api
  --branch d887be015b77abfcfc210814a4ed125b8a3cb8b0
  --auth-key your-auth-key
  --install-target standalone|api-main|worker
  --create-first-worker
  --no-first-worker
  --storage-backend postgres
  --database-url postgresql://...
  --image-queue-database-url postgresql+psycopg2://...
  --git-repo-url https://github.com/your/private-storage.git
  --git-token ghp_xxx
  --git-branch main
  --git-file-path accounts.json
  --git-auth-keys-file-path auth_keys.json
  --with-warp
  --without-warp
  --non-interactive
  --repo-owner biubiubiu125
  --repo-name chatgpt2api
  -h, --help
EOF
}

ui_print() {
  case "${UI_OUT}" in
    "/dev/stdout") printf '%s' "$*" ;;
    "/dev/stderr") printf '%s' "$*" >&2 ;;
    *) printf '%s' "$*" >"${UI_OUT}" ;;
  esac
}

ui_println() {
  case "${UI_OUT}" in
    "/dev/stdout") printf '%s\n' "$*" ;;
    "/dev/stderr") printf '%s\n' "$*" >&2 ;;
    *) printf '%s\n' "$*" >"${UI_OUT}" ;;
  esac
}

is_en() {
  [[ "${INSTALL_LANG}" =~ ^([Ee][Nn]|[Ee]nglish)$ ]]
}

is_noninteractive() {
  [[ "${NONINTERACTIVE}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]
}

release_ref_override_active() {
  [[ "${CLI_BRANCH_SET}" == "1" || "${ENV_RELEASE_REF_SET}" == "1" ]]
}

release_ref_override_value() {
  if [[ "${CLI_BRANCH_SET}" == "1" ]]; then
    printf '%s' "${CLI_BRANCH_VALUE}"
  else
    printf '%s' "${ENV_RELEASE_REF_VALUE}"
  fi
}

normalize_language() {
  case "${INSTALL_LANG}" in
    en|EN|english|English|英文) INSTALL_LANG="en" ;;
    *) INSTALL_LANG="zh" ;;
  esac
}

choose_language() {
  if [[ -n "${INSTALL_LANG}" ]]; then
    normalize_language
    return
  fi
  if is_noninteractive; then
    INSTALL_LANG="zh"
    normalize_language
    return
  fi

  local answer=""
  ui_println "界面语言 / Language"
  ui_println "  1) 中文（默认）"
  ui_println "  2) English"
  answer="$(prompt_input "请选择 / Select" "1")"
  case "${answer}" in
    2|en|EN|english|English) INSTALL_LANG="en" ;;
    *) INSTALL_LANG="zh" ;;
  esac
  normalize_language
}

text() {
  local key="$1"
  if is_en; then
    case "${key}" in
      usage_title) printf 'ChatGPT2API installer' ;;
      usage_usage) printf 'Usage:' ;;
      usage_env) printf 'Environment overrides:' ;;
      usage_flags) printf 'Flags:' ;;
      prefix_error) printf 'ERROR' ;;
      prefix_info) printf 'INFO' ;;
      prefix_warn) printf 'WARN' ;;
      prefix_done) printf 'OK' ;;
      err_missing_cmd) printf 'Missing command' ;;
      err_unknown_arg) printf 'Unknown argument' ;;
      err_mode) printf 'MODE must be docker or python.' ;;
      err_storage) printf 'STORAGE_BACKEND must be json, sqlite, postgres or git.' ;;
      err_install_target) printf 'INSTALL_TARGET must be standalone, api-main or worker.' ;;
      err_auth_key) printf 'A manually entered administrator auth key is required.' ;;
      err_port) printf 'PORT must be a number.' ;;
      err_thread_tokens) printf 'CHATGPT2API_THREAD_TOKENS must be a positive number.' ;;
      err_not_git) printf 'exists but is not a git repository.' ;;
      err_compose) printf 'docker compose plugin not found. Please install Docker Compose v2 first.' ;;
      info_update) printf 'Updating' ;;
      info_clone) printf 'Cloning' ;;
      info_start_docker) printf 'Starting Docker service...' ;;
      info_install_uv) printf 'uv not found, installing...' ;;
      warn_no_npm) printf 'npm not found; frontend build cannot continue.' ;;
      info_build_vue) printf 'Building Vue console...' ;;
      info_install_py) printf 'Installing Python dependencies...' ;;
      info_start_app) printf 'Starting ChatGPT2API on' ;;
      prompt_mode) printf 'Run mode: docker or python' ;;
      prompt_port) printf 'Web/API port' ;;
      prompt_thread_tokens) printf 'Backend thread tokens' ;;
      prompt_dir) printf 'Install directory' ;;
      prompt_branch) printf 'Release commit SHA (Docker) or version tag (Python)' ;;
      prompt_storage) printf 'Storage backend' ;;
      prompt_auth) printf 'Admin auth key' ;;
      prompt_install_target) printf 'Deployment target' ;;
      prompt_warp) printf 'Enable WARP / Privoxy / FlareSolverr compose' ;;
      done_ready) printf 'ChatGPT2API is ready' ;;
      done_auth) printf 'Admin auth key' ;;
      summary_target) printf 'Deployment target' ;;
      summary_url) printf 'Public URL' ;;
      summary_database) printf 'PostgreSQL DATABASE_URL' ;;
      summary_queue_database) printf 'Image queue DATABASE_URL' ;;
      prompt_confirm_install) printf 'Start installation with the above settings' ;;
      done_cancelled) printf 'Installation cancelled' ;;
      *) printf '%s' "${key}" ;;
    esac
    return
  fi

  case "${key}" in
    err_thread_tokens) printf 'CHATGPT2API_THREAD_TOKENS 必须是正整数。' ;;
    prompt_thread_tokens) printf '后端线程池容量' ;;
    usage_title) printf 'ChatGPT2API 安装脚本' ;;
    usage_usage) printf '用法：' ;;
    usage_env) printf '可用环境变量：' ;;
    usage_flags) printf '可用参数：' ;;
    prefix_error) printf '错误' ;;
    prefix_info) printf '信息' ;;
    prefix_warn) printf '警告' ;;
    prefix_done) printf '完成' ;;
    err_missing_cmd) printf '缺少命令' ;;
    err_unknown_arg) printf '未知参数' ;;
    err_mode) printf '运行模式只能是 docker 或 python。' ;;
    err_storage) printf '存储后端只能是 json、sqlite、postgres 或 git。' ;;
    err_install_target) printf '安装端只能是 standalone、api-main 或 worker。' ;;
    err_auth_key) printf '必须手动填写管理员登录密钥，不能自动生成。' ;;
    err_port) printf '端口必须是数字。' ;;
    err_not_git) printf '已存在，但不是 Git 仓库。' ;;
    err_compose) printf '未找到 docker compose 插件，请先安装 Docker Compose v2。' ;;
    info_update) printf '正在更新' ;;
    info_clone) printf '正在克隆' ;;
    info_start_docker) printf '正在启动 Docker 服务...' ;;
    info_install_uv) printf '未找到 uv，正在安装...' ;;
    warn_no_npm) printf '未找到 npm，无法继续构建前端。' ;;
    info_build_vue) printf '正在构建 Vue 控制台...' ;;
    info_install_py) printf '正在安装 Python 依赖...' ;;
    info_start_app) printf '正在启动 ChatGPT2API' ;;
    prompt_mode) printf '运行模式：docker 或 python' ;;
    prompt_port) printf 'Web/API 端口' ;;
    prompt_dir) printf '安装目录' ;;
    prompt_branch) printf 'Release 提交 SHA（Docker）或版本标签（Python）' ;;
    prompt_storage) printf '存储后端' ;;
    prompt_auth) printf '管理员登录密钥' ;;
    prompt_install_target) printf '部署端类型' ;;
    prompt_warp) printf '启用 WARP / Privoxy / FlareSolverr 清障编排' ;;
    done_ready) printf 'ChatGPT2API 已就绪' ;;
    done_auth) printf '管理员密钥' ;;
    summary_target) printf '部署端' ;;
    summary_url) printf '公开访问地址' ;;
    summary_database) printf 'PostgreSQL DATABASE_URL' ;;
    summary_queue_database) printf '图片队列 DATABASE_URL' ;;
    prompt_confirm_install) printf '确认以上配置并开始安装' ;;
    done_cancelled) printf '已取消安装' ;;
    *) printf '%s' "${key}" ;;
  esac
}

prompt_input() {
  local label="$1"
  local default="${2-}"
  local answer=""
  local used_default="0"

  if is_noninteractive; then
    printf '%s' "${default}"
    return
  fi

  if [[ -n "${default}" ]]; then
    ui_print "${label} [${default}]: "
  else
    ui_print "${label}: "
  fi

  IFS= read -r answer <"${UI_IN}" || true
  if [[ -z "${answer}" ]]; then
    answer="${default}"
    used_default="1"
  fi
  if [[ "${used_default}" == "1" && -n "${default}" ]]; then
    ui_println "${label}: ${answer}（默认值）"
  fi
  printf '%s' "${answer}"
}

prompt_secret() {
  local label="$1"
  local answer=""

  if is_noninteractive; then
    printf '%s' ""
    return
  fi

  ui_print "${label}: "
  IFS= read -r -s answer <"${UI_IN}" || true
  ui_println ""
  printf '%s' "${answer}"
}

auth_key_is_placeholder() {
  [[ -z "${1:-}" || "${1}" == "your_secret_key_here" || "${1}" == "your-auth-key" ]]
}

ensure_admin_auth_key() {
  if ! auth_key_is_placeholder "${AUTH_KEY}"; then
    return 0
  fi

  if is_noninteractive; then
    echo "[$(text prefix_error)] $(text err_auth_key)" >&2
    return 1
  fi

  local first_key=""
  local second_key=""
  while true; do
    first_key="$(prompt_secret "$(text prompt_auth)")"
    if [[ -z "${first_key}" ]]; then
      ui_println "[$(text prefix_error)] $(text err_auth_key)"
      continue
    fi
    second_key="$(prompt_secret "再次输入管理员登录密钥")"
    if [[ "${first_key}" != "${second_key}" ]]; then
      ui_println "[$(text prefix_error)] 两次输入的管理员登录密钥不一致，请重新输入。"
      continue
    fi
    AUTH_KEY="${first_key}"
    return 0
  done
}

confirm_installation() {
  if is_noninteractive; then
    return 0
  fi
  if ! confirm "$(text prompt_confirm_install)" "N"; then
    ui_println "[$(text prefix_warn)] $(text done_cancelled)"
    exit 0
  fi
}

confirm() {
  local label="$1"
  local default="${2:-N}"
  local default_choice="1"
  local answer=""

  if is_noninteractive; then
    [[ "${default}" =~ ^([Yy]|1|true|TRUE|yes|YES)$ ]]
    return
  fi

  if [[ "${default}" =~ ^([Yy]|1|true|TRUE|yes|YES)$ ]]; then
    default_choice="2"
  fi

  ui_println "${label}"
  if is_en; then
    ui_println "  1) No"
    ui_println "  2) Yes"
    answer="$(prompt_input "Select" "${default_choice}")"
  else
    ui_println "  1) 否"
    ui_println "  2) 是"
    answer="$(prompt_input "请选择" "${default_choice}")"
  fi

  case "${answer}" in
    2|y|Y|yes|YES|true|TRUE) return 0 ;;
    *) return 1 ;;
  esac
}

normalize_mode_choice() {
  local value="${1:-}"
  value="${value,,}"
  value="${value//[[:space:]]/}"
  case "${value}" in
    1|d|docker) printf 'docker' ;;
    2|p|py|python) printf 'python' ;;
    *) return 1 ;;
  esac
}

normalize_install_target() {
  local value="${1:-}"
  value="${value,,}"
  value="${value//[[:space:]]/}"
  case "${value}" in
    1|standalone|single|single-node|single-node-install)
      printf 'standalone'
      ;;
    2|main|api-main|monitor|monitoring|control|control-plane)
      printf 'api-main'
      ;;
    3|worker|image|image-worker|generation|generation-worker)
      printf 'worker'
      ;;
    *)
      return 1
      ;;
  esac
}

resolve_install_target() {
  local noninteractive="${1:-0}"
  local requested="${INSTALL_TARGET:-${NODE_ROLE:-standalone}}"

  if [[ "${noninteractive}" == "1" || "${CLI_INSTALL_TARGET_SET:-0}" == "1" || -n "${CHATGPT2API_INSTALL_TARGET:-}" || "${NODE_ROLE:-standalone}" != "standalone" ]]; then
    normalize_install_target "${requested}"
    return
  fi
  prompt_install_target
}

prompt_install_target() {
  local answer=""
  local normalized=""
  while true; do
    if is_en; then
      ui_println "Deployment target"
      ui_println "  1) Standalone node (API + monitoring + image generation)"
      ui_println "  2) API main / monitoring node"
      ui_println "  3) Image generation Worker node"
      answer="$(prompt_input "Select" "1")"
    else
      ui_println "部署端类型"
      ui_println "  1) 单机端（API + 监控 + 生图）"
      ui_println "  2) 主控/监控端（API + 监控，不运行生图）"
      ui_println "  3) 生图端 Worker（只运行生图任务）"
      answer="$(prompt_input "请选择" "1")"
    fi
    normalized="$(normalize_install_target "${answer}")" && {
      printf '%s' "${normalized}"
      return
    }
    ui_println "[$(text prefix_error)] $(text err_install_target)"
  done
}

resolve_create_first_worker() {
  case "${CREATE_FIRST_WORKER:-}" in
    1|true|TRUE|yes|YES|y|Y|on|ON)
      return 0
      ;;
    0|false|FALSE|no|NO|n|N|off|OFF)
      return 1
      ;;
    "")
      if is_noninteractive || [[ "${INSTALL_EXISTING}" == "1" ]]; then
        return 1
      fi
      confirm "是否自动生成第一个从节点配置" "Y"
      return
      ;;
    *)
      echo "[$(text prefix_error)] CHATGPT2API_CREATE_FIRST_WORKER must be 0 or 1." >&2
      return 2
      ;;
  esac
}

normalize_storage_choice() {
  local value="${1:-}"
  value="${value,,}"
  value="${value//[[:space:]]/}"
  case "${value}" in
    1|json) printf 'json' ;;
    2|sqlite|sqlite3) printf 'sqlite' ;;
    3|postgres|postgresql|pg) printf 'postgres' ;;
    4|git) printf 'git' ;;
    *) return 1 ;;
  esac
}

prompt_mode_choice() {
  local default="${1:-docker}"
  local normalized=""
  local answer=""
  normalized="$(normalize_mode_choice "${default}")" || normalized="docker"
  local default_choice="1"
  [[ "${normalized}" == "python" ]] && default_choice="2"

  while true; do
    if is_en; then
      ui_println "Run mode"
      ui_println "  1) Docker container (recommended)"
      ui_println "  2) Python source mode"
      answer="$(prompt_input "Select" "${default_choice}")"
    else
      ui_println "运行模式"
      ui_println "  1) Docker 容器（推荐）"
      ui_println "  2) Python 源码运行"
      answer="$(prompt_input "请选择" "${default_choice}")"
    fi
    normalized="$(normalize_mode_choice "${answer}")" && { printf '%s' "${normalized}"; return; }
    ui_println "[$(text prefix_error)] $(text err_mode)"
  done
}

prompt_storage_choice() {
  # Kept for callers from older installer revisions.  New installations do
  # not expose a storage backend menu and always select PostgreSQL.
  printf 'postgres'
}

prompt_storage_details() {
  case "${STORAGE_BACKEND}" in
    sqlite)
      if is_en; then
        DATABASE_URL="$(prompt_input "SQLite DATABASE_URL (blank = auto data/accounts.db)" "${DATABASE_URL}")"
      else
        DATABASE_URL="$(prompt_input "SQLite DATABASE_URL（留空=自动使用 data/accounts.db）" "${DATABASE_URL}")"
      fi
      ;;
    postgres)
      while [[ -z "${DATABASE_URL}" ]]; do
        DATABASE_URL="$(prompt_input "PostgreSQL DATABASE_URL" "${DATABASE_URL}")"
        if [[ -z "${DATABASE_URL}" ]]; then
          ui_println "[$(text prefix_error)] PostgreSQL DATABASE_URL is required."
        fi
      done
      ;;
    git)
      GIT_REPO_URL="$(prompt_input "GIT_REPO_URL" "${GIT_REPO_URL}")"
      GIT_TOKEN="$(prompt_input "GIT_TOKEN" "${GIT_TOKEN}")"
      GIT_BRANCH="$(prompt_input "GIT_BRANCH" "${GIT_BRANCH}")"
      GIT_FILE_PATH="$(prompt_input "GIT_FILE_PATH" "${GIT_FILE_PATH}")"
      GIT_AUTH_KEYS_FILE_PATH="$(prompt_input "GIT_AUTH_KEYS_FILE_PATH" "${GIT_AUTH_KEYS_FILE_PATH}")"
      ;;
  esac
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[$(text prefix_error)] $(text err_missing_cmd): $1" >&2
    exit 1
  fi
}

trash_path() {
  local path=""
  local path_dir=""
  local trash_root=""
  local target=""
  for path in "$@"; do
    if [[ -z "${path}" || (! -e "${path}" && ! -L "${path}") ]]; then
      continue
    fi
    if [[ -n "${INSTALL_DIR:-}" && "${path}" == "${INSTALL_DIR}"/* ]]; then
      trash_root="${INSTALL_DIR}/.chatgpt2api-trash"
    else
      path_dir="$(dirname -- "${path}")"
      trash_root="${path_dir}/.chatgpt2api-trash"
    fi
    mkdir -p -m 700 "${trash_root}"
    if command -v trash-put >/dev/null 2>&1 && trash-put -- "${path}"; then
      continue
    fi
    target="${trash_root}/$(basename "${path}").$(date +%s).$$.${RANDOM}"
    if ! mv -- "${path}" "${target}"; then
      echo "failed to move path to trash: ${path}" >&2
      return 1
    fi
  done
}

generate_auth_key() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
    return
  fi
  if [[ -r /proc/sys/kernel/random/uuid ]]; then
    tr -d '-' </proc/sys/kernel/random/uuid
    return
  fi
  date +%s%N
}

install_target_label() {
  case "${INSTALL_TARGET:-${NODE_ROLE:-standalone}}" in
    api-main) printf '主控/监控端' ;;
    worker) printf '生图端 Worker' ;;
    *) printf '单机端' ;;
  esac
}

print_install_summary() {
  local config_path="${INSTALL_DIR}/data/config.json"
  local public_url="${BASE_URL:-${IMAGE_BASE_URL:-http://localhost:${PORT}}}"
  if [[ "${MODE}" == "python" ]]; then
    config_path="${INSTALL_DIR}/config.json"
  fi

  printf '\n[%s] %s\n' "$(text prefix_done)" "$(text done_ready)"
  printf '%s: %s\n' "$(text summary_target)" "$(install_target_label)"
  printf '%s: %s\n' "$(text summary_url)" "${public_url}"
  printf '%s: %s\n' "$(text prompt_thread_tokens)" "${THREAD_TOKENS}"
  if [[ "${NODE_ROLE:-standalone}" == "api-main" ]]; then
    printf '运行状态: API 存活；Worker 运行态需加入 Worker 后验证\n'
  fi
  if [[ "${NODE_ROLE:-standalone}" != "worker" ]]; then
    printf '%s: %s\n' "$(text done_auth)" "${AUTH_KEY}"
  fi
  if [[ -n "${DATABASE_URL:-}" ]]; then
    local database_label="$(text summary_database)"
    if [[ "${STORAGE_BACKEND:-postgres}" != "postgres" ]]; then
      database_label="DATABASE_URL"
    fi
    printf '%s: %s\n' "${database_label}" "${DATABASE_URL}"
  fi
  if [[ -n "${IMAGE_QUEUE_DATABASE_URL:-}" ]]; then
    printf '%s: %s\n' "$(text summary_queue_database)" "${IMAGE_QUEUE_DATABASE_URL}"
  fi
  printf '配置文件: %s\n' "${config_path}"
  printf '环境文件: %s/.env\n' "${INSTALL_DIR}"
}

apply_install_storage_defaults() {
  if [[ "${INSTALL_EXISTING}" != "1" ]]; then
    STORAGE_BACKEND="postgres"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      --mode)
        MODE="${2:-}"
        shift 2
        ;;
      --port)
        PORT="${2:-}"
        shift 2
        ;;
      --thread-tokens)
        THREAD_TOKENS="${2:-}"
        shift 2
        ;;
      --base-url)
        BASE_URL="${2:-}"
        shift 2
        ;;
      --install-dir)
        INSTALL_DIR="${2:-}"
        shift 2
        ;;
      --branch)
        BRANCH="${2:-}"
        CLI_BRANCH_SET="1"
        CLI_BRANCH_VALUE="${2:-}"
        RELEASE_REF_SELECTED="1"
        shift 2
        ;;
      --auth-key)
        AUTH_KEY="${2:-}"
        shift 2
        ;;
      --install-target)
        INSTALL_TARGET="${2:-}"
        CLI_INSTALL_TARGET_SET="1"
        shift 2
        ;;
      --create-first-worker)
        CREATE_FIRST_WORKER="1"
        shift
        ;;
      --no-first-worker)
        CREATE_FIRST_WORKER="0"
        shift
        ;;
      --storage-backend)
        STORAGE_BACKEND="${2:-}"
        shift 2
        ;;
      --database-url)
        DATABASE_URL="${2:-}"
        shift 2
        ;;
      --image-queue-database-url)
        IMAGE_QUEUE_DATABASE_URL="${2:-}"
        shift 2
        ;;
      --git-repo-url)
        GIT_REPO_URL="${2:-}"
        shift 2
        ;;
      --git-token)
        GIT_TOKEN="${2:-}"
        shift 2
        ;;
      --git-branch)
        GIT_BRANCH="${2:-}"
        shift 2
        ;;
      --git-file-path)
        GIT_FILE_PATH="${2:-}"
        shift 2
        ;;
      --git-auth-keys-file-path)
        GIT_AUTH_KEYS_FILE_PATH="${2:-}"
        shift 2
        ;;
      --with-warp)
        WITH_WARP="1"
        shift
        ;;
      --without-warp)
        WITH_WARP="0"
        shift
        ;;
      --non-interactive)
        NONINTERACTIVE="1"
        shift
        ;;
      --repo-owner)
        REPO_OWNER="${2:-}"
        shift 2
        ;;
      --repo-name)
        REPO_NAME="${2:-}"
        shift 2
        ;;
      *)
        echo "[$(text prefix_error)] $(text err_unknown_arg): $1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done
}

validate_inputs() {
  local normalized=""
  local install_target_requested="${INSTALL_TARGET:-}"

  normalized="$(normalize_mode_choice "${MODE}")" || { echo "[$(text prefix_error)] $(text err_mode)" >&2; exit 1; }
  MODE="${normalized}"

  if [[ -n "${install_target_requested}" ]]; then
    normalized="$(normalize_install_target "${install_target_requested}")" || {
      echo "[$(text prefix_error)] $(text err_install_target)" >&2
      exit 1
    }
    INSTALL_TARGET="${normalized}"
  fi

  normalized="$(normalize_storage_choice "${STORAGE_BACKEND}")" || { echo "[$(text prefix_error)] $(text err_storage)" >&2; exit 1; }
  STORAGE_BACKEND="${normalized}"

  if [[ -z "${PORT}" || ! "${PORT}" =~ ^[0-9]+$ || "${PORT}" -lt 1 || "${PORT}" -gt 65535 ]]; then
    echo "[$(text prefix_error)] $(text err_port)" >&2
    exit 1
  fi

  if ! is_valid_release_ref "${BRANCH}"; then
    echo "[$(text prefix_error)] release ref must be a 40-character commit SHA or a version tag; mutable branches are not allowed." >&2
    exit 1
  fi
  if [[ "${MODE}" == "docker" && ! "${BRANCH}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "[$(text prefix_error)] Docker mode requires a 40-character commit SHA for the downloaded deployment bundle; version tags are supported only in Python mode." >&2
    exit 1
  fi
  if ! is_valid_uv_version "${UV_VERSION}"; then
    echo "[$(text prefix_error)] UV_VERSION must use the numeric MAJOR.MINOR.PATCH format." >&2
    exit 1
  fi

  if [[ -z "${THREAD_TOKENS}" || ! "${THREAD_TOKENS}" =~ ^[0-9]+$ || "${THREAD_TOKENS}" -lt 1 ]]; then
    echo "[$(text prefix_error)] $(text err_thread_tokens)" >&2
    exit 1
  fi

  if [[ -n "${install_target_requested}" && "${NODE_ROLE}" != "worker" ]] && auth_key_is_placeholder "${AUTH_KEY}"; then
    echo "[$(text prefix_error)] $(text err_auth_key)" >&2
    exit 1
  fi

  if [[ "${NODE_ROLE}" == "standalone" && -z "${BASE_URL}" && -z "${IMAGE_BASE_URL}" ]]; then
    echo "[$(text prefix_error)] CHATGPT2API_BASE_URL or CHATGPT2API_IMAGE_BASE_URL is required for standalone public image delivery." >&2
    exit 1
  fi
  if [[ -n "${BASE_URL}" ]] && ! validate_http_base_url "CHATGPT2API_BASE_URL" "${BASE_URL}" 0; then
    exit 1
  fi
  if [[ -n "${IMAGE_BASE_URL}" ]] && ! validate_http_base_url "CHATGPT2API_IMAGE_BASE_URL" "${IMAGE_BASE_URL}" 1; then
    exit 1
  fi

  if [[ -n "${APP_DATABASE_URL}" ]] && ! validate_named_postgres_url "${APP_DATABASE_URL}" "chatgpt2api_app"; then
    echo "[$(text prefix_error)] APP_DATABASE_URL must be a PostgreSQL URL for chatgpt2api_app." >&2
    exit 1
  fi

  if [[ "${STORAGE_BACKEND}" == "postgres" ]]; then
    if [[ -z "${DATABASE_URL}" ]]; then
      echo "[$(text prefix_error)] PostgreSQL DATABASE_URL is required." >&2
      exit 1
    fi
    if ! validate_named_postgres_url "${DATABASE_URL}" "chatgpt2api_app"; then
      echo "[$(text prefix_error)] DATABASE_URL must be a PostgreSQL URL for chatgpt2api_app." >&2
      exit 1
    fi
  fi

  if [[ -z "${IMAGE_QUEUE_DATABASE_URL}" ]]; then
    echo "[$(text prefix_error)] IMAGE_QUEUE_DATABASE_URL is required." >&2
    exit 1
  fi
  if ! validate_named_postgres_url "${IMAGE_QUEUE_DATABASE_URL}" "chatgpt2api_image_queue"; then
    echo "[$(text prefix_error)] IMAGE_QUEUE_DATABASE_URL must be a PostgreSQL URL for chatgpt2api_image_queue." >&2
    exit 1
  fi

  if [[ "${MODE}" == "docker" && -n "${CHATGPT2API_IMAGE}" && ! "${CHATGPT2API_IMAGE}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
    echo "[$(text prefix_error)] CHATGPT2API_IMAGE must end with an immutable sha256 digest in Docker mode." >&2
    exit 1
  fi
  if [[ -n "${CHATGPT2API_IMAGE_DIGEST}" && ! "${CHATGPT2API_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "[$(text prefix_error)] CHATGPT2API_IMAGE_DIGEST must be a sha256 digest." >&2
    exit 1
  fi
  if [[ -n "${CHATGPT2API_IMAGE}" && -n "${CHATGPT2API_IMAGE_DIGEST}" && "${CHATGPT2API_IMAGE}" != *@${CHATGPT2API_IMAGE_DIGEST} ]]; then
    echo "[$(text prefix_error)] CHATGPT2API_IMAGE and CHATGPT2API_IMAGE_DIGEST refer to different images." >&2
    exit 1
  fi

  if [[ "${WITH_WARP}" == "1" ]]; then
    if [[ ! "${CHATGPT2API_WARP_IMAGE}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
      echo "[$(text prefix_error)] CHATGPT2API_WARP_IMAGE must end with an immutable sha256 digest when WARP is enabled." >&2
      exit 1
    fi
    if [[ ! "${CHATGPT2API_PRIVOXY_IMAGE}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
      echo "[$(text prefix_error)] CHATGPT2API_PRIVOXY_IMAGE must end with an immutable sha256 digest when WARP is enabled." >&2
      exit 1
    fi
    if [[ ! "${CHATGPT2API_FLARESOLVERR_IMAGE}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
      echo "[$(text prefix_error)] CHATGPT2API_FLARESOLVERR_IMAGE must end with an immutable sha256 digest when WARP is enabled." >&2
      exit 1
    fi
  fi

  if [[ "${NODE_ROLE:-}" == "api-main" || "${NODE_ROLE:-}" == "worker" ]]; then
    if [[ -z "${CLUSTER_ID:-}" ]]; then
      echo "[$(text prefix_error)] CHATGPT2API_CLUSTER_ID is required for clustered API and Worker nodes." >&2
      exit 1
    fi
    cluster_validate_wireguard_server_ip
    cluster_validate_wireguard_port
    cluster_validate_wireguard_endpoint "${WIREGUARD_SERVER_ENDPOINT:-}"
  fi

  if [[ "${STORAGE_BACKEND}" == "git" ]]; then
    if [[ -z "${GIT_REPO_URL}" || -z "${GIT_TOKEN}" ]]; then
      echo "[$(text prefix_error)] GIT_REPO_URL and GIT_TOKEN are required when STORAGE_BACKEND=git." >&2
      exit 1
    fi
  fi
}

is_valid_release_ref() {
  [[ "${1-}" =~ ^[0-9a-f]{40}$ || "${1-}" =~ ^v?[0-9][0-9A-Za-z.-]*$ ]]
}

is_valid_uv_version() {
  [[ "${1-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

validate_named_postgres_url() {
  local url="${1-}"
  local database_name="${2-}"
  local pattern="^postgres(ql)?([+][Pp][Ss][Yy][Cc][Oo][Pp][Gg]2)?://[^[:space:]/?#]+/${database_name}([?][^#[:space:]]*)?$"
  [[ -n "${database_name}" && "${url}" =~ ${pattern} ]]
}

validate_http_base_url() {
  local label="$1"
  local value="${2%/}"
  local image_only="${3:-0}"
  local remainder=""
  local authority=""
  local path=""

  case "${value}" in
    http://*) remainder="${value#http://}" ;;
    https://*) remainder="${value#https://}" ;;
    *) echo "[$(text prefix_error)] ${label} must be an http or https URL." >&2; return 1 ;;
  esac
  if [[ -z "${remainder}" || "${remainder}" == *[[:space:]]* || "${remainder}" == *\?* || "${remainder}" == *#* ]]; then
    echo "[$(text prefix_error)] ${label} must not contain whitespace, query or fragment." >&2
    return 1
  fi
  authority="${remainder%%/*}"
  if [[ -z "${authority}" || "${authority}" == *"@"* ]]; then
    echo "[$(text prefix_error)] ${label} must contain a host without credentials." >&2
    return 1
  fi
  if [[ "${remainder}" == */* ]]; then
    path="/${remainder#*/}"
    path="${path%/}"
  fi
  if [[ "${image_only}" == "1" && -n "${path}" && "${path}" != "/images" ]]; then
    echo "[$(text prefix_error)] ${label} path must be empty or /images." >&2
    return 1
  fi
  return 0
}

repo_url() {
  printf 'https://github.com/%s/%s.git' "${REPO_OWNER}" "${REPO_NAME}"
}

normalize_repo_remote() {
  local remote="${1-}"
  remote="${remote%/}"
  remote="${remote%.git}"
  case "${remote}" in
    https://github.com/*|http://github.com/*)
      remote="${remote#*github.com/}"
      ;;
    ssh://git@github.com/*)
      remote="${remote#ssh://git@github.com/}"
      ;;
    git@github.com:*)
      remote="${remote#git@github.com:}"
      ;;
    *)
      return 1
      ;;
  esac
  printf '%s' "${remote}"
}

validate_existing_repo_origin() {
  local origin=""
  local normalized=""
  origin="$(git -C "${INSTALL_DIR}" remote get-url origin 2>/dev/null || true)"
  normalized="$(normalize_repo_remote "${origin}" 2>/dev/null || true)"
  if [[ "${normalized}" != "${REPO_OWNER}/${REPO_NAME}" ]]; then
    echo "[$(text prefix_error)] ${INSTALL_DIR} is a different Git repository; refusing to update it." >&2
    return 1
  fi
}

default_image() {
  if [[ -n "${CHATGPT2API_IMAGE}" ]]; then
    printf '%s' "${CHATGPT2API_IMAGE}"
    return
  fi

  if [[ -n "${CHATGPT2API_IMAGE_DIGEST}" ]]; then
    if [[ ! "${CHATGPT2API_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
      echo "CHATGPT2API_IMAGE_DIGEST must be a sha256 digest." >&2
      return 1
    fi
    printf 'ghcr.io/%s/%s@%s' "${REPO_OWNER}" "${REPO_NAME}" "${CHATGPT2API_IMAGE_DIGEST}"
    return
  fi

  if [[ "${BRANCH}" == "${DEFAULT_RELEASE_REF}" ]]; then
    if [[ "${REPO_OWNER}/${REPO_NAME}" != "biubiubiu125/chatgpt2api" ]]; then
      echo "CHATGPT2API_IMAGE or CHATGPT2API_IMAGE_DIGEST is required for custom repository ${REPO_OWNER}/${REPO_NAME}." >&2
      return 1
    fi
    printf 'ghcr.io/%s/%s@%s' "${REPO_OWNER}" "${REPO_NAME}" "${DEFAULT_CHATGPT2API_IMAGE_DIGEST}"
    return
  fi

  echo "CHATGPT2API_IMAGE or CHATGPT2API_IMAGE_DIGEST is required for release ref ${BRANCH}." >&2
  return 1
}

raw_url() {
  if [[ ! "${REPO_OWNER}" =~ ^[A-Za-z0-9_.-]+$ || ! "${REPO_NAME}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "invalid GitHub repository owner or name" >&2
    return 1
  fi
  if ! is_valid_release_ref "${BRANCH}"; then
    echo "invalid immutable release ref: ${BRANCH}" >&2
    return 1
  fi
  printf 'https://raw.githubusercontent.com/%s/%s/%s/%s' "${REPO_OWNER}" "${REPO_NAME}" "${BRANCH}" "$1"
}

release_asset_url() {
  if [[ ! "${REPO_OWNER}" =~ ^[A-Za-z0-9_.-]+$ || ! "${REPO_NAME}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "invalid GitHub repository owner or name" >&2
    return 1
  fi
  if ! [[ "${BRANCH}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "release manifest assets require a 40-character source commit ref" >&2
    return 1
  fi
  printf 'https://github.com/%s/%s/releases/download/chatgpt2api-%s/release-manifest.env' \
    "${REPO_OWNER}" "${REPO_NAME}" "${BRANCH}"
}

download_file() {
  local source_path="$1"
  local target_path="${INSTALL_DIR}/${source_path}"
  local tmp_path="${target_path}.tmp.$$"

  mkdir -p "$(dirname "${target_path}")"
  if ! curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 120 --retry 4 --retry-delay 2 --retry-all-errors \
    "$(raw_url "${source_path}")" -o "${tmp_path}"; then
    trash_path "${tmp_path}"
    return 1
  fi
  mv -f "${tmp_path}" "${target_path}"
}

download_optional_file() {
  local source_path="$1"
  local target_path="${INSTALL_DIR}/${source_path}"
  local tmp_path="${target_path}.tmp.$$"
  local http_status=""
  DOWNLOAD_OPTIONAL_FILE_STATUS="failed"

  mkdir -p "$(dirname "${target_path}")"
  if ! http_status="$(curl --silent --show-error --location --proto '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 120 --retry 4 --retry-delay 2 --retry-all-errors \
    --output "${tmp_path}" --write-out '%{http_code}' \
    "$(raw_url "${source_path}")")"; then
    trash_path "${tmp_path}"
    echo "optional download failed for ${source_path}: network or TLS error" >&2
    return 1
  fi
  case "${http_status}" in
    2??)
      mv -f "${tmp_path}" "${target_path}"
      DOWNLOAD_OPTIONAL_FILE_STATUS="downloaded"
      ;;
    404)
      trash_path "${tmp_path}"
      trash_path "${target_path}"
      DOWNLOAD_OPTIONAL_FILE_STATUS="not_found"
      return 0
      ;;
    *)
      trash_path "${tmp_path}"
      echo "optional download failed for ${source_path}: HTTP ${http_status}" >&2
      return 1
      ;;
  esac
}

download_optional_or_fail() {
  local source_path="$1"
  local status=0
  if download_optional_file "${source_path}"; then
    if [[ "${DOWNLOAD_OPTIONAL_FILE_STATUS:-}" == "not_found" ]]; then
      printf '%s\n' "optional file is not published for this release: ${source_path}" >&2
    fi
    return 0
  else
    status=$?
  fi
  printf '%s\n' "optional file download failed for ${source_path}; refusing to continue." >&2
  return "${status}"
}

validate_existing_deployment_dir() {
  if [[ -e "${INSTALL_DIR}" && ! -d "${INSTALL_DIR}" ]]; then
    echo "[$(text prefix_error)] ${INSTALL_DIR} exists but is not a directory; refusing to continue." >&2
    return 1
  fi
  if [[ ! -d "${INSTALL_DIR}" ]]; then
    return 0
  fi

  local first_entry=""
  first_entry="$(find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)"
  [[ -z "${first_entry}" ]] && return 0

  if [[ -f "${INSTALL_DIR}/docker-compose.yml" ]] && grep -q "chatgpt2api" "${INSTALL_DIR}/docker-compose.yml"; then
    return 0
  fi
  for compose_file in \
    docker-compose.warp.yml \
    docker-compose.cluster-main.yml \
    docker-compose.cluster-worker.yml; do
    if [[ -f "${INSTALL_DIR}/${compose_file}" ]] && grep -q "chatgpt2api" "${INSTALL_DIR}/${compose_file}"; then
      return 0
    fi
  done
  if [[ -f "${INSTALL_DIR}/.env" ]] && grep -Eq '^(CHATGPT2API_|IMAGE_QUEUE_DATABASE_URL=)' "${INSTALL_DIR}/.env"; then
    return 0
  fi
  if [[ -f "${INSTALL_DIR}/deploy/release-manifest.env" ]]; then
    local manifest_ref=""
    local manifest_image=""
    manifest_ref="$(dotenv_read_value "${INSTALL_DIR}/deploy/release-manifest.env" CHATGPT2API_RELEASE_REF || true)"
    manifest_image="$(dotenv_read_value "${INSTALL_DIR}/deploy/release-manifest.env" CHATGPT2API_IMAGE || true)"
    if [[ -n "${manifest_ref}" && -n "${manifest_image}" ]]; then
      return 0
    fi
  fi
  if [[ -f "${INSTALL_DIR}/join/join-signing.pub" ]] || compgen -G "${INSTALL_DIR}/join/worker-*.join" >/dev/null; then
    return 0
  fi

  echo "[$(text prefix_error)] ${INSTALL_DIR} is non-empty but is not a recognized ChatGPT2API deployment; refusing to overwrite it." >&2
  return 1
}

validate_release_manifest_file() {
  local manifest_file="$1"
  local release_ref=""
  local image=""
  local image_digest=""
  local uv_version=""
  local sidecar_key=""
  local sidecar_image=""

  release_ref="$(dotenv_read_value "${manifest_file}" CHATGPT2API_RELEASE_REF || true)"
  image="$(dotenv_read_value "${manifest_file}" CHATGPT2API_IMAGE || true)"
  image_digest="$(dotenv_read_value "${manifest_file}" CHATGPT2API_IMAGE_DIGEST || true)"
  uv_version="$(dotenv_read_value "${manifest_file}" UV_VERSION || true)"
  if [[ ! "${release_ref}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "release manifest has an invalid CHATGPT2API_RELEASE_REF: ${manifest_file}" >&2
    return 1
  fi
  if [[ ! "${image}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
    echo "release manifest has an invalid CHATGPT2API_IMAGE: ${manifest_file}" >&2
    return 1
  fi
  if [[ -n "${image_digest}" && ! "${image_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "release manifest has an invalid CHATGPT2API_IMAGE_DIGEST: ${manifest_file}" >&2
    return 1
  fi
  if [[ -n "${image_digest}" && "${image}" != *@${image_digest} ]]; then
    echo "release manifest image and digest do not match: ${manifest_file}" >&2
    return 1
  fi
  if [[ ! "${uv_version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "release manifest has an invalid UV_VERSION: ${manifest_file}" >&2
    return 1
  fi
  for sidecar_key in \
    CHATGPT2API_WARP_IMAGE \
    CHATGPT2API_PRIVOXY_IMAGE \
    CHATGPT2API_FLARESOLVERR_IMAGE; do
    sidecar_image="$(dotenv_read_value "${manifest_file}" "${sidecar_key}" || true)"
    if [[ ! "${sidecar_image}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
      echo "release manifest has an invalid ${sidecar_key}: ${manifest_file}" >&2
      return 1
    fi
  done
}

reset_release_manifest_values() {
  local key=""
  for key in \
    CHATGPT2API_RELEASE_REF \
    CHATGPT2API_IMAGE \
    CHATGPT2API_IMAGE_DIGEST \
    UV_VERSION \
    CHATGPT2API_WARP_IMAGE \
    CHATGPT2API_PRIVOXY_IMAGE \
    CHATGPT2API_FLARESOLVERR_IMAGE; do
    if declare -p "${key}" 2>/dev/null | grep -q 'declare -x'; then
      continue
    fi
    printf -v "${key}" '%s' ''
  done
}

apply_release_manifest_file() {
  local manifest_file="$1"
  local requested_release_ref="$2"
  local manifest_release_ref=""

  manifest_release_ref="$(dotenv_read_value "${manifest_file}" CHATGPT2API_RELEASE_REF || true)"
  if [[ -n "${requested_release_ref}" && "${manifest_release_ref}" != "${requested_release_ref}" ]]; then
    return 2
  fi
  if ! validate_release_manifest_file "${manifest_file}"; then
    return 1
  fi
  reset_release_manifest_values
  if ! dotenv_load_file_preserving_exported "${manifest_file}"; then
    return 1
  fi
  if release_ref_override_active; then
    BRANCH="$(release_ref_override_value)"
    CHATGPT2API_RELEASE_REF="$(release_ref_override_value)"
  else
    BRANCH="${CHATGPT2API_RELEASE_REF:-${BRANCH:-}}"
  fi
  local manifest_dir="${INSTALL_DIR}/deploy"
  if [[ -d "${manifest_dir}" ]] && [[ -z "$(find "${manifest_dir}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    rmdir -- "${manifest_dir}" 2>/dev/null || true
  fi
  return 0
}

load_release_manifest() {
  local manifest_file="${INSTALL_DIR}/deploy/release-manifest.env"
  local manifest_dir="${INSTALL_DIR}/deploy"
  local manifest_release_ref=""
  local requested_release_ref=""
  local raw_expected_release_ref=""
  local asset_expected_release_ref=""
  if release_ref_override_active; then
    requested_release_ref="$(release_ref_override_value)"
  elif [[ "${RELEASE_REF_SELECTED}" == "1" ]]; then
    requested_release_ref="${BRANCH:-}"
  fi
  if [[ -f "${manifest_file}" ]]; then
    manifest_release_ref="$(dotenv_read_value "${manifest_file}" CHATGPT2API_RELEASE_REF || true)"
    if [[ -z "${requested_release_ref}" || "${manifest_release_ref}" == "${requested_release_ref}" ]]; then
      apply_release_manifest_file "${manifest_file}" "${requested_release_ref}"
      return $?
    fi
  fi

  local tmp_manifest="${INSTALL_DIR}/deploy/release-manifest.env.asset.tmp.$$"
  mkdir -p "$(dirname "${tmp_manifest}")"
  if release_asset_url >/dev/null 2>&1 && curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 30 --retry 3 --retry-delay 2 --retry-all-errors \
    "$(release_asset_url)" -o "${tmp_manifest}"; then
    manifest_release_ref="$(dotenv_read_value "${tmp_manifest}" CHATGPT2API_RELEASE_REF || true)"
    asset_expected_release_ref="${requested_release_ref:-${BRANCH}}"
    if [[ -n "${asset_expected_release_ref}" && "${manifest_release_ref}" != "${asset_expected_release_ref}" ]]; then
      trash_path "${tmp_manifest}"
    elif ! validate_release_manifest_file "${tmp_manifest}"; then
      trash_path "${tmp_manifest}"
      return 1
    elif ! reset_release_manifest_values; then
      trash_path "${tmp_manifest}"
      return 1
    elif ! dotenv_load_file_preserving_exported "${tmp_manifest}"; then
      trash_path "${tmp_manifest}"
      return 1
    else
      if [[ -e "${manifest_file}" || -L "${manifest_file}" ]]; then
        trash_path "${manifest_file}"
      fi
      mv -f "${tmp_manifest}" "${manifest_file}"
      if release_ref_override_active; then
        BRANCH="$(release_ref_override_value)"
        CHATGPT2API_RELEASE_REF="$(release_ref_override_value)"
      else
        BRANCH="${CHATGPT2API_RELEASE_REF:-${BRANCH:-}}"
      fi
      if [[ -d "${manifest_dir}" ]] && [[ -z "$(find "${manifest_dir}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
        rmdir -- "${manifest_dir}" 2>/dev/null || true
      fi
      return 0
    fi
  fi
  trash_path "${tmp_manifest}"

  tmp_manifest="${INSTALL_DIR}/deploy/release-manifest.env.tmp.$$"
  mkdir -p "$(dirname "${tmp_manifest}")"
  if curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 30 --retry 3 --retry-delay 2 --retry-all-errors \
    "$(raw_url "deploy/release-manifest.env")" -o "${tmp_manifest}"; then
    manifest_release_ref="$(dotenv_read_value "${tmp_manifest}" CHATGPT2API_RELEASE_REF || true)"
    raw_expected_release_ref="${requested_release_ref:-${BRANCH}}"
    if [[ -n "${raw_expected_release_ref}" && "${manifest_release_ref}" != "${raw_expected_release_ref}" ]]; then
      trash_path "${tmp_manifest}"
    elif ! validate_release_manifest_file "${tmp_manifest}"; then
      trash_path "${tmp_manifest}"
      return 1
    elif ! reset_release_manifest_values; then
      trash_path "${tmp_manifest}"
      return 1
    elif ! dotenv_load_file_preserving_exported "${tmp_manifest}"; then
      trash_path "${tmp_manifest}"
      return 1
    else
      if [[ -e "${manifest_file}" || -L "${manifest_file}" ]]; then
        trash_path "${manifest_file}"
      fi
      mv -f "${tmp_manifest}" "${manifest_file}"
      if release_ref_override_active; then
        BRANCH="$(release_ref_override_value)"
        CHATGPT2API_RELEASE_REF="$(release_ref_override_value)"
      else
        BRANCH="${CHATGPT2API_RELEASE_REF:-${BRANCH:-}}"
      fi
      if [[ -d "${manifest_dir}" ]] && [[ -z "$(find "${manifest_dir}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
        rmdir -- "${manifest_dir}" 2>/dev/null || true
      fi
      return 0
    fi
  fi

  trash_path "${tmp_manifest}"
  if [[ -d "${manifest_dir}" ]] && [[ -z "$(find "${manifest_dir}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    rmdir -- "${manifest_dir}" 2>/dev/null || true
  fi
  if [[ "${REPO_OWNER}/${REPO_NAME}" != "biubiubiu125/chatgpt2api" || "${BRANCH}" != "${DEFAULT_RELEASE_REF}" ]]; then
    printf '%s\n' "release manifest unavailable for ${REPO_OWNER}/${REPO_NAME}@${BRANCH}; refusing to continue without release-matched image metadata." >&2
    return 1
  fi
  if [[ -z "${CHATGPT2API_IMAGE:-}" && -z "${CHATGPT2API_IMAGE_DIGEST:-}" ]]; then
    CHATGPT2API_IMAGE_DIGEST="${DEFAULT_CHATGPT2API_IMAGE_DIGEST}"
  fi
  printf '%s\n' "release manifest unavailable; using built-in metadata for the pinned default release." >&2
  return 0
}

json_escape() {
  local value="${1-}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '%s' "${value}"
}

dotenv_escape() {
  local value="${1-}"
  value="${value//$'\r'/}"
  value="${value//$'\n'/}"
  if [[ -z "${value}" ]]; then
    printf ''
    return
  fi
  printf "'"
  printf '%s' "${value}" | sed "s/'/'\\\\''/g"
  printf "'"
}

write_default_config_json() {
  local config_file="${INSTALL_DIR}/config.json"
  if [[ "${MODE}" == "docker" ]]; then
    config_file="${INSTALL_DIR}/data/config.json"
  fi
  local tmp_file="${config_file}.tmp"

  mkdir -p "$(dirname "${config_file}")"

  if [[ -f "${config_file}" ]]; then
    return
  fi
  if [[ -e "${config_file}" ]]; then
    echo "[$(text prefix_error)] ${config_file} exists but is not a regular file." >&2
    exit 1
  fi

  cat >"${tmp_file}" <<EOF
{
  "auth-key": "$(json_escape "${AUTH_KEY}")",
  "refresh_account_interval_minute": 5,
  "image_retention_days": 15,
  "image_poll_timeout_secs": 60,
  "image_stream_timeout_secs": 80,
  "auto_remove_rate_limited_accounts": false,
  "auto_remove_invalid_accounts": true,
  "log_levels": ["debug", "info", "warning", "error"],
  "proxy": "",
  "proxy_runtime": {
    "enabled": false,
    "egress_mode": "direct",
    "proxy_url": "",
    "resource_proxy_url": "",
    "skip_ssl_verify": false,
    "reset_session_status_codes": [403],
    "clearance": {
      "enabled": false,
      "mode": "none",
      "cf_cookies": "",
      "cf_clearance": "",
      "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
      "browser": "chrome",
      "flaresolverr_url": "",
      "timeout_sec": 60,
      "refresh_interval": 3600,
      "warm_up_on_start": false
    }
  },
  "base_url": "$(json_escape "${BASE_URL}")",
  "image_base_url": "$(json_escape "${IMAGE_BASE_URL}")",
  "sensitive_words": [],
  "global_system_prompt": "",
  "image_account_concurrency": 1,
  "image_account_retry_enabled": true,
  "image_preflight_token_refresh_enabled": false,
  "image_auth_refresh_concurrency": 10,
  "image_max_account_attempts": 4,
  "image_parallel_generation": true,
  "image_remove_conversation_after_result": false,
  "image_poll_interval_secs": 10,
  "image_poll_initial_wait_secs": 10,
  "image_min_free_mb": 500,
  "image_settle_enabled": false,
  "image_check_before_hit_enabled": false,
  "image_settle_secs": 2,
  "auto_relogin_after_refresh": false
}
EOF

  mv "${tmp_file}" "${config_file}"
  chmod 600 "${config_file}" || true
}

prepare_docker_bundle() {
  need_cmd curl

  validate_existing_deployment_dir || exit 1
  mkdir -p "${INSTALL_DIR}"
  download_file "docker-compose.yml"
  download_optional_or_fail "config.example.yaml"
  if [[ ! -f "${INSTALL_DIR}/deploy/release-manifest.env" ]]; then
    download_optional_or_fail "deploy/release-manifest.env"
  fi

  if [[ "${WITH_WARP}" == "1" ]]; then
    download_file "docker-compose.warp.yml"
    download_file "scripts/init_proxy_config.py"
    download_file "scripts/privoxy-warp.conf"
  fi
  download_file "scripts/bootstrap_database_roles.py"
  download_file "scripts/env_loader.py"
}

prepare_repo() {
  need_cmd git

  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    validate_existing_repo_origin || exit 1
    ui_println "[$(text prefix_info)] $(text info_update) ${INSTALL_DIR}"
    (cd "${INSTALL_DIR}" && git fetch --tags --depth 1 origin "${BRANCH}")
    (cd "${INSTALL_DIR}" && git checkout --detach FETCH_HEAD >/dev/null 2>&1)
    local resolved_ref=""
    resolved_ref="$(cd "${INSTALL_DIR}" && git rev-parse --verify FETCH_HEAD^{commit})"
    if [[ "${BRANCH}" =~ ^[0-9a-f]{40}$ && "${resolved_ref}" != "${BRANCH}" ]]; then
      echo "[$(text prefix_error)] fetched Git commit does not match requested release ref: ${resolved_ref} != ${BRANCH}" >&2
      exit 1
    fi
    BRANCH="${resolved_ref}"
    CHATGPT2API_RELEASE_REF="${resolved_ref}"
    return
  fi

  if [[ -e "${INSTALL_DIR}" && -n "$(find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]]; then
    echo "[$(text prefix_error)] ${INSTALL_DIR} $(text err_not_git)" >&2
    exit 1
  fi

  mkdir -p "$(dirname "${INSTALL_DIR}")"
  ui_println "[$(text prefix_info)] $(text info_clone) $(repo_url) -> ${INSTALL_DIR}"
  git clone --no-checkout --depth 1 "$(repo_url)" "${INSTALL_DIR}"
  (cd "${INSTALL_DIR}" && git fetch --depth 1 origin "${BRANCH}" && git checkout --detach FETCH_HEAD)
  local resolved_ref=""
  resolved_ref="$(cd "${INSTALL_DIR}" && git rev-parse --verify FETCH_HEAD^{commit})"
  if [[ "${BRANCH}" =~ ^[0-9a-f]{40}$ && "${resolved_ref}" != "${BRANCH}" ]]; then
    echo "[$(text prefix_error)] fetched Git commit does not match requested release ref: ${resolved_ref} != ${BRANCH}" >&2
    exit 1
  fi
  BRANCH="${resolved_ref}"
  CHATGPT2API_RELEASE_REF="${resolved_ref}"
}

write_env_file() {
  local env_file="${INSTALL_DIR}/.env"
  local tmp_file="${env_file}.tmp"
  local postgres_password_urlencoded=""
  local resolved_image="${CHATGPT2API_IMAGE:-}"
  postgres_password_urlencoded="$(cluster_urlencode "${POSTGRES_PASSWORD}")"
  if [[ "${MODE}" == "docker" ]]; then
    if ! resolved_image="$(default_image)"; then
      return 1
    fi
  fi

  cat >"${tmp_file}" <<EOF
CHATGPT2API_AUTH_KEY=$(dotenv_escape "${AUTH_KEY}")
CHATGPT2API_PORT=$(dotenv_escape "${PORT}")
CHATGPT2API_THREAD_TOKENS=$(dotenv_escape "${THREAD_TOKENS}")
CHATGPT2API_CONFIG_FILE=$(dotenv_escape "${CHATGPT2API_CONFIG_FILE}")
CHATGPT2API_BACKUP_PASSPHRASE=$(dotenv_escape "${CHATGPT2API_BACKUP_PASSPHRASE}")
CHATGPT2API_MONITOR_COMPLETED_LIMIT=$(dotenv_escape "${CHATGPT2API_MONITOR_COMPLETED_LIMIT}")
CHATGPT2API_MONITOR_EVENT_LIMIT=$(dotenv_escape "${CHATGPT2API_MONITOR_EVENT_LIMIT}")
CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS=$(dotenv_escape "${CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS}")
CHATGPT2API_RUNTIME_LOG_FILE=$(dotenv_escape "${CHATGPT2API_RUNTIME_LOG_FILE}")
HOST=$(dotenv_escape "${HOST}")
LOG_LEVEL=$(dotenv_escape "${LOG_LEVEL}")
UVICORN_WORKERS=$(dotenv_escape "${UVICORN_WORKERS}")
CHATGPT2API_RELEASE_REF=$(dotenv_escape "${BRANCH}")
UV_VERSION=$(dotenv_escape "${UV_VERSION}")
CHATGPT2API_IMAGE=$(dotenv_escape "${resolved_image}")
CHATGPT2API_IMAGE_DIGEST=$(dotenv_escape "${CHATGPT2API_IMAGE_DIGEST}")
CHATGPT2API_WARP_IMAGE=$(dotenv_escape "${CHATGPT2API_WARP_IMAGE}")
CHATGPT2API_PRIVOXY_IMAGE=$(dotenv_escape "${CHATGPT2API_PRIVOXY_IMAGE}")
CHATGPT2API_FLARESOLVERR_IMAGE=$(dotenv_escape "${CHATGPT2API_FLARESOLVERR_IMAGE}")
CHATGPT2API_BASE_URL=$(dotenv_escape "${BASE_URL}")
CHATGPT2API_IMAGE_BASE_URL=$(dotenv_escape "${IMAGE_BASE_URL}")
CHATGPT2API_PYTHON_BIN=$(dotenv_escape "${PYTHON_BIN}")
CHATGPT2API_IMAGE_PORT=$(dotenv_escape "${IMAGE_PORT}")
MODE=$(dotenv_escape "${MODE}")
WITH_WARP=$(dotenv_escape "${WITH_WARP}")
CHATGPT2API_NODE_ROLE=$(dotenv_escape "${NODE_ROLE}")
CHATGPT2API_RUN_API=$(dotenv_escape "${RUN_API}")
CHATGPT2API_RUN_WORKER=$(dotenv_escape "${RUN_WORKER}")
CHATGPT2API_WORKER_ID=$(dotenv_escape "${WORKER_ID}")
CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE=$(dotenv_escape "${CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE}")
CHATGPT2API_WORKER_BIND_HOST=$(dotenv_escape "${CHATGPT2API_WORKER_BIND_HOST}")
CHATGPT2API_WIREGUARD_IP=$(dotenv_escape "${WIREGUARD_IP}")
CHATGPT2API_CLUSTER_ID=$(dotenv_escape "${CLUSTER_ID}")
CHATGPT2API_WORKER_JOINED_MARKER_FILE=$(dotenv_escape "${WORKER_JOINED_MARKER_FILE}")
WIREGUARD_INTERFACE=$(dotenv_escape "${WIREGUARD_INTERFACE}")
WIREGUARD_SERVER_IP=$(dotenv_escape "${WIREGUARD_SERVER_IP}")
WIREGUARD_SERVER_ENDPOINT=$(dotenv_escape "${WIREGUARD_SERVER_ENDPOINT}")
WIREGUARD_PORT=$(dotenv_escape "${WIREGUARD_PORT}")

STORAGE_BACKEND=$(dotenv_escape "${STORAGE_BACKEND}")
APP_DATABASE_URL=$(dotenv_escape "${APP_DATABASE_URL}")
DATABASE_URL=$(dotenv_escape "${DATABASE_URL}")
IMAGE_QUEUE_DATABASE_URL=$(dotenv_escape "${IMAGE_QUEUE_DATABASE_URL}")
IMAGE_QUEUE_INSTANCE_ID=$(dotenv_escape "${IMAGE_QUEUE_INSTANCE_ID}")
IMAGE_QUEUE_VERIFY_RETURNED_URL=$(dotenv_escape "${IMAGE_QUEUE_VERIFY_RETURNED_URL}")
IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS}")
IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS=$(dotenv_escape "${IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS}")
IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES=$(dotenv_escape "${IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES}")
IMAGE_PROMPT_SUFFIX_ENABLED=$(dotenv_escape "${IMAGE_PROMPT_SUFFIX_ENABLED}")
IMAGE_PROMPT_SUFFIX=$(dotenv_escape "${IMAGE_PROMPT_SUFFIX}")
IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS}")
IMAGE_QUEUE_DELIVERY_GRACE_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_DELIVERY_GRACE_SECONDS}")
IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS}")
IMAGE_QUEUE_STARTUP_RETRY_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_STARTUP_RETRY_SECONDS}")
IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS}")
IMAGE_QUEUE_GENERATION_CONCURRENCY=$(dotenv_escape "${IMAGE_QUEUE_GENERATION_CONCURRENCY}")
IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP=$(dotenv_escape "${IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP}")
IMAGE_QUEUE_ABSOLUTE_GUARD=$(dotenv_escape "${IMAGE_QUEUE_ABSOLUTE_GUARD}")
IMAGE_QUEUE_MAX_BACKLOG=$(dotenv_escape "${IMAGE_QUEUE_MAX_BACKLOG}")
IMAGE_QUEUE_PENDING_TTL_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_PENDING_TTL_SECONDS}")
IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS}")
IMAGE_QUEUE_LEGACY_TASK_PATH=$(dotenv_escape "${IMAGE_QUEUE_LEGACY_TASK_PATH}")
IMAGE_QUEUE_LEASE_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_LEASE_SECONDS}")
IMAGE_QUEUE_HEARTBEAT_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_HEARTBEAT_SECONDS}")
IMAGE_QUEUE_POLL_INTERVAL_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_POLL_INTERVAL_SECONDS}")
IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS=$(dotenv_escape "${IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS}")
IMAGE_QUEUE_GENERATION_ATTEMPTS=$(dotenv_escape "${IMAGE_QUEUE_GENERATION_ATTEMPTS}")
IMAGE_QUEUE_DOWNLOAD_ATTEMPTS=$(dotenv_escape "${IMAGE_QUEUE_DOWNLOAD_ATTEMPTS}")
IMAGE_QUEUE_SAVE_ATTEMPTS=$(dotenv_escape "${IMAGE_QUEUE_SAVE_ATTEMPTS}")
IMAGE_QUEUE_CPU_THROTTLE_PERCENT=$(dotenv_escape "${IMAGE_QUEUE_CPU_THROTTLE_PERCENT}")
IMAGE_QUEUE_CPU_PAUSE_PERCENT=$(dotenv_escape "${IMAGE_QUEUE_CPU_PAUSE_PERCENT}")
IMAGE_QUEUE_CPU_RESUME_PERCENT=$(dotenv_escape "${IMAGE_QUEUE_CPU_RESUME_PERCENT}")
IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT=$(dotenv_escape "${IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT}")
IMAGE_QUEUE_MEMORY_PAUSE_PERCENT=$(dotenv_escape "${IMAGE_QUEUE_MEMORY_PAUSE_PERCENT}")
IMAGE_QUEUE_MEMORY_REJECT_PERCENT=$(dotenv_escape "${IMAGE_QUEUE_MEMORY_REJECT_PERCENT}")
IMAGE_QUEUE_DB_POOL_SIZE=$(dotenv_escape "${IMAGE_QUEUE_DB_POOL_SIZE}")
IMAGE_QUEUE_DB_MAX_OVERFLOW=$(dotenv_escape "${IMAGE_QUEUE_DB_MAX_OVERFLOW}")
EDITABLE_FILE_WORKERS=$(dotenv_escape "${EDITABLE_FILE_WORKERS}")
EDITABLE_FILE_MAX_BACKLOG=$(dotenv_escape "${EDITABLE_FILE_MAX_BACKLOG}")
PROMPT_LIBRARY_DEFAULT_URL=$(dotenv_escape "${PROMPT_LIBRARY_DEFAULT_URL}")
PROMPT_LIBRARY_REMOTE_URL=$(dotenv_escape "${PROMPT_LIBRARY_REMOTE_URL}")
CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS=$(dotenv_escape "${CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS}")
CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS=$(dotenv_escape "${CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS}")
CHATGPT2API_PROXY_RUNTIME_ENABLED=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_ENABLED}")
CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE}")
CHATGPT2API_PROXY_RUNTIME_PROXY_URL=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_PROXY_URL}")
CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL}")
CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY}")
CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES}")
CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED}")
CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE}")
CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC}")
CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL}")
CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START}")
CHATGPT2API_PROXY_RUNTIME_BROWSER=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_BROWSER}")
CHATGPT2API_PROXY_RUNTIME_USER_AGENT=$(dotenv_escape "${CHATGPT2API_PROXY_RUNTIME_USER_AGENT}")
CHATGPT2API_FLARESOLVERR_URL=$(dotenv_escape "${CHATGPT2API_FLARESOLVERR_URL}")
WARP_LICENSE_KEY=$(dotenv_escape "${WARP_LICENSE_KEY}")
CHATGPT2API_PYTHON_PID_FILE=$(dotenv_escape "${CHATGPT2API_PYTHON_PID_FILE}")
POSTGRES_PASSWORD=$(dotenv_escape "${POSTGRES_PASSWORD}")
POSTGRES_PASSWORD_URLENCODED=$(dotenv_escape "${postgres_password_urlencoded}")

GIT_REPO_URL=$(dotenv_escape "${GIT_REPO_URL}")
GIT_TOKEN=$(dotenv_escape "${GIT_TOKEN}")
GIT_BRANCH=$(dotenv_escape "${GIT_BRANCH}")
GIT_FILE_PATH=$(dotenv_escape "${GIT_FILE_PATH}")
GIT_AUTH_KEYS_FILE_PATH=$(dotenv_escape "${GIT_AUTH_KEYS_FILE_PATH}")

WARP_SOCKS_PORT=$(dotenv_escape "${WARP_SOCKS_PORT}")
PRIVOXY_PORT=$(dotenv_escape "${PRIVOXY_PORT}")
FLARESOLVERR_PORT=$(dotenv_escape "${FLARESOLVERR_PORT}")
FLARESOLVERR_LOG_LEVEL=$(dotenv_escape "${FLARESOLVERR_LOG_LEVEL}")
TZ=$(dotenv_escape "${TZ}")
EOF

  if [[ "${NODE_ROLE}" == "api-main" ]]; then
    cat >>"${tmp_file}" <<EOF
POSTGRES_ADMIN_USER=$(dotenv_escape "${POSTGRES_ADMIN_USER}")
POSTGRES_ADMIN_PASSWORD=$(dotenv_escape "${POSTGRES_ADMIN_PASSWORD}")
EOF
  fi

  mv "${tmp_file}" "${env_file}"
  chmod 600 "${env_file}" || true
}

wait_docker_app_health() {
  local compose_args=("$@")
  local container_id=""
  local status=""
  local attempt=0
  for attempt in $(seq 1 60); do
    container_id="$(cd "${INSTALL_DIR}" && docker compose "${compose_args[@]}" ps -q app 2>/dev/null || true)"
    if [[ -n "${container_id}" ]]; then
      status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || true)"
      if [[ "${status}" == "healthy" ]]; then
        return 0
      fi
      if [[ "${status}" == "exited" || "${status}" == "dead" ]]; then
        break
      fi
    fi
    sleep 2
  done
  echo "[$(text prefix_error)] Docker app did not become healthy; recent logs:" >&2
  (cd "${INSTALL_DIR}" && docker compose "${compose_args[@]}" ps >&2 || true)
  (cd "${INSTALL_DIR}" && docker compose "${compose_args[@]}" logs --tail=120 >&2 || true)
  exit 1
}

wait_cluster_main_liveness() {
  need_cmd curl
  local port="${PORT:-3000}"
  local timeout_seconds="${CHATGPT2API_MAIN_LIVENESS_TIMEOUT_SECONDS:-120}"
  local deadline=$(( $(date +%s) + timeout_seconds ))
  local url="http://127.0.0.1:${port}/health/live?format=json"

  ui_println "[$(text prefix_info)] waiting for cluster main API liveness: ${url}"
  while (( $(date +%s) < deadline )); do
    if curl --fail --silent --show-error --max-time 5 "${url}" >/dev/null 2>&1; then
      ui_println "[$(text prefix_info)] cluster main API is live; runtime health will wait for Worker join."
      return 0
    fi
    sleep 2
  done

  echo "[$(text prefix_error)] cluster main API did not become live; recent logs:" >&2
  (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-main.yml ps >&2 || true)
  (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-main.yml logs --tail=120 app >&2 || true)
  return 1
}

run_docker() {
  need_cmd docker
  if ! docker compose version >/dev/null 2>&1; then
    echo "[$(text prefix_error)] $(text err_compose)" >&2
    exit 1
  fi

  local compose_args=(-f docker-compose.yml)
  if [[ "${WITH_WARP}" == "1" ]]; then
    compose_args=(-f docker-compose.warp.yml)
  fi

  (cd "${INSTALL_DIR}" && docker compose "${compose_args[@]}" config --quiet)
  prepare_docker_data_permissions
  ui_println "[$(text prefix_info)] $(text info_start_docker)"
  (cd "${INSTALL_DIR}" && docker compose "${compose_args[@]}" pull)
  stop_python_runtime
  (cd "${INSTALL_DIR}" && docker compose "${compose_args[@]}" up -d --remove-orphans)
  wait_docker_app_health "${compose_args[@]}"
}

ensure_python_version() {
  need_cmd "${PYTHON_BIN}"
  if ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)' >/dev/null 2>&1; then
    echo "[$(text prefix_error)] Python 3.13 or newer is required; found ${PYTHON_BIN}." >&2
    return 1
  fi
}

ensure_uv() {
  if ! ensure_python_version; then
    return 1
  fi
  if command -v uv >/dev/null 2>&1; then
    local system_uv_bin=""
    local system_uv_version=""
    system_uv_bin="$(command -v uv)"
    system_uv_version="$("${system_uv_bin}" --version 2>/dev/null | awk 'NR == 1 { print $2 }')"
    if [[ "${system_uv_version}" == "${UV_VERSION}" ]]; then
      UV_BIN="${system_uv_bin}"
      return
    fi
    ui_println "[$(text prefix_warn)] system uv ${system_uv_version:-unknown} does not match pinned uv ${UV_VERSION}; bootstrapping the pinned version."
  fi
  ui_println "[$(text prefix_info)] $(text info_install_uv)"
  local bootstrap_venv="${INSTALL_DIR}/.installer-venv"
  mkdir -p "${INSTALL_DIR}"
  if ! "${PYTHON_BIN}" -m venv "${bootstrap_venv}" >/dev/null 2>&1; then
    if [[ "${EUID:-$(id -u)}" == "0" ]] && command -v apt-get >/dev/null 2>&1; then
      apt-get update
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-venv python3-pip
      "${PYTHON_BIN}" -m venv "${bootstrap_venv}"
    else
      echo "[$(text prefix_error)] python3-venv is required to bootstrap uv; install python3-venv and rerun." >&2
      exit 1
    fi
  fi
  "${bootstrap_venv}/bin/python" -m pip install --disable-pip-version-check --no-input "uv==${UV_VERSION}"
  UV_BIN="${bootstrap_venv}/bin/uv"
  [[ -x "${UV_BIN}" ]] || { echo "[$(text prefix_error)] uv bootstrap failed." >&2; exit 1; }
}

preflight_install_environment() {
  case "${MODE}" in
    docker)
      need_cmd docker
      if ! docker compose version >/dev/null 2>&1; then
        echo "[$(text prefix_error)] $(text err_compose)" >&2
        return 1
      fi
      if ! docker info >/dev/null 2>&1; then
        echo "[$(text prefix_error)] Docker daemon is unavailable." >&2
        return 1
      fi
      need_cmd curl
      ;;
    python)
      if ! ensure_python_version; then
        return 1
      fi
      need_cmd git
      need_cmd curl
      need_cmd npm
      ;;
  esac
}

prepare_docker_data_permissions() {
  local data_dir="${INSTALL_DIR}/data"
  mkdir -p "${data_dir}/images"
  if [[ "${EUID:-$(id -u)}" == "0" ]]; then
    if [[ -d "${data_dir}/postgres" ]]; then
      find "${data_dir}" -mindepth 1 -maxdepth 1 ! -name postgres -exec chown -R 10001:10001 -- {} +
    else
      chown -R 10001:10001 "${data_dir}"
    fi
  elif [[ ! -w "${data_dir}" && ! -w "${data_dir}/images" ]]; then
    echo "[$(text prefix_error)] Docker data directory is not writable by the current user: ${data_dir}" >&2
    return 1
  fi
}

systemd_escape_path_value() {
  local value="${1-}"
  value="${value//\\/\\x5c}"
  value="${value//%/%%}"
  value="${value// /\\x20}"
  value="${value//$'\t'/\\x09}"
  value="${value//$'\n'/\\x0a}"
  printf '%s' "${value}"
}

python_process_matches_install() {
  local pid="$1"
  local expected_cwd=""
  local actual_cwd=""
  local cmdline=""
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  [[ -r "/proc/${pid}/cmdline" && -e "/proc/${pid}/cwd" ]] || return 1
  expected_cwd="$(cd "${INSTALL_DIR}" 2>/dev/null && pwd -P)" || return 1
  actual_cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
  cmdline="$(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null || true)"
  [[ "${actual_cwd}" == "${expected_cwd}" ]] || return 1
  [[ "${cmdline}" == *"scripts.run_uvicorn"* || "${cmdline}" == *"uv run"* ]]
}

stop_python_runtime() {
  local pid_file="${CHATGPT2API_PYTHON_PID_FILE:-${INSTALL_DIR}/data/chatgpt2api.pid}"
  local systemd_unit="/etc/systemd/system/chatgpt2api.service"
  local expected_working_directory=""
  local existing_pid=""
  local stop_deadline=""

  if [[ -d "${INSTALL_DIR}" ]]; then
    expected_working_directory="$(cd "${INSTALL_DIR}" 2>/dev/null && pwd -P || true)"
  fi

  if [[ -n "${expected_working_directory}" && "${EUID:-$(id -u)}" -eq 0 && -f "${systemd_unit}" ]] \
    && command -v systemctl >/dev/null 2>&1; then
    if grep -Fqx "WorkingDirectory=$(systemd_escape_path_value "${expected_working_directory}")" "${systemd_unit}"; then
      if systemctl is-active --quiet "chatgpt2api.service"; then
        ui_println "[$(text prefix_info)] stopping the previous managed Python service"
        if ! systemctl stop "chatgpt2api.service"; then
          echo "[$(text prefix_error)] failed to stop the previous managed Python service." >&2
          return 1
        fi
      fi
      if systemctl is-enabled --quiet "chatgpt2api.service"; then
        ui_println "[$(text prefix_info)] disabling the previous managed Python service"
        if ! systemctl disable "chatgpt2api.service" >/dev/null; then
          echo "[$(text prefix_error)] failed to disable the previous managed Python service." >&2
          return 1
        fi
      fi
    fi
  fi

  if [[ -f "${pid_file}" ]]; then
    existing_pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if python_process_matches_install "${existing_pid}"; then
      ui_println "[$(text prefix_info)] stopping the previous managed Python process"
      if kill -TERM "${existing_pid}" 2>/dev/null; then
        stop_deadline=$(( $(date +%s) + 15 ))
        while python_process_matches_install "${existing_pid}" && (( $(date +%s) < stop_deadline )); do
          sleep 1
        done
        if python_process_matches_install "${existing_pid}"; then
          kill -KILL "${existing_pid}" 2>/dev/null || true
        fi
      fi
    fi
    trash_path "${pid_file}" || true
  fi
}

stop_docker_runtime() {
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi
  if ! docker compose version >/dev/null 2>&1; then
    return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    return 0
  fi

  local compose_file=""
  for compose_file in \
    docker-compose.yml \
    docker-compose.warp.yml \
    docker-compose.local.yml \
    docker-compose.cluster-main.yml \
    docker-compose.cluster-worker.yml; do
    if [[ -f "${INSTALL_DIR}/${compose_file}" ]]; then
      ui_println "[$(text prefix_info)] stopping the previous managed Docker stack: ${compose_file}"
      local down_status=0
      case "${compose_file}" in
        docker-compose.cluster-main.yml)
          (
            cd "${INSTALL_DIR}" && \
            CHATGPT2API_CLUSTER_ID="${CHATGPT2API_CLUSTER_ID:-cluster-placeholder}" \
            WIREGUARD_SERVER_IP="${WIREGUARD_SERVER_IP:-10.77.0.1}" \
            POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-placeholder}" \
            POSTGRES_ADMIN_PASSWORD="${POSTGRES_ADMIN_PASSWORD:-placeholder}" \
            POSTGRES_PASSWORD_URLENCODED="${POSTGRES_PASSWORD_URLENCODED:-placeholder}" \
            POSTGRES_ADMIN_USER="${POSTGRES_ADMIN_USER:-chatgpt2api_admin}" \
            docker compose -f "${compose_file}" down --remove-orphans
          ) || down_status=$?
          ;;
        docker-compose.cluster-worker.yml)
          (
            cd "${INSTALL_DIR}" && \
            CHATGPT2API_CLUSTER_ID="${CHATGPT2API_CLUSTER_ID:-cluster-placeholder}" \
            CHATGPT2API_WORKER_ID="${CHATGPT2API_WORKER_ID:-worker-placeholder}" \
            CHATGPT2API_WIREGUARD_IP="${CHATGPT2API_WIREGUARD_IP:-10.77.0.11}" \
            CHATGPT2API_IMAGE_BASE_URL="${CHATGPT2API_IMAGE_BASE_URL:-http://127.0.0.1/images}" \
            APP_DATABASE_URL="${APP_DATABASE_URL:-postgresql://placeholder:placeholder@127.0.0.1:5432/chatgpt2api_app}" \
            IMAGE_QUEUE_DATABASE_URL="${IMAGE_QUEUE_DATABASE_URL:-postgresql://placeholder:placeholder@127.0.0.1:5432/chatgpt2api_image_queue}" \
            docker compose -f "${compose_file}" down --remove-orphans
          ) || down_status=$?
          ;;
        *)
          (
            cd "${INSTALL_DIR}" && \
            IMAGE_QUEUE_DATABASE_URL="${IMAGE_QUEUE_DATABASE_URL:-postgresql://placeholder:placeholder@127.0.0.1:5432/chatgpt2api_image_queue}" \
            docker compose -f "${compose_file}" down --remove-orphans
          ) || down_status=$?
          ;;
      esac
      if (( down_status != 0 )); then
        echo "[$(text prefix_error)] failed to stop the previous managed Docker stack: ${compose_file}" >&2
        return 1
      fi
    fi
  done
}

build_frontend() {
  if ! command -v npm >/dev/null 2>&1; then
    ui_println "[$(text prefix_warn)] $(text warn_no_npm)"
    exit 1
  fi

  ui_println "[$(text prefix_info)] $(text info_build_vue)"
  (cd "${INSTALL_DIR}/web-vue" && npm ci --no-audit --no-fund && npm run build)
  if [[ ! -d "${INSTALL_DIR}/web-vue/dist" ]]; then
    echo "[$(text prefix_error)] Frontend build did not produce web-vue/dist." >&2
    exit 1
  fi
  local staged_dir=""
  local previous_dir="${INSTALL_DIR}/web_dist.previous"
  if ! staged_dir="$(mktemp -d "${INSTALL_DIR}/.web_dist.staging.XXXXXX")"; then
    echo "[$(text prefix_error)] failed to create a unique frontend staging directory." >&2
    exit 1
  fi
  if ! cp -R "${INSTALL_DIR}/web-vue/dist/." "${staged_dir}/"; then
    trash_path "${staged_dir}" || true
    echo "[$(text prefix_error)] failed to stage the frontend build." >&2
    exit 1
  fi
  if [[ -e "${INSTALL_DIR}/web_dist" || -L "${INSTALL_DIR}/web_dist" ]]; then
    if [[ ! -d "${INSTALL_DIR}/web_dist" || -L "${INSTALL_DIR}/web_dist" ]]; then
      echo "[$(text prefix_error)] existing web_dist is not a regular directory." >&2
      trash_path "${staged_dir}" || true
      exit 1
    fi
    if [[ -e "${previous_dir}" || -L "${previous_dir}" ]]; then
      trash_path "${previous_dir}" || true
    fi
    if ! mv "${INSTALL_DIR}/web_dist" "${previous_dir}"; then
      trash_path "${staged_dir}" || true
      echo "[$(text prefix_error)] failed to preserve the previous frontend build." >&2
      exit 1
    fi
  fi
  if ! mv "${staged_dir}" "${INSTALL_DIR}/web_dist"; then
    if [[ -d "${previous_dir}" && ! -e "${INSTALL_DIR}/web_dist" ]]; then
      mv "${previous_dir}" "${INSTALL_DIR}/web_dist" || true
    fi
    trash_path "${staged_dir}" || true
    echo "[$(text prefix_error)] failed to publish the frontend build." >&2
    exit 1
  fi
}

build_image_upscale_runtime() {
  if ! command -v npm >/dev/null 2>&1; then
    ui_println "[$(text prefix_warn)] $(text warn_no_npm)"
    exit 1
  fi

  local package_dir="${INSTALL_DIR}/scripts/image_upscale"
  if [[ ! -f "${package_dir}/package.json" || ! -f "${package_dir}/package-lock.json" ]]; then
    echo "[$(text prefix_error)] image-upscale package manifests are missing from ${package_dir}." >&2
    exit 1
  fi
  ui_println "[$(text prefix_info)] installing Sharp image-upscale runtime"
  (cd "${package_dir}" && npm ci --omit=dev --no-audit --no-fund)
  if [[ ! -d "${package_dir}/node_modules/sharp" ]]; then
    echo "[$(text prefix_error)] Sharp image-upscale runtime was not installed." >&2
    exit 1
  fi
}

run_python() {
  ensure_uv
  build_frontend
  build_image_upscale_runtime
  ui_println "[$(text prefix_info)] $(text info_install_py)"
  export STORAGE_BACKEND="${STORAGE_BACKEND}"
  export APP_DATABASE_URL="${APP_DATABASE_URL}"
  export DATABASE_URL="${DATABASE_URL}"
  export IMAGE_QUEUE_DATABASE_URL="${IMAGE_QUEUE_DATABASE_URL}"
  export CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS="${CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS}"
  export CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS="${CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS}"
  (cd "${INSTALL_DIR}" && "${UV_BIN}" sync --frozen)
  (cd "${INSTALL_DIR}" && "${UV_BIN}" run --frozen python -m scripts.bootstrap_database_roles)

  stop_docker_runtime

  ui_println "[$(text prefix_info)] $(text info_start_app) http://0.0.0.0:${PORT}"
  cd "${INSTALL_DIR}"
  export CHATGPT2API_AUTH_KEY="${AUTH_KEY}"
  export CHATGPT2API_THREAD_TOKENS="${THREAD_TOKENS}"
  export CHATGPT2API_BASE_URL="${BASE_URL}"
  export CHATGPT2API_IMAGE_BASE_URL="${IMAGE_BASE_URL}"
  export CHATGPT2API_IMAGE_PORT="${IMAGE_PORT}"
  export CHATGPT2API_NODE_ROLE="${NODE_ROLE}"
  export CHATGPT2API_RUN_API="${RUN_API}"
  export CHATGPT2API_RUN_WORKER="${RUN_WORKER}"
  export CHATGPT2API_WORKER_ID="${WORKER_ID}"
  export CHATGPT2API_WIREGUARD_IP="${WIREGUARD_IP}"
  export CHATGPT2API_WORKER_JOINED_MARKER_FILE="${WORKER_JOINED_MARKER_FILE}"
  export PORT="${PORT}"
  export CHATGPT2API_BACKUP_PASSPHRASE="${CHATGPT2API_BACKUP_PASSPHRASE}"
  export CHATGPT2API_MONITOR_COMPLETED_LIMIT="${CHATGPT2API_MONITOR_COMPLETED_LIMIT}"
  export CHATGPT2API_MONITOR_EVENT_LIMIT="${CHATGPT2API_MONITOR_EVENT_LIMIT}"
  export CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS="${CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS}"
  export CHATGPT2API_RUNTIME_LOG_FILE="${CHATGPT2API_RUNTIME_LOG_FILE}"
  export HOST="${HOST}"
  export LOG_LEVEL="${LOG_LEVEL}"
  export UVICORN_WORKERS="${UVICORN_WORKERS}"
  export STORAGE_BACKEND="${STORAGE_BACKEND}"
  export APP_DATABASE_URL="${APP_DATABASE_URL}"
  export DATABASE_URL="${DATABASE_URL}"
  export IMAGE_QUEUE_DATABASE_URL="${IMAGE_QUEUE_DATABASE_URL}"
  export IMAGE_QUEUE_INSTANCE_ID="${IMAGE_QUEUE_INSTANCE_ID}"
  export IMAGE_QUEUE_VERIFY_RETURNED_URL="${IMAGE_QUEUE_VERIFY_RETURNED_URL}"
  export IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS}"
  export IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS}"
  export IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES="${IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES}"
  export IMAGE_PROMPT_SUFFIX_ENABLED="${IMAGE_PROMPT_SUFFIX_ENABLED}"
  export IMAGE_PROMPT_SUFFIX="${IMAGE_PROMPT_SUFFIX}"
  export IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS="${IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS}"
  export IMAGE_QUEUE_DELIVERY_GRACE_SECONDS="${IMAGE_QUEUE_DELIVERY_GRACE_SECONDS}"
  export IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS="${IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS}"
  export IMAGE_QUEUE_STARTUP_RETRY_SECONDS="${IMAGE_QUEUE_STARTUP_RETRY_SECONDS}"
  export IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS="${IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS}"
  export IMAGE_QUEUE_GENERATION_CONCURRENCY="${IMAGE_QUEUE_GENERATION_CONCURRENCY}"
  export IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP="${IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP}"
  export IMAGE_QUEUE_ABSOLUTE_GUARD="${IMAGE_QUEUE_ABSOLUTE_GUARD}"
  export IMAGE_QUEUE_MAX_BACKLOG="${IMAGE_QUEUE_MAX_BACKLOG}"
  export IMAGE_QUEUE_PENDING_TTL_SECONDS="${IMAGE_QUEUE_PENDING_TTL_SECONDS}"
  export IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS="${IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS}"
  export IMAGE_QUEUE_LEGACY_TASK_PATH="${IMAGE_QUEUE_LEGACY_TASK_PATH}"
  export IMAGE_QUEUE_LEASE_SECONDS="${IMAGE_QUEUE_LEASE_SECONDS}"
  export IMAGE_QUEUE_HEARTBEAT_SECONDS="${IMAGE_QUEUE_HEARTBEAT_SECONDS}"
  export IMAGE_QUEUE_POLL_INTERVAL_SECONDS="${IMAGE_QUEUE_POLL_INTERVAL_SECONDS}"
  export IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS="${IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS}"
  export IMAGE_QUEUE_GENERATION_ATTEMPTS="${IMAGE_QUEUE_GENERATION_ATTEMPTS}"
  export IMAGE_QUEUE_DOWNLOAD_ATTEMPTS="${IMAGE_QUEUE_DOWNLOAD_ATTEMPTS}"
  export IMAGE_QUEUE_SAVE_ATTEMPTS="${IMAGE_QUEUE_SAVE_ATTEMPTS}"
  export IMAGE_QUEUE_CPU_THROTTLE_PERCENT="${IMAGE_QUEUE_CPU_THROTTLE_PERCENT}"
  export IMAGE_QUEUE_CPU_PAUSE_PERCENT="${IMAGE_QUEUE_CPU_PAUSE_PERCENT}"
  export IMAGE_QUEUE_CPU_RESUME_PERCENT="${IMAGE_QUEUE_CPU_RESUME_PERCENT}"
  export IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT="${IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT}"
  export IMAGE_QUEUE_MEMORY_PAUSE_PERCENT="${IMAGE_QUEUE_MEMORY_PAUSE_PERCENT}"
  export IMAGE_QUEUE_MEMORY_REJECT_PERCENT="${IMAGE_QUEUE_MEMORY_REJECT_PERCENT}"
  export IMAGE_QUEUE_DB_POOL_SIZE="${IMAGE_QUEUE_DB_POOL_SIZE}"
  export IMAGE_QUEUE_DB_MAX_OVERFLOW="${IMAGE_QUEUE_DB_MAX_OVERFLOW}"
  export EDITABLE_FILE_WORKERS="${EDITABLE_FILE_WORKERS}"
  export EDITABLE_FILE_MAX_BACKLOG="${EDITABLE_FILE_MAX_BACKLOG}"
  export PROMPT_LIBRARY_DEFAULT_URL="${PROMPT_LIBRARY_DEFAULT_URL}"
  export PROMPT_LIBRARY_REMOTE_URL="${PROMPT_LIBRARY_REMOTE_URL}"
  export CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS="${CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS}"
  export CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS="${CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS}"
  export CHATGPT2API_PROXY_RUNTIME_ENABLED="${CHATGPT2API_PROXY_RUNTIME_ENABLED}"
  export CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE="${CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE}"
  export CHATGPT2API_PROXY_RUNTIME_PROXY_URL="${CHATGPT2API_PROXY_RUNTIME_PROXY_URL}"
  export CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL="${CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL}"
  export CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY="${CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY}"
  export CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES="${CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES}"
  export CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED}"
  export CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE}"
  export CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC}"
  export CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL}"
  export CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START="${CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START}"
  export CHATGPT2API_PROXY_RUNTIME_BROWSER="${CHATGPT2API_PROXY_RUNTIME_BROWSER}"
  export CHATGPT2API_PROXY_RUNTIME_USER_AGENT="${CHATGPT2API_PROXY_RUNTIME_USER_AGENT}"
  export CHATGPT2API_FLARESOLVERR_URL="${CHATGPT2API_FLARESOLVERR_URL}"
  export WARP_LICENSE_KEY="${WARP_LICENSE_KEY}"
  export GIT_REPO_URL="${GIT_REPO_URL}"
  export GIT_TOKEN="${GIT_TOKEN}"
  export GIT_BRANCH="${GIT_BRANCH}"
  export GIT_FILE_PATH="${GIT_FILE_PATH}"
  export GIT_AUTH_KEYS_FILE_PATH="${GIT_AUTH_KEYS_FILE_PATH}"
  local pid_file="${CHATGPT2API_PYTHON_PID_FILE:-${INSTALL_DIR}/data/chatgpt2api.pid}"
  local log_file="${INSTALL_DIR}/data/chatgpt2api-python.log"
  local launcher_file="${INSTALL_DIR}/run-python.sh"
  local systemd_unit="/etc/systemd/system/chatgpt2api.service"
  mkdir -p "$(dirname "${pid_file}")"

  local uv_bin_escaped=""
  local install_dir_escaped=""
  local env_file_escaped=""
  uv_bin_escaped="$(printf '%q' "${UV_BIN}")"
  install_dir_escaped="$(printf '%q' "${INSTALL_DIR}")"
  env_file_escaped="$(printf '%q' "${INSTALL_DIR}/.env")"
  cat >"${launcher_file}" <<EOF
#!/usr/bin/env bash
 set -euo pipefail
 cd ${install_dir_escaped}
 export CHATGPT2API_ENV_FILE=${env_file_escaped}
exec ${uv_bin_escaped} run --frozen python -m scripts.run_uvicorn
EOF
  chmod 700 "${launcher_file}"

  if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1 && [[ "${EUID:-$(id -u)}" == "0" ]]; then
    local systemd_install_dir=""
    local systemd_launcher_file=""
    local systemd_log_file=""
    systemd_install_dir="$(systemd_escape_path_value "${INSTALL_DIR}")"
    systemd_launcher_file="$(systemd_escape_path_value "${launcher_file}")"
    systemd_log_file="$(systemd_escape_path_value "${log_file}")"
    cat >"${systemd_unit}" <<EOF
[Unit]
Description=chatgpt2api Python application
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${systemd_install_dir}
ExecStart=/bin/bash ${systemd_launcher_file}
Restart=on-failure
RestartSec=5
StandardOutput=append:${systemd_log_file}
StandardError=append:${systemd_log_file}

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "chatgpt2api.service" >/dev/null
    systemctl restart "chatgpt2api.service"

    local app_pid=""
    app_pid="$(systemctl show "chatgpt2api.service" --property=MainPID --value 2>/dev/null || true)"
    if [[ ! "${app_pid}" =~ ^[1-9][0-9]*$ ]]; then
      app_pid=""
    fi
    if ! wait_python_runtime_health "${PORT}" "${app_pid}" "${pid_file}" "${log_file}"; then
      return 1
    fi
    printf '%s\n' "${app_pid:-0}" >"${pid_file}"
    return 0
  fi

  if [[ -f "${pid_file}" ]]; then
    local existing_pid=""
    existing_pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if python_process_matches_install "${existing_pid}"; then
      kill "${existing_pid}" 2>/dev/null || true
      local stop_deadline=$(( $(date +%s) + 15 ))
      while python_process_matches_install "${existing_pid}" && (( $(date +%s) < stop_deadline )); do
        sleep 1
      done
      if python_process_matches_install "${existing_pid}"; then
        kill -KILL "${existing_pid}" 2>/dev/null || true
      fi
    elif [[ "${existing_pid}" =~ ^[1-9][0-9]*$ ]] && kill -0 "${existing_pid}" 2>/dev/null; then
      ui_println "[$(text prefix_warn)] ignoring stale Python PID file ${pid_file}; PID ${existing_pid} does not belong to this installation."
    fi
    trash_path "${pid_file}"
  fi
  nohup "${launcher_file}" >"${log_file}" 2>&1 &
  local app_pid=$!
  printf '%s\n' "${app_pid}" >"${pid_file}"
  if ! wait_python_runtime_health "${PORT}" "${app_pid}" "${pid_file}" "${log_file}"; then
    kill "${app_pid}" 2>/dev/null || true
    wait "${app_pid}" 2>/dev/null || true
    trash_path "${pid_file}"
    return 1
  fi
}

wait_python_liveness() {
  wait_python_health "$1" "$2" "$3" "$4" "liveness"
}

wait_python_runtime_health() {
  wait_python_health "$1" "$2" "$3" "$4" "runtime"
}

wait_python_health() {
  local port="$1"
  local app_pid="$2"
  local pid_file="$3"
  local log_file="$4"
  local scope="$5"
  need_cmd curl
  local url=""
  if [[ "${scope}" == "runtime" ]]; then
    url="http://127.0.0.1:${port}/health?format=json&scope=runtime"
  else
    url="http://127.0.0.1:${port}/health/live?format=json"
  fi
  local attempt=""
  for attempt in $(seq 1 60); do
    if [[ -n "${app_pid}" && "${app_pid}" != "0" ]] && ! kill -0 "${app_pid}" 2>/dev/null; then
      echo "[$(text prefix_error)] Python app exited before becoming ${scope}-ready; recent logs:" >&2
      tail -n 120 "${log_file}" >&2 || true
      return 1
    fi
    if curl --fail --silent --show-error --max-time 5 "${url}" >/dev/null 2>&1; then
      ui_println "[$(text prefix_done)] Python app is ${scope}-ready: ${url}"
      return 0
    fi
    sleep 2
  done
  echo "[$(text prefix_error)] Python app did not become ${scope}-ready; recent logs:" >&2
  tail -n 120 "${log_file}" >&2 || true
  return 1
}

WIREGUARD_SERVER_IP="${WIREGUARD_SERVER_IP:-10.77.0.1}"
WIREGUARD_PORT="${WIREGUARD_PORT:-51820}"
WIREGUARD_SERVER_ENDPOINT="${WIREGUARD_SERVER_ENDPOINT:-}"
JOIN_TTL_SECONDS="${JOIN_TTL_SECONDS:-604800}"
JOIN_ACTIVATION_GRACE_SECONDS="${JOIN_ACTIVATION_GRACE_SECONDS:-900}"
CLUSTER_ID="${CHATGPT2API_CLUSTER_ID:-${CLUSTER_ID:-}}"
JOIN_NONCE="${JOIN_NONCE:-}"
JOIN_RELEASE_REF="${JOIN_RELEASE_REF:-}"
JOIN_CHATGPT2API_IMAGE="${JOIN_CHATGPT2API_IMAGE:-}"
JOIN_CHATGPT2API_IMAGE_DIGEST="${JOIN_CHATGPT2API_IMAGE_DIGEST:-}"
JOIN_UV_VERSION="${JOIN_UV_VERSION:-}"
WORKER_JOINED_MARKER_FILE="${WORKER_JOINED_MARKER_FILE:-/app/data/worker.joined}"
WIREGUARD_INTERFACE="${WIREGUARD_INTERFACE:-wg-chatgpt2api}"
WORKER_WIREGUARD_CONFIG_ACTIVE="${WORKER_WIREGUARD_CONFIG_ACTIVE:-0}"

cluster_resolve_wireguard_interface() {
  if [[ "${WIREGUARD_INTERFACE}" == "wg-chatgpt2api" && ! -f "/etc/wireguard/${WIREGUARD_INTERFACE}.conf" && -f /etc/wireguard/wg0.conf ]] && grep -q '^# chatgpt2api managed WireGuard' /etc/wireguard/wg0.conf; then
    # Preserve a managed wg0 deployment from earlier cluster releases. Never
    # select or overwrite an unrelated wg0 interface.
    WIREGUARD_INTERFACE="wg0"
  fi
  if [[ ! "${WIREGUARD_INTERFACE}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$ ]]; then
    echo "[$(text prefix_error)] invalid WireGuard interface name: ${WIREGUARD_INTERFACE}" >&2
    exit 1
  fi
}

cluster_wireguard_config_file() {
  cluster_resolve_wireguard_interface
  printf '/etc/wireguard/%s.conf' "${WIREGUARD_INTERFACE}"
}

cluster_worker_ip() {
  local worker_no="$1"
  if [[ -z "${worker_no}" || ! "${worker_no}" =~ ^[1-9][0-9]*$ || "${worker_no}" -gt 244 ]]; then
    echo "[$(text prefix_error)] worker number must be 1-244." >&2
    exit 1
  fi
  printf '10.77.0.%d' "$((10 + worker_no))"
}

cluster_validate_wireguard_server_ip() {
  if [[ "${WIREGUARD_SERVER_IP:-10.77.0.1}" != "10.77.0.1" ]]; then
    echo "[$(text prefix_error)] WIREGUARD_SERVER_IP must be 10.77.0.1 for chatgpt2api cluster database private network." >&2
    exit 1
  fi
}

cluster_validate_wireguard_port() {
  local port="${WIREGUARD_PORT:-}"
  if [[ "${port}" != "51820" ]]; then
    echo "[$(text prefix_error)] WireGuard port must be 51820/udp for chatgpt2api cluster private network." >&2
    exit 1
  fi
}

cluster_validate_wireguard_endpoint() {
  local endpoint="${1:-${WIREGUARD_SERVER_ENDPOINT:-}}"
  if [[ "${endpoint}" =~ ^\[[0-9A-Fa-f:]+\]$ ]]; then
    return 0
  fi
  if [[ "${endpoint}" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
    return 0
  fi
  echo "[$(text prefix_error)] WireGuard endpoint must be a hostname, IPv4 address, or bracketed IPv6 address." >&2
  exit 1
}

cluster_validate_worker_public_image_base_url() {
  local image_base_url="${1:-${IMAGE_BASE_URL:-}}"
  local public_entry_mode="${2:-${CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE:-direct}}"
  local url="${image_base_url%/}"
  local scheme=""
  local remainder=""
  local authority=""
  local path=""
  if [[ "${public_entry_mode}" != "direct" && "${public_entry_mode}" != "proxy" ]]; then
    echo "[$(text prefix_error)] CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE must be direct or proxy." >&2
    return 1
  fi
  case "${url}" in
    http://*)
      scheme="http"
      remainder="${url#http://}"
      ;;
    https://*)
      scheme="https"
      remainder="${url#https://}"
      ;;
    *)
      echo "[$(text prefix_error)] worker image base URL must be an http or https URL without query or fragment." >&2
      return 1
      ;;
  esac
  if [[ "${public_entry_mode}" == "direct" && "${scheme}" != "http" ]]; then
    echo "[$(text prefix_error)] direct worker public entry requires an http image base URL." >&2
    return 1
  fi
  if [[ "${remainder}" == *"?"* || "${remainder}" == *"#"* ]]; then
    echo "[$(text prefix_error)] worker image base URL must be an http or https URL without credentials, query or fragment." >&2
    return 1
  fi
  authority="${remainder%%/*}"
  if [[ "${remainder}" == */* ]]; then
    path="/${remainder#*/}"
    path="${path%/}"
  fi
  if [[ -z "${authority}" || "${authority}" == *"@"* ]]; then
    echo "[$(text prefix_error)] worker image base URL must be an http or https URL without credentials, query or fragment." >&2
    return 1
  fi
  if [[ -n "${path}" && "${path}" != "/images" ]]; then
    echo "[$(text prefix_error)] worker image base URL path must be empty or /images." >&2
    return 1
  fi
  return 0
}

dotenv_key_allowed() {
  case "$1" in
    AUTH_KEY|BASE_URL|IMAGE_BASE_URL|IMAGE_PORT|WORKER_ID|WIREGUARD_IP|NODE_ROLE|CLUSTER_ID| \
     CHATGPT2API_AUTH_KEY|CHATGPT2API_BACKUP_PASSPHRASE|CHATGPT2API_CONFIG_FILE| \
     CHATGPT2API_MONITOR_COMPLETED_LIMIT|CHATGPT2API_MONITOR_EVENT_LIMIT| \
     CHATGPT2API_PORT|CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS|CHATGPT2API_RUNTIME_LOG_FILE| \
     CHATGPT2API_THREAD_TOKENS|CHATGPT2API_IMAGE| \
     CHATGPT2API_IMAGE_DIGEST|CHATGPT2API_RELEASE_REF|UV_VERSION| \
     CHATGPT2API_BASE_URL|CHATGPT2API_IMAGE_BASE_URL|CHATGPT2API_PYTHON_BIN|CHATGPT2API_IMAGE_PORT|CHATGPT2API_NODE_ROLE|CHATGPT2API_INSTALL_TARGET|CHATGPT2API_CREATE_FIRST_WORKER| \
    CHATGPT2API_WARP_IMAGE|CHATGPT2API_PRIVOXY_IMAGE|CHATGPT2API_FLARESOLVERR_IMAGE| \
     CHATGPT2API_RUN_API|CHATGPT2API_RUN_WORKER|CHATGPT2API_WORKER_ID|CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE| \
     CHATGPT2API_WORKER_BIND_HOST|CHATGPT2API_WIREGUARD_IP| \
    CHATGPT2API_CLUSTER_ID|CHATGPT2API_WORKER_JOINED_MARKER_FILE|CHATGPT2API_IMAGE_QUEUE_INSTANCE_ID| \
    CHATGPT2API_IMAGE_QUEUE_VERIFY_RETURNED_URL|CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS| \
    CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS|CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES| \
    IMAGE_PROMPT_SUFFIX_ENABLED|IMAGE_PROMPT_SUFFIX| \
    IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS|IMAGE_QUEUE_DELIVERY_GRACE_SECONDS| \
    IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS|IMAGE_QUEUE_STARTUP_RETRY_SECONDS| \
    IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS|IMAGE_QUEUE_GENERATION_CONCURRENCY| \
    IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP|IMAGE_QUEUE_ABSOLUTE_GUARD|IMAGE_QUEUE_MAX_BACKLOG| \
     IMAGE_QUEUE_PENDING_TTL_SECONDS|IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS| \
     CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS|CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS| \
    CHATGPT2API_PROXY_RUNTIME_ENABLED|CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE| \
    CHATGPT2API_PROXY_RUNTIME_PROXY_URL|CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL| \
    CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY|CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES| \
    CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED|CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE| \
    CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC|CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL| \
    CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START|CHATGPT2API_PROXY_RUNTIME_BROWSER| \
    CHATGPT2API_PROXY_RUNTIME_USER_AGENT|CHATGPT2API_FLARESOLVERR_URL|WARP_LICENSE_KEY| \
     CHATGPT2API_PYTHON_PID_FILE| \
     MODE|WITH_WARP| \
     WIREGUARD_INTERFACE|WIREGUARD_SERVER_IP|WIREGUARD_SERVER_ENDPOINT|WIREGUARD_PORT| \
    STORAGE_BACKEND|APP_DATABASE_URL|DATABASE_URL|IMAGE_QUEUE_DATABASE_URL|IMAGE_QUEUE_INSTANCE_ID| \
    IMAGE_QUEUE_VERIFY_RETURNED_URL|IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS| \
    IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS|IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES| \
    IMAGE_QUEUE_ARTIFACT_ROOT|IMAGE_QUEUE_LEGACY_TASK_PATH|IMAGE_QUEUE_LEASE_SECONDS| \
    IMAGE_QUEUE_HEARTBEAT_SECONDS|IMAGE_QUEUE_POLL_INTERVAL_SECONDS|IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS| \
    IMAGE_QUEUE_GENERATION_ATTEMPTS|IMAGE_QUEUE_DOWNLOAD_ATTEMPTS|IMAGE_QUEUE_SAVE_ATTEMPTS| \
    IMAGE_QUEUE_CPU_THROTTLE_PERCENT|IMAGE_QUEUE_CPU_PAUSE_PERCENT|IMAGE_QUEUE_CPU_RESUME_PERCENT| \
    IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT|IMAGE_QUEUE_MEMORY_PAUSE_PERCENT|IMAGE_QUEUE_MEMORY_REJECT_PERCENT| \
    IMAGE_QUEUE_DB_POOL_SIZE|IMAGE_QUEUE_DB_MAX_OVERFLOW| \
    EDITABLE_FILE_WORKERS|EDITABLE_FILE_MAX_BACKLOG|PROMPT_LIBRARY_DEFAULT_URL|PROMPT_LIBRARY_REMOTE_URL| \
    POSTGRES_PASSWORD|POSTGRES_ADMIN_USER|POSTGRES_ADMIN_PASSWORD|POSTGRES_PASSWORD_URLENCODED| \
    GIT_REPO_URL|GIT_TOKEN|GIT_BRANCH|GIT_FILE_PATH|GIT_AUTH_KEYS_FILE_PATH| \
    WARP_SOCKS_PORT|PRIVOXY_PORT|FLARESOLVERR_PORT|FLARESOLVERR_LOG_LEVEL|HOST|LOG_LEVEL|UVICORN_WORKERS|TZ| \
CHATGPT2API_MAIN_LIVENESS_TIMEOUT_SECONDS)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

dotenv_decode_single_quoted() {
  local input="$1"
  local output=""
  local index=0
  local char=""
  local escaped_quote=""

  while (( index < ${#input} )); do
    char="${input:index:1}"
    if [[ "${char}" == "'" ]]; then
      escaped_quote="${input:index:4}"
      if [[ "${escaped_quote}" != "'\\''" ]]; then
        return 1
      fi
      output+="'"
      index=$((index + 4))
      continue
    fi
    output+="${char}"
    index=$((index + 1))
  done

  printf -v DOTENV_DECODED_VALUE '%s' "${output}"
}

dotenv_parse_value() {
  local raw="$1"
  local value=""
  local first="${raw:0:1}"
  local last="${raw: -1}"

  value="$(printf '%s' "${raw}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  if [[ -z "${value}" ]]; then
    DOTENV_DECODED_VALUE=""
    return 0
  fi

  first="${value:0:1}"
  last="${value: -1}"
  if [[ "${first}" == "'" ]]; then
    [[ "${last}" == "'" && ${#value} -ge 2 ]] || return 1
    dotenv_decode_single_quoted "${value:1:${#value}-2}" || return 1
    value="${DOTENV_DECODED_VALUE}"
  elif [[ "${first}" == '"' ]]; then
    [[ "${last}" == '"' && ${#value} -ge 2 ]] || return 1
    value="${value:1:${#value}-2}"
    [[ "${value}" != *'"'* ]] || return 1
  else
    [[ "${value}" != *[[:space:]]* ]] || return 1
  fi

  case "${value}" in
    *'$('*|*'`'*|*'${'*)
      return 1
      ;;
  esac
  DOTENV_DECODED_VALUE="${value}"
}

dotenv_load_file() {
  local env_file="$1"
  local line=""
  local key=""
  local raw_value=""

  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" == *=* ]] || { echo "invalid dotenv line in ${env_file}" >&2; return 1; }
    key="${line%%=*}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "invalid dotenv key in ${env_file}: ${key}" >&2; return 1; }
    dotenv_key_allowed "${key}" || { echo "unsupported dotenv key in ${env_file}: ${key}" >&2; return 1; }
    raw_value="${line#*=}"
    dotenv_parse_value "${raw_value}" || { echo "invalid dotenv value in ${env_file}: ${key}" >&2; return 1; }
    printf -v "${key}" '%s' "${DOTENV_DECODED_VALUE}"
  done <"${env_file}"
}

dotenv_load_file_preserving_exported() {
  local env_file="$1"
  local line=""
  local key=""
  local raw_value=""

  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" == *=* ]] || { echo "invalid dotenv line in ${env_file}" >&2; return 1; }
    key="${line%%=*}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "invalid dotenv key in ${env_file}: ${key}" >&2; return 1; }
    dotenv_key_allowed "${key}" || { echo "unsupported dotenv key in ${env_file}: ${key}" >&2; return 1; }
    raw_value="${line#*=}"
    dotenv_parse_value "${raw_value}" || { echo "invalid dotenv value in ${env_file}: ${key}" >&2; return 1; }
    if declare -p "${key}" 2>/dev/null | grep -q 'declare -x'; then
      continue
    fi
    printf -v "${key}" '%s' "${DOTENV_DECODED_VALUE}"
  done <"${env_file}"
}

dotenv_read_value() {
  local env_file="$1"
  local key="$2"
  (
    unset "${key}" 2>/dev/null || true
    dotenv_load_file "${env_file}"
    printf '%s' "${!key-}"
  )
}

dotenv_has_key() {
  local env_file="$1"
  local key="$2"
  grep -Eq "^[[:space:]]*${key}[[:space:]]*=" "${env_file}"
}

infer_existing_storage_backend() {
  local configured="${1:-}"
  if [[ -n "${configured}" ]]; then
    normalize_storage_choice "${configured}"
    return
  fi

  local database_url="${DATABASE_URL:-}"
  case "${database_url,,}" in
    sqlite:*|sqlite3:*)
      printf 'sqlite'
      return
      ;;
    postgresql:*|postgresql+psycopg2:*|postgres://*)
      printf 'postgres'
      return
      ;;
  esac

  if [[ -f "${INSTALL_DIR}/data/accounts.json" || -f "${INSTALL_DIR}/data/auth_keys.json" ]]; then
    printf 'json'
    return
  fi
  if [[ -f "${INSTALL_DIR}/data/accounts.db" ]]; then
    printf 'sqlite'
    return
  fi

  printf 'postgres'
}

cluster_load_env() {
  load_existing_install_env
}

load_existing_install_env() {
  local env_file="${INSTALL_DIR}/.env"
  if [[ -f "${env_file}" ]]; then
    INSTALL_EXISTING="1"
    local storage_backend_declared="0"
    local env_release_ref=""
    local release_env_allowed="1"
    local previous_branch="${BRANCH-}"
    local previous_release_ref="${CHATGPT2API_RELEASE_REF-}"
    local previous_image="${CHATGPT2API_IMAGE-}"
    local previous_image_digest="${CHATGPT2API_IMAGE_DIGEST-}"
    local previous_warp_image="${CHATGPT2API_WARP_IMAGE-}"
    local previous_privoxy_image="${CHATGPT2API_PRIVOXY_IMAGE-}"
    local previous_flaresolverr_image="${CHATGPT2API_FLARESOLVERR_IMAGE-}"
    local previous_uv_version="${UV_VERSION-}"

    if dotenv_has_key "${env_file}" STORAGE_BACKEND; then
      storage_backend_declared="1"
    fi
    if release_ref_override_active; then
      env_release_ref="$(dotenv_read_value "${env_file}" CHATGPT2API_RELEASE_REF || true)"
      if [[ -z "${env_release_ref}" || "${env_release_ref}" != "$(release_ref_override_value)" ]]; then
        release_env_allowed="0"
      fi
    fi
    if ! dotenv_load_file_preserving_exported "${env_file}"; then
      return 1
    fi
    AUTH_KEY="${CHATGPT2API_AUTH_KEY:-${AUTH_KEY:-}}"
    PORT="${CHATGPT2API_PORT:-${PORT:-3000}}"
    THREAD_TOKENS="${CHATGPT2API_THREAD_TOKENS:-${THREAD_TOKENS:-80}}"
    BASE_URL="${CHATGPT2API_BASE_URL:-${BASE_URL:-}}"
    IMAGE_BASE_URL="${CHATGPT2API_IMAGE_BASE_URL:-${IMAGE_BASE_URL:-}}"
    PYTHON_BIN="${CHATGPT2API_PYTHON_BIN:-${PYTHON_BIN:-python3}}"
    IMAGE_PORT="${CHATGPT2API_IMAGE_PORT:-${IMAGE_PORT:-3000}}"
    NODE_ROLE="${CHATGPT2API_NODE_ROLE:-${NODE_ROLE:-standalone}}"
    INSTALL_TARGET="${CHATGPT2API_INSTALL_TARGET:-${INSTALL_TARGET:-${NODE_ROLE:-standalone}}}"
    RUN_API="${CHATGPT2API_RUN_API:-${RUN_API:-}}"
    RUN_WORKER="${CHATGPT2API_RUN_WORKER:-${RUN_WORKER:-}}"
    CREATE_FIRST_WORKER="${CHATGPT2API_CREATE_FIRST_WORKER:-${CREATE_FIRST_WORKER:-}}"
    WORKER_ID="${CHATGPT2API_WORKER_ID:-${WORKER_ID:-}}"
    WIREGUARD_IP="${CHATGPT2API_WIREGUARD_IP:-${WIREGUARD_IP:-}}"
    CLUSTER_ID="${CHATGPT2API_CLUSTER_ID:-${CLUSTER_ID:-}}"
    CHATGPT2API_IMAGE="${CHATGPT2API_IMAGE:-}"
    CHATGPT2API_IMAGE_DIGEST="${CHATGPT2API_IMAGE_DIGEST:-}"
    CHATGPT2API_WARP_IMAGE="${CHATGPT2API_WARP_IMAGE:-${DEFAULT_CHATGPT2API_WARP_IMAGE}}"
    CHATGPT2API_PRIVOXY_IMAGE="${CHATGPT2API_PRIVOXY_IMAGE:-${DEFAULT_CHATGPT2API_PRIVOXY_IMAGE}}"
    CHATGPT2API_FLARESOLVERR_IMAGE="${CHATGPT2API_FLARESOLVERR_IMAGE:-${DEFAULT_CHATGPT2API_FLARESOLVERR_IMAGE}}"
    CHATGPT2API_BASE_URL="${CHATGPT2API_BASE_URL:-${BASE_URL:-}}"
    CHATGPT2API_IMAGE_BASE_URL="${CHATGPT2API_IMAGE_BASE_URL:-${IMAGE_BASE_URL:-}}"
    CHATGPT2API_IMAGE_PORT="${CHATGPT2API_IMAGE_PORT:-${IMAGE_PORT:-}}"
    MODE="${MODE:-}"
    WITH_WARP="${WITH_WARP:-0}"
    BRANCH="${CHATGPT2API_RELEASE_REF:-${BRANCH:-}}"
    UV_VERSION="${UV_VERSION:-0.8.17}"
    APP_DATABASE_URL="${APP_DATABASE_URL:-}"
    DATABASE_URL="${DATABASE_URL:-}"
    IMAGE_QUEUE_DATABASE_URL="${IMAGE_QUEUE_DATABASE_URL:-}"
    if [[ "${storage_backend_declared}" != "1" ]]; then
      STORAGE_BACKEND="$(infer_existing_storage_backend)"
    fi
    IMAGE_QUEUE_INSTANCE_ID="${IMAGE_QUEUE_INSTANCE_ID:-}"
    CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS="${CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS:-30}"
    CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS="${CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS:-2}"
    IMAGE_QUEUE_VERIFY_RETURNED_URL="${IMAGE_QUEUE_VERIFY_RETURNED_URL:-true}"
    IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS:-5}"
    IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS:-3}"
    IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES="${IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES:-65536}"
    IMAGE_PROMPT_SUFFIX_ENABLED="${IMAGE_PROMPT_SUFFIX_ENABLED:-true}"
    IMAGE_PROMPT_SUFFIX="${IMAGE_PROMPT_SUFFIX:-}"
    IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS="${IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS:-900}"
    IMAGE_QUEUE_DELIVERY_GRACE_SECONDS="${IMAGE_QUEUE_DELIVERY_GRACE_SECONDS:-604800}"
    IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS="${IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS:-2592000}"
    IMAGE_QUEUE_STARTUP_RETRY_SECONDS="${IMAGE_QUEUE_STARTUP_RETRY_SECONDS:-5}"
    IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS="${IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS:-300}"
    IMAGE_QUEUE_GENERATION_CONCURRENCY="${IMAGE_QUEUE_GENERATION_CONCURRENCY:-}"
    IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP="${IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP:-99999}"
    IMAGE_QUEUE_ABSOLUTE_GUARD="${IMAGE_QUEUE_ABSOLUTE_GUARD:-}"
    IMAGE_QUEUE_MAX_BACKLOG="${IMAGE_QUEUE_MAX_BACKLOG:-50}"
    IMAGE_QUEUE_PENDING_TTL_SECONDS="${IMAGE_QUEUE_PENDING_TTL_SECONDS:-1800}"
    IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS="${IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS:-1800}"
    CHATGPT2API_PROXY_RUNTIME_ENABLED="${CHATGPT2API_PROXY_RUNTIME_ENABLED:-}"
    CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE="${CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE:-}"
    CHATGPT2API_PROXY_RUNTIME_PROXY_URL="${CHATGPT2API_PROXY_RUNTIME_PROXY_URL:-}"
    CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL="${CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL:-}"
    CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY="${CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY:-}"
    CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES="${CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES:-}"
    CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED:-}"
    CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE:-}"
    CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC:-}"
    CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL="${CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL:-}"
    CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START="${CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START:-}"
    CHATGPT2API_PROXY_RUNTIME_BROWSER="${CHATGPT2API_PROXY_RUNTIME_BROWSER:-}"
    CHATGPT2API_PROXY_RUNTIME_USER_AGENT="${CHATGPT2API_PROXY_RUNTIME_USER_AGENT:-}"
    CHATGPT2API_FLARESOLVERR_URL="${CHATGPT2API_FLARESOLVERR_URL:-}"
    WARP_LICENSE_KEY="${WARP_LICENSE_KEY:-}"
    CHATGPT2API_PYTHON_PID_FILE="${CHATGPT2API_PYTHON_PID_FILE:-}"
    POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
    POSTGRES_ADMIN_USER="${POSTGRES_ADMIN_USER:-chatgpt2api_admin}"
    POSTGRES_ADMIN_PASSWORD="${POSTGRES_ADMIN_PASSWORD:-}"
    POSTGRES_PASSWORD_URLENCODED="${POSTGRES_PASSWORD_URLENCODED:-}"
    AUTH_KEY="${CHATGPT2API_AUTH_KEY:-${AUTH_KEY:-}}"
    IMAGE_BASE_URL="${CHATGPT2API_IMAGE_BASE_URL:-${IMAGE_BASE_URL:-}}"
    IMAGE_PORT="${CHATGPT2API_IMAGE_PORT:-${IMAGE_PORT:-}}"
    WORKER_ID="${CHATGPT2API_WORKER_ID:-${WORKER_ID:-}}"
    WIREGUARD_IP="${CHATGPT2API_WIREGUARD_IP:-${WIREGUARD_IP:-}}"
    WORKER_JOINED_MARKER_FILE="${CHATGPT2API_WORKER_JOINED_MARKER_FILE:-${WORKER_JOINED_MARKER_FILE:-/app/data/worker.joined}}"
    WIREGUARD_INTERFACE="${WIREGUARD_INTERFACE:-wg-chatgpt2api}"
    WIREGUARD_SERVER_IP="${WIREGUARD_SERVER_IP:-10.77.0.1}"
    WIREGUARD_SERVER_ENDPOINT="${WIREGUARD_SERVER_ENDPOINT:-}"
    WIREGUARD_PORT="${WIREGUARD_PORT:-51820}"
    NODE_ROLE="${CHATGPT2API_NODE_ROLE:-${NODE_ROLE:-}}"
    CLUSTER_ID="${CHATGPT2API_CLUSTER_ID:-${CLUSTER_ID:-}}"

    if [[ "${release_env_allowed}" != "1" ]]; then
      if release_ref_override_active; then
        BRANCH="$(release_ref_override_value)"
        CHATGPT2API_RELEASE_REF="$(release_ref_override_value)"
      else
        BRANCH="${previous_branch}"
        CHATGPT2API_RELEASE_REF="${previous_release_ref}"
      fi
      CHATGPT2API_IMAGE="${previous_image}"
      CHATGPT2API_IMAGE_DIGEST="${previous_image_digest}"
      CHATGPT2API_WARP_IMAGE="${previous_warp_image}"
      CHATGPT2API_PRIVOXY_IMAGE="${previous_privoxy_image}"
      CHATGPT2API_FLARESOLVERR_IMAGE="${previous_flaresolverr_image}"
      UV_VERSION="${previous_uv_version}"
    elif release_ref_override_active; then
      BRANCH="$(release_ref_override_value)"
      CHATGPT2API_RELEASE_REF="$(release_ref_override_value)"
    fi
  fi
}

cluster_ensure_cluster_id() {
  if [[ -n "${CLUSTER_ID}" ]]; then
    return
  fi
  CLUSTER_ID="cluster-$(generate_auth_key)"
  local env_file="${INSTALL_DIR}/.env"
  if [[ -f "${env_file}" ]]; then
    printf '\nCHATGPT2API_CLUSTER_ID=%s\n' "$(dotenv_escape "${CLUSTER_ID}")" >>"${env_file}"
    chmod 600 "${env_file}" || true
  fi
}

cluster_base64_file() {
  base64 <"$1" | tr -d '\n'
}

cluster_decode_base64_to_file() {
  local value="$1"
  local output_file="$2"
  printf '%s' "${value}" | base64 -d >"${output_file}"
}

cluster_require_root() {
  local uid="${EUID:-$(id -u)}"
  if [[ "${uid}" != "0" ]]; then
    echo "[$(text prefix_error)] WireGuard setup requires root. Please run with sudo." >&2
    exit 1
  fi
}

cluster_install_wireguard_tools() {
  if command -v wg >/dev/null 2>&1 && command -v wg-quick >/dev/null 2>&1 && command -v ip >/dev/null 2>&1; then
    need_cmd openssl
    return
  fi
  ui_println "[$(text prefix_info)] installing WireGuard tools..."
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard iproute2 iputils-ping netcat-openbsd curl openssl
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y wireguard-tools iproute iputils nmap-ncat curl openssl
  elif command -v yum >/dev/null 2>&1; then
    yum install -y wireguard-tools iproute iputils nmap-ncat curl openssl
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache wireguard-tools iproute2 iputils busybox-extras curl openssl
  else
    echo "[$(text prefix_error)] cannot install WireGuard automatically on this system." >&2
    exit 1
  fi
  need_cmd wg
  need_cmd wg-quick
  need_cmd ip
  need_cmd openssl
}

cluster_ensure_wireguard_server_keys() {
  cluster_require_root
  cluster_install_wireguard_tools
  cluster_resolve_wireguard_interface
  mkdir -p /etc/wireguard
  chmod 700 /etc/wireguard || true
  local private_key="/etc/wireguard/${WIREGUARD_INTERFACE}.key"
  local public_key="/etc/wireguard/${WIREGUARD_INTERFACE}.pub"
  if [[ ! -f "${private_key}" ]]; then
    umask 077
    wg genkey >"${private_key}"
  fi
  local expected_public_key=""
  expected_public_key="$(wg pubkey <"${private_key}")"
  if [[ ! -f "${public_key}" || "$(tr -d '[:space:]' <"${public_key}")" != "${expected_public_key}" ]]; then
    printf '%s\n' "${expected_public_key}" >"${public_key}"
  fi
  chmod 600 "${private_key}" || true
}

cluster_ensure_join_signing_key() {
  local join_dir="${INSTALL_DIR}/join"
  local private_key="${join_dir}/join-signing.key"
  local public_key="${join_dir}/join-signing.pub"
  need_cmd openssl
  mkdir -p "${join_dir}"
  chmod 700 "${join_dir}" || true
  if [[ ! -f "${private_key}" ]]; then
    umask 077
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${private_key}" >/dev/null 2>&1
  fi
  local public_key_tmp="${public_key}.tmp.$$"
  if ! openssl rsa -in "${private_key}" -pubout -out "${public_key_tmp}" >/dev/null 2>&1; then
    trash_path "${public_key_tmp}"
    echo "[$(text prefix_error)] failed to derive the Worker join signing public key." >&2
    return 1
  fi
  mv -f "${public_key_tmp}" "${public_key}"
  chmod 600 "${private_key}" || true
}

cluster_sign_payload() {
  local payload_file="$1"
  local private_key="${INSTALL_DIR}/join/join-signing.key"
  openssl dgst -sha256 -sign "${private_key}" -binary "${payload_file}" | base64 | tr -d '\n'
}

cluster_wireguard_server_config() {
  cluster_ensure_wireguard_server_keys
  local config_file=""
  config_file="$(cluster_wireguard_config_file)"
  if [[ ! -f "${config_file}" ]]; then
    cat >"${config_file}" <<EOF
# chatgpt2api managed WireGuard server
[Interface]
Address = ${WIREGUARD_SERVER_IP}/24
ListenPort = ${WIREGUARD_PORT}
PrivateKey = $(cat "/etc/wireguard/${WIREGUARD_INTERFACE}.key")
EOF
    chmod 600 "${config_file}" || true
  elif ! grep -q '^# chatgpt2api managed WireGuard server$' "${config_file}"; then
    echo "[$(text prefix_error)] WireGuard config exists but is not managed by chatgpt2api: ${config_file}" >&2
    exit 1
  elif ! grep -q "Address = ${WIREGUARD_SERVER_IP}/24" "${config_file}"; then
    echo "[$(text prefix_error)] managed WireGuard config has a different server address: ${config_file}" >&2
    exit 1
  elif ! grep -Fqx "ListenPort = ${WIREGUARD_PORT}" "${config_file}"; then
    echo "[$(text prefix_error)] managed WireGuard config has a different listen port: ${config_file}" >&2
    exit 1
  elif ! grep -Fqx "PrivateKey = $(cat "/etc/wireguard/${WIREGUARD_INTERFACE}.key")" "${config_file}"; then
    echo "[$(text prefix_error)] managed WireGuard config has a different private key: ${config_file}" >&2
    exit 1
  fi
}

cluster_allow_wireguard_firewall() {
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
    if ! ufw allow "${WIREGUARD_PORT}/udp" >/dev/null 2>&1; then
      echo "[$(text prefix_error)] failed to allow WireGuard UDP port ${WIREGUARD_PORT} in UFW." >&2
      return 1
    fi
    if ! ufw allow from 10.77.0.0/24 to any port 5432 proto tcp >/dev/null 2>&1; then
      echo "[$(text prefix_error)] failed to allow PostgreSQL TCP 5432 from the WireGuard subnet in UFW." >&2
      return 1
    fi
  fi
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    if ! firewall-cmd --permanent --add-port="${WIREGUARD_PORT}/udp" >/dev/null 2>&1; then
      echo "[$(text prefix_error)] failed to allow WireGuard UDP port ${WIREGUARD_PORT} in firewalld." >&2
      return 1
    fi
    if ! firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=10.77.0.0/24 port port=5432 protocol=tcp accept' >/dev/null 2>&1; then
      echo "[$(text prefix_error)] failed to allow PostgreSQL TCP 5432 from the WireGuard subnet in firewalld." >&2
      return 1
    fi
    if ! firewall-cmd --reload >/dev/null 2>&1; then
      echo "[$(text prefix_error)] failed to reload firewalld after applying the WireGuard rules." >&2
      return 1
    fi
  fi
}

cluster_start_wireguard_interface() {
  cluster_install_wireguard_tools
  cluster_resolve_wireguard_interface
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now "wg-quick@${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || wg-quick up "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || true
  else
    wg-quick up "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || true
  fi
  if ! wg show "${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
    echo "[$(text prefix_error)] WireGuard ${WIREGUARD_INTERFACE} failed to start." >&2
    exit 1
  fi
}

cluster_setup_main_wireguard() {
  cluster_require_root
  cluster_wireguard_server_config
  cluster_allow_wireguard_firewall
  cluster_start_wireguard_interface
}

cluster_add_wireguard_peer() {
  local worker_id="$1"
  local worker_ip="$2"
  local worker_public_key="$3"
  local config_file=""
  config_file="$(cluster_wireguard_config_file)"
  if grep -q "AllowedIPs = ${worker_ip}/32" "${config_file}"; then
    echo "[$(text prefix_error)] WireGuard peer already exists for ${worker_ip}." >&2
    return 1
  fi
  if grep -q "PublicKey = ${worker_public_key}" "${config_file}"; then
    echo "[$(text prefix_error)] WireGuard peer public key already exists for ${worker_id}." >&2
    return 1
  fi
  cat >>"${config_file}" <<EOF

# chatgpt2api ${worker_id}
[Peer]
PublicKey = ${worker_public_key}
AllowedIPs = ${worker_ip}/32
EOF
  if ! wg show "${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
    cluster_remove_wireguard_peer "${worker_id}" "${worker_public_key}"
    echo "[$(text prefix_error)] WireGuard ${WIREGUARD_INTERFACE} is not running." >&2
    return 1
  fi
  if ! wg set "${WIREGUARD_INTERFACE}" peer "${worker_public_key}" allowed-ips "${worker_ip}/32" >/dev/null 2>&1; then
    cluster_remove_wireguard_peer "${worker_id}" "${worker_public_key}"
    echo "[$(text prefix_error)] failed to activate WireGuard peer for ${worker_id}." >&2
    return 1
  fi
  if wg show "${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
    if ! wg syncconf "${WIREGUARD_INTERFACE}" <(wg-quick strip "${WIREGUARD_INTERFACE}") >/dev/null 2>&1; then
      cluster_remove_wireguard_peer "${worker_id}" "${worker_public_key}"
      echo "[$(text prefix_error)] failed to persist WireGuard peer for ${worker_id}." >&2
      return 1
    fi
  fi
}

cluster_remove_wireguard_peer() {
  local worker_id="$1"
  local worker_public_key="$2"
  local config_file=""
  config_file="$(cluster_wireguard_config_file)"
  if [[ -f "${config_file}" ]]; then
    local tmp_file="${config_file}.tmp.$$"
    awk -v marker="# chatgpt2api ${worker_id}" '
      $0 == marker { skip = 1; next }
      skip && $0 ~ /^\[Peer\]$/ { next }
      skip && $0 ~ /^PublicKey = / { next }
      skip && $0 ~ /^AllowedIPs = / { skip = 0; next }
      { print }
    ' "${config_file}" >"${tmp_file}" && cat "${tmp_file}" >"${config_file}"
    trash_path "${tmp_file}"
  fi
  if command -v wg >/dev/null 2>&1 && wg show "${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
    wg set "${WIREGUARD_INTERFACE}" peer "${worker_public_key}" remove >/dev/null 2>&1 || true
    wg syncconf "${WIREGUARD_INTERFACE}" <(wg-quick strip "${WIREGUARD_INTERFACE}") >/dev/null 2>&1 || true
  fi
}

cluster_check_worker_database_record() {
  local worker_id="$1"
  if ! command -v docker >/dev/null 2>&1; then
    echo "[$(text prefix_error)] Docker is required to verify the existing Worker database record." >&2
    return 1
  fi
  local found=""
  if ! found="$(docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" chatgpt2api-postgres \
    psql -U chatgpt2api_runtime -d chatgpt2api_image_queue -tAc \
    "SELECT 1 FROM image_worker_state WHERE worker_id='${worker_id}' LIMIT 1;" 2>/dev/null)"; then
    echo "[$(text prefix_error)] failed to query the existing Worker database record: ${worker_id}" >&2
    return 1
  fi
  found="$(printf '%s' "${found}" | tr -d '[:space:]')"
  case "${found}" in
    "") return 0 ;;
    1)
      echo "[$(text prefix_error)] database worker record already exists: ${worker_id}" >&2
      return 1
      ;;
    *)
      echo "[$(text prefix_error)] unexpected Worker database preflight result: ${found}" >&2
      return 1
      ;;
  esac
}

cluster_urlencode() {
  local raw="${1-}"
  local encoded=""
  local i char hex
  local LC_ALL=C
  for ((i = 0; i < ${#raw}; i++)); do
    char="${raw:i:1}"
    case "${char}" in
      [a-zA-Z0-9.~_-])
        encoded+="${char}"
        ;;
      *)
        printf -v hex '%%%02X' "'${char}"
        encoded+="${hex}"
        ;;
    esac
  done
  printf '%s' "${encoded}"
}

cluster_external_app_db_url() {
  printf 'postgresql://%s:%s@%s:5432/chatgpt2api_app' \
    "$(cluster_urlencode "chatgpt2api_runtime")" \
    "$(cluster_urlencode "${POSTGRES_PASSWORD}")" \
    "${WIREGUARD_SERVER_IP}"
}

cluster_external_queue_db_url() {
  printf 'postgresql://%s:%s@%s:5432/chatgpt2api_image_queue' \
    "$(cluster_urlencode "chatgpt2api_runtime")" \
    "$(cluster_urlencode "${POSTGRES_PASSWORD}")" \
    "${WIREGUARD_SERVER_IP}"
}

cluster_join_payload_json() {
  cat <<EOF
{
  "token": "$(json_escape "${TOKEN}")",
  "worker_id": "$(json_escape "${WORKER_ID}")",
  "worker_no": ${WORKER_NO},
  "wireguard_ip": "$(json_escape "${WIREGUARD_IP}")",
  "wireguard_server_ip": "$(json_escape "${WIREGUARD_SERVER_IP}")",
  "wireguard_server_endpoint": "$(json_escape "${WIREGUARD_SERVER_ENDPOINT}")",
  "wireguard_port": ${WIREGUARD_PORT},
  "wireguard_server_public_key": "$(json_escape "${WIREGUARD_SERVER_PUBLIC_KEY}")",
  "wireguard_worker_private_key": "$(json_escape "${WIREGUARD_WORKER_PRIVATE_KEY}")",
  "wireguard_worker_public_key": "$(json_escape "${WIREGUARD_WORKER_PUBLIC_KEY}")",
  "app_database_url": "$(json_escape "${APP_DATABASE_URL}")",
  "image_queue_database_url": "$(json_escape "${IMAGE_QUEUE_DATABASE_URL}")",
  "signing_public_key_b64": "$(json_escape "${SIGNING_PUBLIC_KEY_B64}")",
  "cluster_id": "$(json_escape "${CLUSTER_ID}")",
  "nonce": "$(json_escape "${JOIN_NONCE}")",
  "expires_at": "$(json_escape "${JOIN_EXPIRES_AT}")"
}
EOF
}

cluster_resolve_worker_join_file() {
  local legacy_file="${INSTALL_DIR}/join/worker.join"
  if [[ -f "${legacy_file}" ]]; then
    printf '%s\n' "${legacy_file}"
    return 0
  fi

  local candidate=""
  local -a candidates=()
  if [[ -d "${INSTALL_DIR}/join" ]]; then
    while IFS= read -r candidate; do
      [[ -n "${candidate}" ]] && candidates+=("${candidate}")
    done < <(find "${INSTALL_DIR}/join" -maxdepth 1 -type f -name 'worker-*.join' -print | sort)
  fi

  if [[ "${#candidates[@]}" -eq 1 ]]; then
    printf '%s\n' "${candidates[0]}"
    return 0
  fi
  if [[ "${#candidates[@]}" -gt 1 ]]; then
    echo "[$(text prefix_error)] multiple Worker join files found; pass the join file path explicitly." >&2
    return 1
  fi
  printf '%s\n' "${legacy_file}"
}

cluster_run_join_payload_python() {
  local compose_file="$1"
  local mode="$2"
  local operation="$3"
  local payload_json="$4"
  local python_code=""
  python_code="$(cat <<'PY'
import json
import os
import sys

from services.cluster_join_store import ClusterJoinStore

payload = json.load(sys.stdin)
store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
operation = sys.argv[1]
try:
    if operation == "issue":
        store.issue_worker_join(payload)
    elif operation == "revoke":
        if not store.revoke_worker_join(payload.get("token")):
            raise SystemExit(1)
    elif operation == "validate":
        if store.validate_worker_join(payload) is None:
            print("join token is invalid, expired, already used, or payload mismatch", file=sys.stderr)
            raise SystemExit(1)
    elif operation == "consume":
        if store.consume_worker_join(payload) is None:
            print("join token is invalid, expired, already used, or payload mismatch", file=sys.stderr)
            raise SystemExit(1)
    elif operation == "activate":
        if store.activate_worker_join(payload) is None:
            raise SystemExit(1)
    elif operation == "mark-failed":
        if store.mark_activation_failed(payload) is None:
            raise SystemExit(1)
    elif operation == "reopen":
        if store.reopen_worker_join(payload) is None:
            raise SystemExit(1)
    else:
        raise ValueError(f"unsupported join operation: {operation}")
except Exception as exc:
    print(f"join database operation failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
)"

  if [[ "${mode}" == "exec" ]]; then
    printf '%s' "${payload_json}" | (
      cd "${INSTALL_DIR}" &&
      docker compose -f "${compose_file}" exec -T app /app/.venv/bin/python -c "${python_code}" "${operation}"
    )
  else
    printf '%s' "${payload_json}" | (
      cd "${INSTALL_DIR}" &&
      docker compose -f "${compose_file}" run --rm --no-deps -T app /app/.venv/bin/python -c "${python_code}" "${operation}"
    )
  fi
}

cluster_issue_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! cluster_run_join_payload_python docker-compose.cluster-main.yml exec issue "${payload_json}"; then
    echo "[$(text prefix_error)] failed to write worker join token to app database." >&2
    return 1
  fi
}

cluster_revoke_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! cluster_run_join_payload_python docker-compose.cluster-main.yml exec revoke "${payload_json}"; then
    echo "[$(text prefix_warn)] failed to revoke pending worker join token; please check ${WORKER_ID} manually." >&2
    return 1
  fi
}

cluster_revoke_pending_worker() {
  local worker_id="$1"
  if ! (cd "${INSTALL_DIR}" && CHATGPT2API_WORKER_ID_TO_REVOKE="${worker_id}" docker compose -f docker-compose.cluster-main.yml exec -T -e CHATGPT2API_WORKER_ID_TO_REVOKE app /app/.venv/bin/python - <<'PY'
import os
import sys

from services.cluster_join_store import ClusterJoinStore

store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
try:
    revoked = store.revoke_pending_worker(os.environ["CHATGPT2API_WORKER_ID_TO_REVOKE"])
except Exception as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)
if revoked is None:
    print("pending worker join not found", file=sys.stderr)
    sys.exit(1)
PY
  ); then
    echo "[$(text prefix_error)] failed to revoke pending worker join for ${worker_id}." >&2
    return 1
  fi
}

cluster_validate_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! cluster_run_join_payload_python docker-compose.cluster-worker.yml run validate "${payload_json}"; then
    echo "[$(text prefix_error)] failed to validate worker join token against app database." >&2
    return 1
  fi
}

cluster_consume_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! cluster_run_join_payload_python docker-compose.cluster-worker.yml run consume "${payload_json}"; then
    echo "[$(text prefix_error)] failed to consume worker join token." >&2
    return 1
  fi
}

cluster_activate_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! cluster_run_join_payload_python docker-compose.cluster-worker.yml exec activate "${payload_json}"; then
    echo "[$(text prefix_error)] failed to finalize worker join activation." >&2
    return 1
  fi
}

cluster_mark_activation_failed() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! cluster_run_join_payload_python docker-compose.cluster-worker.yml run mark-failed "${payload_json}"; then
    echo "[$(text prefix_warn)] failed to mark worker join activation as failed; main-node review is required." >&2
    return 1
  fi
}

cluster_reopen_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! cluster_run_join_payload_python docker-compose.cluster-worker.yml run reopen "${payload_json}"; then
    echo "[$(text prefix_warn)] failed to reopen worker join token; rerun can still resume if join status remains activating." >&2
    return 1
  fi
}

cluster_worker_preflight_cmd() {
  if ! (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml run --rm --no-deps -T app /app/.venv/bin/python - <<'PY'
import os
from sqlalchemy import create_engine, text

from services.database_url import validate_database_role_marker

for variable, role in (
    ("APP_DATABASE_URL", "app"),
    ("IMAGE_QUEUE_DATABASE_URL", "image_queue"),
):
    url = os.environ.get(variable, "")
    if not url:
        raise RuntimeError(f"{variable} is empty")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as connection:
        validate_database_role_marker(connection, role)
        connection.execute(text("SELECT 1"))
PY
  ); then
    echo "[$(text prefix_error)] worker database preflight failed; check WireGuard and database role markers." >&2
    return 1
  fi
}

cluster_prepare_main_bundle() {
  need_cmd curl
  validate_existing_deployment_dir || exit 1
  mkdir -p "${INSTALL_DIR}/deploy/postgres-init" "${INSTALL_DIR}/data" "${INSTALL_DIR}/join"
  download_file "docker-compose.cluster-main.yml"
  download_file "deploy/postgres-init/001-create-cluster-databases.sh"
  download_file "deploy/nginx-worker-images.example.conf"
  if [[ ! -f "${INSTALL_DIR}/deploy/release-manifest.env" ]]; then
    download_optional_or_fail "deploy/release-manifest.env"
  fi
  chmod +x "${INSTALL_DIR}/deploy/postgres-init/001-create-cluster-databases.sh" || true
}

cluster_prepare_worker_bundle() {
  need_cmd curl
  validate_existing_deployment_dir || exit 1
  mkdir -p "${INSTALL_DIR}/data" "${INSTALL_DIR}/join"
  download_file "docker-compose.cluster-worker.yml"
  download_file "deploy/nginx-worker-images.example.conf"
  if [[ ! -f "${INSTALL_DIR}/deploy/release-manifest.env" ]]; then
    download_optional_or_fail "deploy/release-manifest.env"
  fi
}

cluster_write_worker_nginx_config() {
  local template_file="${INSTALL_DIR}/deploy/nginx-worker-images.example.conf"
  local target_file="${INSTALL_DIR}/deploy/nginx-worker-images.conf"
  if [[ ! "${IMAGE_PORT}" =~ ^[0-9]+$ || "${IMAGE_PORT}" -lt 1 || "${IMAGE_PORT}" -gt 65535 ]]; then
    echo "[$(text prefix_error)] CHATGPT2API_IMAGE_PORT must be a TCP port between 1 and 65535." >&2
    return 1
  fi
  if [[ ! -f "${template_file}" ]]; then
    echo "[$(text prefix_error)] worker Nginx template is missing: ${template_file}" >&2
    return 1
  fi
  mkdir -p "$(dirname "${target_file}")"
  sed "s/__CHATGPT2API_IMAGE_PORT__/${IMAGE_PORT}/g" "${template_file}" >"${target_file}.tmp"
  mv "${target_file}.tmp" "${target_file}"
  chmod 600 "${target_file}" || true
  ui_println "Worker Nginx config: ${target_file}"
}

cluster_confirm_worker_image_proxy() {
  local config_file="${INSTALL_DIR}/deploy/nginx-worker-images.conf"
  local public_entry_mode="${CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE:-direct}"
  if [[ "${public_entry_mode}" != "direct" && "${public_entry_mode}" != "proxy" ]]; then
    echo "[$(text prefix_error)] CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE must be direct or proxy." >&2
    return 1
  fi
  if ! cluster_validate_worker_public_image_base_url "${IMAGE_BASE_URL:-}" "${public_entry_mode}"; then
    return 1
  fi
  if [[ "${public_entry_mode}" == "direct" ]]; then
    CHATGPT2API_WORKER_BIND_HOST="0.0.0.0"
    return 0
  fi
  CHATGPT2API_WORKER_BIND_HOST="127.0.0.1"
  ui_println "Worker Nginx config: ${config_file}"
  ui_println "worker-check will verify ${IMAGE_BASE_URL} after activation; activation first verifies the internal Worker chain, and a public proxy failure keeps the Worker running so you can fix Nginx and rerun worker-check."
  return 0
}

prompt_worker_public_entry_mode() {
  local answer=""
  while true; do
    ui_println "图片公开入口模式"
    ui_println "  1) direct - Worker 直接暴露图片端口"
    ui_println "  2) proxy  - 通过 Nginx 反向代理暴露"
    answer="$(prompt_input "请选择" "1")"
    case "${answer,,}" in
      1|direct)
        printf 'direct'
        return
        ;;
      2|proxy)
        printf 'proxy'
        return
        ;;
    esac
    ui_println "[$(text prefix_error)] CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE must be direct or proxy."
  done
}

CLUSTER_JOIN_TRANSACTION_ACTIVE="${CLUSTER_JOIN_TRANSACTION_ACTIVE:-0}"
CLUSTER_JOIN_TRANSACTION_PEER_ADDED="${CLUSTER_JOIN_TRANSACTION_PEER_ADDED:-0}"
CLUSTER_JOIN_TRANSACTION_TOKEN_ISSUED="${CLUSTER_JOIN_TRANSACTION_TOKEN_ISSUED:-0}"
CLUSTER_JOIN_TRANSACTION_WORKER_ID="${CLUSTER_JOIN_TRANSACTION_WORKER_ID:-}"
CLUSTER_JOIN_TRANSACTION_WORKER_PUBLIC_KEY="${CLUSTER_JOIN_TRANSACTION_WORKER_PUBLIC_KEY:-}"
CLUSTER_JOIN_TRANSACTION_JOIN_FILE="${CLUSTER_JOIN_TRANSACTION_JOIN_FILE:-}"
CLUSTER_JOIN_TRANSACTION_JOIN_TMP_FILE="${CLUSTER_JOIN_TRANSACTION_JOIN_TMP_FILE:-}"
CLUSTER_JOIN_TRANSACTION_PAYLOAD_FILE="${CLUSTER_JOIN_TRANSACTION_PAYLOAD_FILE:-}"
CLUSTER_JOIN_TRANSACTION_REGISTRY="${CLUSTER_JOIN_TRANSACTION_REGISTRY:-}"
CLUSTER_JOIN_TRANSACTION_REGISTRY_TOUCH="${CLUSTER_JOIN_TRANSACTION_REGISTRY_TOUCH:-0}"

cluster_join_transaction_cleanup() {
  if [[ "${CLUSTER_JOIN_TRANSACTION_ACTIVE:-0}" != "1" ]]; then
    return 0
  fi
  CLUSTER_JOIN_TRANSACTION_ACTIVE="0"

  if [[ "${CLUSTER_JOIN_TRANSACTION_PEER_ADDED:-0}" == "1" ]]; then
    cluster_remove_wireguard_peer \
      "${CLUSTER_JOIN_TRANSACTION_WORKER_ID}" \
      "${CLUSTER_JOIN_TRANSACTION_WORKER_PUBLIC_KEY}" || true
  fi
  if [[ "${CLUSTER_JOIN_TRANSACTION_TOKEN_ISSUED:-0}" == "1" ]]; then
    cluster_revoke_join_token || true
  fi
  if [[ "${CLUSTER_JOIN_TRANSACTION_REGISTRY_TOUCH:-0}" == "1" && -f "${CLUSTER_JOIN_TRANSACTION_REGISTRY:-}" ]]; then
    local registry_cleanup_file="${CLUSTER_JOIN_TRANSACTION_REGISTRY}.cleanup.$$"
    if awk -F $'\t' -v worker_id="${CLUSTER_JOIN_TRANSACTION_WORKER_ID}" \
      '$1 != worker_id { print }' \
      "${CLUSTER_JOIN_TRANSACTION_REGISTRY}" >"${registry_cleanup_file}"; then
      mv -f "${registry_cleanup_file}" "${CLUSTER_JOIN_TRANSACTION_REGISTRY}" || true
    else
      trash_path "${registry_cleanup_file}" || true
    fi
  fi
  trash_path \
    "${CLUSTER_JOIN_TRANSACTION_JOIN_FILE:-}" \
    "${CLUSTER_JOIN_TRANSACTION_JOIN_TMP_FILE:-}" \
    "${CLUSTER_JOIN_TRANSACTION_PAYLOAD_FILE:-}" || true
}

cluster_write_join_file() {
  local worker_no="$1"
  local worker_id="worker-${worker_no}"
  local worker_ip=""
  worker_ip="$(cluster_worker_ip "${worker_no}")"

  cluster_load_env
  cluster_validate_wireguard_server_ip
  cluster_ensure_cluster_id
  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    echo "[$(text prefix_error)] POSTGRES_PASSWORD is required before creating worker join files." >&2
    exit 1
  fi
  if [[ -z "${WIREGUARD_SERVER_ENDPOINT}" ]]; then
    WIREGUARD_SERVER_ENDPOINT="$(prompt_input "WireGuard 主节点公网 IP/域名" "${WIREGUARD_SERVER_ENDPOINT}")"
  fi
  if [[ -z "${WIREGUARD_SERVER_ENDPOINT}" ]]; then
    echo "[$(text prefix_error)] WIREGUARD_SERVER_ENDPOINT is required." >&2
    exit 1
  fi
  cluster_validate_wireguard_endpoint "${WIREGUARD_SERVER_ENDPOINT}"

  local join_dir="${INSTALL_DIR}/join"
  local registry="${join_dir}/workers.tsv"
  local join_file="${join_dir}/${worker_id}.join"
  mkdir -p -m 700 "${join_dir}"
  chmod 700 "${join_dir}" || true

  if [[ -f "${join_file}" ]]; then
    echo "[$(text prefix_error)] join file already exists: ${join_file}" >&2
    exit 1
  fi
  if [[ -f "${registry}" ]] && grep -Eq "^${worker_id}[[:space:]]" "${registry}"; then
    echo "[$(text prefix_error)] ${worker_id} already exists." >&2
    exit 1
  fi
  if [[ -f "${registry}" ]] && grep -Eq "[[:space:]]${worker_ip}([[:space:]]|$)" "${registry}"; then
    echo "[$(text prefix_error)] WireGuard IP already exists: ${worker_ip}" >&2
    exit 1
  fi
  local wireguard_config_file=""
  cluster_resolve_wireguard_interface
  wireguard_config_file="$(cluster_wireguard_config_file)"
  if [[ -f "${wireguard_config_file}" ]] && grep -q "${worker_ip}/32" "${wireguard_config_file}"; then
    echo "[$(text prefix_error)] WireGuard peer already exists for ${worker_ip}." >&2
    exit 1
  fi
  cluster_check_worker_database_record "${worker_id}"

  cluster_setup_main_wireguard
  cluster_ensure_join_signing_key

  local worker_private_key=""
  local worker_public_key=""
  local server_public_key=""
  local app_db_url=""
  local queue_db_url=""
  local signing_public_key_b64=""
  local release_ref=""
  local image_ref=""
  local image_digest=""
  worker_private_key="$(wg genkey)"
  worker_public_key="$(printf '%s' "${worker_private_key}" | wg pubkey)"
  server_public_key="$(cat "/etc/wireguard/${WIREGUARD_INTERFACE}.pub")"
  app_db_url="$(cluster_external_app_db_url)"
  queue_db_url="$(cluster_external_queue_db_url)"
  signing_public_key_b64="$(cluster_base64_file "${join_dir}/join-signing.pub")"
  MODE="docker"
  NODE_ROLE="api-main"
  RUN_API="true"
  RUN_WORKER="false"
  STORAGE_BACKEND="postgres"
  PORT="${PORT:-3000}"
  APP_DATABASE_URL="${app_db_url}"
  DATABASE_URL="${app_db_url}"
  IMAGE_QUEUE_DATABASE_URL="${queue_db_url}"
  validate_inputs
  release_ref="${BRANCH}"
  image_ref="${CHATGPT2API_IMAGE:-}"
  if [[ -z "${image_ref}" ]]; then
    image_ref="$(default_image)"
  fi
  image_digest="${CHATGPT2API_IMAGE_DIGEST:-}"
  if [[ -z "${image_digest}" && "${image_ref}" == *@sha256:* ]]; then
    image_digest="sha256:${image_ref##*@sha256:}"
  fi
  if [[ -z "${release_ref}" || -z "${image_ref}" || -z "${image_digest}" || -z "${UV_VERSION}" ]]; then
    echo "[$(text prefix_error)] release metadata is incomplete; cannot create a Worker join file." >&2
    exit 1
  fi

  local token=""
  local nonce=""
  local expires_at=""
  token="$(generate_auth_key)"
  nonce="$(generate_auth_key)"
  expires_at="$(($(date +%s) + JOIN_TTL_SECONDS))"

  WORKER_ID="${worker_id}"
  WORKER_NO="${worker_no}"
  WIREGUARD_IP="${worker_ip}"
  WIREGUARD_SERVER_PUBLIC_KEY="${server_public_key}"
  WIREGUARD_WORKER_PRIVATE_KEY="${worker_private_key}"
  WIREGUARD_WORKER_PUBLIC_KEY="${worker_public_key}"
  APP_DATABASE_URL="${app_db_url}"
  IMAGE_QUEUE_DATABASE_URL="${queue_db_url}"
  TOKEN="${token}"
  JOIN_NONCE="${nonce}"
  JOIN_EXPIRES_AT="${expires_at}"
  SIGNING_PUBLIC_KEY_B64="${signing_public_key_b64}"

  local payload_file=""
  local join_tmp_file=""
  payload_file="$(mktemp)"
  join_tmp_file="${join_file}.tmp.$$"
  cat >"${payload_file}" <<EOF
VERSION=1
WORKER_ID=${worker_id}
WORKER_NO=${worker_no}
WIREGUARD_IP=${worker_ip}
WIREGUARD_SERVER_IP=${WIREGUARD_SERVER_IP}
WIREGUARD_SERVER_ENDPOINT=${WIREGUARD_SERVER_ENDPOINT}
WIREGUARD_PORT=${WIREGUARD_PORT}
WIREGUARD_SERVER_PUBLIC_KEY=${server_public_key}
WIREGUARD_WORKER_PRIVATE_KEY=${worker_private_key}
WIREGUARD_WORKER_PUBLIC_KEY=${worker_public_key}
APP_DATABASE_URL=${app_db_url}
IMAGE_QUEUE_DATABASE_URL=${queue_db_url}
TOKEN=${token}
CLUSTER_ID=${CLUSTER_ID}
JOIN_NONCE=${JOIN_NONCE}
EXPIRES_AT=${expires_at}
SIGNING_PUBLIC_KEY_B64=${signing_public_key_b64}
CHATGPT2API_RELEASE_REF=${release_ref}
CHATGPT2API_IMAGE=${image_ref}
CHATGPT2API_IMAGE_DIGEST=${image_digest}
UV_VERSION=${UV_VERSION}
EOF

  CLUSTER_JOIN_TRANSACTION_ACTIVE="1"
  CLUSTER_JOIN_TRANSACTION_PEER_ADDED="0"
  CLUSTER_JOIN_TRANSACTION_TOKEN_ISSUED="0"
  CLUSTER_JOIN_TRANSACTION_WORKER_ID="${worker_id}"
  CLUSTER_JOIN_TRANSACTION_WORKER_PUBLIC_KEY="${worker_public_key}"
  CLUSTER_JOIN_TRANSACTION_JOIN_FILE="${join_file}"
  CLUSTER_JOIN_TRANSACTION_JOIN_TMP_FILE="${join_tmp_file}"
  CLUSTER_JOIN_TRANSACTION_PAYLOAD_FILE="${payload_file}"
  CLUSTER_JOIN_TRANSACTION_REGISTRY="${registry}"
  CLUSTER_JOIN_TRANSACTION_REGISTRY_TOUCH="0"
  trap 'cluster_join_transaction_cleanup; exit 1' INT TERM ERR

  local signature=""
  signature="$(cluster_sign_payload "${payload_file}")"

  CLUSTER_JOIN_TRANSACTION_TOKEN_ISSUED="1"
  if ! cluster_issue_join_token; then
    cluster_join_transaction_cleanup
    trap - INT TERM ERR
    exit 1
  fi
  CLUSTER_JOIN_TRANSACTION_PEER_ADDED="1"
  if ! cluster_add_wireguard_peer "${worker_id}" "${worker_ip}" "${worker_public_key}"; then
    cluster_join_transaction_cleanup
    trap - INT TERM ERR
    exit 1
  fi
  if ! {
    cat "${payload_file}" >"${join_tmp_file}" &&
    printf 'SIGNATURE=%s\n' "${signature}" >>"${join_tmp_file}" &&
    (chmod 600 "${join_tmp_file}" || true) &&
    mv "${join_tmp_file}" "${join_file}"
  }; then
    cluster_join_transaction_cleanup
    trap - INT TERM ERR
    exit 1
  fi
  trash_path "${payload_file}"
  CLUSTER_JOIN_TRANSACTION_REGISTRY_TOUCH="1"
  if ! printf '%s\t%s\t%s\t%s\t%s\n' "${worker_id}" "${worker_ip}" "${worker_public_key}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${join_file}" >>"${registry}"; then
    cluster_join_transaction_cleanup
    trap - INT TERM ERR
    echo "[$(text prefix_error)] failed to persist worker registry entry for ${worker_id}." >&2
    exit 1
  fi
  CLUSTER_JOIN_TRANSACTION_ACTIVE="0"
  trap - INT TERM ERR

  ui_println "Worker join file:"
  ui_println "${join_file}"
  ui_println "Copy this join file to the worker and run:"
  ui_println "bash install.sh worker ${INSTALL_DIR}/join/${worker_id}.join"
  ui_println "Copy join-signing.pub independently to ${INSTALL_DIR}/join/join-signing.pub on that worker, or set CHATGPT2API_JOIN_SIGNING_PUBLIC_KEY_B64."
  ui_println "Do not reuse ${worker_id}."
}

cluster_verify_join_file() {
  local join_file="$1"
  if [[ ! -f "${join_file}" ]]; then
    echo "[$(text prefix_error)] join file not found: ${join_file}" >&2
    exit 1
  fi
  need_cmd openssl
  need_cmd base64

  local public_key_b64=""
  local actual=""
  public_key_b64="$(grep '^SIGNING_PUBLIC_KEY_B64=' "${join_file}" | tail -n 1 | cut -d= -f2-)"
  actual="$(grep '^SIGNATURE=' "${join_file}" | tail -n 1 | cut -d= -f2-)"
  if [[ -z "${public_key_b64}" || -z "${actual}" ]]; then
    echo "[$(text prefix_error)] join file signature fields are missing." >&2
    exit 1
  fi
  local trusted_public_key_b64="${CHATGPT2API_JOIN_SIGNING_PUBLIC_KEY_B64:-}"
  local trusted_public_key_file="${INSTALL_DIR}/join/join-signing.pub"
  if [[ -z "${trusted_public_key_b64}" && -f "${trusted_public_key_file}" ]]; then
    trusted_public_key_b64="$(cluster_base64_file "${trusted_public_key_file}")"
  fi
  if [[ -z "${trusted_public_key_b64}" ]]; then
    echo "[$(text prefix_error)] trusted join signing public key is required. Copy join-signing.pub independently or set CHATGPT2API_JOIN_SIGNING_PUBLIC_KEY_B64." >&2
    exit 1
  fi
  if [[ "${trusted_public_key_b64}" != "${public_key_b64}" ]]; then
    echo "[$(text prefix_error)] join file signing public key does not match trusted key." >&2
    exit 1
  fi
  public_key_b64="${trusted_public_key_b64}"

  local payload_file=""
  local public_key_file=""
  local signature_file=""
  payload_file="$(mktemp)"
  public_key_file="$(mktemp)"
  signature_file="$(mktemp)"
  grep -v '^SIGNATURE=' "${join_file}" >"${payload_file}"
  if ! cluster_decode_base64_to_file "${public_key_b64}" "${public_key_file}" 2>/dev/null; then
    trash_path "${payload_file}" "${public_key_file}" "${signature_file}"
    echo "[$(text prefix_error)] join file public key is invalid." >&2
    exit 1
  fi
  if ! cluster_decode_base64_to_file "${actual}" "${signature_file}" 2>/dev/null; then
    trash_path "${payload_file}" "${public_key_file}" "${signature_file}"
    echo "[$(text prefix_error)] join file signature encoding is invalid." >&2
    exit 1
  fi
  if ! openssl dgst -sha256 -verify "${public_key_file}" -signature "${signature_file}" "${payload_file}" >/dev/null 2>&1; then
    trash_path "${payload_file}" "${public_key_file}" "${signature_file}"
    echo "[$(text prefix_error)] join file signature is invalid." >&2
    exit 1
  fi
  trash_path "${payload_file}" "${public_key_file}" "${signature_file}"
}

cluster_read_join_file() {
  local join_file="$1"
  JOIN_VERSION=""
  WORKER_ID=""
  WORKER_NO=""
  WIREGUARD_IP=""
  WIREGUARD_SERVER_IP=""
  WIREGUARD_SERVER_ENDPOINT=""
  WIREGUARD_PORT=""
  WIREGUARD_SERVER_PUBLIC_KEY=""
  WIREGUARD_WORKER_PRIVATE_KEY=""
  WIREGUARD_WORKER_PUBLIC_KEY=""
  APP_DATABASE_URL=""
  IMAGE_QUEUE_DATABASE_URL=""
  TOKEN=""
  CLUSTER_ID=""
  JOIN_NONCE=""
  JOIN_EXPIRES_AT=""
  SIGNING_PUBLIC_KEY_B64=""
  JOIN_RELEASE_REF=""
  JOIN_CHATGPT2API_IMAGE=""
  JOIN_CHATGPT2API_IMAGE_DIGEST=""
  JOIN_UV_VERSION=""
  while IFS='=' read -r key value; do
    case "${key}" in
      VERSION) JOIN_VERSION="${value}" ;;
      WORKER_ID) WORKER_ID="${value}" ;;
      WORKER_NO) WORKER_NO="${value}" ;;
      WIREGUARD_IP) WIREGUARD_IP="${value}" ;;
      WIREGUARD_SERVER_IP) WIREGUARD_SERVER_IP="${value}" ;;
      WIREGUARD_SERVER_ENDPOINT) WIREGUARD_SERVER_ENDPOINT="${value}" ;;
      WIREGUARD_PORT) WIREGUARD_PORT="${value}" ;;
      WIREGUARD_SERVER_PUBLIC_KEY) WIREGUARD_SERVER_PUBLIC_KEY="${value}" ;;
      WIREGUARD_WORKER_PRIVATE_KEY) WIREGUARD_WORKER_PRIVATE_KEY="${value}" ;;
      WIREGUARD_WORKER_PUBLIC_KEY) WIREGUARD_WORKER_PUBLIC_KEY="${value}" ;;
      APP_DATABASE_URL) APP_DATABASE_URL="${value}" ;;
      IMAGE_QUEUE_DATABASE_URL) IMAGE_QUEUE_DATABASE_URL="${value}" ;;
      TOKEN) TOKEN="${value}" ;;
      CLUSTER_ID) CLUSTER_ID="${value}" ;;
      JOIN_NONCE) JOIN_NONCE="${value}" ;;
      EXPIRES_AT) JOIN_EXPIRES_AT="${value}" ;;
      SIGNING_PUBLIC_KEY_B64) SIGNING_PUBLIC_KEY_B64="${value}" ;;
      CHATGPT2API_RELEASE_REF) JOIN_RELEASE_REF="${value}" ;;
      CHATGPT2API_IMAGE) JOIN_CHATGPT2API_IMAGE="${value}" ;;
      CHATGPT2API_IMAGE_DIGEST) JOIN_CHATGPT2API_IMAGE_DIGEST="${value}" ;;
      UV_VERSION) JOIN_UV_VERSION="${value}" ;;
    esac
  done <"${join_file}"

  if [[ -z "${JOIN_VERSION:-}" || -z "${WORKER_ID:-}" || -z "${WORKER_NO:-}" || -z "${WIREGUARD_IP:-}" || -z "${WIREGUARD_SERVER_IP:-}" || -z "${WIREGUARD_SERVER_ENDPOINT:-}" || -z "${WIREGUARD_PORT:-}" || -z "${WIREGUARD_SERVER_PUBLIC_KEY:-}" || -z "${WIREGUARD_WORKER_PRIVATE_KEY:-}" || -z "${WIREGUARD_WORKER_PUBLIC_KEY:-}" || -z "${APP_DATABASE_URL:-}" || -z "${IMAGE_QUEUE_DATABASE_URL:-}" || -z "${TOKEN:-}" || -z "${CLUSTER_ID:-}" || -z "${JOIN_NONCE:-}" || -z "${JOIN_EXPIRES_AT:-}" || -z "${SIGNING_PUBLIC_KEY_B64:-}" || -z "${JOIN_RELEASE_REF:-}" || -z "${JOIN_CHATGPT2API_IMAGE:-}" || -z "${JOIN_CHATGPT2API_IMAGE_DIGEST:-}" || -z "${JOIN_UV_VERSION:-}" ]]; then
    echo "[$(text prefix_error)] join file is missing required fields." >&2
    exit 1
  fi

  if [[ "${JOIN_VERSION}" != "1" ]]; then
    echo "[$(text prefix_error)] unsupported join file version: ${JOIN_VERSION}" >&2
    exit 1
  fi
  if [[ ! "${WORKER_ID}" =~ ^worker-[0-9]+$ ]]; then
    echo "[$(text prefix_error)] join file contains an invalid worker_id: ${WORKER_ID}" >&2
    exit 1
  fi
  if [[ "${WORKER_ID}" != "worker-${WORKER_NO}" ]]; then
    echo "[$(text prefix_error)] worker id does not match worker number: ${WORKER_ID} / ${WORKER_NO}" >&2
    exit 1
  fi
  local expected_ip=""
  expected_ip="$(cluster_worker_ip "${WORKER_NO}")"
  if [[ "${WIREGUARD_IP}" != "${expected_ip}" ]]; then
    echo "[$(text prefix_error)] worker ${WORKER_ID} must use WireGuard IP ${expected_ip}, got ${WIREGUARD_IP}." >&2
    exit 1
  fi
  cluster_validate_wireguard_port
  cluster_validate_wireguard_endpoint "${WIREGUARD_SERVER_ENDPOINT}"
}

cluster_validate_join_release_metadata() {
  if release_ref_override_active && [[ "${BRANCH}" != "${JOIN_RELEASE_REF}" ]]; then
    echo "[$(text prefix_error)] Worker release ref does not match the main node: ${BRANCH} != ${JOIN_RELEASE_REF}" >&2
    return 1
  fi
  if release_ref_override_active && [[ -n "${CHATGPT2API_IMAGE:-}" && "${CHATGPT2API_IMAGE}" != "${JOIN_CHATGPT2API_IMAGE}" ]]; then
    echo "[$(text prefix_error)] Worker image does not match the main node release metadata." >&2
    return 1
  fi
  if release_ref_override_active && [[ -n "${CHATGPT2API_IMAGE_DIGEST:-}" && "${CHATGPT2API_IMAGE_DIGEST}" != "${JOIN_CHATGPT2API_IMAGE_DIGEST}" ]]; then
    echo "[$(text prefix_error)] Worker image digest does not match the main node release metadata." >&2
    return 1
  fi
  if release_ref_override_active && [[ -n "${UV_VERSION:-}" && "${UV_VERSION}" != "${JOIN_UV_VERSION}" ]]; then
    echo "[$(text prefix_error)] Worker uv version does not match the main node release metadata." >&2
    return 1
  fi
  BRANCH="${JOIN_RELEASE_REF}"
  CHATGPT2API_RELEASE_REF="${JOIN_RELEASE_REF}"
  CHATGPT2API_IMAGE="${JOIN_CHATGPT2API_IMAGE}"
  CHATGPT2API_IMAGE_DIGEST="${JOIN_CHATGPT2API_IMAGE_DIGEST}"
  UV_VERSION="${JOIN_UV_VERSION}"
}

cluster_write_worker_wireguard_config() {
  cluster_require_root
  cluster_install_wireguard_tools
  cluster_resolve_wireguard_interface
  mkdir -p /etc/wireguard
  chmod 700 /etc/wireguard || true
  local config_file=""
  config_file="$(cluster_wireguard_config_file)"
  if [[ -f "${config_file}" ]] && ! grep -q '^# chatgpt2api managed WireGuard worker$' "${config_file}"; then
    echo "[$(text prefix_error)] WireGuard config exists but is not managed by chatgpt2api: ${config_file}" >&2
    exit 1
  fi
  cat >"${config_file}" <<EOF
# chatgpt2api managed WireGuard worker
[Interface]
Address = ${WIREGUARD_IP}/32
PrivateKey = ${WIREGUARD_WORKER_PRIVATE_KEY}

[Peer]
PublicKey = ${WIREGUARD_SERVER_PUBLIC_KEY}
Endpoint = ${WIREGUARD_SERVER_ENDPOINT}:${WIREGUARD_PORT}
AllowedIPs = ${WIREGUARD_SERVER_IP}/32
PersistentKeepalive = 25
EOF
  chmod 600 "${config_file}" || true
  local wireguard_start_status=0
  if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl enable --now "wg-quick@${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
      wg-quick down "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || true
      wg-quick up "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || wireguard_start_status=1
    fi
  else
    wg-quick down "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || true
    wg-quick up "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || wireguard_start_status=1
  fi
  if [[ "${wireguard_start_status}" != "0" ]]; then
    cluster_remove_worker_wireguard_config || true
    echo "[$(text prefix_error)] WireGuard ${WIREGUARD_INTERFACE} failed to start on worker." >&2
    return 1
  fi
  if ! wg show "${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
    cluster_remove_worker_wireguard_config || true
    echo "[$(text prefix_error)] WireGuard ${WIREGUARD_INTERFACE} failed to start on worker." >&2
    return 1
  fi
}

cluster_remove_worker_wireguard_config() {
  cluster_resolve_wireguard_interface
  local config_file=""
  local down_status=0
  config_file="$(cluster_wireguard_config_file)"
  if [[ -f "${config_file}" ]] && grep -q '^# chatgpt2api managed WireGuard worker$' "${config_file}"; then
    if command -v systemctl >/dev/null 2>&1; then
      if ! systemctl disable --now "wg-quick@${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
        down_status=1
      fi
    fi
    if (( down_status != 0 )) && command -v wg-quick >/dev/null 2>&1; then
      wg-quick down "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || true
    fi
    trash_path "${config_file}"
  fi
  WORKER_WIREGUARD_CONFIG_ACTIVE="0"
}

cluster_worker_marker_host_path() {
  local marker="${CHATGPT2API_WORKER_JOINED_MARKER_FILE:-${WORKER_JOINED_MARKER_FILE:-/app/data/worker.joined}}"
  case "${marker}" in
    /app/data/*)
      local relative="${marker#/app/data/}"
      if [[ -z "${relative}" || "${relative}" == *".."* ]]; then
        echo "[$(text prefix_error)] invalid worker join marker path: ${marker}" >&2
        return 1
      fi
      printf '%s/data/%s' "${INSTALL_DIR}" "${relative}"
      ;;
    *)
      echo "[$(text prefix_error)] CHATGPT2API_WORKER_JOINED_MARKER_FILE must be inside /app/data" >&2
      return 1
      ;;
  esac
}

cluster_write_joined_marker() {
  local marker_status="${1:-joined}"
  if [[ "${marker_status}" != "activating" && "${marker_status}" != "joined" ]]; then
    echo "[$(text prefix_error)] invalid worker join marker status: ${marker_status}" >&2
    return 1
  fi
  local join_token_digest=""
  join_token_digest="$(printf '%s' "${TOKEN}" | openssl dgst -sha256 -r | awk '{print $1}')"
  local marker_file=""
  marker_file="$(cluster_worker_marker_host_path)" || return 1
  local tmp_file="${marker_file}.tmp.$$"
  local activation_grace="${JOIN_ACTIVATION_GRACE_SECONDS:-900}"
  if [[ ! "${activation_grace}" =~ ^[0-9]+$ || "${activation_grace}" -lt 1 ]]; then
    activation_grace="900"
  fi
  mkdir -p "$(dirname "${marker_file}")"
  {
    printf 'worker_id=%s\n' "${WORKER_ID}"
    printf 'wireguard_ip=%s\n' "${WIREGUARD_IP}"
    printf 'status=%s\n' "${marker_status}"
    printf 'cluster_id=%s\n' "${CLUSTER_ID}"
    printf 'join_nonce=%s\n' "${JOIN_NONCE}"
    printf 'join_token_sha256=%s\n' "${join_token_digest}"
    if [[ "${marker_status}" == "activating" ]]; then
      printf 'activation_expires_at=%s\n' "$(($(date +%s) + activation_grace))"
    fi
    printf 'joined_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"${tmp_file}"
  chmod 600 "${tmp_file}" || true
  mv "${tmp_file}" "${marker_file}"
}

cluster_cleanup_worker_join_file() {
  local join_file="$1"
  if [[ -n "${join_file}" && -f "${join_file}" ]]; then
    trash_path "${join_file}"
  fi
}

cluster_fail_worker_activation() {
  if ! cluster_down_compose "docker-compose.cluster-worker.yml"; then
    echo "[$(text prefix_error)] failed to fully clean the worker Compose stack after activation failure." >&2
  fi
  cluster_remove_worker_state "${WORKER_ID:-${CHATGPT2API_WORKER_ID:-}}" || true
  cluster_remove_worker_joined_marker
  if [[ "${WORKER_JOIN_ACTIVATED:-0}" == "1" ]]; then
    cluster_mark_activation_failed || true
  elif [[ "${WORKER_JOIN_ACTIVATING:-0}" == "1" && "${WORKER_JOIN_CONSUMED:-0}" == "1" ]]; then
    cluster_reopen_join_token || true
  fi
  cluster_remove_worker_wireguard_config
}

cluster_worker_activation_cleanup_on_signal() {
  local exit_status="${1:-1}"
  trap - INT TERM ERR
  if [[ "${WORKER_JOIN_ACTIVATING:-0}" == "1" || "${WORKER_JOIN_ACTIVATED:-0}" == "1" ]]; then
    cluster_fail_worker_activation || true
  elif [[ "${WORKER_WIREGUARD_CONFIG_ACTIVE:-0}" == "1" ]]; then
    cluster_remove_worker_wireguard_config || true
  fi
  exit "${exit_status}"
}

cluster_remove_worker_joined_marker() {
  local marker_file=""
  if marker_file="$(cluster_worker_marker_host_path 2>/dev/null)"; then
    trash_path "${marker_file}"
  fi
  trash_path "${INSTALL_DIR}/data/worker.joined"
}

cluster_ensure_compose() {
  need_cmd docker
  if ! docker compose version >/dev/null 2>&1; then
    echo "[$(text prefix_error)] $(text err_compose)" >&2
    exit 1
  fi
}

cluster_pull_compose() {
  local compose_file="$1"
  cluster_ensure_compose
  (cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" config --quiet)
  (cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" pull)
}

cluster_up_compose() {
  local compose_file="$1"
  shift
  cluster_ensure_compose
  if [[ "${compose_file}" == "docker-compose.cluster-main.yml" ]]; then
    if ! command -v ip >/dev/null 2>&1 || ! ip -4 addr show dev "${WIREGUARD_INTERFACE}" 2>/dev/null | grep -Fq "inet ${WIREGUARD_SERVER_IP}/"; then
      echo "[$(text prefix_error)] WireGuard ${WIREGUARD_INTERFACE} does not have ${WIREGUARD_SERVER_IP}/24; configure and activate it before starting cluster PostgreSQL." >&2
      return 1
    fi
  fi
  (cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" config --quiet)
  prepare_docker_data_permissions
  (cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" up -d --remove-orphans "$@")
}

cluster_wait_service_healthy() {
  local compose_file="$1"
  local service="$2"
  local timeout_seconds="${3:-180}"
  local deadline=$(( $(date +%s) + timeout_seconds ))
  local container_id=""
  local status=""

  while (( $(date +%s) < deadline )); do
    container_id="$(cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" ps -q "${service}" 2>/dev/null || true)"
    if [[ -n "${container_id}" ]]; then
      status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || true)"
      case "${status}" in
        healthy)
          return 0
          ;;
        exited|dead)
          break
          ;;
      esac
    fi
    sleep 2
  done

  echo "[$(text prefix_error)] ${service} did not become healthy in ${compose_file}; recent status:" >&2
  (cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" ps >&2 || true)
  (cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" logs --tail=120 "${service}" >&2 || true)
  return 1
}

cluster_down_compose() {
  local compose_file="$1"
  local down_status=0
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && [[ -f "${INSTALL_DIR}/${compose_file}" ]]; then
    if ! (cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" down --remove-orphans); then
      down_status=1
      echo "[$(text prefix_error)] failed to stop Compose stack: ${compose_file}" >&2
    fi
  fi
  return "${down_status}"
}

cluster_run_compose() {
  local compose_file="$1"
  cluster_pull_compose "${compose_file}"
  cluster_up_compose "${compose_file}"
}

cluster_reconcile_main_databases() {
  if ! cluster_wait_service_healthy "docker-compose.cluster-main.yml" postgres 180; then
    exit 1
  fi
  if ! (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-main.yml exec -T postgres sh /docker-entrypoint-initdb.d/001-create-cluster-databases.sh); then
    echo "[$(text prefix_error)] failed to reconcile cluster database names or role markers." >&2
    exit 1
  fi
}

cluster_main_cmd() {
  local cli_args=("$@")
  local create_first_worker="0"
  choose_language
  MODE="docker"
  INSTALL_TARGET="api-main"
  NODE_ROLE="api-main"
  RUN_API="true"
  RUN_WORKER="false"
  STORAGE_BACKEND="postgres"
  load_existing_install_env
  parse_args "${cli_args[@]}"
  MODE="docker"
  INSTALL_TARGET="api-main"
  NODE_ROLE="api-main"
  RUN_API="true"
  RUN_WORKER="false"
  STORAGE_BACKEND="postgres"
  ensure_admin_auth_key || exit 1
  if ! is_noninteractive; then
    BASE_URL="$(prompt_input "API 后台域名" "${BASE_URL}")"
    PORT="$(prompt_input "$(text prompt_port)" "${PORT}")"
    if [[ -z "${POSTGRES_PASSWORD}" ]]; then
      POSTGRES_PASSWORD="$(prompt_input "PostgreSQL 密码" "")"
    fi
    if [[ -z "${POSTGRES_ADMIN_PASSWORD}" ]]; then
      POSTGRES_ADMIN_PASSWORD="$(prompt_input "PostgreSQL 管理员密码" "")"
    fi
    if [[ -z "${WIREGUARD_SERVER_ENDPOINT}" ]]; then
      WIREGUARD_SERVER_ENDPOINT="$(prompt_input "WireGuard 主节点公网 IP/域名" "${WIREGUARD_SERVER_ENDPOINT}")"
    fi
    WIREGUARD_PORT="$(prompt_input "WireGuard 端口" "${WIREGUARD_PORT}")"
    INSTALL_DIR="$(prompt_input "$(text prompt_dir)" "${INSTALL_DIR}")"
    BRANCH="$(prompt_input "$(text prompt_branch)" "${BRANCH}")"
    RELEASE_REF_SELECTED="1"
  fi
  if [[ "${RELEASE_REF_SELECTED}" == "1" ]]; then
    load_release_manifest
  fi
  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    POSTGRES_PASSWORD="$(generate_auth_key)"
  fi
  if [[ -z "${POSTGRES_ADMIN_PASSWORD}" ]]; then
    POSTGRES_ADMIN_PASSWORD="$(generate_auth_key)"
  fi
  cluster_ensure_cluster_id
  if [[ -z "${WIREGUARD_SERVER_ENDPOINT}" ]]; then
    echo "[$(text prefix_error)] WIREGUARD_SERVER_ENDPOINT is required." >&2
    exit 1
  fi
  APP_DATABASE_URL="$(cluster_external_app_db_url)"
  DATABASE_URL="${APP_DATABASE_URL}"
  IMAGE_QUEUE_DATABASE_URL="$(cluster_external_queue_db_url)"

  local first_worker_choice_status="0"
  resolve_create_first_worker || first_worker_choice_status="$?"
  if [[ "${first_worker_choice_status}" == "2" ]]; then
    exit 1
  fi
  if [[ "${first_worker_choice_status}" == "0" ]]; then
    create_first_worker="1"
  fi
  validate_inputs
  print_install_summary
  confirm_installation
  cluster_prepare_main_bundle
  write_default_config_json
  write_env_file
  cluster_setup_main_wireguard
  cluster_pull_compose "docker-compose.cluster-main.yml"
  if ! stop_python_runtime; then
    echo "[$(text prefix_error)] failed to stop the previous managed Python runtime before starting cluster main." >&2
    exit 1
  fi
  if ! stop_docker_runtime; then
    echo "[$(text prefix_error)] failed to stop the previous managed Docker runtime before starting cluster main." >&2
    exit 1
  fi
  cluster_up_compose "docker-compose.cluster-main.yml" postgres
  cluster_wait_service_healthy "docker-compose.cluster-main.yml" postgres 180
  cluster_reconcile_main_databases
  cluster_up_compose "docker-compose.cluster-main.yml" app
  cluster_wait_service_healthy "docker-compose.cluster-main.yml" app 180
  wait_cluster_main_liveness

  if [[ "${create_first_worker}" == "1" ]]; then
    local first_worker=""
    first_worker="$(prompt_input "第一个 Worker 编号" "1")"
    cluster_write_join_file "${first_worker}"
  fi
  print_install_summary
}

cluster_worker_cmd() {
  local join_file=""
  if [[ $# -gt 0 && "${1}" != --* ]]; then
    join_file="$1"
    shift
  fi
  local cli_args=("$@")
  choose_language
  MODE="docker"
  INSTALL_TARGET="worker"
  NODE_ROLE="worker"
  RUN_API="false"
  RUN_WORKER="true"
  STORAGE_BACKEND="postgres"
  WORKER_JOIN_ACTIVATING="0"
  WORKER_JOIN_ACTIVATED="0"
  WORKER_JOIN_CONSUMED="0"
  if [[ -z "${INSTALL_DIR}" ]]; then INSTALL_DIR="$(prompt_input "$(text prompt_dir)" "${INSTALL_DIR}")"; fi
  load_existing_install_env
  parse_args "${cli_args[@]}"
  MODE="docker"
  NODE_ROLE="worker"
  RUN_API="false"
  RUN_WORKER="true"
  STORAGE_BACKEND="postgres"
  if [[ -z "${join_file}" ]]; then
    join_file="$(cluster_resolve_worker_join_file)" || exit 1
    if [[ ! -f "${join_file}" ]] && ! is_noninteractive; then
      join_file="$(prompt_input "Worker join 文件路径" "${join_file}")"
    fi
  fi
  cluster_verify_join_file "${join_file}"
  cluster_read_join_file "${join_file}"
  if ! cluster_validate_join_release_metadata; then
    exit 1
  fi
  ui_println "Worker ID: ${WORKER_ID}"
  ui_println "WireGuard IP: ${WIREGUARD_IP}"
  ui_println "集群 ID: ${CLUSTER_ID}"
  ui_println "PostgreSQL DATABASE_URL: ${APP_DATABASE_URL}"
  ui_println "图片队列 DATABASE_URL: ${IMAGE_QUEUE_DATABASE_URL}"
  IMAGE_QUEUE_INSTANCE_ID="${IMAGE_QUEUE_INSTANCE_ID:-${WORKER_ID}-${JOIN_NONCE}}"
  if [[ -n "${JOIN_EXPIRES_AT:-}" && "$(date +%s)" -gt "${JOIN_EXPIRES_AT}" ]]; then
    echo "[$(text prefix_error)] join file expired." >&2
    exit 1
  fi
  if ! is_noninteractive; then
    IMAGE_BASE_URL="$(prompt_input "请输入当前从节点图片返回 URL" "${IMAGE_BASE_URL}")"
    CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE="$(prompt_worker_public_entry_mode)"
    IMAGE_PORT="$(prompt_input "图片端口" "${IMAGE_PORT}")"
    INSTALL_DIR="$(prompt_input "$(text prompt_dir)" "${INSTALL_DIR}")"
  fi
  if [[ -z "${IMAGE_BASE_URL}" ]]; then
    echo "[$(text prefix_error)] CHATGPT2API_IMAGE_BASE_URL is required." >&2
    exit 1
  fi
  if auth_key_is_placeholder "${AUTH_KEY}"; then
    AUTH_KEY="$(generate_auth_key)"
  fi
  POSTGRES_ADMIN_USER=""
  POSTGRES_ADMIN_PASSWORD=""
  DATABASE_URL="${APP_DATABASE_URL}"
  validate_inputs
  print_install_summary
  confirm_installation
  cluster_prepare_worker_bundle
  cluster_write_worker_nginx_config
  if ! cluster_confirm_worker_image_proxy; then
    exit 1
  fi
  if ! cluster_write_worker_wireguard_config; then
    cluster_remove_worker_wireguard_config || true
    exit 1
  fi
  WORKER_WIREGUARD_CONFIG_ACTIVE="1"
  trap 'cluster_worker_activation_cleanup_on_signal $?' INT TERM ERR
  write_default_config_json
  write_env_file
  cluster_pull_compose "docker-compose.cluster-worker.yml"
  if ! cluster_validate_join_token; then
    cluster_remove_worker_wireguard_config
    exit 1
  fi
  if ! cluster_worker_preflight_cmd; then
    cluster_remove_worker_wireguard_config
    exit 1
  fi
  WORKER_JOIN_ACTIVATING="1"
  # The database transition may commit just before a signal interrupts this
  # shell; treat the consume as possibly committed until a failed command
  # proves otherwise so cleanup can safely reopen an activating token.
  WORKER_JOIN_CONSUMED="1"
  if ! cluster_consume_join_token; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  if ! cluster_write_joined_marker "activating"; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  if ! stop_python_runtime; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  if ! stop_docker_runtime; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  if ! cluster_up_compose "docker-compose.cluster-worker.yml"; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  if ! cluster_wait_service_healthy "docker-compose.cluster-worker.yml" app 180; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  if ! cluster_worker_check_cmd --skip-public-delivery; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  if ! cluster_activate_join_token; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  # From this point the database token is joined.  Cleanup must mark that
  # activation as failed instead of trying to reopen an already-joined row.
  WORKER_JOIN_ACTIVATED="1"
  if ! cluster_write_joined_marker "joined"; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  if ! cluster_wait_worker_runtime_health; then
    cluster_fail_worker_activation
    trap - INT TERM ERR
    exit 1
  fi
  WORKER_JOIN_ACTIVATING="0"
  if ! cluster_worker_check_cmd; then
    echo "[$(text prefix_error)] worker joined but final health check failed; keep the stack running and rerun worker-check after fixing the reported cause." >&2
    WORKER_WIREGUARD_CONFIG_ACTIVE="0"
    trap - INT TERM ERR
    exit 1
  fi
  WORKER_WIREGUARD_CONFIG_ACTIVE="0"
  trap - INT TERM ERR
  cluster_cleanup_worker_join_file "${join_file}"
  print_install_summary
}

cluster_wait_worker_runtime_health() {
  need_cmd curl
  local port="${IMAGE_PORT:-3000}"
  local timeout_seconds="${CHATGPT2API_WORKER_RUNTIME_TIMEOUT_SECONDS:-180}"
  local deadline=$(( $(date +%s) + timeout_seconds ))
  local url="http://127.0.0.1:${port}/health?format=json&scope=runtime"

  ui_println "[$(text prefix_info)] waiting for worker runtime readiness: ${url}"
  while (( $(date +%s) < deadline )); do
    if curl --fail --silent --show-error --max-time 5 "${url}" >/dev/null 2>&1; then
      ui_println "[$(text prefix_done)] worker runtime is ready: ${url}"
      return 0
    fi
    sleep 2
  done

  echo "[$(text prefix_error)] worker runtime did not become ready; recent status:" >&2
  (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml ps >&2 || true)
  (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml logs --tail=120 app >&2 || true)
  return 1
}

cluster_create_worker_cmd() {
  choose_language
  if [[ -z "${INSTALL_DIR}" ]]; then INSTALL_DIR="$(prompt_input "$(text prompt_dir)" "${INSTALL_DIR}")"; fi
  cluster_write_join_file "${1:-}"
}

cluster_rotate_worker_cmd() {
  choose_language
  if [[ -z "${INSTALL_DIR}" ]]; then INSTALL_DIR="$(prompt_input "$(text prompt_dir)" "${INSTALL_DIR}")"; fi
  local worker_no="${1:-}"
  local worker_id="worker-${worker_no}"
  local registry="${INSTALL_DIR}/join/workers.tsv"
  local join_file="${INSTALL_DIR}/join/${worker_id}.join"
  local worker_public_key=""
  cluster_worker_ip "${worker_no}" >/dev/null
  cluster_load_env
  if [[ ! -f "${registry}" ]]; then
    echo "[$(text prefix_error)] worker registry does not exist: ${registry}" >&2
    exit 1
  fi
  worker_public_key="$(awk -F '\t' -v worker="${worker_id}" '$1 == worker { print $3; exit }' "${registry}")"
  if [[ -z "${worker_public_key}" ]]; then
    echo "[$(text prefix_error)] ${worker_id} does not exist in the worker registry." >&2
    exit 1
  fi
  if ! cluster_revoke_pending_worker "${worker_id}"; then
    exit 1
  fi
  cluster_remove_wireguard_peer "${worker_id}" "${worker_public_key}"
  awk -F '\t' -v worker="${worker_id}" '$1 != worker { print }' "${registry}" >"${registry}.tmp.$$"
  mv "${registry}.tmp.$$" "${registry}"
  trash_path "${join_file}"
  cluster_write_join_file "${worker_no}"
}

cluster_cleanup_consumed_join_files() {
  if [[ ! -f "${INSTALL_DIR}/docker-compose.cluster-main.yml" ]] || ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    return
  fi
  local worker_id=""
  while IFS= read -r worker_id; do
    if [[ "${worker_id}" =~ ^worker-[0-9]+$ ]]; then
      trash_path "${INSTALL_DIR}/join/${worker_id}.join"
    fi
  done < <(
    (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-main.yml exec -T app /app/.venv/bin/python - <<'PY'
from services.cluster_join_store import ClusterJoinStore
import os

store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
for item in store.list_worker_joins():
    if item.get("status") == "joined":
        print(item["worker_id"])
PY
    ) 2>/dev/null || true
  )
}

cluster_status_cmd() {
  INSTALL_DIR="${INSTALL_DIR:-/opt/chatgpt2api}"
  cluster_load_env
  cluster_cleanup_consumed_join_files
  ui_println "Install dir: ${INSTALL_DIR}"
  cluster_resolve_wireguard_interface
  if command -v wg >/dev/null 2>&1 && wg show "${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
    ui_println "WireGuard interface: ${WIREGUARD_INTERFACE}"
    wg show "${WIREGUARD_INTERFACE}" >"${UI_OUT}" || true
  else
    ui_println "WireGuard interface: ${WIREGUARD_INTERFACE} (not running)"
  fi
  if [[ -f "${INSTALL_DIR}/join/workers.tsv" ]]; then
    ui_println "Workers:"
    cat "${INSTALL_DIR}/join/workers.tsv" >"${UI_OUT}" || true
    ui_println "Worker WireGuard reachability:"
    while IFS=$'\t' read -r worker_id worker_ip worker_public_key _created_at _join_file; do
      if [[ -z "${worker_id}" || -z "${worker_ip}" ]]; then
        continue
      fi
      if command -v ping >/dev/null 2>&1; then
        if ping -c 1 -W 2 "${worker_ip}" >/dev/null 2>&1; then
          ui_println "[OK] ${worker_id} ${worker_ip} reachable by WireGuard IP"
        else
          ui_println "[WARN] ${worker_id} ${worker_ip} is not reachable by WireGuard IP ping"
        fi
      else
        ui_println "[WARN] skip ${worker_id} ${worker_ip} ping: ping not found"
      fi
      if command -v wg >/dev/null 2>&1 && wg show "${WIREGUARD_INTERFACE}" latest-handshakes >/dev/null 2>&1; then
        local handshake_ts=""
        local now_ts=""
        handshake_ts="$(wg show "${WIREGUARD_INTERFACE}" latest-handshakes 2>/dev/null | awk -v key="${worker_public_key}" '$1 == key { print $2; exit }')"
        now_ts="$(date +%s)"
        if [[ "${handshake_ts}" =~ ^[0-9]+$ && "${handshake_ts}" -gt 0 ]]; then
          ui_println "[OK] ${worker_id} WireGuard latest handshake age $((now_ts - handshake_ts))s"
        else
          ui_println "[WARN] ${worker_id} WireGuard latest handshake not found"
        fi
      fi
    done <"${INSTALL_DIR}/join/workers.tsv"
  fi
  if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx chatgpt2api-postgres; then
    ui_println "PostgreSQL status:"
    if docker exec chatgpt2api-postgres pg_isready -U chatgpt2api_runtime -d chatgpt2api_app >/dev/null 2>&1; then
      ui_println "[OK] app database chatgpt2api_app is ready"
    else
      ui_println "[WARN] app database chatgpt2api_app is not ready"
    fi
    if docker exec chatgpt2api-postgres pg_isready -U chatgpt2api_runtime -d chatgpt2api_image_queue >/dev/null 2>&1; then
      ui_println "[OK] image queue database chatgpt2api_image_queue is ready"
    else
      ui_println "[WARN] image queue database chatgpt2api_image_queue is not ready"
    fi
    ui_println "Queue depth:"
    docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" chatgpt2api-postgres \
      psql -U chatgpt2api_runtime -d chatgpt2api_image_queue -P pager=off -c \
      "SELECT (SELECT count(*) FROM image_tasks WHERE status IN ('queued','retrying')) AS queued_tasks, (SELECT count(*) FROM image_jobs WHERE status IN ('queued','retry_wait')) AS queued_jobs, (SELECT count(*) FROM image_jobs WHERE status IN ('leased','running')) AS active_jobs, (SELECT count(*) FROM image_account_leases WHERE expires_at > now()) AS active_account_leases, (SELECT count(*) FROM image_tasks WHERE status = 'success' AND delivery_status <> 'acknowledged') AS unacknowledged_success, (SELECT min(created_at) FROM image_tasks WHERE status = 'queued') AS oldest_queued_at;" \
      >"${UI_OUT}" 2>&1 || true
    ui_println "Join records:"
    docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" chatgpt2api-postgres \
      psql -U chatgpt2api_runtime -d chatgpt2api_app -P pager=off -c \
      "SELECT worker_id, wireguard_ip, status, expires_at, joined_at FROM chatgpt2api_worker_join_token ORDER BY worker_no;" \
      >"${UI_OUT}" 2>&1 || true
    ui_println "Worker heartbeats:"
    docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" chatgpt2api-postgres \
      psql -U chatgpt2api_runtime -d chatgpt2api_image_queue -P pager=off -c \
      "SELECT worker_id, heartbeat_at, effective_concurrency, pause_reason FROM image_worker_state ORDER BY heartbeat_at DESC;" \
      >"${UI_OUT}" 2>&1 || true
  fi
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && [[ -d "${INSTALL_DIR}" ]]; then
    if [[ "${NODE_ROLE:-}" == "worker" ]]; then
      if [[ -f "${INSTALL_DIR}/docker-compose.cluster-worker.yml" ]]; then
        (cd "${INSTALL_DIR}" && docker compose -f "docker-compose.cluster-worker.yml" ps) || true
      fi
    elif [[ -f "${INSTALL_DIR}/docker-compose.cluster-main.yml" ]]; then
      (cd "${INSTALL_DIR}" && docker compose -f "docker-compose.cluster-main.yml" ps) || true
    fi
  fi
}

cluster_record_worker_delivery_check() {
  local worker_id="$1"
  local check_url="$2"
  local healthy="$3"
  local error_message="${4:-}"
  if [[ -z "${worker_id}" ]] || ! command -v docker >/dev/null 2>&1 || [[ ! -f "${INSTALL_DIR}/docker-compose.cluster-worker.yml" ]]; then
    return 1
  fi
  (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - "${worker_id}" "${check_url}" "${healthy}" "${error_message}" <<'PY'
import sys

from services.image_queue.database import ImageQueueDatabase
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.settings import ImageQueueSettings

worker_id, check_url, healthy, error_message = sys.argv[1:5]
def _bool_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", "none", "null", ""}:
        return False
    return bool(value)

expected_healthy = _bool_value(healthy)
expected_status = "healthy" if expected_healthy else "unhealthy"
database = ImageQueueDatabase(ImageQueueSettings.from_env())
database.start()
repository = ImageQueueRepository(database)
try:
    ok = repository.record_worker_delivery_status(
        worker_id,
        healthy=expected_healthy,
        url=check_url,
        error=error_message,
    )
    if not ok:
        raise SystemExit("worker delivery status row not found")
    queue_snapshot = repository.queue_snapshot()
    worker = next(
        (
            item for item in queue_snapshot.get("workers", [])
            if str(item.get("worker_id") or "") == worker_id
        ),
        None,
    )
    if worker is None:
        raise SystemExit("worker delivery status worker row not found after write")
    resource_snapshot = dict(worker.get("resource_snapshot") or {})
    if (
        resource_snapshot.get("delivery_status") != expected_status
        or str(resource_snapshot.get("delivery_url") or "") != check_url
    ):
        raise SystemExit("worker delivery status was not persisted")
finally:
    database.dispose()
PY
  ) >/dev/null 2>&1
}

cluster_worker_join_marker_status() {
  local marker_file=""
  marker_file="$(cluster_worker_marker_host_path)" || return 1
  if [[ ! -f "${marker_file}" ]]; then
    return 1
  fi
  awk -F '=' '$1 == "status" { print $2; exit }' "${marker_file}"
}

cluster_record_worker_activation_heartbeat() {
  local worker_id="$1"
  local worker_ip="$2"
  local image_base_url="$3"
  if [[ -z "${worker_id}" ]] || ! command -v docker >/dev/null 2>&1 || [[ ! -f "${INSTALL_DIR}/docker-compose.cluster-worker.yml" ]]; then
    return 1
  fi
  (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - "${worker_id}" "${worker_ip}" "${image_base_url}" <<'PY'
import sys

from services.image_queue.database import ImageQueueDatabase
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.settings import ImageQueueSettings

worker_id, worker_ip, image_base_url = sys.argv[1:4]
database = ImageQueueDatabase(ImageQueueSettings.from_env())
database.start()
repository = ImageQueueRepository(database)
try:
    repository.update_worker_state(
        worker_id,
        resource_snapshot={
            "node_role": "worker",
            "run_api": False,
            "run_worker": True,
            "wireguard_ip": worker_ip,
            "image_base_url": image_base_url,
            "cluster_join_status": "activating",
            "current_concurrency": 0,
            "effective_concurrency": 0,
            "remaining_capacity": 0,
        },
        effective_concurrency=0,
        pause_reason="activating",
    )
finally:
    database.dispose()
PY
  ) >/dev/null 2>&1
}

cluster_remove_worker_state() {
  local worker_id="$1"
  if [[ -z "${worker_id}" ]] || ! command -v docker >/dev/null 2>&1 || [[ ! -f "${INSTALL_DIR}/docker-compose.cluster-worker.yml" ]]; then
    return 1
  fi
  (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml run --rm --no-deps -T app /app/.venv/bin/python - "${worker_id}" <<'PY'
import sys

from services.image_queue.database import ImageQueueDatabase
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.settings import ImageQueueSettings

worker_id = sys.argv[1]
database = ImageQueueDatabase(ImageQueueSettings.from_env())
database.start()
repository = ImageQueueRepository(database)
try:
    repository.delete_worker_state(worker_id)
finally:
    database.dispose()
PY
  ) >/dev/null 2>&1
}

cluster_worker_check_cmd() {
  local check_public_delivery="1"
  case "${1:-}" in
    "")
      ;;
    --skip-public-delivery)
      check_public_delivery="0"
      ;;
    *)
      echo "[$(text prefix_error)] unknown worker-check option: ${1}" >&2
      return 1
      ;;
  esac
  cluster_load_env
  cluster_resolve_wireguard_interface
  local failures=0
  local worker_id="${CHATGPT2API_WORKER_ID:-${WORKER_ID:-}}"
  local worker_ip="${CHATGPT2API_WIREGUARD_IP:-${WIREGUARD_IP:-}}"
  local image_base_url="${CHATGPT2API_IMAGE_BASE_URL:-${IMAGE_BASE_URL:-}}"
  local marker_status=""
  marker_status="$(cluster_worker_join_marker_status 2>/dev/null || true)"

  ui_println "Worker ID: ${worker_id}"
  ui_println "WireGuard IP: ${worker_ip}"

  case "${marker_status}" in
    activating|joined)
      ui_println "[OK] worker join marker status: ${marker_status}"
      ;;
    *)
      ui_println "[FAILED] worker join marker is missing or invalid"
      failures=$((failures + 1))
      ;;
  esac

  if command -v wg >/dev/null 2>&1 && wg show "${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
    ui_println "[OK] WireGuard interface ${WIREGUARD_INTERFACE} is running"
  else
    ui_println "[FAILED] WireGuard interface ${WIREGUARD_INTERFACE} is not running"
    failures=$((failures + 1))
  fi

  if ! command -v ping >/dev/null 2>&1; then
    ui_println "[WARN] ping is unavailable; relying on WireGuard handshake and database checks"
  elif ping -c 1 -W 2 "${WIREGUARD_SERVER_IP}" >/dev/null 2>&1; then
    ui_println "[OK] ping ${WIREGUARD_SERVER_IP}"
  else
    ui_println "[WARN] cannot ping ${WIREGUARD_SERVER_IP}; relying on WireGuard handshake and database checks"
  fi

  if command -v wg >/dev/null 2>&1 && wg show "${WIREGUARD_INTERFACE}" latest-handshakes >/dev/null 2>&1; then
    local latest_handshake=""
    local now_ts=""
    latest_handshake="$(wg show "${WIREGUARD_INTERFACE}" latest-handshakes 2>/dev/null | awk '{print $2}' | sort -nr | head -n 1)"
    now_ts="$(date +%s)"
    if [[ "${latest_handshake}" =~ ^[0-9]+$ && "${latest_handshake}" -gt 0 && $((now_ts - latest_handshake)) -le 180 ]]; then
      ui_println "[OK] WireGuard handshake age $((now_ts - latest_handshake))s"
    else
      ui_println "[FAILED] WireGuard has no recent handshake"
      failures=$((failures + 1))
    fi
  fi

  if command -v nc >/dev/null 2>&1; then
    if nc -z -w 3 "${WIREGUARD_SERVER_IP}" 5432 >/dev/null 2>&1; then
      ui_println "[OK] PostgreSQL ${WIREGUARD_SERVER_IP}:5432 reachable"
    else
      ui_println "[FAILED] cannot connect PostgreSQL ${WIREGUARD_SERVER_IP}:5432"
      failures=$((failures + 1))
    fi
  elif command -v timeout >/dev/null 2>&1; then
    if timeout 3 bash -c "cat < /dev/null > /dev/tcp/${WIREGUARD_SERVER_IP}/5432" >/dev/null 2>&1; then
      ui_println "[OK] PostgreSQL ${WIREGUARD_SERVER_IP}:5432 reachable"
    else
      ui_println "[FAILED] cannot connect PostgreSQL ${WIREGUARD_SERVER_IP}:5432"
      failures=$((failures + 1))
    fi
  else
    ui_println "[WARN] skip TCP check: nc/timeout not found"
  fi

  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && [[ -f "${INSTALL_DIR}/docker-compose.cluster-worker.yml" ]]; then
    if (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - APP_DATABASE_URL app <<'PY'
import os, sys
from sqlalchemy import create_engine, text
from services.database_url import validate_database_role_marker
var, role = sys.argv[1], sys.argv[2]
url = os.environ.get(var, "")
engine = create_engine(url, pool_pre_ping=True)
with engine.begin() as connection:
    validate_database_role_marker(connection, role)
    connection.execute(text("SELECT 1"))
PY
    ); then
      ui_println "[OK] app database role/connectivity"
    else
      ui_println "[FAILED] app database cannot connect or role marker is not app"
      failures=$((failures + 1))
    fi

    if (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - IMAGE_QUEUE_DATABASE_URL image_queue <<'PY'
import os, sys
from sqlalchemy import create_engine, text
from services.database_url import validate_database_role_marker
var, role = sys.argv[1], sys.argv[2]
url = os.environ.get(var, "")
engine = create_engine(url, pool_pre_ping=True)
with engine.begin() as connection:
    validate_database_role_marker(connection, role)
    connection.execute(text("SELECT 1"))
PY
    ); then
      ui_println "[OK] image queue database role/connectivity"
    else
      ui_println "[FAILED] image queue database cannot connect or role marker is not image_queue"
      failures=$((failures + 1))
    fi

    if [[ "${marker_status}" == "activating" ]]; then
      if cluster_record_worker_activation_heartbeat "${worker_id}" "${worker_ip}" "${image_base_url}"; then
        ui_println "[OK] worker activation heartbeat is persisted"
      else
        ui_println "[FAILED] worker activation heartbeat could not be persisted"
        failures=$((failures + 1))
      fi
    fi

    if (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - "${worker_id}" "${marker_status}" <<'PY'
import json, os, sys, time
from sqlalchemy import create_engine, text
worker_id = sys.argv[1]
marker_status = sys.argv[2]
url = os.environ.get("IMAGE_QUEUE_DATABASE_URL", "")
engine = create_engine(url, pool_pre_ping=True)
fresh_heartbeat_seconds = 30
deadline = time.time() + 30
while time.time() < deadline:
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT heartbeat_at, resource_snapshot, "
                "EXTRACT(EPOCH FROM (now() - heartbeat_at)) AS heartbeat_age_seconds "
                "FROM image_worker_state WHERE worker_id = :worker_id"
            ),
            {"worker_id": worker_id},
        ).first()
    if row is not None:
        heartbeat_age_seconds = float(row.heartbeat_age_seconds or 0)
        if heartbeat_age_seconds > fresh_heartbeat_seconds:
            time.sleep(1)
            continue
        if marker_status == "joined":
            snapshot = row[1] or {}
            if isinstance(snapshot, str):
                snapshot = json.loads(snapshot or "{}")
            if not str((snapshot or {}).get("instance_id") or "").strip():
                time.sleep(1)
                continue
        sys.exit(0)
    time.sleep(1)
raise SystemExit("worker heartbeat is stale or not found")
PY
    ); then
      ui_println "[OK] worker heartbeat is persisted"
    else
      ui_println "[FAILED] worker heartbeat was not found in image queue database"
      failures=$((failures + 1))
    fi

    if (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - <<'PY'
from services.config import config
path = config.images_dir / ".chatgpt2api-worker-write-check"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("ok", encoding="utf-8")
path.unlink(missing_ok=True)
PY
    ); then
      ui_println "[OK] image directory is writable"
    else
      ui_println "[FAILED] image directory is not writable"
      failures=$((failures + 1))
    fi

    if [[ "${check_public_delivery}" == "1" ]]; then
      local image_base_url_valid="0"
      if [[ -n "${image_base_url}" ]]; then
        if (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - <<'PY'
import os

from services.returned_url_verifier import validate_public_image_base_url

validate_public_image_base_url(
    os.environ.get("CHATGPT2API_IMAGE_BASE_URL", ""),
    resolve_host=True,
)
PY
        ); then
          image_base_url_valid="1"
        else
          ui_println "[FAILED] worker image base URL is not publicly valid: ${image_base_url}"
          if ! cluster_record_worker_delivery_check "${worker_id}" "${image_base_url}" "false" "worker image base URL is not publicly valid"; then
            ui_println "[FAILED] failed to record worker delivery status"
            failures=$((failures + 1))
          fi
          failures=$((failures + 1))
        fi
      fi

      if [[ -n "${image_base_url}" && "${image_base_url_valid}" == "1" ]] && command -v curl >/dev/null 2>&1; then
        local check_token="$(date +%s)-$$-${RANDOM}${RANDOM}"
        local check_name=".chatgpt2api-worker-check-${worker_id}-${check_token}.png"
        local check_url=""
        local check_download_name="${check_name}.download"
        local check_download_path="${INSTALL_DIR}/data/images/${check_download_name}"
        local check_expected_sha256=""
        mkdir -p "${INSTALL_DIR}/data/images"
        trash_path "${check_download_path}"
        if check_expected_sha256="$(cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - "${check_name}" <<'PY'
import hashlib
import sys
from PIL import Image
from services.config import config
name = sys.argv[1].lstrip("/")
path = config.images_dir / name
path.parent.mkdir(parents=True, exist_ok=True)
Image.new("RGB", (1, 1), (17, 99, 203)).save(path, format="PNG")
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY
        )"; then
          check_expected_sha256="$(printf '%s' "${check_expected_sha256}" | tr -d '\r\n')"
          check_url="$(cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - "${image_base_url}" "${check_name}" <<'PY'
import sys
from services.image_url import build_public_image_url
print(build_public_image_url(sys.argv[1], sys.argv[2]))
PY
          )" || check_url=""
          check_url="$(printf '%s' "${check_url}" | tr -d '\r\n')"
          if [[ -z "${check_url}" ]]; then
            ui_println "[FAILED] could not build public image URL for worker-check"
            if ! cluster_record_worker_delivery_check "${worker_id}" "${image_base_url}" "false" "worker-check could not build public image URL"; then
              ui_println "[FAILED] failed to record worker delivery status"
              failures=$((failures + 1))
            fi
            failures=$((failures + 1))
          elif curl -fsS -H "Cache-Control: no-cache" -m 10 "${check_url}" -o "${check_download_path}" && (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - "${check_name}" "${check_download_name}" "${check_expected_sha256}" <<'PY'
import hashlib
import sys
from services.config import config
from utils.image_tokens import verify_image_bytes
original_name = sys.argv[1].lstrip("/")
download_name = sys.argv[2].lstrip("/")
expected_sha256 = sys.argv[3]
original = (config.images_dir / original_name).read_bytes()
downloaded = (config.images_dir / download_name).read_bytes()
verify_image_bytes(original)
verify_image_bytes(downloaded)
if hashlib.sha256(original).hexdigest() != expected_sha256:
    raise SystemExit("worker-check original image checksum mismatch")
if hashlib.sha256(downloaded).hexdigest() != expected_sha256:
    raise SystemExit("worker-check downloaded image checksum mismatch")
PY
          ); then
            ui_println "[OK] image URL is publicly reachable: ${check_url}"
            if ! cluster_record_worker_delivery_check "${worker_id}" "${check_url}" "true" ""; then
              ui_println "[FAILED] failed to record worker delivery status"
              failures=$((failures + 1))
            fi
          else
            ui_println "[FAILED] image URL is not publicly reachable: ${check_url}"
            if ! cluster_record_worker_delivery_check "${worker_id}" "${check_url}" "false" "worker-check image URL is not publicly reachable"; then
              ui_println "[FAILED] failed to record worker delivery status"
              failures=$((failures + 1))
            fi
            failures=$((failures + 1))
          fi
          (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-worker.yml exec -T app /app/.venv/bin/python - "${check_name}" "${check_download_name}" <<'PY'
import sys
from services.config import config
for name in sys.argv[1:]:
    (config.images_dir / name.lstrip("/")).unlink(missing_ok=True)
PY
          ) >/dev/null 2>&1 || true
          trash_path "${check_download_path}"
        else
          ui_println "[FAILED] could not create public image URL check file"
          if ! cluster_record_worker_delivery_check "${worker_id}" "${image_base_url}" "false" "worker-check could not create public image URL check file"; then
            ui_println "[FAILED] failed to record worker delivery status"
            failures=$((failures + 1))
          fi
          failures=$((failures + 1))
        fi
      elif [[ -z "${image_base_url}" || "${image_base_url_valid}" != "1" ]]; then
        if [[ -z "${image_base_url}" ]]; then
          ui_println "[FAILED] CHATGPT2API_IMAGE_BASE_URL is missing; cannot verify public image URL"
          if ! cluster_record_worker_delivery_check "${worker_id}" "${image_base_url}" "false" "worker-check image URL is missing"; then
            ui_println "[FAILED] failed to record worker delivery status"
            failures=$((failures + 1))
          fi
          failures=$((failures + 1))
        fi
      else
        ui_println "[FAILED] CHATGPT2API_IMAGE_BASE_URL or curl is missing; cannot verify public image URL"
        if ! cluster_record_worker_delivery_check "${worker_id}" "${image_base_url}" "false" "worker-check image URL or curl is missing"; then
          ui_println "[FAILED] failed to record worker delivery status"
          failures=$((failures + 1))
        fi
        failures=$((failures + 1))
      fi
    else
      ui_println "[INFO] public image delivery check deferred until worker activation completes."
    fi
  else
    ui_println "[FAILED] docker compose worker stack is not available for container checks"
    failures=$((failures + 1))
  fi

  if [[ "${failures}" -gt 0 ]]; then
    ui_println "Worker check failed: ${failures} item(s) failed."
    return 1
  fi
  ui_println "Worker check passed."
}

cluster_dispatch() {
  local command_name="${1:-}"
  case "${command_name}" in
    main)
      shift
      parse_args "$@"
      cluster_main_cmd "$@"
      ;;
    worker)
      shift
      local join_file=""
      if [[ "${1:-}" != --* && -n "${1:-}" ]]; then
        join_file="$1"
        shift
      fi
      parse_args "$@"
      cluster_worker_cmd "${join_file}" "$@"
      ;;
    create-worker)
      shift
      local worker_no="${1:-}"
      if [[ -n "${worker_no}" ]]; then
        shift
      fi
      parse_args "$@"
      cluster_create_worker_cmd "${worker_no}"
      ;;
    rotate-worker)
      shift
      local rotate_worker_no="${1:-}"
      if [[ -n "${rotate_worker_no}" ]]; then
        shift
      fi
      parse_args "$@"
      cluster_rotate_worker_cmd "${rotate_worker_no}"
      ;;
    status)
      shift
      parse_args "$@"
      cluster_status_cmd "$@"
      ;;
    worker-check)
      shift
      parse_args "$@"
      cluster_worker_check_cmd "$@"
      ;;
    *)
      return 1
      ;;
  esac
}

main() {
  parse_args "$@"
  load_existing_install_env
  # Explicit CLI flags remain the highest-priority source on rerun.
  parse_args "$@"

  local noninteractive="0"
  if [[ "${NONINTERACTIVE}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]; then
    noninteractive="1"
    INSTALL_LANG="${INSTALL_LANG:-zh}"
    normalize_language
  else
    choose_language
  fi

  if [[ -z "${INSTALL_TARGET}" ]]; then
    INSTALL_TARGET="${NODE_ROLE:-standalone}"
  fi
  if ! INSTALL_TARGET="$(resolve_install_target "${noninteractive}")"; then
    INSTALL_TARGET="$(normalize_install_target "${INSTALL_TARGET}")" || {
      echo "[$(text prefix_error)] $(text err_install_target)" >&2
      exit 1
    }
  fi

  case "${INSTALL_TARGET}" in
    api-main)
      cluster_main_cmd "$@"
      return $?
      ;;
    worker)
      cluster_worker_cmd "" "$@"
      return $?
      ;;
    standalone)
      NODE_ROLE="standalone"
      RUN_API="true"
      RUN_WORKER="true"
      ;;
    *)
      echo "[$(text prefix_error)] $(text err_install_target)" >&2
      exit 1
      ;;
  esac
  apply_install_storage_defaults

  if [[ "${noninteractive}" == "1" ]]; then
    MODE="${MODE:-docker}"
    MODE="$(normalize_mode_choice "${MODE}")" || { echo "[$(text prefix_error)] $(text err_mode)" >&2; exit 1; }
  else
    if [[ -z "${MODE}" ]]; then
      MODE="$(prompt_mode_choice "docker")"
    else
      MODE="$(normalize_mode_choice "${MODE}")" || { echo "[$(text prefix_error)] $(text err_mode)" >&2; exit 1; }
    fi
    ensure_admin_auth_key || exit 1
    BASE_URL="$(prompt_input "CHATGPT2API_BASE_URL（API/图片公网地址）" "${BASE_URL}")"
    PORT="$(prompt_input "$(text prompt_port)" "${PORT}")"
    THREAD_TOKENS="$(prompt_input "$(text prompt_thread_tokens)" "${THREAD_TOKENS}")"
    while [[ "${STORAGE_BACKEND}" == "postgres" && -z "${DATABASE_URL}" ]]; do
      DATABASE_URL="$(prompt_input "PostgreSQL DATABASE_URL" "${DATABASE_URL}")"
      if [[ -z "${DATABASE_URL}" ]]; then
        ui_println "[$(text prefix_error)] PostgreSQL DATABASE_URL is required."
      fi
    done
    while [[ -z "${IMAGE_QUEUE_DATABASE_URL}" ]]; do
      IMAGE_QUEUE_DATABASE_URL="$(prompt_input "Image queue PostgreSQL DATABASE_URL" "${IMAGE_QUEUE_DATABASE_URL}")"
      if [[ -z "${IMAGE_QUEUE_DATABASE_URL}" ]]; then
        ui_println "[$(text prefix_error)] Image queue PostgreSQL DATABASE_URL is required."
      fi
    done
    INSTALL_DIR="$(prompt_input "$(text prompt_dir)" "${INSTALL_DIR}")"
    BRANCH="$(prompt_input "$(text prompt_branch)" "${BRANCH}")"
    RELEASE_REF_SELECTED="1"

    if [[ "${MODE}" == "docker" ]]; then
      if confirm "$(text prompt_warp)" "${WITH_WARP}"; then
        WITH_WARP="1"
      else
        WITH_WARP="0"
      fi
    fi
  fi

  preflight_install_environment

  if [[ "${MODE}" == "docker" && "${RELEASE_REF_SELECTED}" == "1" ]]; then
    load_release_manifest
  fi

  if [[ "${noninteractive}" == "1" ]]; then
    ensure_admin_auth_key || exit 1
  fi
  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    POSTGRES_PASSWORD="$(generate_auth_key)"
  fi

  validate_inputs
  print_install_summary
  confirm_installation
  if [[ "${MODE}" == "docker" ]]; then
    prepare_docker_bundle
  else
    prepare_repo
  fi
  write_default_config_json
  write_env_file

  if [[ "${MODE}" == "docker" ]]; then
    run_docker
  else
    run_python
  fi

  print_install_summary
}

if [[ -z "${BASH_SOURCE[0]-}" || "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    main|worker|create-worker|rotate-worker|status|worker-check)
      cluster_dispatch "$@"
      exit $?
      ;;
  esac

  main "$@"
fi
