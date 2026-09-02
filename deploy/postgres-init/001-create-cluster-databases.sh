#!/usr/bin/env sh
set -eu

RUNTIME_USER="${POSTGRES_USER:-chatgpt2api_runtime}"
RUNTIME_PASSWORD="${POSTGRES_PASSWORD:-}"
ADMIN_USER="${CHATGPT2API_POSTGRES_ADMIN_USER:-chatgpt2api_admin}"
ADMIN_PASSWORD="${CHATGPT2API_POSTGRES_ADMIN_PASSWORD:-}"
APP_DB="${POSTGRES_DB:-chatgpt2api_app}"
QUEUE_DB="${CHATGPT2API_IMAGE_QUEUE_DB:-chatgpt2api_image_queue}"

validate_identifier() {
  value="$1"
  case "${value}" in
    ""|*[!A-Za-z0-9_]*)
      echo "invalid PostgreSQL identifier: ${value}" >&2
      exit 1
      ;;
  esac
}

validate_identifier "${RUNTIME_USER}"
validate_identifier "${ADMIN_USER}"
validate_identifier "${APP_DB}"
validate_identifier "${QUEUE_DB}"

if [ "${RUNTIME_USER}" = "${ADMIN_USER}" ]; then
  echo "POSTGRES_USER and CHATGPT2API_POSTGRES_ADMIN_USER must be different" >&2
  exit 1
fi
if [ -z "${RUNTIME_PASSWORD}" ]; then
  echo "POSTGRES_PASSWORD is required" >&2
  exit 1
fi
if [ -z "${ADMIN_PASSWORD}" ]; then
  echo "CHATGPT2API_POSTGRES_ADMIN_PASSWORD is required" >&2
  exit 1
fi

warn() {
  echo "$*" >&2
}

bootstrap_psql() {
  PGPASSWORD="${RUNTIME_PASSWORD}" psql -v ON_ERROR_STOP=1 --username "${RUNTIME_USER}" "$@"
}

admin_psql() {
  PGPASSWORD="${ADMIN_PASSWORD}" psql -v ON_ERROR_STOP=1 --username "${ADMIN_USER}" "$@"
}

# Passwords are reconciled with a bare `ALTER ROLE ... PASSWORD`, never with
# `WITH LOGIN PASSWORD`: PostgreSQL 16+ treats any attribute clause as a role
# alteration, which a role may not perform on itself. A plain password change is
# always allowed on your own role, and on roles you created.
# The value travels through a psql variable on stdin because `-c` sends its
# argument verbatim and never interpolates `:'name'`.
set_role_password() {
  role="$1"
  password="$2"
  if bootstrap_psql --dbname postgres -v role_password="${password}" >/dev/null 2>&1 <<SQL
ALTER ROLE "${role}" PASSWORD :'role_password';
SQL
  then
    return 0
  fi
  admin_psql --dbname postgres -v role_password="${password}" >/dev/null 2>&1 <<SQL
ALTER ROLE "${role}" PASSWORD :'role_password';
SQL
}

# Attribute hardening is best effort. PostgreSQL refuses to clear SUPERUSER on the
# bootstrap superuser, and from 16 onward a CREATEROLE role may only alter roles it
# created -- so no role can restrict itself.
harden_role() {
  role="$1"
  attributes="$2"
  if bootstrap_psql --dbname postgres \
    -c "ALTER ROLE \"${role}\" WITH ${attributes}" >/dev/null 2>&1; then
    return 0
  fi
  admin_psql --dbname postgres \
    -c "ALTER ROLE \"${role}\" WITH ${attributes}" >/dev/null 2>&1
}

role_is_superuser() {
  role="$1"
  superuser=""
  superuser="$(bootstrap_psql --dbname postgres -tAc \
    "SELECT rolsuper FROM pg_roles WHERE rolname = '${role}'" 2>/dev/null | tr -d '[:space:]')" \
    || superuser=""
  [ "${superuser}" = "t" ]
}

ensure_admin_role() {
  admin_exists="$(bootstrap_psql --dbname postgres -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname = '${ADMIN_USER}'" | tr -d '[:space:]')"
  if [ "${admin_exists}" != "1" ]; then
    bootstrap_psql --dbname postgres -v admin_password="${ADMIN_PASSWORD}" <<SQL
CREATE ROLE "${ADMIN_USER}"
  WITH LOGIN NOSUPERUSER CREATEDB CREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'admin_password';
