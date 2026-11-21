"use client";

// The number of open items next to „Zu prüfen“ in the menu (phase 10 D266), moved unchanged from
// the old sidebar (Sidebar.tsx).
import { useEffect, useRef, useState } from "react";

import { INBOX_CHANGED, INBOX_REUSE_MS, fetchInbox } from "@/lib/inboxCount";

const COUNT_REFRESH_MS = 60_000;
const ROUTE_REFRESH_MS = INBOX_REUSE_MS;

/** The number of items GET /inbox lists, which is what the review page shows: fetched when
 * the app opens, every minute while the tab is visible, when the tab becomes visible again,
 * and on a route change only when the count is older than ROUTE_REFRESH_MS; taken from the
 * page's INBOX_CHANGED event after it reloads its list. GET /inbox is the API's most
 * expensive read, and fetching it on every click slowed every other page on the small
 * container (14 Nov 2026). A failed fetch shows no number (null), without an error. Each fetch
 * keeps its list in lib/inboxCount.ts, where Übersicht's Zu prüfen tile reads it instead of
 * asking the server again (21 Nov 2026). */
export function useInboxCount(pathname: string): number | null {
  const [count, setCount] = useState<number | null>(null);
  const refreshRef = useRef<(() => void) | null>(null);
  const fetchedAt = useRef(0);

  useEffect(() => {
    if (Date.now() - fetchedAt.current >= ROUTE_REFRESH_MS) refreshRef.current?.();
  }, [pathname]);

  useEffect(() => {
    let cancelled = false;
    let latest = 0; // a slower, older fetch never overwrites a newer count

    function refresh() {
      const mine = ++latest;
      fetchedAt.current = Date.now();
      fetchInbox().then(
        (items) => {
          if (!cancelled && mine === latest) setCount(items.length);
        },
        () => {
          if (!cancelled && mine === latest) setCount(null);
        },
      );
    }
    function refreshIfVisible() {
      if (document.visibilityState === "visible") refresh();
    }
    function onChanged(event: Event) {
      const detail: unknown = (event as CustomEvent<unknown>).detail;
      if (typeof detail === "number") {
        latest += 1;
        fetchedAt.current = Date.now();
        setCount(detail);
      } else {
        refresh();
      }
    }

    refresh();
    refreshRef.current = refresh;
    const timer = window.setInterval(refreshIfVisible, COUNT_REFRESH_MS);
    document.addEventListener("visibilitychange", refreshIfVisible);
    window.addEventListener(INBOX_CHANGED, onChanged);
    return () => {
      cancelled = true;
      refreshRef.current = null;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshIfVisible);
      window.removeEventListener(INBOX_CHANGED, onChanged);
    };
  }, []);

  return count;
}
