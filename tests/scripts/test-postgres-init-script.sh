#!/usr/bin/env bash
# Regression guard for deploy/postgres-init/001-create-cluster-databases.sh.
#
# This script runs both as a PostgreSQL image init hook and as the installer's
# reconcile step, so a non-zero exit aborts the container start or the install.
# PostgreSQL forbids two things the script used to attempt unconditionally:
#   * clearing SUPERUSER on the cluster's bootstrap superuser, and
#   * a CREATEROLE role altering a role it did not create (PostgreSQL 16+).
# When a real PostgreSQL server is available the assertions run against it;
# otherwise the structural checks still hold the shape of the script in place.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INIT_SCRIPT="${ROOT_DIR}/deploy/postgres-init/001-create-cluster-databases.sh"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

[[ -f "${INIT_SCRIPT}" ]] || fail "missing ${INIT_SCRIPT}"
sh -n "${INIT_SCRIPT}" || fail 'init script is not valid POSIX shell'

grep -Fq 'role_is_superuser' "${INIT_SCRIPT}" \
  || fail 'init script does not detect a bootstrap superuser runtime role'
grep -Fq 'finalize_runtime_role' "${INIT_SCRIPT}" \
  || fail 'init script still demotes the runtime role unconditionally'
grep -Fq 'demote_runtime_user' "${INIT_SCRIPT}" \
  && fail 'init script still contains the unconditional demote step'
grep -Eq 'ALTER ROLE "\$\{role\}" PASSWORD' "${INIT_SCRIPT}" \
  || fail 'password reconciliation must not combine attribute clauses with PASSWORD'
# Reconciling the runtime role must not ask for NOSUPERUSER: that is rejected for the
# bootstrap superuser, and on an external server the role cannot restrict itself either.
if sed -n '/^finalize_runtime_role() {/,/^}/p' "${INIT_SCRIPT}" | grep -Fq 'NOSUPERUSER'; then
  fail 'finalize_runtime_role must not request NOSUPERUSER'
fi
if sed -n '/^ensure_admin_role() {/,/^}/p' "${INIT_SCRIPT}" | grep -Eq 'harden_role.*(REPLICATION|BYPASSRLS)'; then
  fail 'ensure_admin_role must not reconcile attributes it cannot hold'
fi

PG_BIN=""
for candidate in /usr/lib/postgresql/*/bin /usr/pgsql-*/bin /usr/local/pgsql/bin; do
  if [[ -x "${candidate}/initdb" && -x "${candidate}/pg_ctl" && -x "${candidate}/psql" ]]; then
    PG_BIN="${candidate}"
  fi
done
if [[ -z "${PG_BIN}" ]]; then
  printf '[postgres-init-script] structural assertions passed; skipped live PostgreSQL checks\n'
  exit 0
fi

WORK="$(mktemp -d)"
PORT="${CHATGPT2API_TEST_POSTGRES_PORT:-55561}"
cleanup() {
  "${PG_BIN}/pg_ctl" -D "${WORK}/data" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "${WORK}"
}
trap cleanup EXIT

export PATH="${PG_BIN}:${PATH}"
export PGHOST="${WORK}/sock" PGPORT="${PORT}"
export POSTGRES_DB=chatgpt2api_app
export CHATGPT2API_IMAGE_QUEUE_DB=chatgpt2api_image_queue
export CHATGPT2API_POSTGRES_ADMIN_USER=chatgpt2api_admin
export CHATGPT2API_POSTGRES_ADMIN_PASSWORD=test-admin-pass

start_server() {
  local bootstrap_user="$1"
  local bootstrap_password="$2"
  rm -rf "${WORK}/data" "${WORK}/sock"
  mkdir -p "${WORK}/data" "${WORK}/sock"
  printf '%s' "${bootstrap_password}" >"${WORK}/pw"
  initdb -D "${WORK}/data" -U "${bootstrap_user}" \
    --auth-local=trust --auth-host=scram-sha-256 --pwfile="${WORK}/pw" >/dev/null \
    || fail 'initdb failed'
  pg_ctl -D "${WORK}/data" \
    -o "-p ${PORT} -k ${WORK}/sock -c listen_addresses=''" -w start >/dev/null \
    || fail 'pg_ctl start failed'
}

query() {
  PGPASSWORD="$2" psql -tAc "$4" -U "$1" -d "$3" 2>/dev/null | tr -d '[:space:]'
}

