"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Breadcrumbs } from "@/components/ui";

import { AccountForm } from "../AccountForm";

export default function NewAccountPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <NewAccount />
    </Suspense>
  );
}

/** `?kind=credit_card` or `?kind=depot` pre-selects the Kontoart (phase 6 D176 (3)). */
function NewAccount() {
  const kind = useSearchParams().get("kind");
  return (
    <section>
      <Breadcrumbs items={[{ href: "/accounts", label: "Konten" }, { label: "Konto anlegen" }]} />
      <h1>Konto anlegen</h1>
      <div className="card">
        <AccountForm initialKind={kind} />
      </div>
    </section>
  );
}
