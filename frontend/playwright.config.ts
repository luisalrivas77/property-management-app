// End-to-end tests (docs/phases/phase-1-step-3.md §10.3): Chromium against the real stack.
//
//   scripts/e2e-db.sh up          # once: a fresh, migrated hausbuch_e2e database
//   cd frontend && npx playwright test
//   scripts/e2e-db.sh down        # drop it again
//
// Playwright starts two servers: the API (uvicorn, as the app role, against hausbuch_e2e)
// and the web app as production runs it (`next build`, then the standalone server.js, whose
// /api rewrite proxies to that API). The browser only talks to the web origin, as in
// production (§9.3). Each test provisions its own workspace with `hausbuch bootstrap`
// (e2e/support.ts), so the tests are independent and may run in parallel.
//
// Reports, traces and the API's blob store go outside the source tree (default: the system
// temp directory; HAUSBUCH_E2E_ARTIFACTS overrides it), so `eslint .` never sees them.
//
// Environment (optional): HAUSBUCH_E2E_WEB_PORT (3100), HAUSBUCH_E2E_API_PORT (8100),
// HAUSBUCH_E2E_SKIP_BUILD=1 to reuse the last `next build` of this configuration, and
// e2e-db.sh's variables (database name, host, passwords).
import { execFileSync } from "node:child_process";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { artifactsDir, blobRoot } from "./e2e/artifacts";

const frontend = import.meta.dirname;
const repo = path.resolve(frontend, "..");
const webPort = Number(process.env.HAUSBUCH_E2E_WEB_PORT ?? 3100);
const apiPort = Number(process.env.HAUSBUCH_E2E_API_PORT ?? 8100);
// localhost, not 127.0.0.1: WebAuthn needs a domain as RP id, and "localhost" is a secure
// context over plain http.
const origin = `http://localhost:${webPort}`;
const apiUrl = `http://127.0.0.1:${apiPort}`;

// The database URLs come from the script that creates the database, so there is one source.
const databaseEnv = Object.fromEntries(
  execFileSync(path.join(repo, "scripts", "e2e-db.sh"), ["env"], { encoding: "utf8" })
    .trim()
    .split("\n")
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
);

const build = process.env.HAUSBUCH_E2E_SKIP_BUILD === "1" ? "" : "npm run build && ";

export default defineConfig({
  testDir: "./e2e",
  outputDir: path.join(artifactsDir, "results"),
  globalTeardown: "./e2e/global-teardown.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0, // a flaky e2e test is a bug to fix, not to retry
  workers: process.env.CI ? 2 : 3,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: path.join(artifactsDir, "report"), open: "never" }],
  ],
  use: {
    baseURL: origin,
    locale: "de-DE",
    timezoneId: "Europe/Berlin",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      name: "api",
      cwd: path.join(repo, "backend"),
      command: `uv run --quiet uvicorn hausbuch.api.main:app --host 127.0.0.1 --port ${apiPort} --no-access-log`,
      url: `${apiUrl}/healthz`,
      env: {
        HAUSBUCH_DATABASE_URL: databaseEnv.HAUSBUCH_DATABASE_URL ?? "",
        // Test-only keys: the e2e database is thrown away after the run.
        HAUSBUCH_IBAN_HMAC_KEY: "e2e0".repeat(16),
        HAUSBUCH_SESSION_KEY: "e2e1".repeat(16),
        HAUSBUCH_RP_ID: "localhost",
        HAUSBUCH_ORIGIN: origin,
        HAUSBUCH_COOKIE_SECURE: "false",
        HAUSBUCH_BLOB_STORE: "local",
        HAUSBUCH_BLOB_ROOT: blobRoot,
        HAUSBUCH_RUN_WORKER: "false",
      },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: "ignore", // one JSON line per request; DEBUG=pw:webserver shows them
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
    },
    {
      name: "web",
      cwd: frontend,
      // The standalone server is what the web image runs (deploy/web.Dockerfile); it needs the
      // static assets next to it, as the image copies them.
      command:
        `${build}rm -rf .next/standalone/.next/static && cp -r .next/static .next/standalone/.next/static && ` +
        "exec node .next/standalone/server.js",
      url: `${origin}/login`,
      env: {
        // Fixed into the /api rewrite at build time (next.config.ts).
        HAUSBUCH_API_URL: apiUrl,
        NEXT_TELEMETRY_DISABLED: "1",
        PORT: String(webPort),
        HOSTNAME: "0.0.0.0",
      },
      reuseExistingServer: !process.env.CI,
      timeout: 300_000, // includes `next build`
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
    },
  ],
});