# Scenario 1: the built-in stack, where POSTGRES_USER is the bootstrap superuser.
start_server chatgpt2api_runtime test-runtime-pass
export POSTGRES_USER=chatgpt2api_runtime POSTGRES_PASSWORD=test-runtime-pass
for attempt in 1 2 3; do
  sh "${INIT_SCRIPT}" >"${WORK}/builtin-${attempt}.log" 2>&1 \
    || fail "built-in PostgreSQL init failed on run ${attempt} (see ${WORK}/builtin-${attempt}.log)"
done
[[ "$(query chatgpt2api_admin test-admin-pass postgres \
  "SELECT count(*) FROM pg_database WHERE datname IN ('chatgpt2api_app','chatgpt2api_image_queue')")" == "2" ]] \
  || fail 'built-in init did not create both databases'
[[ "$(query chatgpt2api_admin test-admin-pass chatgpt2api_app \
  "SELECT role FROM chatgpt2api_database_role WHERE id='default'")" == "app" ]] \
  || fail 'app database role marker is wrong'
[[ "$(query chatgpt2api_admin test-admin-pass chatgpt2api_image_queue \
  "SELECT role FROM chatgpt2api_database_role WHERE id='default'")" == "image_queue" ]] \
  || fail 'image queue database role marker is wrong'
[[ "$(query chatgpt2api_runtime test-runtime-pass chatgpt2api_app "SELECT 1")" == "1" ]] \
  || fail 'runtime role can no longer log in after reconciliation'
grep -Fq 'bootstrap superuser' "${WORK}/builtin-1.log" \
  || fail 'init script did not explain why the runtime role keeps SUPERUSER'

# Rotating the admin password must reconcile instead of failing.
CHATGPT2API_POSTGRES_ADMIN_PASSWORD=test-admin-pass-rotated \
  sh "${INIT_SCRIPT}" >"${WORK}/rotate.log" 2>&1 \
  || fail 'admin password rotation failed'
[[ "$(query chatgpt2api_admin test-admin-pass-rotated postgres "SELECT 1")" == "1" ]] \
  || fail 'rotated admin password does not work'

# A database whose marker disagrees must still abort.
PGPASSWORD=test-admin-pass-rotated psql -q -U chatgpt2api_admin -d chatgpt2api_app \
  -c "UPDATE chatgpt2api_database_role SET role='image_queue'" >/dev/null 2>&1
if CHATGPT2API_POSTGRES_ADMIN_PASSWORD=test-admin-pass-rotated \
  sh "${INIT_SCRIPT}" >"${WORK}/mismatch.log" 2>&1; then
  fail 'a mismatched database role marker did not abort the init script'
fi
grep -Fq 'database role mismatch' "${WORK}/mismatch.log" \
  || fail 'role mismatch abort did not explain itself'

# Scenario 2: an external PostgreSQL, where the runtime role is a plain login role.
start_server pgboot test-boot-pass
psql -q -U pgboot -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE chatgpt2api_runtime LOGIN CREATEDB CREATEROLE PASSWORD 'test-runtime-pass'" \
  >/dev/null || fail 'could not create the external runtime role'
export CHATGPT2API_POSTGRES_ADMIN_PASSWORD=test-admin-pass
for attempt in 1 2 3; do
  sh "${INIT_SCRIPT}" >"${WORK}/external-${attempt}.log" 2>&1 \
    || fail "external PostgreSQL init failed on run ${attempt} (see ${WORK}/external-${attempt}.log)"
done
[[ "$(query chatgpt2api_admin test-admin-pass postgres \
  "SELECT count(*) FROM pg_database WHERE datname IN ('chatgpt2api_app','chatgpt2api_image_queue')")" == "2" ]] \
  || fail 'external init did not create both databases'
[[ "$(query chatgpt2api_runtime test-runtime-pass chatgpt2api_app "SELECT 1")" == "1" ]] \
  || fail 'external runtime role cannot connect to the app database'
if PGPASSWORD=test-runtime-pass psql -q -U chatgpt2api_runtime -d chatgpt2api_app \
  -c "UPDATE chatgpt2api_database_role SET role='app2'" >/dev/null 2>&1; then
  fail 'the runtime role must not be able to rewrite the database role marker'
fi

printf '[postgres-init-script] all assertions passed\n'
