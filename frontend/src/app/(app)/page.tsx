"use client";

// PLACEHOLDER (chunk 3a): where sign-in and enrollment land. Chunk 3b replaces this with the
// Übersicht of §9.1 (property cards, "Immobilie anlegen").
import Link from "next/link";

import { useAuth } from "@/auth/AuthGate";

export default function OverviewPage() {
  const { me } = useAuth();
  return (
    <section className="stack">
      <h1>Übersicht</h1>
      <p>
        Angemeldet als {me.user.display_name} im Haushalt „{me.workspace.name}“.
      </p>
      <p className="muted">
        Die Immobilien-Übersicht folgt. Passkeys verwalten: <Link href="/settings">Einstellungen</Link>.
      </p>
    </section>
  );
}
