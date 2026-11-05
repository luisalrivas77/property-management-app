# syntax=docker/dockerfile:1
#
# Hausbuch web image: the Next.js standalone server (docs/phases/phase-1-step-3.md §9.3, §14).
# Build from the repo root, naming the API container's URL:
#
#   docker build -f deploy/web.Dockerfile \
#     --build-arg HAUSBUCH_API_URL=https://<api-container-host> -t hausbuch-web .
#
# HAUSBUCH_API_URL is a BUILD argument, not a runtime setting (§10.6): next.config.ts turns
# it into the /api/* rewrite when `next build` runs, and the standalone server reads the
# rewrite from the build's routes manifest. Setting it on the running container changes
# nothing, so a new API URL means a new web image. The browser never sees this URL; it calls
# /api/* on the web origin and this server proxies.

# The base image is an argument only so a build behind a TLS-intercepting proxy can swap in a
# copy that trusts the proxy's CA. Production builds use the default.
ARG NODE_IMAGE=node:22-trixie-slim

# --- deps: the locked node_modules ----------------------------------------------------------
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# --- build ---------------------------------------------------------------------------------
FROM deps AS build
ARG HAUSBUCH_API_URL
RUN if [ -z "$HAUSBUCH_API_URL" ]; then \
      echo "HAUSBUCH_API_URL build argument is required (the API container's URL)" >&2; exit 1; \
    fi
ENV HAUSBUCH_API_URL=${HAUSBUCH_API_URL} \
    NEXT_TELEMETRY_DISABLED=1
COPY frontend/ ./
# public/ is optional in the source tree; the runtime stage copies it either way.
RUN mkdir -p public && npm run build

# --- runtime: server.js plus the static assets, nothing else --------------------------------
FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=8080
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

# The image's built-in unprivileged user; it owns nothing in /app.
USER node

EXPOSE 8080

# Exec form: node is PID 1 and gets the platform's SIGTERM.
CMD ["node", "server.js"]
