# syntax=docker/dockerfile:1
#
# Hausbuch API image (docs/phases/phase-1-step-3.md §3.7, §14). Build from the repo root:
#
#   docker build -f deploy/api.Dockerfile -t hausbuch-api .
#
# One image, two uses (deploy/scaleway.md):
#   - the `hausbuch-api` Serverless Container: the default CMD, uvicorn on port 8080 with the
#     job worker inside the process (§3.7), min-scale 1;
#   - one-off commands, run with the command overridden and the owner URL
#     (HAUSBUCH_ADMIN_DATABASE_URL) injected only there, never into the API container:
#       hausbuch-migrate                                    alembic upgrade head, as the owner
#       hausbuch bootstrap --workspace … --email … --name …  first workspace and user
#       hausbuch invite --email … [--revoke-passkeys] [--revoke-sessions]
#
# No secrets are baked in: every HAUSBUCH_* setting comes from the environment at run time.

# The base image is an argument only so a build behind a TLS-intercepting proxy can swap in a
# copy that trusts the proxy's CA. Production builds use the default.
ARG PYTHON_IMAGE=python:3.12-slim-trixie

# --- build: resolve the locked dependencies into /app/.venv ------------------------------
FROM ${PYTHON_IMAGE} AS build

# uv from PyPI, pinned; it stays in this stage only.
ARG UV_VERSION=0.11.32
RUN pip install --no-cache-dir --disable-pip-version-check "uv==${UV_VERSION}"

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
# Dependencies first, so source changes do not re-resolve them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
# Then the project itself, as a regular (non-editable) install: the runtime image needs no
# source tree.
COPY backend/src ./src
RUN uv sync --locked --no-dev --no-editable

# --- runtime -------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system --gid 10001 hausbuch \
 && useradd --system --uid 10001 --gid hausbuch --no-create-home \
            --home-dir /nonexistent --shell /usr/sbin/nologin hausbuch

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Migrations for the `alembic upgrade head` Job (alembic.ini finds them via %(here)s).
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY --chmod=0755 deploy/hausbuch-migrate.sh /usr/local/bin/hausbuch-migrate

# Read-only for the app user: it owns nothing it could rewrite.
USER 10001:10001

EXPOSE 8080

# Docker's HEALTHCHECK is for local runs only: Scaleway ignores it and uses the container's
# own HTTP probe, configured on /healthz in deploy/scaleway.md.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]

# Exec form, so uvicorn is PID 1 and receives the platform's SIGTERM itself. The graceful
# shutdown bound (25 s) sits above HAUSBUCH_SHUTDOWN_GRACE_S (20 s) and below the platform's
# kill timeout (Q15, measured at first deploy). No access log: the app logs one redacted JSON
# line per request (§3.6).
CMD ["uvicorn", "hausbuch.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--timeout-graceful-shutdown", "25"]
