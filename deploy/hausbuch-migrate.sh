#!/bin/sh
# `alembic upgrade head` as the schema owner (docs/phases/phase-1-step-3.md §14).
#
# Installed in the API image as /usr/local/bin/hausbuch-migrate and run as a one-off command
# (a Serverless Job, or `docker run`), never by the API container. It reads the owner URL
# from HAUSBUCH_ADMIN_DATABASE_URL, the same variable `hausbuch bootstrap` and
# `hausbuch invite` read, and hands it to Alembic as HAUSBUCH_DATABASE_URL for this process
# only. So the one-off commands share one secret, and no environment ever carries the owner
# URL under the API's variable name.
set -eu

if [ -z "${HAUSBUCH_ADMIN_DATABASE_URL:-}" ]; then
    echo "hausbuch-migrate: HAUSBUCH_ADMIN_DATABASE_URL is not set (the hausbuch_owner URL)" >&2
    exit 1
fi

HAUSBUCH_DATABASE_URL="$HAUSBUCH_ADMIN_DATABASE_URL" exec alembic -c /app/alembic.ini upgrade head "$@"
