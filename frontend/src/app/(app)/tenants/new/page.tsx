"use client";

import { useRouter } from "next/navigation";

import { Breadcrumbs } from "@/components/ui";

import { TenantForm } from "../TenantForm";

export default function NewTenantPage() {
  const router = useRouter();
  return (
    <section>
      <Breadcrumbs items={[{ href: "/tenants", label: "Mieter" }, { label: "Mieter anlegen" }]} />
      <h1>Mieter anlegen</h1>
      <div className="card">
        <TenantForm onSaved={(tenant) => router.push(`/tenants/${tenant.id}`)} />
      </div>
    </section>
  );
}
