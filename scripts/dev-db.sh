#!/usr/bin/env bash
# Set up a local development database the way production is set up (phase 1 step 3, Q11).
#
# It mirrors the one-time steps of docs/phases/phase-1-step-3.md §14 (and deploy/scaleway.md)
# on a local PostgreSQL 16:
#   1. the two LOGIN roles, neither superuser nor BYPASSRLS: hausbuch_owner (owns the schema;
#      migrations and the CLI) and hausbuch_app (the API; created before the first migration
#      so migration 0001 grants it its privileges);
#   2. GRANT hausbuch_owner TO <admin> WITH SET TRUE; CREATE DATABASE … OWNER hausbuch_owner;
#      then, still inside that window and as the owner (SET ROLE), REVOKE CONNECT … FROM
#      PUBLIC and GRANT CONNECT to the two roles only; REVOKE hausbuch_owner FROM <admin>.
#      (§14 lists the REVOKE CONNECT after the membership is revoked; for a non-superuser
#      admin, as on Scaleway, that is a silent no-op. deploy/scaleway.md has the fixed order.)
#   3. the CONNECT changes once more as hausbuch_owner itself, so a re-run repairs them;
#   4. alembic upgrade head as hausbuch_owner (never as the admin or a superuser).
# Then it prints the two URLs to export.
#
# Safe to re-run: existing roles and an existing database are kept, steps 3 and 4 run again
# (both are idempotent), and nothing is dropped. Role passwords are set only when a role is
# created, or with --reset-passwords: the roles are cluster-wide and shared with the test
# suite and with any other local Hausbuch database, so changing a password silently could break
# another setup.
#
# Usage: scripts/dev-db.sh [--reset-passwords]
#
# Environment (all optional):
#   HAUSBUCH_DEV_DB              database name (default hausbuch_dev). Names starting with
#                                hausbuch_test_ are refused: the test suite creates and drops
#                                databases with that prefix.
#   HAUSBUCH_DEV_HOST            host of the local server (default localhost)
#   HAUSBUCH_DEV_PORT            port (default 5432)
#   HAUSBUCH_DEV_ADMIN_URL       libpq URL of an admin role that may create roles and databases
#                                (default postgresql://postgres:postgres@HOST:PORT/postgres).
#                                Locally that is usually a superuser; on Scaleway it is the
#                                instance's admin, which is not. The steps work for both.
#   HAUSBUCH_DEV_OWNER_PASSWORD  hausbuch_owner's password (default hausbuch_owner_dev)
#   HAUSBUCH_DEV_APP_PASSWORD    hausbuch_app's password (default hausbuch_app_dev)
#
# To remove the database again (the roles stay; the test suite uses them too):
#   psql "$HAUSBUCH_DEV_ADMIN_URL" -c 'DROP DATABASE hausbuch_dev WITH (FORCE)'
set -euo pipefail

OWNER=hausbuch_owner
APP=hausbuch_app

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

reset_passwords=false
for arg in "$@"; do
    case "$arg" in
        --reset-passwords) reset_passwords=true ;;
        -h | --help) usage; exit 0 ;;
        *) echo "dev-db.sh: unknown argument: $arg (see --help)" >&2; exit 2 ;;
    esac
done

die() { echo "dev-db.sh: $*" >&2; exit 1; }
step() { echo "==> $*"; }

