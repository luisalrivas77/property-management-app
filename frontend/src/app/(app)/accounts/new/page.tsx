"use client";

import { Breadcrumbs } from "@/components/ui";

import { AccountForm } from "../AccountForm";

export default function NewAccountPage() {
  return (
    <section>
      <Breadcrumbs items={[{ href: "/accounts", label: "Konten" }, { label: "Konto anlegen" }]} />
      <h1>Konto anlegen</h1>
      <div className="card">
        <AccountForm />
      </div>
    </section>
  );
}
