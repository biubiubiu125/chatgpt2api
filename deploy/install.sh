#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="${REPO_OWNER:-biubiubiu125}"
REPO_NAME="${REPO_NAME:-chatgpt2api}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/chatgpt2api}"
PORT="${CHATGPT2API_PORT:-${PORT:-3000}}"
THREAD_TOKENS="${CHATGPT2API_THREAD_TOKENS:-${THREAD_TOKENS:-80}}"
BASE_URL="${CHATGPT2API_BASE_URL:-${BASE_URL:-}}"
IMAGE_BASE_URL="${CHATGPT2API_IMAGE_BASE_URL:-${IMAGE_BASE_URL:-}}"
IMAGE_PORT="${CHATGPT2API_IMAGE_PORT:-${IMAGE_PORT:-3000}}"
NODE_ROLE="${CHATGPT2API_NODE_ROLE:-${NODE_ROLE:-standalone}}"
RUN_API="${CHATGPT2API_RUN_API:-${RUN_API:-}}"
RUN_WORKER="${CHATGPT2API_RUN_WORKER:-${RUN_WORKER:-}}"
WORKER_ID="${CHATGPT2API_WORKER_ID:-${WORKER_ID:-}}"
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
STORAGE_BACKEND="${STORAGE_BACKEND:-json}"
APP_DATABASE_URL="${APP_DATABASE_URL:-}"
DATABASE_URL="${DATABASE_URL:-}"
IMAGE_QUEUE_DATABASE_URL="${IMAGE_QUEUE_DATABASE_URL:-}"
IMAGE_QUEUE_INSTANCE_ID="${IMAGE_QUEUE_INSTANCE_ID:-}"
IMAGE_QUEUE_VERIFY_RETURNED_URL="${IMAGE_QUEUE_VERIFY_RETURNED_URL:-true}"
IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS:-5}"
IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS:-3}"
IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES="${IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES:-65536}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
POSTGRES_ADMIN_USER="${POSTGRES_ADMIN_USER:-chatgpt2api_admin}"
POSTGRES_ADMIN_PASSWORD="${POSTGRES_ADMIN_PASSWORD:-}"
INSTALL_LANG="${INSTALL_LANG:-}"
CHATGPT2API_IMAGE="${CHATGPT2API_IMAGE:-}"
GIT_REPO_URL="${GIT_REPO_URL:-}"
GIT_TOKEN="${GIT_TOKEN:-}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_FILE_PATH="${GIT_FILE_PATH:-accounts.json}"
GIT_AUTH_KEYS_FILE_PATH="${GIT_AUTH_KEYS_FILE_PATH:-auth_keys.json}"

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
  curl -fsSL https://raw.githubusercontent.com/biubiubiu125/chatgpt2api/main/deploy/install.sh | sudo bash
EOF

  printf '\n%s\n' "$(text usage_env)"
  cat <<'EOF'
  BRANCH=main
  INSTALL_DIR=/opt/chatgpt2api
  PORT=3000
  CHATGPT2API_THREAD_TOKENS=80
  CHATGPT2API_BASE_URL=https://api.example.com
  CHATGPT2API_IMAGE_BASE_URL=https://img-1.example.com/images
  CHATGPT2API_IMAGE_PORT=3000
  CHATGPT2API_NODE_ROLE=standalone|api-main|worker
  CHATGPT2API_RUN_API=true|false
  CHATGPT2API_RUN_WORKER=true|false
  CHATGPT2API_WORKER_ID=worker-1
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
  STORAGE_BACKEND=json|sqlite|postgres|git
  DATABASE_URL=postgresql://...
  IMAGE_QUEUE_DATABASE_URL=postgresql+psycopg2://...
  IMAGE_QUEUE_INSTANCE_ID=worker-1-join-nonce
  IMAGE_QUEUE_VERIFY_RETURNED_URL=true
  IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS=5
  IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS=3
  IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES=65536
  CHATGPT2API_WORKER_IMAGE_PROXY_READY=1
  POSTGRES_PASSWORD=strong-password-for-compose-postgres
  POSTGRES_ADMIN_USER=chatgpt2api_admin
  POSTGRES_ADMIN_PASSWORD=main-node-only-admin-password
  INSTALL_LANG=zh|en
  CHATGPT2API_IMAGE=ghcr.io/biubiubiu125/chatgpt2api:latest
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
  --branch main
  --auth-key your-auth-key
  --storage-backend json|sqlite|postgres|git
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
      prompt_branch) printf 'Git branch or tag' ;;
      prompt_storage) printf 'Storage backend' ;;
      prompt_auth) printf 'Admin auth key' ;;
      prompt_warp) printf 'Enable WARP / Privoxy / FlareSolverr compose' ;;
      done_ready) printf 'ChatGPT2API is ready' ;;
      done_auth) printf 'Admin auth key' ;;
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
    prompt_branch) printf 'Git 分支或标签' ;;
    prompt_storage) printf '存储后端' ;;
    prompt_auth) printf '管理员登录密钥' ;;
    prompt_warp) printf '启用 WARP / Privoxy / FlareSolverr 清障编排' ;;
    done_ready) printf 'ChatGPT2API 已就绪' ;;
    done_auth) printf '管理员登录密钥' ;;
    *) printf '%s' "${key}" ;;
  esac
}

prompt_input() {
  local label="$1"
  local default="${2-}"
  local answer=""

  if [[ -n "${default}" ]]; then
    ui_print "${label} [${default}]: "
  else
    ui_print "${label}: "
  fi

  IFS= read -r answer <"${UI_IN}" || true
  if [[ -z "${answer}" ]]; then
    answer="${default}"
  fi
  printf '%s' "${answer}"
}

