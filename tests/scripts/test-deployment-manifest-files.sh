#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARCHIVE_FILE="$(mktemp)"
trap 'rm -f "${ARCHIVE_FILE}"' EXIT

if grep -qxF '*' "${ROOT_DIR}/.gitignore"; then
  printf 'blanket repository ignore rule hides deployment source files\n' >&2
  exit 1
fi
for ignored_path in \
  .env \
  data \
  deploy/worker-1.join \
  deploy/workers.tsv \
  deploy/nginx-worker-images.conf \
  run-python.sh; do
  git -C "${ROOT_DIR}" check-ignore -q -- "${ignored_path}" || {
    printf 'runtime deployment artifact is not ignored: %s\n' "${ignored_path}" >&2
    exit 1
  }
done

required_files=(
  "scripts/bootstrap_database_roles.py"
  "tests/scripts/test-install-script.sh"
  "tests/scripts/test-frontend-script-manifest.sh"
  "web-vue/scripts/test-debug-center-editable-tasks.mjs"
  "web-vue/scripts/test-register-provider-view.mjs"
  "web-vue/scripts/test-studio-image-task-closed-loop.mjs"
  "web-vue/scripts/test-studio-image-task-runtime.mjs"
  "web-vue/scripts/test-third-party-links.mjs"
  "web-vue/scripts/test-safe-external-url.mjs"
  "deploy/release-manifest.env"
)

for relative_path in "${required_files[@]}"; do
  absolute_path="${ROOT_DIR}/${relative_path}"
  [[ -f "${absolute_path}" ]] || {
    printf 'missing required deployment file: %s\n' "${relative_path}" >&2
    exit 1
  }
  if git -C "${ROOT_DIR}" check-ignore -q -- "${relative_path}"; then
    printf 'required deployment file is ignored: %s\n' "${relative_path}" >&2
    exit 1
  fi
  git -C "${ROOT_DIR}" ls-files --error-unmatch -- "${relative_path}" >/dev/null 2>&1 || {
    printf 'required deployment file is not tracked: %s\n' "${relative_path}" >&2
    exit 1
  }
done

grep -Eq '^USER[[:space:]]+chatgpt2api$' "${ROOT_DIR}/Dockerfile" \
  || {
    printf 'application image does not declare a non-root runtime user\n' >&2
    exit 1
  }
grep -Eq '^ENV PORT=3000$' "${ROOT_DIR}/Dockerfile" \
  || {
    printf 'non-root application image does not use an unprivileged listen port\n' >&2
    exit 1
  }
grep -Eq '^EXPOSE 3000$' "${ROOT_DIR}/Dockerfile" \
  || {
    printf 'Dockerfile does not expose the unprivileged application port\n' >&2
    exit 1
  }
if grep -Eq '容器内服务端口：`80`|后端默认监听 `0\.0\.0\.0:80`' "${ROOT_DIR}/README.md"; then
  printf 'README still documents the obsolete container port 80\n' >&2
  exit 1
fi

for compose_file in \
  "${ROOT_DIR}/docker-compose.yml" \
  "${ROOT_DIR}/docker-compose.warp.yml" \
  "${ROOT_DIR}/docker-compose.local.yml" \
  "${ROOT_DIR}/docker-compose.cluster-main.yml" \
  "${ROOT_DIR}/docker-compose.cluster-worker.yml"; do
  grep -q '^  data-permissions:' "${compose_file}" \
    || {
      printf 'bind-mounted data permissions initializer is missing: %s\n' "${compose_file}" >&2
      exit 1
    }
  grep -q 'data-permissions:' "${compose_file}" \
    || {
      printf 'application does not wait for bind-mounted data permissions: %s\n' "${compose_file}" >&2
      exit 1
    }
  grep -q '! -name postgres' "${compose_file}" \
    || {
      printf 'data permissions initializer would chown PostgreSQL data: %s\n' "${compose_file}" >&2
      exit 1
    }
  for runtime_key in \
    CHATGPT2API_AUTH_KEY CHATGPT2API_BASE_URL CHATGPT2API_IMAGE_BASE_URL \
    CHATGPT2API_THREAD_TOKENS \
    CHATGPT2API_BACKUP_PASSPHRASE \
    CHATGPT2API_MONITOR_COMPLETED_LIMIT \
    CHATGPT2API_MONITOR_EVENT_LIMIT \
    CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS \
    CHATGPT2API_RUNTIME_LOG_FILE \
    HOST LOG_LEVEL UVICORN_WORKERS \
    IMAGE_QUEUE_LEGACY_TASK_PATH IMAGE_QUEUE_LEASE_SECONDS \
    IMAGE_QUEUE_HEARTBEAT_SECONDS IMAGE_QUEUE_POLL_INTERVAL_SECONDS \
    IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS IMAGE_QUEUE_GENERATION_ATTEMPTS \
    IMAGE_QUEUE_DOWNLOAD_ATTEMPTS IMAGE_QUEUE_SAVE_ATTEMPTS \
    IMAGE_QUEUE_CPU_THROTTLE_PERCENT IMAGE_QUEUE_CPU_PAUSE_PERCENT \
    IMAGE_QUEUE_CPU_RESUME_PERCENT IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT \
    IMAGE_QUEUE_MEMORY_PAUSE_PERCENT IMAGE_QUEUE_MEMORY_REJECT_PERCENT \
    IMAGE_QUEUE_DB_POOL_SIZE IMAGE_QUEUE_DB_MAX_OVERFLOW \
    EDITABLE_FILE_WORKERS EDITABLE_FILE_MAX_BACKLOG \
    PROMPT_LIBRARY_DEFAULT_URL PROMPT_LIBRARY_REMOTE_URL; do
    grep -Eq "^      ${runtime_key}:" "${compose_file}" \
      || {
        printf 'runtime environment key is not passed into the app container: %s (%s)\n' "${runtime_key}" "${compose_file}" >&2
        exit 1
      }
  done
