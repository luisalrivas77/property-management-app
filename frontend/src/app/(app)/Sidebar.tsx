"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/auth/AuthGate";
import { BrandMark } from "@/lib/BrandMark";
import { ErrorNotice } from "@/lib/ErrorNotice";

// §9.2 navigation, as the mockups' left sidebar (UI style decision, 5 Nov 2026): Hausbuch ·
// Übersicht · Dokumente · Konten · Eigentümer · Mieter · Einstellungen, then the user's name and
// Abmelden. On a phone it stacks above the content (globals.css, .shell).
const NAV = [
  { href: "/", label: "Übersicht" },
  { href: "/documents", label: "Dokumente" },
  { href: "/accounts", label: "Konten" },
  { href: "/owners", label: "Eigentümer" },
  { href: "/tenants", label: "Mieter" },
  { href: "/settings", label: "Einstellungen" },
] as const;

function isCurrent(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
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