confirm() {
  local label="$1"
  local default="${2:-N}"
  local default_choice="1"
  local answer=""

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
  local default="${1:-json}"
  local normalized=""
  local answer=""
  normalized="$(normalize_storage_choice "${default}")" || normalized="json"
  local default_choice="1"
  case "${normalized}" in
    json) default_choice="1" ;;
    sqlite) default_choice="2" ;;
    postgres) default_choice="3" ;;
    git) default_choice="4" ;;
  esac

  while true; do
    if is_en; then
      ui_println "Storage backend"
      ui_println "  1) json     - local JSON files (simple/default)"
      ui_println "  2) sqlite   - local SQLite database"
      ui_println "  3) postgres - external PostgreSQL database"
      ui_println "  4) git      - private Git repository"
      answer="$(prompt_input "Select" "${default_choice}")"
    else
      ui_println "存储后端"
      ui_println "  1) json     - 本地 JSON 文件（简单/默认）"
      ui_println "  2) sqlite   - 本地 SQLite 数据库"
      ui_println "  3) postgres - 外部 PostgreSQL 数据库"
      ui_println "  4) git      - 私有 Git 仓库存储"
      answer="$(prompt_input "请选择" "${default_choice}")"
    fi
    normalized="$(normalize_storage_choice "${answer}")" && { printf '%s' "${normalized}"; return; }
    ui_println "[$(text prefix_error)] $(text err_storage)"
  done
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
        shift 2
        ;;
      --auth-key)
        AUTH_KEY="${2:-}"
        shift 2
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

  normalized="$(normalize_mode_choice "${MODE}")" || { echo "[$(text prefix_error)] $(text err_mode)" >&2; exit 1; }
  MODE="${normalized}"

  normalized="$(normalize_storage_choice "${STORAGE_BACKEND}")" || { echo "[$(text prefix_error)] $(text err_storage)" >&2; exit 1; }
  STORAGE_BACKEND="${normalized}"

  if [[ -z "${PORT}" || ! "${PORT}" =~ ^[0-9]+$ ]]; then
    echo "[$(text prefix_error)] $(text err_port)" >&2
    exit 1
  fi

  if [[ -z "${THREAD_TOKENS}" || ! "${THREAD_TOKENS}" =~ ^[0-9]+$ || "${THREAD_TOKENS}" -lt 1 ]]; then
    echo "[$(text prefix_error)] $(text err_thread_tokens)" >&2
    exit 1
  fi

  if [[ "${STORAGE_BACKEND}" == "postgres" && -z "${DATABASE_URL}" ]]; then
    echo "[$(text prefix_error)] PostgreSQL DATABASE_URL is required." >&2
    exit 1
  fi

  if [[ -z "${IMAGE_QUEUE_DATABASE_URL}" ]]; then
    echo "[$(text prefix_error)] IMAGE_QUEUE_DATABASE_URL is required." >&2
    exit 1
  fi

  if [[ "${NODE_ROLE:-}" == "api-main" || "${NODE_ROLE:-}" == "worker" ]]; then
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

repo_url() {
  printf 'https://github.com/%s/%s.git' "${REPO_OWNER}" "${REPO_NAME}"
}

default_image() {
  if [[ -n "${CHATGPT2API_IMAGE}" ]]; then
    printf '%s' "${CHATGPT2API_IMAGE}"
    return
  fi

  if [[ "${BRANCH}" =~ ^v?[0-9] ]]; then
    printf 'ghcr.io/%s/%s:%s' "${REPO_OWNER}" "${REPO_NAME}" "${BRANCH}"
    return
  fi

  printf 'ghcr.io/%s/%s:latest' "${REPO_OWNER}" "${REPO_NAME}"
}

raw_url() {
  printf 'https://raw.githubusercontent.com/%s/%s/%s/%s' "${REPO_OWNER}" "${REPO_NAME}" "${BRANCH}" "$1"
}

download_file() {
  local source_path="$1"
  local target_path="${INSTALL_DIR}/${source_path}"

  mkdir -p "$(dirname "${target_path}")"
  curl -fsSL "$(raw_url "${source_path}")" -o "${target_path}"
}

download_optional_file() {
  local source_path="$1"
  download_file "${source_path}" || true
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

  mkdir -p "${INSTALL_DIR}"
  download_file "docker-compose.yml"
  download_optional_file "config.example.yaml"

  if [[ "${WITH_WARP}" == "1" ]]; then
    download_file "docker-compose.warp.yml"
    download_file "scripts/init_proxy_config.py"
    download_file "scripts/privoxy-warp.conf"
  fi
}

