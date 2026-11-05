"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/auth/AuthGate";
import { ErrorNotice } from "@/lib/ErrorNotice";

// §9.2: Hausbuch · Übersicht · Dokumente · Eigentümer · Einstellungen · name · Abmelden.
// Dokumente and Eigentümer are chunk 3b's screens.
const NAV = [
  { href: "/", label: "Übersicht" },
  { href: "/documents", label: "Dokumente" },
  { href: "/owners", label: "Eigentümer" },
  { href: "/settings", label: "Einstellungen" },
] as const;

function isCurrent(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function TopBar() {
  const { me, signOut } = useAuth();
  const pathname = usePathname();
  const [error, setError] = useState<unknown>(null);

  return (
    <header className="topbar">
      <nav aria-label="Hauptnavigation">
        <Link href="/" className="brand">
          Hausbuch
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
        <span className="spacer" />
        <span className="muted">{me.user.display_name}</span>
        <button type="button" className="link" onClick={() => signOut().catch(setError)}>
          Abmelden
        </button>
      </nav>
      <ErrorNotice error={error} />
    </header>
  );
}
