#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${1:?install directory is required}"
REQUEST_DIR="${CHATGPT2API_CLUSTER_JOIN_REQUEST_DIR:-${INSTALL_DIR}/data/cluster-join-requests}"
INSTALL_SCRIPT="${INSTALL_DIR}/deploy/install.sh"
POLL_SECONDS="${CHATGPT2API_CLUSTER_JOIN_HELPER_POLL_SECONDS:-1}"
MAX_SECONDS="${CHATGPT2API_CLUSTER_JOIN_HELPER_MAX_SECONDS:-90}"
LOG_FILE="${CHATGPT2API_CLUSTER_JOIN_HELPER_LOG_FILE:-${INSTALL_DIR}/data/cluster-join-helper.log}"

if [[ -z "${MAX_SECONDS}" || ! "${MAX_SECONDS}" =~ ^[0-9]+$ || "${MAX_SECONDS}" -lt 1 ]]; then
  MAX_SECONDS="90"
fi

mkdir -p "${REQUEST_DIR}"
chmod 700 "${REQUEST_DIR}" || true
chown 10001:10001 "${REQUEST_DIR}" 2>/dev/null || true
touch "${LOG_FILE}"
chmod 600 "${LOG_FILE}" || true

retire_file() {
  local source="$1"
  [[ -e "${source}" ]] || return 0
  local trash_dir="${REQUEST_DIR}/.trash"
  mkdir -p "${trash_dir}"
  chmod 700 "${trash_dir}" || true
  mv -f "${source}" "${trash_dir}/$(basename "${source}").$(date +%s%N)" 2>/dev/null || true
}

write_status() {
  local status_path="$1"
  local status="$2"
  local temporary="${status_path}.tmp.$$"
  if ! printf '%s\n' "${status}" >"${temporary}"; then
    return 1
  fi
  chown 10001:10001 "${temporary}" 2>/dev/null || true
  chmod 600 "${temporary}" || true
  if ! mv -f "${temporary}" "${status_path}"; then
    retire_file "${temporary}"
    return 1
  fi
}

write_error() {
  local error_path="$1"
  local error_message="$2"
  local temporary="${error_path}.tmp.$$"
  if ! printf '%s\n' "${error_message}" >"${temporary}"; then
    return 1
  fi
  chown 10001:10001 "${temporary}" 2>/dev/null || true
  chmod 600 "${temporary}" || true
  if ! mv -f "${temporary}" "${error_path}"; then
    retire_file "${temporary}"
    return 1
  fi
}

# Summarize installer output for the API. Prefer the last line the installer
# marked as an error, because the management API classifies conflicts (a worker
# number that is already taken) from this text; fall back to the last non-empty
# line. Takes the captured text, not a path: the output file is retired as soon
# as it has been read.
summarize_output() {
  local output_text="$1"
  local summary=""
  summary="$(printf '%s\n' "${output_text}" \
    | tail -n 40 \
    | awk '/\[错误\]|\[ERROR\]|[Ee]rror|already exists/ { line=$0 } END { print line }')"
  if [[ -z "${summary}" ]]; then
    summary="$(printf '%s\n' "${output_text}" | tail -n 40 | awk 'NF { line=$0 } END { print line }')"
  fi
  summary="${summary//$'\r'/ }"
  summary="${summary//$'\n'/ }"
  summary="${summary# }"
  summary="${summary% }"
  if [[ -z "${summary}" ]]; then
    summary="Worker join helper failed"
  fi
  printf '%s' "${summary:0:300}"
}

request_is_cancelled() {
  local request_id="$1"
  local processing_path="$2"
  [[ -e "${REQUEST_DIR}/${request_id}.cancel" || ! -e "${processing_path}" ]]
}

# List every PID still in the installer's process group. `setsid` starts the installer
# in a brand-new group whose id equals its pid, so this sees the installer and every
# helper it spawned, and never anything belonging to this helper.
process_group_pids() {
  local pgid="$1"
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -g "${pgid}" 2>/dev/null | awk 'NF'
    return 0
  fi
  ps -o pid= -g "${pgid}" 2>/dev/null | awk 'NF'
}