prepare_repo() {
  need_cmd git

  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    ui_println "[$(text prefix_info)] $(text info_update) ${INSTALL_DIR}"
    (cd "${INSTALL_DIR}" && git fetch --tags origin)
    (cd "${INSTALL_DIR}" && git checkout "${BRANCH}" >/dev/null 2>&1) || (cd "${INSTALL_DIR}" && git checkout -b "${BRANCH}" "origin/${BRANCH}")
    if (cd "${INSTALL_DIR}" && git ls-remote --exit-code --heads origin "${BRANCH}" >/dev/null 2>&1); then
      (cd "${INSTALL_DIR}" && git pull --ff-only origin "${BRANCH}")
    fi
    return
  fi

  if [[ -e "${INSTALL_DIR}" && -n "$(find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]]; then
    echo "[$(text prefix_error)] ${INSTALL_DIR} $(text err_not_git)" >&2
    exit 1
  fi

  mkdir -p "$(dirname "${INSTALL_DIR}")"
  ui_println "[$(text prefix_info)] $(text info_clone) $(repo_url) -> ${INSTALL_DIR}"
  git clone --branch "${BRANCH}" --depth 1 "$(repo_url)" "${INSTALL_DIR}"
}

write_env_file() {
  local env_file="${INSTALL_DIR}/.env"
  local tmp_file="${env_file}.tmp"
  local postgres_password_urlencoded=""
  postgres_password_urlencoded="$(cluster_urlencode "${POSTGRES_PASSWORD}")"

  cat >"${tmp_file}" <<EOF
CHATGPT2API_AUTH_KEY=$(dotenv_escape "${AUTH_KEY}")
CHATGPT2API_PORT=$(dotenv_escape "${PORT}")
CHATGPT2API_THREAD_TOKENS=$(dotenv_escape "${THREAD_TOKENS}")
CHATGPT2API_IMAGE=$(dotenv_escape "$(default_image)")
CHATGPT2API_BASE_URL=$(dotenv_escape "${BASE_URL}")
CHATGPT2API_IMAGE_BASE_URL=$(dotenv_escape "${IMAGE_BASE_URL}")
CHATGPT2API_IMAGE_PORT=$(dotenv_escape "${IMAGE_PORT}")
CHATGPT2API_NODE_ROLE=$(dotenv_escape "${NODE_ROLE}")
CHATGPT2API_RUN_API=$(dotenv_escape "${RUN_API}")
CHATGPT2API_RUN_WORKER=$(dotenv_escape "${RUN_WORKER}")
CHATGPT2API_WORKER_ID=$(dotenv_escape "${WORKER_ID}")
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
POSTGRES_PASSWORD=$(dotenv_escape "${POSTGRES_PASSWORD}")
POSTGRES_PASSWORD_URLENCODED=$(dotenv_escape "${postgres_password_urlencoded}")

GIT_REPO_URL=$(dotenv_escape "${GIT_REPO_URL}")
GIT_TOKEN=$(dotenv_escape "${GIT_TOKEN}")
GIT_BRANCH=$(dotenv_escape "${GIT_BRANCH}")
GIT_FILE_PATH=$(dotenv_escape "${GIT_FILE_PATH}")
GIT_AUTH_KEYS_FILE_PATH=$(dotenv_escape "${GIT_AUTH_KEYS_FILE_PATH}")

WARP_SOCKS_PORT=$(dotenv_escape "40000")
PRIVOXY_PORT=$(dotenv_escape "40080")
FLARESOLVERR_PORT=$(dotenv_escape "8191")
FLARESOLVERR_LOG_LEVEL=$(dotenv_escape "info")
TZ=$(dotenv_escape "Asia/Shanghai")
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
  (cd "${INSTALL_DIR}" && docker compose "${compose_args[@]}" logs --tail=120 app >&2 || true)
  exit 1
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

  ui_println "[$(text prefix_info)] $(text info_start_docker)"
  (cd "${INSTALL_DIR}" && docker compose "${compose_args[@]}" pull)
  (cd "${INSTALL_DIR}" && docker compose "${compose_args[@]}" up -d)
  wait_docker_app_health "${compose_args[@]}"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi
  need_cmd curl
  ui_println "[$(text prefix_info)] $(text info_install_uv)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
  need_cmd uv
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
  rm -rf "${INSTALL_DIR}/web_dist"
  mkdir -p "${INSTALL_DIR}/web_dist"
  cp -R "${INSTALL_DIR}/web-vue/dist/." "${INSTALL_DIR}/web_dist/"
}

run_python() {
  ensure_uv
  build_frontend
  ui_println "[$(text prefix_info)] $(text info_install_py)"
  (cd "${INSTALL_DIR}" && uv sync --frozen)

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
  export STORAGE_BACKEND="${STORAGE_BACKEND}"
  export APP_DATABASE_URL="${APP_DATABASE_URL}"
  export DATABASE_URL="${DATABASE_URL}"
  export IMAGE_QUEUE_DATABASE_URL="${IMAGE_QUEUE_DATABASE_URL}"
  export IMAGE_QUEUE_INSTANCE_ID="${IMAGE_QUEUE_INSTANCE_ID}"
  export IMAGE_QUEUE_VERIFY_RETURNED_URL="${IMAGE_QUEUE_VERIFY_RETURNED_URL}"
  export IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS}"
  export IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS}"
  export IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES="${IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES}"
  export GIT_REPO_URL="${GIT_REPO_URL}"
  export GIT_TOKEN="${GIT_TOKEN}"
  export GIT_BRANCH="${GIT_BRANCH}"
  export GIT_FILE_PATH="${GIT_FILE_PATH}"
  export GIT_AUTH_KEYS_FILE_PATH="${GIT_AUTH_KEYS_FILE_PATH}"
  exec uv run python -m scripts.run_uvicorn
}

