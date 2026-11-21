"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Breadcrumbs, LoadingLine } from "@/components/ui";
import { useT } from "@/i18n";
import { accountForms } from "@/i18n/accountForms";

import { AccountForm } from "../AccountForm";

export default function NewAccountPage() {
  return (
    <Suspense fallback={<LoadingLine />}>
      <NewAccount />
    </Suspense>
  );
}

/** `?kind=credit_card` or `?kind=depot` pre-selects the Kontoart (phase 6 D176 (3)). */
function NewAccount() {
  const kind = useSearchParams().get("kind");
  const t = useT(accountForms);
  return (
    <section>
      <Breadcrumbs items={[{ href: "/accounts", label: t.breadcrumbAccounts }, { label: t.createAccount }]} />
      <h1>{t.createAccount}</h1>
      <div className="card">
        <AccountForm initialKind={kind} />
      </div>
    </section>
  );
}
