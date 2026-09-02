#!/usr/bin/env bash
# Regression guard for deploy/cluster-join-helper.sh.
#
# The helper runs the installer's `create-worker` / `rotate-worker` on the host on
# behalf of the containerized management API. Cancelling a request has to stop that
# installer *and everything it spawned*: `create-worker` adds a WireGuard peer, issues
# a one-time join token and appends to workers.tsv, so work that keeps running after
# the API answered would leave a half-registered Worker nobody consumes.
#
# A shell blocked on a foreground command dies on SIGTERM without forwarding it when no
# TERM trap is installed, which orphans that command. These assertions drive the real
# helper with stub installers covering both shapes.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HELPER="${ROOT_DIR}/deploy/cluster-join-helper.sh"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

[[ -f "${HELPER}" ]] || fail "missing ${HELPER}"
bash -n "${HELPER}" || fail 'helper is not valid bash'

WORK=""
HELPER_PID=""
STUB_CHILD=""
# Every step is best effort: a `kill` or `pkill` that matches nothing must not turn a
# passing run into a failure, and under `set -e` a failing command in an EXIT trap ends
# the shell with that status before `return 0` is ever reached.
cleanup() {
  if [[ -n "${HELPER_PID}" ]]; then
    kill "${HELPER_PID}" 2>/dev/null || true
  fi
  if [[ -n "${STUB_CHILD}" ]]; then
    pkill -f "${STUB_CHILD}" 2>/dev/null || true
  fi
  if [[ -n "${WORK}" ]]; then
    rm -rf "${WORK}" || true
  fi
  return 0
}
trap cleanup EXIT

# The stub's long-running work lives in a uniquely named script, so a path match finds
# exactly the processes this test started and nothing else.
running_stubs() {
  local count=""
  count="$(pgrep -f "$1" 2>/dev/null | wc -l)"
  printf '%s' "${count:-0}"
}

start_helper() {
  local install_dir="$1"
  local grace="$2"
  mkdir -p "${install_dir}/data/cluster-join-requests"
  CHATGPT2API_CLUSTER_JOIN_HELPER_ROLLBACK_SECONDS="${grace}" \
    nohup bash "${install_dir}/deploy/cluster-join-helper.sh" "${install_dir}" >/dev/null 2>&1 &
  HELPER_PID="$!"
}

submit_request() {
  printf 'worker_no=%s\noperation=create\n' "$1" >"${WORK}/data/cluster-join-requests/req.request"
}

wait_for_stub() {
  local deadline=$(( $(date +%s) + 25 ))
  while (( $(date +%s) < deadline )); do
    if (( "$(running_stubs "$1")" > 0 )); then
      return 0
    fi
    sleep 1
  done
  fail "stub installer never started: $1"
}

wait_for_no_stub() {
  local deadline=$(( $(date +%s) + $2 ))
  while (( $(date +%s) < deadline )); do
    if (( "$(running_stubs "$1")" == 0 )); then
      return 0
    fi
    sleep 1
  done
  return 1
}

# An installer that ignores SIGTERM leaves its child running once the parent shell
# exits. The helper must notice the group is still busy and force it down.
WORK="$(mktemp -d)"
STUB_CHILD="${WORK}/stub-work-orphan.sh"
mkdir -p "${WORK}/deploy" "${WORK}/join" "${WORK}/data"
cp "${HELPER}" "${WORK}/deploy/"
printf '#!/usr/bin/env bash\nsleep 600\n' >"${STUB_CHILD}"
chmod +x "${STUB_CHILD}"
cat >"${WORK}/deploy/install.sh" <<EOF
#!/usr/bin/env bash
bash "${STUB_CHILD}"
echo unreachable
EOF
chmod +x "${WORK}/deploy/install.sh"
start_helper "${WORK}" 3
submit_request 9
wait_for_stub "${STUB_CHILD}"
: >"${WORK}/data/cluster-join-requests/req.cancel"
wait_for_no_stub "${STUB_CHILD}" 45 \
  || fail 'cancelling a request left the installer descendants running'
grep -Fq 'forcing termination' "${WORK}/data/cluster-join-helper.log" \
  || fail 'helper did not report escalating to a forced termination'
kill "${HELPER_PID}" 2>/dev/null || true
HELPER_PID=""
pkill -f "${STUB_CHILD}" 2>/dev/null || true
rm -rf "${WORK}"

# An installer that does trap SIGTERM must be given its grace period to roll back:
# its cleanup runs `wg`, `psql` and `docker compose`, and killing the group up front
# would strand a half-registered Worker.
WORK="$(mktemp -d)"
STUB_CHILD="${WORK}/stub-work-rollback.sh"
mkdir -p "${WORK}/deploy" "${WORK}/join" "${WORK}/data"
cp "${HELPER}" "${WORK}/deploy/"
printf '#!/usr/bin/env bash\nsleep 600\n' >"${STUB_CHILD}"
chmod +x "${STUB_CHILD}"
cat >"${WORK}/deploy/install.sh" <<EOF
#!/usr/bin/env bash
work_pid=""
rollback() {
  printf 'started\n' >>"${WORK}/rollback.log"
  # A real installer's cleanup tears down what it started before exiting.
  [ -n "\${work_pid}" ] && kill "\${work_pid}" 2>/dev/null
  sleep 3
  printf 'finished\n' >>"${WORK}/rollback.log"
  exit 1
}
trap rollback TERM
bash "${STUB_CHILD}" &
work_pid="\$!"
wait
EOF
chmod +x "${WORK}/deploy/install.sh"
start_helper "${WORK}" 25
submit_request 9
wait_for_stub "${STUB_CHILD}"
: >"${WORK}/data/cluster-join-requests/req.cancel"
wait_for_no_stub "${STUB_CHILD}" 45 \
  || fail 'a rolled-back installer left its descendants running'
grep -Fqx 'started' "${WORK}/rollback.log" \
  || fail 'the installer rollback never started'
# The rollback outlives the work it tears down, so give it a moment to finish. If the
# helper had killed the group up front it would never reach its last line at all.
rollback_deadline=$(( $(date +%s) + 20 ))
while (( $(date +%s) < rollback_deadline )); do
  grep -Fqx 'finished' "${WORK}/rollback.log" && break
  sleep 1
done
grep -Fqx 'finished' "${WORK}/rollback.log" \
  || fail 'the installer rollback was cut short instead of being given its grace period'
if grep -Fq 'forcing termination' "${WORK}/data/cluster-join-helper.log"; then
  fail 'a clean rollback must not be escalated to a forced termination'
fi

printf '[cluster-join-helper] all assertions passed\n'
