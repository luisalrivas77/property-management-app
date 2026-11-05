#!/usr/bin/env bash
# A throwaway database for the Playwright end-to-end tests (phase 1 step 3, §10.3).
#
# It is set up exactly like the dev database, by scripts/dev-db.sh (the two non-superuser
# roles, the database owned by hausbuch_owner, CONNECT for the two roles only, migrations as
# the owner), only under its own name, and dropped again afterwards. Each test then
# provisions its own workspace with `hausbuch bootstrap` (as the owner, like production) and
# gets the one-time enrollment link, so the tests never share data.
#
# Usage: scripts/e2e-db.sh <command>
#
#   up [--reset-passwords]   drop the e2e database if it exists, create it afresh with
#                            dev-db.sh and migrate it; --reset-passwords is passed on (see
#                            dev-db.sh: the roles are cluster-wide, so a local cluster whose
#                            roles have other passwords needs it once)
#   bootstrap [--email E] [--name N] [--workspace W] [--origin URL]
#                            provision a new workspace and its first user; prints only the
#                            enrollment URL (defaults: a unique e2e-…@example.org address,
#                            "E2E", "E2E Haushalt", HAUSBUCH_ORIGIN or http://localhost:3100)
#   env                      print HAUSBUCH_DATABASE_URL (the API's app role) and
#                            HAUSBUCH_ADMIN_DATABASE_URL (the owner) as KEY=value lines
#   down                     drop the e2e database (no error if it is gone)
#
# Every command is safe to repeat. Only the e2e database is ever created or dropped: its name
# must be hausbuch_e2e or start with hausbuch_e2e_, and a database of that name is dropped
# only if hausbuch_owner owns it. The roles are kept (the dev database and the test suite use
# them too).
#
# Environment (all optional): HAUSBUCH_E2E_DB (default hausbuch_e2e), and dev-db.sh's
# HAUSBUCH_DEV_HOST, HAUSBUCH_DEV_PORT, HAUSBUCH_DEV_ADMIN_URL, HAUSBUCH_DEV_OWNER_PASSWORD,
# HAUSBUCH_DEV_APP_PASSWORD with the same defaults.
set -euo pipefail

OWNER=hausbuch_owner

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
die() { echo "e2e-db.sh: $*" >&2; exit 1; }
step() { echo "==> $*" >&2; }

DB=${HAUSBUCH_E2E_DB:-hausbuch_e2e}
HOST=${HAUSBUCH_DEV_HOST:-localhost}
PORT=${HAUSBUCH_DEV_PORT:-5432}
ADMIN_URL=${HAUSBUCH_DEV_ADMIN_URL:-postgresql://postgres:postgres@$HOST:$PORT/postgres}
OWNER_PASSWORD=${HAUSBUCH_DEV_OWNER_PASSWORD:-hausbuch_owner_dev}
APP_PASSWORD=${HAUSBUCH_DEV_APP_PASSWORD:-hausbuch_app_dev}

[[ $DB =~ ^hausbuch_e2e(_[a-z0-9_]{1,50})?$ ]] \
    || die "HAUSBUCH_E2E_DB must be hausbuch_e2e or hausbuch_e2e_<lower-case suffix>"

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$SCRIPTS/../backend" && pwd)"
APP_URL="postgresql+psycopg://hausbuch_app:$APP_PASSWORD@$HOST:$PORT/$DB"
OWNER_URL="postgresql+psycopg://$OWNER:$OWNER_PASSWORD@$HOST:$PORT/$DB"

admin() { psql "$ADMIN_URL" -X -q -At -v ON_ERROR_STOP=1 "$@"; }

drop_database() {
    command -v psql >/dev/null || die "psql not found (install the PostgreSQL 16 client)"
    local owner
    owner=$(admin -v db="$DB" <<<"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :'db';") \
        || die "cannot connect with HAUSBUCH_DEV_ADMIN_URL"
    if [[ -z $owner ]]; then
        step "Database $DB does not exist"
    elif [[ $owner != "$OWNER" ]]; then
        die "database $DB is owned by $owner, not $OWNER; not dropping it"
    else
        step "Dropping database $DB"
        admin -v db="$DB" <<<'DROP DATABASE :"db" WITH (FORCE);'
    fi
}

cmd_up() {
    local passthrough=()
    for arg in "$@"; do
        case "$arg" in
            --reset-passwords) passthrough+=("$arg") ;;
            *) die "up: unknown argument: $arg" ;;
        esac
    done
    drop_database
    # dev-db.sh prints its progress and the URLs on stdout; keep this script's stdout for
    # what callers parse.
    HAUSBUCH_DEV_DB="$DB" "$SCRIPTS/dev-db.sh" "${passthrough[@]}" >&2
    step "Database $DB is ready for the e2e tests"
}

cmd_bootstrap() {
    local email="" name="E2E" workspace="E2E Haushalt" origin=${HAUSBUCH_ORIGIN:-http://localhost:3100}
    while (($#)); do
        case "$1" in
            --email) email=${2:?--email needs a value}; shift 2 ;;
            --name) name=${2:?--name needs a value}; shift 2 ;;
            --workspace) workspace=${2:?--workspace needs a value}; shift 2 ;;
            --origin) origin=${2:?--origin needs a value}; shift 2 ;;
            *) die "bootstrap: unknown argument: $1" ;;
        esac
    done
    # app_user.email is unique across workspaces, so every call needs its own address.
    [[ -n $email ]] || email="e2e-$(date +%s%N)-$RANDOM@example.org"
    local output url
    # --another-workspace: every test provisions its own workspace in the one e2e database.
    output=$(cd "$BACKEND" && HAUSBUCH_ADMIN_DATABASE_URL="$OWNER_URL" uv run --quiet hausbuch bootstrap \
        --another-workspace --workspace "$workspace" --email "$email" --name "$name" --origin "$origin") \
        || die "hausbuch bootstrap failed (see above; has scripts/e2e-db.sh up run?)"
    url=$(sed -n 's|^\(https\{0,1\}://[^[:space:]]*/enroll#token=[A-Za-z0-9_-]*\).*|\1|p' <<<"$output")
    [[ -n $url ]] || die "hausbuch bootstrap printed no enrollment URL"
    echo "$url"
}

cmd_env() {
    echo "HAUSBUCH_DATABASE_URL=$APP_URL"
    echo "HAUSBUCH_ADMIN_DATABASE_URL=$OWNER_URL"
}

command=${1:-}
[[ -n $command ]] || { usage >&2; exit 2; }
shift
case "$command" in
    up) cmd_up "$@" ;;
    bootstrap) cmd_bootstrap "$@" ;;
    env) (($# == 0)) || die "env takes no arguments"; cmd_env ;;
    down) (($# == 0)) || die "down takes no arguments"; drop_database ;;
    -h | --help | help) usage ;;
    *) die "unknown command: $command (see --help)" ;;
esac