DB=${HAUSBUCH_DEV_DB:-hausbuch_dev}
HOST=${HAUSBUCH_DEV_HOST:-localhost}
PORT=${HAUSBUCH_DEV_PORT:-5432}
ADMIN_URL=${HAUSBUCH_DEV_ADMIN_URL:-postgresql://postgres:postgres@$HOST:$PORT/postgres}
OWNER_PASSWORD=${HAUSBUCH_DEV_OWNER_PASSWORD:-hausbuch_owner_dev}
APP_PASSWORD=${HAUSBUCH_DEV_APP_PASSWORD:-hausbuch_app_dev}

[[ $DB =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || die "HAUSBUCH_DEV_DB must be a lower-case identifier"
[[ $DB == hausbuch_test_* ]] && die "HAUSBUCH_DEV_DB must not start with hausbuch_test_ (the tests' throwaway databases)"
[[ $DB == postgres || $DB == template0 || $DB == template1 ]] && die "pick a database name of its own"
for pw in "$OWNER_PASSWORD" "$APP_PASSWORD"; do
    # Kept URL-safe so the printed URLs need no percent-encoding.
    [[ $pw =~ ^[A-Za-z0-9._~-]{8,}$ ]] || die "dev passwords must be 8+ characters of A-Z a-z 0-9 . _ ~ -"
done

command -v psql >/dev/null || die "psql not found (install the PostgreSQL 16 client)"
command -v uv >/dev/null || die "uv not found (https://docs.astral.sh/uv/)"
BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"

# Every statement on its own (CREATE DATABASE cannot run inside a transaction block); stop at
# the first error. Values go in as psql variables, quoted by psql (:'x' literal, :"x" name).
admin() { psql "$ADMIN_URL" -X -q -At -v ON_ERROR_STOP=1 "$@"; }

step "Checking the server and the admin role"
server_version=$(admin -c 'SHOW server_version_num') || die "cannot connect with HAUSBUCH_DEV_ADMIN_URL"
(( server_version >= 160000 )) || die "PostgreSQL 16 or later is needed (GRANT … WITH SET), found $server_version"
admin_role=$(admin -c 'SELECT current_user')
[[ $admin_role == "$OWNER" || $admin_role == "$APP" ]] && die "the admin URL must not connect as $admin_role"

# --- 1. Roles ------------------------------------------------------------------------------
ensure_role() {
    local role=$1 password=$2
    if [[ -z $(admin -v role="$role" <<<"SELECT 1 FROM pg_roles WHERE rolname = :'role';") ]]; then
        step "Creating role $role"
        admin -v role="$role" -v pw="$password" <<'SQL'
CREATE ROLE :"role" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD :'pw';
SQL
    else
        # Check rather than ALTER … NOSUPERUSER: a non-superuser admin (Scaleway's) may not
        # touch those attributes even to set the value they already have.
        local attrs
        attrs=$(admin -v role="$role" <<<"SELECT rolsuper OR rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = :'role';")
        [[ ${attrs%%|*} == f ]] || die "role $role is a superuser or BYPASSRLS; Hausbuch's roles must be neither"
        if [[ ${attrs#*|} == t ]]; then
            step "Role $role exists"
        else
            step "Role $role exists without LOGIN (the test suite creates it that way); adding LOGIN"
            admin -v role="$role" <<<'ALTER ROLE :"role" LOGIN;'
        fi
        if $reset_passwords; then
            step "Resetting $role's password (--reset-passwords)"
            admin -v role="$role" -v pw="$password" <<'SQL'
ALTER ROLE :"role" PASSWORD :'pw';
SQL
        fi
    fi
}
ensure_role "$OWNER" "$OWNER_PASSWORD"
ensure_role "$APP" "$APP_PASSWORD"   # before the first migration, so 0001's grants run

# --- 2. The database, owned by hausbuch_owner ---------------------------------------------
db_owner=$(admin -v db="$DB" <<<"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :'db';")
if [[ -z $db_owner ]]; then
    step "Creating database $DB owned by $OWNER (§14, in deploy/scaleway.md's corrected order)"
    # A non-superuser admin may create a database owned by another role only while it may
    # SET ROLE to it, and only the owner (or a member acting as it) can change the database's
    # privileges: REVOKE CONNECT run by the admin after the membership is gone is a silent
    # no-op ("no privileges could be revoked"). So CONNECT is fixed inside the window, as the
    # owner. The membership is revoked again whether or not the block succeeded.
    admin -v owner="$OWNER" -v admin="$admin_role" <<'SQL'
GRANT :"owner" TO :"admin" WITH SET TRUE;
SQL
    created=true
    admin -v owner="$OWNER" -v app="$APP" -v db="$DB" <<'SQL' || created=false
CREATE DATABASE :"db" OWNER :"owner";
SET ROLE :"owner";
REVOKE CONNECT ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO :"owner", :"app";
RESET ROLE;
SQL
    admin -v owner="$OWNER" -v admin="$admin_role" <<'SQL'
REVOKE :"owner" FROM :"admin";
SQL
    $created || die "creating $DB failed (see the error above)"
elif [[ $db_owner != "$OWNER" ]]; then
    die "database $DB exists but is owned by $db_owner, not $OWNER; pick another HAUSBUCH_DEV_DB"
else
    step "Database $DB exists and is owned by $OWNER"
fi

OWNER_URL="postgresql://$OWNER:$OWNER_PASSWORD@$HOST:$PORT/$DB"
APP_URL="postgresql://$APP:$APP_PASSWORD@$HOST:$PORT/$DB"
for pair in "$OWNER $OWNER_URL" "$APP $APP_URL"; do
    role=${pair%% *} url=${pair#* }
    if ! psql "$url" -X -q -At -c 'SELECT 1' >/dev/null 2>&1; then
        var=HAUSBUCH_DEV_APP_PASSWORD
        [[ $role == "$OWNER" ]] && var=HAUSBUCH_DEV_OWNER_PASSWORD
        die "cannot sign in as $role with the configured password: the role existed before
with another password. Set $var to that password, or re-run with --reset-passwords
(this changes it for every local database that uses $role; the test suite does not mind,
it never signs in as $role)."
    fi
done

# --- 3. CONNECT for the two roles only (again, as the owner: idempotent) -------------------
step "Revoking CONNECT from PUBLIC; granting it to $OWNER and $APP (as $OWNER)"
psql "$OWNER_URL" -X -q -At -v ON_ERROR_STOP=1 -v db="$DB" -v owner="$OWNER" -v app="$APP" <<'SQL'
REVOKE CONNECT ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO :"owner", :"app";
SQL

# --- 4. Migrations, as the owner ----------------------------------------------------------
step "Migrating $DB as $OWNER (alembic upgrade head)"
(cd "$BACKEND" && HAUSBUCH_DATABASE_URL="postgresql+psycopg://${OWNER_URL#postgresql://}" \
    uv run --quiet alembic upgrade head)

# --- Check what we built ------------------------------------------------------------------
step "Checking the result"
check=$(admin -v db="$DB" -v owner="$OWNER" -v app="$APP" <<'SQL'
SELECT string_agg(format('%s super=%s bypassrls=%s login=%s', rolname, rolsuper, rolbypassrls,
                         rolcanlogin), '; ' ORDER BY rolname)
FROM pg_roles WHERE rolname IN (:'owner', :'app');
SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :'db';
SELECT has_database_privilege('public', :'db', 'CONNECT');
SQL
)
echo "$check" | sed 's/^/    /'
[[ $(echo "$check" | sed -n 3p) == f ]] || die "PUBLIC can still connect to $DB"

cat <<EOF

Database $DB is ready. For the API and the CLI (docs/phases/phase-1-step-3.md, "Running it"):

  export HAUSBUCH_DATABASE_URL=postgresql+psycopg://${APP_URL#postgresql://}
  export HAUSBUCH_ADMIN_DATABASE_URL=postgresql+psycopg://${OWNER_URL#postgresql://}

The API connects as $APP and refuses $OWNER; bootstrap, invite and migrations use
HAUSBUCH_ADMIN_DATABASE_URL. Later migrations:
  (cd backend && HAUSBUCH_DATABASE_URL=\$HAUSBUCH_ADMIN_DATABASE_URL uv run alembic upgrade head)
EOF