WIREGUARD_SERVER_IP="${WIREGUARD_SERVER_IP:-10.77.0.1}"
WIREGUARD_PORT="${WIREGUARD_PORT:-51820}"
WIREGUARD_SERVER_ENDPOINT="${WIREGUARD_SERVER_ENDPOINT:-}"
JOIN_TTL_SECONDS="${JOIN_TTL_SECONDS:-604800}"
JOIN_ACTIVATION_GRACE_SECONDS="${JOIN_ACTIVATION_GRACE_SECONDS:-900}"
CLUSTER_ID="${CHATGPT2API_CLUSTER_ID:-${CLUSTER_ID:-}}"
JOIN_NONCE="${JOIN_NONCE:-}"
WORKER_JOINED_MARKER_FILE="${WORKER_JOINED_MARKER_FILE:-/app/data/worker.joined}"
WIREGUARD_INTERFACE="${WIREGUARD_INTERFACE:-wg-chatgpt2api}"

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
  local port="${WIREGUARD_PORT:-51820}"
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

cluster_load_env() {
  local env_file="${INSTALL_DIR}/.env"
  if [[ -f "${env_file}" ]]; then
    # shellcheck disable=SC1090
    set -a; . "${env_file}"; set +a
    APP_DATABASE_URL="${APP_DATABASE_URL:-}"
    DATABASE_URL="${DATABASE_URL:-}"
    IMAGE_QUEUE_DATABASE_URL="${IMAGE_QUEUE_DATABASE_URL:-}"
    IMAGE_QUEUE_INSTANCE_ID="${IMAGE_QUEUE_INSTANCE_ID:-}"
    IMAGE_QUEUE_VERIFY_RETURNED_URL="${IMAGE_QUEUE_VERIFY_RETURNED_URL:-true}"
    IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS:-5}"
    IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS="${IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS:-3}"
    IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES="${IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES:-65536}"
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
    wg pubkey <"${private_key}" >"${public_key}"
  elif [[ ! -f "${public_key}" ]]; then
    wg pubkey <"${private_key}" >"${public_key}"
  fi
  chmod 600 "${private_key}" || true
}

cluster_ensure_join_signing_key() {
  local join_dir="${INSTALL_DIR}/join"
  local private_key="${join_dir}/join-signing.key"
  local public_key="${join_dir}/join-signing.pub"
  need_cmd openssl
  mkdir -p "${join_dir}"
  if [[ ! -f "${private_key}" ]]; then
    umask 077
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${private_key}" >/dev/null 2>&1
    openssl rsa -in "${private_key}" -pubout -out "${public_key}" >/dev/null 2>&1
  elif [[ ! -f "${public_key}" ]]; then
    openssl rsa -in "${private_key}" -pubout -out "${public_key}" >/dev/null 2>&1
  fi
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
  fi
}

cluster_allow_wireguard_firewall() {
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
    ufw allow "${WIREGUARD_PORT}/udp" >/dev/null 2>&1 || true
  fi
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${WIREGUARD_PORT}/udp" >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
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
    rm -f "${tmp_file}"
  fi
  if command -v wg >/dev/null 2>&1 && wg show "${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
    wg set "${WIREGUARD_INTERFACE}" peer "${worker_public_key}" remove >/dev/null 2>&1 || true
    wg syncconf "${WIREGUARD_INTERFACE}" <(wg-quick strip "${WIREGUARD_INTERFACE}") >/dev/null 2>&1 || true
  fi
}

cluster_check_worker_database_record() {
  local worker_id="$1"
  if ! command -v docker >/dev/null 2>&1; then
    return
  fi
  local found=""
  found="$(docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" chatgpt2api-postgres \
    psql -U chatgpt2api_runtime -d chatgpt2api_image_queue -tAc \
    "SELECT 1 FROM image_worker_state WHERE worker_id='${worker_id}' LIMIT 1;" 2>/dev/null || true)"
  if [[ "${found}" =~ 1 ]]; then
    echo "[$(text prefix_error)] database worker record already exists: ${worker_id}" >&2
    exit 1
  fi
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

cluster_issue_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! (cd "${INSTALL_DIR}" && CHATGPT2API_JOIN_PAYLOAD_JSON="${payload_json}" docker compose -f docker-compose.cluster-main.yml exec -T -e CHATGPT2API_JOIN_PAYLOAD_JSON app /app/.venv/bin/python - <<'PY'
import json
import os
import sys

from services.cluster_join_store import ClusterJoinStore

payload = json.loads(os.environ["CHATGPT2API_JOIN_PAYLOAD_JSON"])
store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
try:
    store.issue_worker_join(payload)
except Exception as exc:
    print(f"failed to issue worker join token: {exc}", file=sys.stderr)
    sys.exit(1)
PY
  ); then
    echo "[$(text prefix_error)] failed to write worker join token to app database." >&2
    return 1
  fi
}

cluster_revoke_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! (cd "${INSTALL_DIR}" && CHATGPT2API_JOIN_PAYLOAD_JSON="${payload_json}" docker compose -f docker-compose.cluster-main.yml exec -T -e CHATGPT2API_JOIN_PAYLOAD_JSON app /app/.venv/bin/python - <<'PY'
import json
import os
import sys

from services.cluster_join_store import ClusterJoinStore

payload = json.loads(os.environ["CHATGPT2API_JOIN_PAYLOAD_JSON"])
store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
if not store.revoke_worker_join(payload.get("token")):
    sys.exit(1)
PY
  ); then
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
  if ! (cd "${INSTALL_DIR}" && CHATGPT2API_JOIN_PAYLOAD_JSON="${payload_json}" docker compose -f docker-compose.cluster-worker.yml run --rm --no-deps -T -e CHATGPT2API_JOIN_PAYLOAD_JSON app /app/.venv/bin/python - <<'PY'
import json
import os
import sys

from services.cluster_join_store import ClusterJoinStore

payload = json.loads(os.environ["CHATGPT2API_JOIN_PAYLOAD_JSON"])
store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
if store.validate_worker_join(payload) is None:
    print("join token is invalid, expired, already used, or payload mismatch", file=sys.stderr)
    sys.exit(1)
PY
  ); then
    echo "[$(text prefix_error)] failed to validate worker join token against app database." >&2
    return 1
  fi
}