# Stop the installer and let it roll back. Only the installer itself is signalled, not
# its process group: its cleanup trap has to run `wg`, `psql` and `docker compose` to
# remove the WireGuard peer and revoke the join token, and killing the group first would
# take those helpers down with it and strand a half-registered Worker.
# The group is only signalled once the grace period expires and work is still running,
# so nothing is left behind in the background.
terminate_child() {
  local child_pid="$1"
  local process_group="$2"
  local grace_seconds="${CHATGPT2API_CLUSTER_JOIN_HELPER_ROLLBACK_SECONDS:-30}"
  local rollback_deadline=0
  local remaining=""

  if [[ -z "${grace_seconds}" || ! "${grace_seconds}" =~ ^[0-9]+$ ]]; then
    grace_seconds="30"
  fi
  if [[ -z "${child_pid}" ]] || ! kill -0 "${child_pid}" 2>/dev/null; then
    return 0
  fi

  kill -TERM "${child_pid}" 2>/dev/null || true
  # Waiting on the installer's pid alone is not enough. A shell blocked on a foreground
  # command dies on SIGTERM without forwarding it whenever no TERM trap is installed
  # yet, which orphans that command: a cancelled request could still finish adding the
  # WireGuard peer and issuing the join token long after the API answered. Wait for the
  # whole group to drain instead, so both the rollback path and the orphan path are
  # covered.
  rollback_deadline=$(( $(date +%s) + grace_seconds ))
  while (( $(date +%s) < rollback_deadline )); do
    if [[ "${process_group}" == "1" ]]; then
      [[ -z "$(process_group_pids "${child_pid}")" ]] && break
    else
      kill -0 "${child_pid}" 2>/dev/null || break
    fi
    sleep 1
  done

  if [[ "${process_group}" == "1" ]]; then
    remaining="$(process_group_pids "${child_pid}")"
    if [[ -n "${remaining}" ]]; then
      printf '%s\n' "installer did not roll back within ${grace_seconds}s; forcing termination" >>"${LOG_FILE}" || true
      # shellcheck disable=SC2086 # remaining is a whitespace-separated PID list.
      kill -KILL -- "-${child_pid}" 2>/dev/null || kill -KILL ${remaining} 2>/dev/null || true
    fi
  elif kill -0 "${child_pid}" 2>/dev/null; then
    printf '%s\n' "installer did not roll back within ${grace_seconds}s; forcing termination" >>"${LOG_FILE}" || true
    kill -KILL "${child_pid}" 2>/dev/null || true
  fi
  wait "${child_pid}" 2>/dev/null || true
}

recover_processing_requests() {
  local processing_path=""
  local request_id=""
  local status_path=""
  local request_path=""
  local cancel_path=""

  while IFS= read -r processing_path; do
    [[ -n "${processing_path}" ]] || continue
    request_id="$(basename "${processing_path}" .processing)"
    status_path="${REQUEST_DIR}/${request_id}.status"
    request_path="${REQUEST_DIR}/${request_id}.request"
    cancel_path="${REQUEST_DIR}/${request_id}.cancel"
    if [[ -e "${cancel_path}" ]]; then
      printf '%s\n' "recover discarded cancelled Worker join request ${request_id}" >>"${LOG_FILE}" || true
      retire_file "${processing_path}"
      retire_file "${cancel_path}"
    elif [[ -e "${status_path}" ]]; then
      # The helper may have published the response and been interrupted just
      # before retiring the processing marker. Keep the response available
      # to the API and only remove the stale marker.
      retire_file "${processing_path}"
    elif [[ ! -e "${request_path}" ]]; then
      mv -f "${processing_path}" "${request_path}" 2>/dev/null || true
    else
      retire_file "${processing_path}"
    fi
  done < <(find "${REQUEST_DIR}" -maxdepth 1 -type f -name '*.processing' -print | sort)

  local cancel_path=""
  while IFS= read -r cancel_path; do
    [[ -n "${cancel_path}" ]] || continue
    request_id="$(basename "${cancel_path}" .cancel)"
    if [[ ! -e "${REQUEST_DIR}/${request_id}.request" && ! -e "${REQUEST_DIR}/${request_id}.processing" ]]; then
      retire_file "${cancel_path}"
    fi
  done < <(find "${REQUEST_DIR}" -maxdepth 1 -type f -name '*.cancel' -print | sort)
}

