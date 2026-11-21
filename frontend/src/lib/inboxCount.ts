// The sidebar's count of open items on the review page ("Zu prüfen"). The page announces
// each fresh load of its list with this window event, so the count follows an answer at
// once instead of at the next poll; its detail is the number of items the page now shows.
// Without a number the sidebar fetches GET /inbox itself.
//
// The menu's count and Übersicht's Zu prüfen tile read the same list (21 Nov 2026): GET /inbox
// is the API's most expensive read, and the tile's own fetch started only after the menu's had
// finished, so fetchShared could not share them and one Übersicht load computed the list twice.
// The menu's fetch now keeps its answer here; the tile shares the menu's fetch while it runs,
// else takes its answer while it is younger than TILE_REUSE_MS (the few seconds one page load
// takes, so a list kept from before a change the app does not announce is not shown later), and
// fetches on its own otherwise or when the menu's fetch failed. Any INBOX_CHANGED forgets the
// kept list, and so does a new sign-in or a sign-out (resetInbox), so one session's list never
// reaches the next.
import { api, ok } from "@/api/client";
import type { components } from "@/api/types";

type InboxItem = components["schemas"]["InboxItem"];

export const INBOX_CHANGED = "hausbuch:inbox-changed";

/** How old a kept list may be when another part of the page reads it; also the age after which
 * the menu refetches its count on a route change. */
export const INBOX_REUSE_MS = 30_000;

/** How old the menu's list may be for Übersicht's tile: long enough for one page load. */
export const TILE_REUSE_MS = 5_000;

let kept: { items: InboxItem[]; at: number } | null = null;
let running: Promise<InboxItem[]> | null = null;
let generation = 0; // bumped by every change; a request sent after it keeps its answer (a GET
// shared in flight by fetchShared may still be the one sent before)

/** GET /inbox, its answer kept for recentInbox. A slower, older request never replaces the
 * answer of a newer one. */
export function fetchInbox(): Promise<InboxItem[]> {
  const mine = generation;
  const request = ok(api.GET("/api/inbox"));
  running = request;
  request.then(
    (items) => {
      if (running === request) {
        running = null;
        if (mine === generation) kept = { items, at: Date.now() };
      }
    },
    () => {
      if (running === request) running = null;
    },
  );
  return request;
}

/** The menu's running request (falling back to an own request when it fails), else the list as
 * the menu last read it when that is younger than maxAgeMs, else a new request. */
export function recentInbox(maxAgeMs: number = TILE_REUSE_MS): Promise<InboxItem[]> {
  if (running) return running.catch(() => fetchInbox());
  if (kept && Date.now() - kept.at < maxAgeMs) return Promise.resolve(kept.items);
  return fetchInbox();
}

/** Forgets the kept list and any running request without announcing a change: at a sign-in and
 * a sign-out, so a list read for one session is never shown in the next. */
export function resetInbox(): void {
  generation += 1;
  kept = null;
  running = null;
}

export function notifyInboxChanged(count?: number): void {
  resetInbox();
  window.dispatchEvent(new CustomEvent(INBOX_CHANGED, { detail: count }));
}