cluster_consume_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! (cd "${INSTALL_DIR}" && CHATGPT2API_JOIN_PAYLOAD_JSON="${payload_json}" docker compose -f docker-compose.cluster-worker.yml run --rm --no-deps -T -e CHATGPT2API_JOIN_PAYLOAD_JSON app /app/.venv/bin/python - <<'PY'
import json
import os
import sys

from services.cluster_join_store import ClusterJoinStore

payload = json.loads(os.environ["CHATGPT2API_JOIN_PAYLOAD_JSON"])
store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
if store.consume_worker_join(payload) is None:
    print("join token is invalid, expired, already used, or payload mismatch", file=sys.stderr)
    sys.exit(1)
PY
  ); then
    echo "[$(text prefix_error)] failed to consume worker join token." >&2
    return 1
  fi
}

cluster_activate_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! (cd "${INSTALL_DIR}" && CHATGPT2API_JOIN_PAYLOAD_JSON="${payload_json}" docker compose -f docker-compose.cluster-worker.yml exec -T -e CHATGPT2API_JOIN_PAYLOAD_JSON app /app/.venv/bin/python - <<'PY'
import json
import os
import sys

from services.cluster_join_store import ClusterJoinStore

payload = json.loads(os.environ["CHATGPT2API_JOIN_PAYLOAD_JSON"])
store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
if store.activate_worker_join(payload) is None:
    sys.exit(1)
PY
  ); then
    echo "[$(text prefix_error)] failed to finalize worker join activation." >&2
    return 1
  fi
}

cluster_mark_activation_failed() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! (cd "${INSTALL_DIR}" && CHATGPT2API_JOIN_PAYLOAD_JSON="${payload_json}" docker compose -f docker-compose.cluster-worker.yml run --rm --no-deps -T -e CHATGPT2API_JOIN_PAYLOAD_JSON app /app/.venv/bin/python - <<'PY'
import json
import os
import sys

from services.cluster_join_store import ClusterJoinStore

payload = json.loads(os.environ["CHATGPT2API_JOIN_PAYLOAD_JSON"])
store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
if store.mark_activation_failed(payload) is None:
    sys.exit(1)
PY
  ); then
    echo "[$(text prefix_warn)] failed to mark worker join activation as failed; main-node review is required." >&2
    return 1
  fi
}

cluster_reopen_join_token() {
  local payload_json=""
  payload_json="$(cluster_join_payload_json)"
  if ! (cd "${INSTALL_DIR}" && CHATGPT2API_JOIN_PAYLOAD_JSON="${payload_json}" docker compose -f docker-compose.cluster-worker.yml run --rm --no-deps -T -e CHATGPT2API_JOIN_PAYLOAD_JSON app /app/.venv/bin/python - <<'PY'
import json
import os
import sys

from services.cluster_join_store import ClusterJoinStore

payload = json.loads(os.environ["CHATGPT2API_JOIN_PAYLOAD_JSON"])
store = ClusterJoinStore(os.environ["APP_DATABASE_URL"])
if store.reopen_worker_join(payload) is None:
    sys.exit(1)
PY
  ); then
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
  mkdir -p "${INSTALL_DIR}/deploy/postgres-init" "${INSTALL_DIR}/data" "${INSTALL_DIR}/join"
  download_file "docker-compose.cluster-main.yml"
  download_file "deploy/postgres-init/001-create-cluster-databases.sh"
  download_optional_file "deploy/nginx-worker-images.example.conf"
  chmod +x "${INSTALL_DIR}/deploy/postgres-init/001-create-cluster-databases.sh" || true
}

