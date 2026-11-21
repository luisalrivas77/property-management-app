// The sidebar's count of open items on the review page ("Zu prüfen"). The page announces
// each fresh load of its list with this window event, so the count follows an answer at
// once instead of at the next poll; its detail is the number of items the page now shows.
// Without a number the sidebar fetches GET /inbox itself.
//
// The menu's count and Übersicht's Zu prüfen tile read the same list (21 Nov 2026): GET /inbox
// is the API's most expensive read, and the tile's own fetch started only after the menu's had
// finished, so fetchShared could not share them and one Übersicht load computed the list twice.
// The menu's fetch now keeps its answer here; the tile takes it while it is younger than
// INBOX_REUSE_MS (the age the menu itself refetches after on a route change), shares the menu's
// fetch while it runs, and fetches on its own when neither exists or the menu's fetch failed.
// Any INBOX_CHANGED forgets the kept list, so a changed inbox is always asked again.
import { api, ok } from "@/api/client";
import type { components } from "@/api/types";

type InboxItem = components["schemas"]["InboxItem"];

export const INBOX_CHANGED = "hausbuch:inbox-changed";

/** How old a kept list may be when another part of the page reads it; also the age after which
 * the menu refetches its count on a route change. */
export const INBOX_REUSE_MS = 30_000;

let kept: { items: InboxItem[]; at: number } | null = null;
let running: Promise<InboxItem[]> | null = null;
let generation = 0; // bumped by every change; an answer from before a change is never kept

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

/** The inbox list as the menu last read it when that is younger than maxAgeMs, else the menu's
 * running request (falling back to an own request when it fails), else a new request. */
export function recentInbox(maxAgeMs: number = INBOX_REUSE_MS): Promise<InboxItem[]> {
  if (kept && Date.now() - kept.at < maxAgeMs) return Promise.resolve(kept.items);
  if (running) return running.catch(() => fetchInbox());
  return fetchInbox();
}

function forgetInbox(): void {
  generation += 1;
  kept = null;
  running = null;
}

export function notifyInboxChanged(count?: number): void {
  forgetInbox();
  window.dispatchEvent(new CustomEvent(INBOX_CHANGED, { detail: count }));
}
