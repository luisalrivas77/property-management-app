"use client";

// The menu (phase 10 D266, mockup 14-C2). One <nav> in the DOM, placed by shell.css: below
// 800 px a bar at the bottom of the screen, from 800 px tabs in a bar at the top. Its five
// entries are Übersicht, Zu prüfen (with the number of open items), Haushalt, Anlagen and
// „Mehr“, a button that opens Konten, Immobilien, Steuern, Planung, Dokumente and Einstellungen
// (a drop-down on the computer, a sheet above the bottom bar on the phone), with the user's name
// and Abmelden. Mieter and Eigentümer are no entries any more: their pages are reached from
// Immobilien and still open by URL. Every route kept (§5.1).
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { useAuth } from "@/auth/AuthGate";
import { useT } from "@/i18n";
import { shell } from "@/i18n/shell";
import { BrandMark } from "@/lib/BrandMark";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { Icon, type IconName } from "@/lib/icons";

import { useInboxCount } from "./useInboxCount";

type ShellKey = keyof typeof shell.de;

interface Entry {
  href: string;
  label: ShellKey;
  icon: IconName;
  /** Routes without an entry of their own that belong to this one. */
  also?: readonly string[];
}

interface MoreEntry extends Entry {
  hint?: ShellKey;
}

const PRIMARY: readonly Entry[] = [
  { href: "/", label: "overview", icon: "overview" },
  // The capture page (phase-2.md §9) is reached from the review page.
  { href: "/inbox", label: "inbox", icon: "inbox", also: ["/capture"] },
  { href: "/household", label: "household", icon: "household" },
  { href: "/assets", label: "assets", icon: "assets" },
];

const MORE: readonly MoreEntry[] = [
  { href: "/accounts", label: "accounts", hint: "accountsHint", icon: "accounts" },
  {
    href: "/properties",
    label: "properties",
    hint: "propertiesHint",
    icon: "properties",
    // Mieter and Eigentümer live inside Immobilien now (D266), with the pages under a property.
    also: ["/units", "/tenancies", "/tenants", "/owners", "/loans"],
  },
  { href: "/tax", label: "tax", hint: "taxHint", icon: "tax" },
  { href: "/planning", label: "planning", hint: "planningHint", icon: "planning" },
  { href: "/documents", label: "documents", hint: "documentsHint", icon: "documents" },
  { href: "/settings", label: "settings", icon: "settings" },
];

function under(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function isCurrent(pathname: string, entry: Entry): boolean {
  if (entry.href === "/") return pathname === "/";
  return [entry.href, ...(entry.also ?? [])].some((href) => under(pathname, href));
}

/** „Mehr“ open or closed. It is open for the page it was opened on, so a navigation closes it
 * without an effect; Escape, a click outside and a click on an entry close it too. */
function useMoreMenu(pathname: string) {
  const [openOn, setOpenOn] = useState<string | null>(null);
  const open = openOn === pathname;
  const containerRef = useRef<HTMLLIElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      // Handled here, so a detail panel under the menu stays open (it skips a prevented Escape).
      event.preventDefault();
      setOpenOn(null);
      buttonRef.current?.focus();
    }
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpenOn(null);
    }
    // Capture phase: before the panel's listener, which was added earlier on the same node.
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  return {
    open,
    containerRef,
    buttonRef,
    toggle: () => setOpenOn(open ? null : pathname),
    close: () => setOpenOn(null),
  };
}

export function AppNav() {
  const { me, signOut } = useAuth();
  const pathname = usePathname();
  const t = useT(shell);
  const [error, setError] = useState<unknown>(null);
  const openItems = useInboxCount(pathname);
  const { open: moreOpen, containerRef, buttonRef, toggle: toggleMore, close: closeMore } = useMoreMenu(pathname);
  const menuId = useId();
  const moreCurrent = MORE.some((entry) => isCurrent(pathname, entry));

  return (
    <header className="appbar">
      <Link href="/" className="brand appbar-brand" aria-label={t.brandHome}>
        <BrandMark />
        <span className="appbar-brand-name">Hausbuch</span>
      </Link>
      <nav aria-label={t.mainNavigation} className="mainnav">
        <ul className="mainnav-list">
          {PRIMARY.map((entry) => (
            <li key={entry.href}>
              <Link
                href={entry.href}
                className="mainnav-link"
                aria-current={isCurrent(pathname, entry) ? "page" : undefined}
              >
                <span className="mainnav-icon">
                  <Icon name={entry.icon} />
                  {entry.href === "/inbox" && openItems ? (
                    <span className="mainnav-count" aria-hidden="true">
                      {openItems}
                    </span>
                  ) : null}
                </span>
                <span className="mainnav-label">{t[entry.label] as string}</span>
                {entry.href === "/inbox" && openItems ? (
                  <span className="visually-hidden">{t.inboxOpen(openItems)}</span>
                ) : null}
              </Link>
            </li>
          ))}
          <li className="mainnav-more" ref={containerRef}>
            <button
              ref={buttonRef}
              type="button"
              className="mainnav-link"
              aria-expanded={moreOpen}
              aria-controls={menuId}
              data-current={moreCurrent || undefined}
              onClick={toggleMore}
            >
              <span className="mainnav-icon">
                <Icon name="more" />
              </span>
              <span className="mainnav-label">{t.more}</span>
              <Icon name="chevronDown" size={16} className="mainnav-chevron" />
            </button>
            <div id={menuId} className="more-menu" hidden={!moreOpen}>
              <div className="more-head">
                <h2 className="more-title">{t.moreTitle}</h2>
                <span className="more-user">{me.user.display_name}</span>
                <button type="button" className="link" onClick={() => signOut().catch(setError)}>
                  {t.signOut}
                </button>
              </div>
              <ErrorNotice error={error} />
              <div className="more-actions">
                <Link href="/documents" className="more-action" onClick={closeMore}>
                  <span className="more-action-icon">
                    <Icon name="upload" />
                  </span>
                  {t.upload}
                </Link>
                <Link href="/capture" className="more-action" onClick={closeMore}>
                  <span className="more-action-icon">
                    <Icon name="camera" />
                  </span>
                  {t.capturePhoto}
                </Link>
              </div>
              <ul className="more-list">
                {MORE.map((entry) => (
                  <li key={entry.href}>
                    <Link
                      href={entry.href}
                      className="more-link"
                      aria-current={isCurrent(pathname, entry) ? "page" : undefined}
                      // The entry's name is its label; the line under it is its description.
                      aria-labelledby={`${menuId}-${entry.label}`}
                      aria-describedby={entry.hint ? `${menuId}-${entry.label}-hint` : undefined}
                      onClick={closeMore}
                    >
                      <Icon name={entry.icon} className="more-link-icon" />
                      <span className="more-link-text">
                        <span id={`${menuId}-${entry.label}`} className="more-link-label">
                          {t[entry.label] as string}
                        </span>
                        {entry.hint ? (
                          <span id={`${menuId}-${entry.label}-hint`} className="more-link-hint">
                            {t[entry.hint] as string}
                          </span>
                        ) : null}
                      </span>
                      <Icon name="chevronRight" size={18} className="more-link-chevron" />
                    </Link>
                  </li>
                ))}
              </ul>
              <p className="more-note">{t.movedNote}</p>
            </div>
          </li>
        </ul>
      </nav>
      <div className="appbar-actions">
        <Link href="/documents" className="button-link appbar-upload">
          <Icon name="upload" size={18} />
          <span className="appbar-upload-label">{t.upload}</span>
        </Link>
      </div>
    </header>
  );
}
