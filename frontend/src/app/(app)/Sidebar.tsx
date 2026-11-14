"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api, ok } from "@/api/client";
import { useAuth } from "@/auth/AuthGate";
import { BrandMark } from "@/lib/BrandMark";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { INBOX_CHANGED } from "@/lib/inboxCount";

// §9.2 navigation, as the mockups' left sidebar (UI style decision, 5 Nov 2026): Hausbuch ·
// Übersicht · Zu prüfen (the review page, phase 2 step 4) · Dokumente · Konten · Anlagen (the
// depot's funds, phase 6 step 4) · Haushalt
// (the household's month, phase 6 §13) · Steuern
// (phase 3) · Planung (phase 4) · Eigentümer · Mieter · Einstellungen, then the user's name and Abmelden. On a phone it stacks above the
// content (globals.css, .shell). "Zu prüfen" carries the number of open items.
const NAV = [
  { href: "/", label: "Übersicht" },
  { href: "/inbox", label: "Zu prüfen" },
  { href: "/documents", label: "Dokumente" },
  { href: "/accounts", label: "Konten" },
  { href: "/assets", label: "Anlagen" },
  { href: "/household", label: "Haushalt" },
  { href: "/tax", label: "Steuern" },
  { href: "/planning", label: "Planung" },
  { href: "/owners", label: "Eigentümer" },
  { href: "/tenants", label: "Mieter" },
  { href: "/settings", label: "Einstellungen" },
] as const;

// Pages without an entry of their own, highlighted under the area they belong to: the capture
// page (phase-2.md §9) is reached from the review page.
const AREA_OF: Record<string, string> = { "/capture": "/inbox" };

const COUNT_REFRESH_MS = 60_000;
const ROUTE_REFRESH_MS = 30_000;

function isCurrent(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  const area = AREA_OF[pathname] ?? pathname;
  return area === href || area.startsWith(`${href}/`);
}

/** The number of items GET /inbox lists, which is what the review page shows: fetched when
 * the app opens, every minute while the tab is visible, when the tab becomes visible again,
 * and on a route change only when the count is older than ROUTE_REFRESH_MS; taken from the
 * page's INBOX_CHANGED event after it reloads its list. GET /inbox is the API's most
 * expensive read, and fetching it on every click slowed every other page on the small
 * container (14 Nov 2026). A failed fetch shows no number (null), without an error. */
function useInboxCount(pathname: string): number | null {
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
      ok(api.GET("/api/inbox")).then(
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

export function Sidebar() {
  const { me, signOut } = useAuth();
  const pathname = usePathname();
  const [error, setError] = useState<unknown>(null);
  const openItems = useInboxCount(pathname);

  return (
    <header className="sidebar">
      <nav aria-label="Hauptnavigation">
        <Link href="/" className="brand">
          <BrandMark />
          <span>Hausbuch</span>
        </Link>
        {NAV.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className="nav-link"
            aria-current={isCurrent(pathname, href) ? "page" : undefined}
          >
            {label}
            {href === "/inbox" && openItems ? (
              <span className="nav-count">
                <span aria-hidden="true">{openItems}</span>
                <span className="visually-hidden">, {openItems} offen</span>
              </span>
            ) : null}
          </Link>
        ))}
        <div className="sidebar-user">
          <span className="user-name">{me.user.display_name}</span>
          <button type="button" className="link" onClick={() => signOut().catch(setError)}>
            Abmelden
          </button>
        </div>
      </nav>
      <ErrorNotice error={error} />
    </header>
  );
}