cluster_prepare_worker_bundle() {
  need_cmd curl
  mkdir -p "${INSTALL_DIR}/data" "${INSTALL_DIR}/join"
  download_file "docker-compose.cluster-worker.yml"
  download_optional_file "deploy/nginx-worker-images.example.conf"
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
  if [[ "${CHATGPT2API_WORKER_IMAGE_PROXY_READY:-}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]; then
    return 0
  fi
  ui_println "Before continuing, include ${config_file} in the server block for ${IMAGE_BASE_URL} and reload the public reverse proxy."
  ui_println "The generated locations only expose /images/, /image-thumbnails/ and health checks; all other paths return 403."
  if ! confirm "Public worker image proxy is configured and reloaded" "N"; then
    echo "[$(text prefix_error)] worker install stopped before join-token consumption; configure the public image proxy, then rerun the worker command." >&2
    return 1
  fi
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
  mkdir -p "${join_dir}"

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
  worker_private_key="$(wg genkey)"
  worker_public_key="$(printf '%s' "${worker_private_key}" | wg pubkey)"
  server_public_key="$(cat "/etc/wireguard/${WIREGUARD_INTERFACE}.pub")"
  app_db_url="$(cluster_external_app_db_url)"
  queue_db_url="$(cluster_external_queue_db_url)"
  signing_public_key_b64="$(cluster_base64_file "${join_dir}/join-signing.pub")"

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
EOF

  local signature=""
  signature="$(cluster_sign_payload "${payload_file}")"

  if ! cluster_issue_join_token; then
    rm -f "${payload_file}" "${join_tmp_file}"
    exit 1
  fi
  if ! cluster_add_wireguard_peer "${worker_id}" "${worker_ip}" "${worker_public_key}"; then
    cluster_revoke_join_token || true
    rm -f "${payload_file}" "${join_tmp_file}"
    exit 1
  fi
  if ! {
    cat "${payload_file}" >"${join_tmp_file}" &&
    printf 'SIGNATURE=%s\n' "${signature}" >>"${join_tmp_file}" &&
    (chmod 600 "${join_tmp_file}" || true) &&
    mv "${join_tmp_file}" "${join_file}"
  }; then
    cluster_remove_wireguard_peer "${worker_id}" "${worker_public_key}"
    cluster_revoke_join_token || true
    rm -f "${payload_file}" "${join_tmp_file}"
    exit 1
  fi
  rm -f "${payload_file}"
  if ! printf '%s\t%s\t%s\t%s\t%s\n' "${worker_id}" "${worker_ip}" "${worker_public_key}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${join_file}" >>"${registry}"; then
    cluster_remove_wireguard_peer "${worker_id}" "${worker_public_key}"
    cluster_revoke_join_token || true
    rm -f "${join_file}"
    echo "[$(text prefix_error)] failed to persist worker registry entry for ${worker_id}." >&2
    exit 1
  fi

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
    rm -f "${payload_file}" "${public_key_file}" "${signature_file}"
    echo "[$(text prefix_error)] join file public key is invalid." >&2
    exit 1
  fi
  if ! cluster_decode_base64_to_file "${actual}" "${signature_file}" 2>/dev/null; then
    rm -f "${payload_file}" "${public_key_file}" "${signature_file}"
    echo "[$(text prefix_error)] join file signature encoding is invalid." >&2
    exit 1
  fi
  if ! openssl dgst -sha256 -verify "${public_key_file}" -signature "${signature_file}" "${payload_file}" >/dev/null 2>&1; then
    rm -f "${payload_file}" "${public_key_file}" "${signature_file}"
    echo "[$(text prefix_error)] join file signature is invalid." >&2
    exit 1
  fi
  rm -f "${payload_file}" "${public_key_file}" "${signature_file}"
}

cluster_read_join_file() {
  local join_file="$1"
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
  while IFS='=' read -r key value; do
    case "${key}" in
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
    esac
  done <"${join_file}"

  if [[ -z "${WORKER_ID:-}" || -z "${WORKER_NO:-}" || -z "${WIREGUARD_IP:-}" || -z "${WIREGUARD_SERVER_IP:-}" || -z "${WIREGUARD_SERVER_ENDPOINT:-}" || -z "${WIREGUARD_SERVER_PUBLIC_KEY:-}" || -z "${WIREGUARD_WORKER_PRIVATE_KEY:-}" || -z "${WIREGUARD_WORKER_PUBLIC_KEY:-}" || -z "${APP_DATABASE_URL:-}" || -z "${IMAGE_QUEUE_DATABASE_URL:-}" || -z "${TOKEN:-}" || -z "${CLUSTER_ID:-}" || -z "${JOIN_NONCE:-}" || -z "${JOIN_EXPIRES_AT:-}" || -z "${SIGNING_PUBLIC_KEY_B64:-}" ]]; then
    echo "[$(text prefix_error)] join file is missing required fields." >&2
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
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now "wg-quick@${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || {
      wg-quick down "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || true
      wg-quick up "${WIREGUARD_INTERFACE}" >/dev/null 2>&1
    }
  else
    wg-quick down "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || true
    wg-quick up "${WIREGUARD_INTERFACE}" >/dev/null 2>&1
  fi
  if ! wg show "${WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
    echo "[$(text prefix_error)] WireGuard ${WIREGUARD_INTERFACE} failed to start on worker." >&2
    exit 1
  fi
}

cluster_remove_worker_wireguard_config() {
  cluster_resolve_wireguard_interface
  local config_file=""
  config_file="$(cluster_wireguard_config_file)"
  if [[ -f "${config_file}" ]] && grep -q '^# chatgpt2api managed WireGuard worker$' "${config_file}"; then
    if command -v systemctl >/dev/null 2>&1; then
      systemctl disable --now "wg-quick@${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || true
    elif command -v wg-quick >/dev/null 2>&1; then
      wg-quick down "${WIREGUARD_INTERFACE}" >/dev/null 2>&1 || true
    fi
    rm -f "${config_file}"
  fi
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
    rm -f "${join_file}"
  fi
}

cluster_fail_worker_activation() {
  cluster_down_compose "docker-compose.cluster-worker.yml"
  cluster_remove_worker_state "${WORKER_ID:-${CHATGPT2API_WORKER_ID:-}}" || true
  cluster_remove_worker_joined_marker
  if [[ "${WORKER_JOIN_ACTIVATING:-0}" == "1" ]]; then
    cluster_reopen_join_token || true
  fi
  cluster_remove_worker_wireguard_config
}

cluster_remove_worker_joined_marker() {
  local marker_file=""
  if marker_file="$(cluster_worker_marker_host_path 2>/dev/null)"; then
    rm -f "${marker_file}"
  fi
  rm -f "${INSTALL_DIR}/data/worker.joined"
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
  (cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" up -d "$@")
}

cluster_down_compose() {
  local compose_file="$1"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && [[ -f "${INSTALL_DIR}/${compose_file}" ]]; then
    (cd "${INSTALL_DIR}" && docker compose -f "${compose_file}" down) || true
  fi
}

cluster_run_compose() {
  local compose_file="$1"
  cluster_pull_compose "${compose_file}"
  cluster_up_compose "${compose_file}"
}

cluster_reconcile_main_databases() {
  local attempt=""
  for attempt in $(seq 1 30); do
    if (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-main.yml exec -T postgres pg_isready -U chatgpt2api_runtime -d chatgpt2api_app >/dev/null 2>&1); then
      break
    fi
    sleep 1
  done
  if ! (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-main.yml exec -T postgres pg_isready -U chatgpt2api_runtime -d chatgpt2api_app >/dev/null 2>&1); then
    echo "[$(text prefix_error)] PostgreSQL did not become ready for cluster database reconciliation." >&2
    exit 1
  fi
  if ! (cd "${INSTALL_DIR}" && docker compose -f docker-compose.cluster-main.yml exec -T postgres sh /docker-entrypoint-initdb.d/001-create-cluster-databases.sh); then
    echo "[$(text prefix_error)] failed to reconcile cluster database names or role markers." >&2
    exit 1
  fi
}

cluster_main_cmd() {
  choose_language
  MODE="docker"
  NODE_ROLE="api-main"
  RUN_API="true"
  RUN_WORKER="false"
  STORAGE_BACKEND="postgres"
  if [[ -z "${PORT}" ]]; then PORT="$(prompt_input "$(text prompt_port)" "3000")"; fi
  if [[ -z "${BASE_URL}" ]]; then BASE_URL="$(prompt_input "API 后台域名" "${BASE_URL}")"; fi
  if [[ -z "${INSTALL_DIR}" ]]; then INSTALL_DIR="$(prompt_input "$(text prompt_dir)" "${INSTALL_DIR}")"; fi
  if [[ -z "${BRANCH}" ]]; then BRANCH="$(prompt_input "$(text prompt_branch)" "${BRANCH}")"; fi
  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    POSTGRES_PASSWORD="$(prompt_input "PostgreSQL 密码" "")"
  fi
  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    POSTGRES_PASSWORD="$(generate_auth_key)"
  fi
  if [[ -z "${POSTGRES_ADMIN_PASSWORD}" ]]; then
    POSTGRES_ADMIN_PASSWORD="$(generate_auth_key)"
  fi
  cluster_ensure_cluster_id
  if [[ -z "${AUTH_KEY}" || "${AUTH_KEY}" == "your_secret_key_here" ]]; then
    AUTH_KEY="$(prompt_input "$(text prompt_auth)" "")"
  fi
  if [[ -z "${AUTH_KEY}" ]]; then
    AUTH_KEY="$(generate_auth_key)"
  fi
  WIREGUARD_PORT="$(prompt_input "WireGuard 端口" "${WIREGUARD_PORT}")"
  if [[ -z "${WIREGUARD_SERVER_ENDPOINT}" ]]; then
    WIREGUARD_SERVER_ENDPOINT="$(prompt_input "WireGuard 主节点公网 IP/域名" "${WIREGUARD_SERVER_ENDPOINT}")"
  fi
  if [[ -z "${WIREGUARD_SERVER_ENDPOINT}" ]]; then
    echo "[$(text prefix_error)] WIREGUARD_SERVER_ENDPOINT is required." >&2
    exit 1
  fi
  APP_DATABASE_URL="$(cluster_external_app_db_url)"
  DATABASE_URL="${APP_DATABASE_URL}"
  IMAGE_QUEUE_DATABASE_URL="$(cluster_external_queue_db_url)"

  validate_inputs
  cluster_prepare_main_bundle
  write_default_config_json
  write_env_file
  cluster_setup_main_wireguard
  cluster_pull_compose "docker-compose.cluster-main.yml"
  cluster_up_compose "docker-compose.cluster-main.yml" postgres
  cluster_reconcile_main_databases
  cluster_up_compose "docker-compose.cluster-main.yml" app

  if confirm "是否自动生成第一个从节点配置" "Y"; then
    local first_worker=""
    first_worker="$(prompt_input "第一个 Worker 编号" "1")"
    cluster_write_join_file "${first_worker}"
  fi
}

cluster_worker_cmd() {
  choose_language
  MODE="docker"
  NODE_ROLE="worker"
  RUN_API="false"
  RUN_WORKER="true"
  STORAGE_BACKEND="postgres"
  if [[ -z "${INSTALL_DIR}" ]]; then INSTALL_DIR="$(prompt_input "$(text prompt_dir)" "${INSTALL_DIR}")"; fi
  local join_file="${1:-${INSTALL_DIR}/join/worker.join}"
  cluster_verify_join_file "${join_file}"
  cluster_read_join_file "${join_file}"
  IMAGE_QUEUE_INSTANCE_ID="${IMAGE_QUEUE_INSTANCE_ID:-${WORKER_ID}-${JOIN_NONCE}}"
  if [[ -n "${JOIN_EXPIRES_AT:-}" && "$(date +%s)" -gt "${JOIN_EXPIRES_AT}" ]]; then
    echo "[$(text prefix_error)] join file expired." >&2
    exit 1
  fi
  if [[ -z "${IMAGE_BASE_URL}" ]]; then IMAGE_BASE_URL="$(prompt_input "请输入当前从节点图片返回 URL" "${IMAGE_BASE_URL}")"; fi
  if [[ -z "${IMAGE_BASE_URL}" ]]; then
    echo "[$(text prefix_error)] CHATGPT2API_IMAGE_BASE_URL is required." >&2
    exit 1
  fi
  AUTH_KEY="${AUTH_KEY:-$(generate_auth_key)}"
  POSTGRES_ADMIN_USER=""
  POSTGRES_ADMIN_PASSWORD=""
  DATABASE_URL="${APP_DATABASE_URL}"
  validate_inputs
  cluster_prepare_worker_bundle
  cluster_write_worker_nginx_config
  if ! cluster_confirm_worker_image_proxy; then
    exit 1
  fi
  cluster_write_worker_wireguard_config
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
  if ! cluster_consume_join_token; then
    cluster_remove_worker_wireguard_config
    exit 1
  fi
  WORKER_JOIN_ACTIVATING="1"
  if ! cluster_write_joined_marker "activating"; then
    cluster_fail_worker_activation
    exit 1
  fi
  if ! cluster_up_compose "docker-compose.cluster-worker.yml"; then
    cluster_fail_worker_activation
    exit 1
  fi
  if ! cluster_worker_check_cmd; then
    cluster_fail_worker_activation
    exit 1
  fi
  if ! cluster_write_joined_marker "joined"; then
    cluster_fail_worker_activation
    exit 1
  fi
  if ! cluster_activate_join_token; then
    cluster_fail_worker_activation
    exit 1
  fi
  WORKER_JOIN_ACTIVATING="0"
  if ! cluster_worker_check_cmd; then
    echo "[$(text prefix_error)] worker joined but final health check failed; keep the stack running and rerun worker-check after fixing the reported cause." >&2
    exit 1
  fi
  cluster_cleanup_worker_join_file "${join_file}"
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
  rm -f "${join_file}"
  cluster_write_join_file "${worker_no}"
}

cluster_cleanup_consumed_join_files() {
  if [[ ! -f "${INSTALL_DIR}/docker-compose.cluster-main.yml" ]] || ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    return
  fi
  local worker_id=""
  while IFS= read -r worker_id; do
    if [[ "${worker_id}" =~ ^worker-[0-9]+$ ]]; then
      rm -f "${INSTALL_DIR}/join/${worker_id}.join"
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
expected_healthy = healthy.strip().lower() in {"1", "true", "yes", "on"}
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

  if command -v ping >/dev/null 2>&1 && ping -c 1 -W 2 "${WIREGUARD_SERVER_IP}" >/dev/null 2>&1; then
    ui_println "[OK] ping ${WIREGUARD_SERVER_IP}"
  else
    ui_println "[FAILED] cannot ping ${WIREGUARD_SERVER_IP}; check WireGuard endpoint, port and firewall"
    failures=$((failures + 1))
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
      rm -f "${check_download_path}"
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
        rm -f "${check_download_path}"
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
      cluster_worker_cmd "${join_file}"
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
  if [[ "${NONINTERACTIVE}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]; then
    INSTALL_LANG="${INSTALL_LANG:-zh}"
    normalize_language
    MODE="${MODE:-docker}"
    MODE="$(normalize_mode_choice "${MODE}")" || { echo "[$(text prefix_error)] $(text err_mode)" >&2; exit 1; }
    STORAGE_BACKEND="$(normalize_storage_choice "${STORAGE_BACKEND}")" || { echo "[$(text prefix_error)] $(text err_storage)" >&2; exit 1; }
  else
    choose_language

    if [[ -z "${MODE}" ]]; then
      MODE="$(prompt_mode_choice "docker")"
    else
      MODE="$(normalize_mode_choice "${MODE}")" || { echo "[$(text prefix_error)] $(text err_mode)" >&2; exit 1; }
    fi
    PORT="$(prompt_input "$(text prompt_port)" "${PORT}")"
    THREAD_TOKENS="$(prompt_input "$(text prompt_thread_tokens)" "${THREAD_TOKENS}")"
    BASE_URL="$(prompt_input "CHATGPT2API_BASE_URL (optional)" "${BASE_URL}")"
    INSTALL_DIR="$(prompt_input "$(text prompt_dir)" "${INSTALL_DIR}")"
    BRANCH="$(prompt_input "$(text prompt_branch)" "${BRANCH}")"
    STORAGE_BACKEND="$(prompt_storage_choice "${STORAGE_BACKEND}")"
    prompt_storage_details

    if [[ -z "${IMAGE_QUEUE_DATABASE_URL}" ]]; then
      IMAGE_QUEUE_DATABASE_URL="$(prompt_input "Image queue PostgreSQL URL" "${IMAGE_QUEUE_DATABASE_URL}")"
    fi

    if [[ "${MODE}" == "docker" ]]; then
      if confirm "$(text prompt_warp)" "${WITH_WARP}"; then
        WITH_WARP="1"
      else
        WITH_WARP="0"
      fi
    fi
  fi

  if [[ -z "${AUTH_KEY}" || "${AUTH_KEY}" == "your_secret_key_here" ]]; then
    AUTH_KEY="$(generate_auth_key)"
  fi
  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    POSTGRES_PASSWORD="$(generate_auth_key)"
  fi
  if [[ ! "${NONINTERACTIVE}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]; then
    AUTH_KEY="$(prompt_input "$(text prompt_auth)" "${AUTH_KEY}")"
  fi

  validate_inputs
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

  ui_println ""
  ui_println "[$(text prefix_done)] $(text done_ready): http://localhost:${PORT}"
  ui_println "[$(text prefix_done)] $(text done_auth): ${AUTH_KEY}"
}

case "${1:-}" in
  main|worker|create-worker|rotate-worker|status|worker-check)
    cluster_dispatch "$@"
    exit $?
    ;;
esac

main "$@"
