"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback } from "react";

import { api, ok } from "@/api/client";
import { Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { loans } from "@/i18n/loans";

import { LoanForm } from "../LoanForm";

export default function NewLoanPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <NewLoan />
    </Suspense>
  );
}

function NewLoan() {
  const propertyId = useSearchParams().get("property");
  const load = useCallback(
    async () => (propertyId ? ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: propertyId } } })) : null),
    [propertyId],
  );
  const property = useLoad(load);
  const t = useT(loans);
  return (
    <section>
      <Loading state={property}>
        {(found) =>
          found === null ? (
            <p>
              Ein Darlehen gehört zu einer Immobilie: dort unter „Darlehen“ anlegen (<Link href="/">Übersicht</Link>).
            </p>
          ) : (
            <>
              <Breadcrumbs items={[{ href: `/properties/${found.id}?tab=darlehen`, label: found.label }, { label: t.newTitle }]} />
              <h1>{t.newTitle}</h1>
              <div className="card">
                <LoanForm propertyId={found.id} />
              </div>
            </>
          )
        }
      </Loading>
    </section>
  );
}
