"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/auth/AuthGate";
import { BrandMark } from "@/lib/BrandMark";
import { ErrorNotice } from "@/lib/ErrorNotice";

// §9.2 navigation, as the mockups' left sidebar (UI style decision, 5 Nov 2026): Hausbuch ·
// Übersicht · Posteingang (phase 2 step 4) · Dokumente · Konten · Eigentümer · Mieter ·
// Einstellungen, then the user's name and Abmelden. On a phone it stacks above the content
// (globals.css, .shell).
const NAV = [
  { href: "/", label: "Übersicht" },
  { href: "/inbox", label: "Posteingang" },
  { href: "/documents", label: "Dokumente" },
  { href: "/accounts", label: "Konten" },
  { href: "/owners", label: "Eigentümer" },
  { href: "/tenants", label: "Mieter" },
  { href: "/settings", label: "Einstellungen" },
] as const;

// Pages without an entry of their own, highlighted under the area they belong to: the capture
// page (phase-2.md §9) is reached from the Posteingang.
const AREA_OF: Record<string, string> = { "/capture": "/inbox" };

function isCurrent(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  const area = AREA_OF[pathname] ?? pathname;
  return area === href || area.startsWith(`${href}/`);
}

export function Sidebar() {
  const { me, signOut } = useAuth();
  const pathname = usePathname();
  const [error, setError] = useState<unknown>(null);

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