done

for compose_file in "${ROOT_DIR}/docker-compose.yml" "${ROOT_DIR}/docker-compose.warp.yml"; do
  grep -q 'CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS' "${compose_file}" \
    || {
      printf 'database bootstrap retry settings are missing: %s\n' "${compose_file}" >&2
      exit 1
    }
  grep -Eq ':3000(["]|$)' "${compose_file}" \
    || {
      printf 'compose app is not mapped to the unprivileged container port: %s\n' "${compose_file}" >&2
      exit 1
    }
done

grep -q 'test: \["CMD-SHELL", "wget -q' "${ROOT_DIR}/docker-compose.warp.yml" \
  || {
    printf 'WARP Privoxy healthcheck depends on a command not guaranteed by the image: expected BusyBox wget\n' >&2
    exit 1
  }
if grep -q 'test: \["CMD-SHELL", "nc -z' "${ROOT_DIR}/docker-compose.warp.yml"; then
  printf 'WARP Privoxy healthcheck still depends on nc, which the pinned image does not install\n' >&2
  exit 1
fi
for compose_file in "${ROOT_DIR}/docker-compose.yml" "${ROOT_DIR}/docker-compose.warp.yml"; do
  if grep -Fq './scripts:/app/scripts:ro' "${compose_file}"; then
    printf 'Compose overlays the image scripts directory and hides the runtime launcher: %s\n' "${compose_file}" >&2
    exit 1
  fi
done
grep -Fq './scripts/bootstrap_database_roles.py:/app/scripts/bootstrap_database_roles.py:ro' \
  "${ROOT_DIR}/docker-compose.yml" \
  || {
    printf 'standard database bootstrap does not use a single-file script mount\n' >&2
    exit 1
  }
grep -Fq './scripts/env_loader.py:/app/scripts/env_loader.py:ro' \
  "${ROOT_DIR}/docker-compose.yml" \
  || {
    printf 'standard database bootstrap does not mount the safe dotenv loader\n' >&2
    exit 1
  }
grep -Fq './scripts/init_proxy_config.py:/app/scripts/init_proxy_config.py:ro' \
  "${ROOT_DIR}/docker-compose.warp.yml" \
  || {
    printf 'WARP proxy initialization does not use a single-file script mount\n' >&2
    exit 1
  }
grep -Fq './scripts/bootstrap_database_roles.py:/app/scripts/bootstrap_database_roles.py:ro' \
  "${ROOT_DIR}/docker-compose.warp.yml" \
  || {
    printf 'WARP database bootstrap does not use a single-file script mount\n' >&2
    exit 1
  }
grep -Fq './scripts/env_loader.py:/app/scripts/env_loader.py:ro' \
  "${ROOT_DIR}/docker-compose.warp.yml" \
  || {
    printf 'WARP database bootstrap does not mount the safe dotenv loader\n' >&2
    exit 1
  }

for documentation_file in "${ROOT_DIR}/README.md" "${ROOT_DIR}/docs/deployment.md"; do
  grep -q 'GRANT USAGE, CREATE ON SCHEMA public' "${documentation_file}" \
    || {
      printf 'external PostgreSQL schema permission instructions are missing: %s\n' "${documentation_file}" >&2
      exit 1
    }
done

index_tree="$(git -C "${ROOT_DIR}" write-tree)"
git -C "${ROOT_DIR}" archive --format=tar "${index_tree}" >"${ARCHIVE_FILE}"
for relative_path in "${required_files[@]}"; do
  tar -tf "${ARCHIVE_FILE}" | grep -Fx "${relative_path}" >/dev/null || {
    printf 'required deployment file is absent from the Git archive: %s\n' "${relative_path}" >&2
    exit 1
  }
done

printf '[deployment-manifest-files] all assertions passed\n'