SQL
    return 0
  fi
  if ! set_role_password "${ADMIN_USER}" "${ADMIN_PASSWORD}"; then
    echo "unable to reconcile the ${ADMIN_USER} password" >&2
    exit 1
  fi
  # REPLICATION and BYPASSRLS are intentionally left alone: changing either one
  # requires holding that same attribute, which neither role does. They are set
  # correctly at CREATE time and never granted afterwards.
  if ! harden_role "${ADMIN_USER}" "LOGIN CREATEDB CREATEROLE"; then
    warn "warning: unable to reconcile ${ADMIN_USER} attributes; keeping the existing ones"
  fi
}

finalize_runtime_role() {
  if ! set_role_password "${RUNTIME_USER}" "${RUNTIME_PASSWORD}"; then
    echo "unable to reconcile the ${RUNTIME_USER} password" >&2
    exit 1
  fi
  if role_is_superuser "${RUNTIME_USER}"; then
    warn "notice: ${RUNTIME_USER} is this cluster's bootstrap superuser and cannot be demoted;"
    warn "        keep PostgreSQL unexposed, or provision a separate bootstrap superuser."
    return 0
  fi
  # A CREATEROLE role cannot restrict itself on PostgreSQL 16+, so this only
  # succeeds when some other role created the runtime role and still holds ADMIN
  # OPTION on it. Leaving CREATEDB/CREATEROLE in place is acceptable: the runtime
  # role has no rights on the role marker tables and owns neither database.
  if ! harden_role "${RUNTIME_USER}" "LOGIN NOCREATEDB NOCREATEROLE"; then
    warn "notice: ${RUNTIME_USER} keeps its current role attributes; PostgreSQL only lets"
    warn "        the role that created it restrict them."
  fi
}

ensure_database() {
  database="$1"
  if ! admin_psql --dbname postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '${database}'" | grep -qx 1; then
    if ! admin_psql --dbname postgres -c "CREATE DATABASE \"${database}\" OWNER \"${ADMIN_USER}\""; then
      bootstrap_psql --dbname postgres -c "CREATE DATABASE \"${database}\" OWNER \"${ADMIN_USER}\""
    fi
  else
    if ! admin_psql --dbname postgres -c "ALTER DATABASE \"${database}\" OWNER TO \"${ADMIN_USER}\""; then
      bootstrap_psql --dbname postgres -c "ALTER DATABASE \"${database}\" OWNER TO \"${ADMIN_USER}\""
    fi
  fi
}

grant_runtime_access() {
  database="$1"
  admin_psql --dbname postgres <<SQL
REVOKE ALL ON DATABASE "${database}" FROM PUBLIC;
GRANT CONNECT ON DATABASE "${database}" TO "${RUNTIME_USER}";
SQL
  admin_psql --dbname "${database}" <<SQL
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO "${RUNTIME_USER}";
ALTER DEFAULT PRIVILEGES FOR ROLE "${ADMIN_USER}" IN SCHEMA public
  GRANT ALL PRIVILEGES ON TABLES TO "${RUNTIME_USER}";
ALTER DEFAULT PRIVILEGES FOR ROLE "${ADMIN_USER}" IN SCHEMA public
  GRANT ALL PRIVILEGES ON SEQUENCES TO "${RUNTIME_USER}";
SQL
}

grant_role_marker_access() {
  database="$1"
  admin_psql --dbname "${database}" <<SQL
REVOKE ALL PRIVILEGES ON chatgpt2api_database_role FROM "${RUNTIME_USER}";
GRANT SELECT ON chatgpt2api_database_role TO "${RUNTIME_USER}";
SQL
}

ensure_role_marker() {
  database="$1"
  expected_role="$2"
  admin_psql --dbname "${database}" <<'SQL'
CREATE TABLE IF NOT EXISTS chatgpt2api_database_role (
  id text PRIMARY KEY,
  role text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
SQL
  actual_role="$(admin_psql --dbname "${database}" -tAc \
    "SELECT role FROM chatgpt2api_database_role WHERE id = 'default'" | tr -d '[:space:]')"
  if [ -n "${actual_role}" ] && [ "${actual_role}" != "${expected_role}" ]; then
    echo "database role mismatch for ${database}: expected ${expected_role}, got ${actual_role}" >&2
    exit 1
  fi
  if [ -z "${actual_role}" ]; then
    admin_psql --dbname "${database}" -c \
      "INSERT INTO chatgpt2api_database_role(id, role) VALUES ('default', '${expected_role}')"
  fi
}

ensure_admin_role
ensure_database "${APP_DB}"
ensure_database "${QUEUE_DB}"
grant_runtime_access "${APP_DB}"
grant_runtime_access "${QUEUE_DB}"
ensure_role_marker "${APP_DB}" app
ensure_role_marker "${QUEUE_DB}" image_queue
grant_role_marker_access "${APP_DB}"
grant_role_marker_access "${QUEUE_DB}"
finalize_runtime_role
