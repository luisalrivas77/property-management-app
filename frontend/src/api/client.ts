// The one API client (docs/phases/phase-1-step-3.md §9.3): same origin through the Next
// rewrite, cookies only, typed on paths by openapi-fetch.
//
// - Every request carries a fresh X-Request-Id (the API's correlation id, §3.2).
// - Every non-2xx response is thrown as an ApiError (the parsed ErrorResponse), so a call
//   that returns has succeeded; use `ok()` to get its data without the error branch.
// - A 401 also calls the handler registered by the AuthGate, which routes to /login (§6.4).
// - A 503 `shutting_down` (a deploy in progress, §3.7) is retried once after Retry-After.
// - Two identical GETs in flight at once share one request (fetchShared).
// - Every request carries Accept-Language, the language chosen on this device (de or en,
//   phase 10 D284), so the server can answer in it once its English templates exist (step 4c).
import createClient, { type Middleware } from "openapi-fetch";

import { getLocale } from "@/i18n/locale";

import { ApiError, readErrorBody } from "./errors";
import type { paths } from "./types"; // generated from openapi.json: `npm run gen:types`

const MAX_RETRY_AFTER_MS = 10_000;
const DEFAULT_RETRY_AFTER_MS = 2_000;

let onUnauthorized: (() => void) | null = null;

/** Called on every 401 while set; the AuthGate sets it (and clears it on unmount). */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

function retryAfterMs(response: Response): number {
  const seconds = Number(response.headers.get("Retry-After"));
  if (!Number.isFinite(seconds) || seconds <= 0) return DEFAULT_RETRY_AFTER_MS;
  return Math.min(seconds * 1000, MAX_RETRY_AFTER_MS);
}

async function isShuttingDown(response: Response): Promise<boolean> {
  if (response.status !== 503) return false;
  const body = await readErrorBody(response.clone());
  return body?.error === "shutting_down";
}

/** fetch with one retry on 503 shutting_down. The API refuses those requests before running
 * anything (§3.7), so repeating a POST is safe. The request is cloned before it is sent,
 * because sending consumes its body. */
async function fetchWithDeployRetry(request: Request): Promise<Response> {
  const spare = request.clone();
  const response = await fetch(request);
  if (!(await isShuttingDown(response))) return response;
  await new Promise((resolve) => setTimeout(resolve, retryAfterMs(response)));
  return fetch(spare);
}

const requestId: Middleware = {
  onRequest({ request }) {
    request.headers.set("X-Request-Id", crypto.randomUUID());
    request.headers.set("Accept-Language", getLocale());
    return request;
  },
};

const throwErrors: Middleware = {
  async onResponse({ request, response }) {
    if (response.ok) return undefined;
    const body = await readErrorBody(response.clone());
    if (response.status === 401) onUnauthorized?.();
    throw new ApiError(
      response.status,
      body,
      response.headers.get("X-Request-Id") ?? request.headers.get("X-Request-Id"),
    );
  },
};

/** GETs in flight, by URL and language. A page whose parts ask for the same read at once
 * (Übersicht's tiles and key figures both read /api/health; its Zu prüfen tile and the menu's
 * counter both read /api/inbox) sends it once and each part gets its own copy of the answer.
 * Only while the request runs: an answer is never kept, so a later read always asks the
 * server again (21 Nov 2026: the duplicate reads were a third of Übersicht's server time). */
const inFlight = new Map<string, Promise<Response>>();

function fetchShared(request: Request): Promise<Response> {
  if (request.method !== "GET") return fetchWithDeployRetry(request);
  const key = `${request.headers.get("Accept-Language") ?? ""} ${request.url}`;
  let shared = inFlight.get(key);
  if (!shared) {
    shared = fetchWithDeployRetry(request);
    inFlight.set(key, shared);
    const forget = () => {
      if (inFlight.get(key) === shared) inFlight.delete(key);
    };
    shared.then(forget, forget);
  }
  return shared.then((response) => response.clone());
}

export const api = createClient<paths>({
  baseUrl: "", // same origin: /api/* is proxied by the Next server (next.config.ts)
  credentials: "same-origin",
  fetch: fetchShared,
});
api.use(requestId, throwErrors);

/** The data of a call that succeeded (non-2xx responses have already thrown ApiError). */
export async function ok<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data } = await call;
  return data as T;
}
