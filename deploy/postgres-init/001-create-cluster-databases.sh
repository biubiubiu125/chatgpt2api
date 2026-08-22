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

bootstrap_psql() {
  PGPASSWORD="${RUNTIME_PASSWORD}" psql -v ON_ERROR_STOP=1 --username "${RUNTIME_USER}" "$@"
}

admin_psql() {
  PGPASSWORD="${ADMIN_PASSWORD}" psql -v ON_ERROR_STOP=1 --username "${ADMIN_USER}" "$@"
}

admin_exists="$(bootstrap_psql --dbname postgres -tAc \
  "SELECT 1 FROM pg_roles WHERE rolname = '${ADMIN_USER}'" | tr -d '[:space:]')"
if [ "${admin_exists}" != "1" ]; then
  bootstrap_psql --dbname postgres -v admin_password="${ADMIN_PASSWORD}" <<SQL
CREATE ROLE "${ADMIN_USER}"
  WITH LOGIN NOSUPERUSER CREATEDB CREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'admin_password';
SQL
fi

if ! admin_psql --dbname postgres -v admin_password="${ADMIN_PASSWORD}" <<SQL
ALTER ROLE "${ADMIN_USER}"
  WITH LOGIN NOSUPERUSER CREATEDB CREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'admin_password';
SQL
then
  bootstrap_psql --dbname postgres -v admin_password="${ADMIN_PASSWORD}" <<SQL
ALTER ROLE "${ADMIN_USER}"
  WITH LOGIN NOSUPERUSER CREATEDB CREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'admin_password';
SQL
fi

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

demote_runtime_user() {
  if ! bootstrap_psql --dbname postgres -v runtime_password="${RUNTIME_PASSWORD}" <<SQL
ALTER ROLE "${RUNTIME_USER}"
  WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'runtime_password';
SQL
  then
    admin_psql --dbname postgres -v runtime_password="${RUNTIME_PASSWORD}" <<SQL
ALTER ROLE "${RUNTIME_USER}"
  WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'runtime_password';
SQL
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

ensure_database "${APP_DB}"
ensure_database "${QUEUE_DB}"
grant_runtime_access "${APP_DB}"
grant_runtime_access "${QUEUE_DB}"
ensure_role_marker "${APP_DB}" app
ensure_role_marker "${QUEUE_DB}" image_queue
grant_role_marker_access "${APP_DB}"
grant_role_marker_access "${QUEUE_DB}"
demote_runtime_user
