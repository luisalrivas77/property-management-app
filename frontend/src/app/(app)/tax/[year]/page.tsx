"use client";

// Steuern › Anlage V for one tax year (phase-3.md §11, docs/mockups/04-tax-year.png). The
// property is ?immobilie=… (the first one when absent); its selector is hidden with one
// property. KAP and SO are tabs of phase 8 and stay disabled.
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { api, ok } from "@/api/client";
import { AnlageVScreen } from "@/components/tax";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const loadProperties = () => ok(api.GET("/api/properties"));

export default function TaxYearPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <TaxYear />
    </Suspense>
  );
}

function TaxYear() {
  const { year: yearParam } = useParams<{ year: string }>();
  const search = useSearchParams();
  const requested = search.get("immobilie");
  const properties = useLoad(loadProperties);
  const year = /^\d{4}$/.test(yearParam) ? Number(yearParam) : null;

  return (
    <section>
      {year === null ? (
        <p className="muted">
          Kein gültiges Steuerjahr. <Link href="/tax">Zum aktuellen Jahr</Link>
        </p>
      ) : (
        <Loading state={properties}>
          {(list) => {
            if (list.length === 0) {
              return (
                <p className="muted">
                  Noch keine Immobilie. <Link href="/properties/new">Immobilie anlegen</Link>, dann entsteht hier der
                  Entwurf der Anlage V.
                </p>
              );
            }
            const property = list.find((p) => p.id === requested) ?? list[0];
            if (!property) return null;
            return <AnlageVScreen key={property.id} property={property} properties={list} year={year} />;
          }}
        </Loading>
      )}
    </section>
  );
}
