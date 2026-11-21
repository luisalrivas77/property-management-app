"use client";

import { PropertyForm } from "../PropertyForm";
import { Breadcrumbs } from "@/components/ui";
import { useT } from "@/i18n";
import { propertyPage } from "@/i18n/propertyPage";

export default function NewPropertyPage() {
  const t = useT(propertyPage);
  return (
    <section>
      <Breadcrumbs items={[{ href: "/", label: t.overviewCrumb }, { label: t.pfCreate }]} />
      <h1>{t.pfCreate}</h1>
      <div className="card">
        <PropertyForm />
      </div>
    </section>
  );
}
