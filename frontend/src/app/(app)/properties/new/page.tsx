"use client";

import { PropertyForm } from "../PropertyForm";
import { Breadcrumbs } from "@/components/ui";

export default function NewPropertyPage() {
  return (
    <section>
      <Breadcrumbs items={[{ href: "/", label: "Übersicht" }, { label: "Immobilie anlegen" }]} />
      <h1>Immobilie anlegen</h1>
      <div className="card">
        <PropertyForm />
      </div>
    </section>
  );
}