process_request() {
  local request_path="$1"
  local request_id=""
  local processing_path=""
  local worker_no=""
  local status_path=""
  local join_path=""
  local output=""
  local exit_code=0
  local generated_file=""
  local join_path_tmp=""
  local cancel_path=""
  local output_file=""
  local child_pid=""
  local process_group="0"
  local deadline=0
  local error_path=""
  local operation=""
  local installer_command=""

  request_id="$(basename "${request_path}" .request)"
  processing_path="${REQUEST_DIR}/${request_id}.processing"
  cancel_path="${REQUEST_DIR}/${request_id}.cancel"
  error_path="${REQUEST_DIR}/${request_id}.error"
  if ! mv -f "${request_path}" "${processing_path}" 2>/dev/null; then
    return 0
  fi
  if [[ -e "${cancel_path}" ]]; then
    printf '%s\n' "cancelled Worker join request ${request_id} before processing" >>"${LOG_FILE}" || true
    retire_file "${processing_path}"
    retire_file "${cancel_path}"
    return 0
  fi

  worker_no="$(sed -n 's/^worker_no=\([0-9][0-9]*\)$/\1/p' "${processing_path}" | head -n 1 || true)"
  # A request without an operation comes from an older management API; treat it as a
  # create so an in-flight request survives an upgrade.
  operation="$(sed -n 's/^operation=\([a-z-]*\)$/\1/p' "${processing_path}" | head -n 1 || true)"
  operation="${operation:-create}"
  status_path="${REQUEST_DIR}/${request_id}.status"
  join_path="${REQUEST_DIR}/${request_id}.join"
  if [[ ! "${worker_no}" =~ ^[0-9]+$ ]] || (( worker_no < 1 || worker_no > 244 )); then
    write_error "${error_path}" "invalid Worker number: ${worker_no}" || true
    write_status "${status_path}" "error" || true
    retire_file "${processing_path}"
    return 0
  fi
  case "${operation}" in
    create) installer_command="create-worker" ;;
    rotate) installer_command="rotate-worker" ;;
    *)
      write_error "${error_path}" "unsupported Worker join operation: ${operation}" || true
      write_status "${status_path}" "error" || true
      retire_file "${processing_path}"
      return 0
      ;;
  esac

  if [[ ! -f "${INSTALL_SCRIPT}" ]]; then
    printf '%s\n' "missing installer: ${INSTALL_SCRIPT}" >>"${LOG_FILE}" || true
    write_error "${error_path}" "missing installer: ${INSTALL_SCRIPT}" || true
    write_status "${status_path}" "error" || true
    retire_file "${processing_path}"
    return 0
  fi

  printf '%s\n' "processing ${operation} Worker join request ${request_id} for worker-${worker_no}" >>"${LOG_FILE}" || true
  output_file="${REQUEST_DIR}/.${request_id}.output.$$"
  if command -v setsid >/dev/null 2>&1; then
    setsid env INSTALL_DIR="${INSTALL_DIR}" NONINTERACTIVE=1 \
      bash "${INSTALL_SCRIPT}" "${installer_command}" "${worker_no}" >"${output_file}" 2>&1 &
    child_pid="$!"
    process_group="1"
  else
    INSTALL_DIR="${INSTALL_DIR}" NONINTERACTIVE=1 \
      bash "${INSTALL_SCRIPT}" "${installer_command}" "${worker_no}" >"${output_file}" 2>&1 &
    child_pid="$!"
  fi
  deadline=$(( $(date +%s) + MAX_SECONDS ))
  while kill -0 "${child_pid}" 2>/dev/null; do
    if request_is_cancelled "${request_id}" "${processing_path}"; then
      printf '%s\n' "cancelled Worker join request ${request_id} for worker-${worker_no}" >>"${LOG_FILE}" || true
      terminate_child "${child_pid}" "${process_group}"
      retire_file "${output_file}"
      retire_file "${processing_path}"
      retire_file "${cancel_path}"
      return 0
    fi
    if (( $(date +%s) >= deadline )); then
      printf '%s\n' "timed out Worker join request ${request_id} for worker-${worker_no} after ${MAX_SECONDS}s" >>"${LOG_FILE}" || true
      write_error "${error_path}" "timed out Worker join request for worker-${worker_no} after ${MAX_SECONDS}s" || true
      terminate_child "${child_pid}" "${process_group}"
      write_status "${status_path}" "error" || true
      retire_file "${output_file}"
      retire_file "${processing_path}"
      retire_file "${cancel_path}"
      return 0
    fi
    sleep "${POLL_SECONDS}"
  done
  if wait "${child_pid}"; then
    exit_code=0
  else
    exit_code=$?
  fi
  output="$(cat "${output_file}" 2>/dev/null || true)"
  retire_file "${output_file}"
  printf '%s\n' "${output}" >>"${LOG_FILE}" || true

  generated_file="${INSTALL_DIR}/join/worker-${worker_no}.join"
  if (( exit_code == 0 )) && [[ -s "${generated_file}" ]]; then
    join_path_tmp="${join_path}.tmp.$$"
    if ! request_is_cancelled "${request_id}" "${processing_path}" && cp -- "${generated_file}" "${join_path_tmp}"; then
      chown 10001:10001 "${join_path_tmp}" 2>/dev/null || true
      chmod 600 "${join_path_tmp}" || true
      if mv -f "${join_path_tmp}" "${join_path}" && write_status "${status_path}" "ok"; then
        retire_file "${error_path}"
        :
      else
        printf '%s\n' "failed to publish Worker join response for worker-${worker_no}" >>"${LOG_FILE}" || true
        retire_file "${join_path_tmp}"
        retire_file "${join_path}"
        write_status "${status_path}" "error" || true
      fi
    else
      printf '%s\n' "failed to publish Worker join response for worker-${worker_no}" >>"${LOG_FILE}" || true
      retire_file "${join_path_tmp}"
      retire_file "${join_path}"
      write_status "${status_path}" "error" || true
    fi
  else
    write_error "${error_path}" "join ${operation} failed for worker-${worker_no}: $(summarize_output "${output}")" || true
    write_status "${status_path}" "error" || true
  fi
  retire_file "${processing_path}"
  retire_file "${cancel_path}"
}

recover_processing_requests

while true; do
  request_path=""
  while IFS= read -r candidate; do
    request_path="${candidate}"
    break
  done < <(find "${REQUEST_DIR}" -maxdepth 1 -type f -name '*.request' -print | sort)

  if [[ -z "${request_path}" ]]; then
    sleep "${POLL_SECONDS}"
    continue
  fi

  process_request "${request_path}" || true
done
