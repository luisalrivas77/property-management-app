"use client";

import { useRouter } from "next/navigation";

import { Breadcrumbs } from "@/components/ui";
import { useT } from "@/i18n";
import { tenants } from "@/i18n/tenants";

import { TenantForm } from "../TenantForm";

export default function NewTenantPage() {
  const router = useRouter();
  const t = useT(tenants);
  return (
    <section>
      <Breadcrumbs items={[{ href: "/tenants", label: t.list }, { label: t.newTitle }]} />
      <h1>{t.newTitle}</h1>
      <div className="card">
        <TenantForm onSaved={(tenant) => router.push(`/tenants/${tenant.id}`)} />
      </div>
    </section>
  );
}
