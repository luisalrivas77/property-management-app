"use client";

// Steuern › one tax year (phase-3.md §11, phase-4b.md §10, docs/mockups/04-tax-year.png): the
// Gesamt card of the whole return above the tabs, then Anlage V (?immobilie=…, the first one
// when absent; its selector is hidden with one property), Anlage N (?tab=anlage-n,
// ?eigentuemer=…) or Eure Angaben (?tab=angaben, ?eigentuemer=…; phase 10 D281). KAP and SO
// are tabs of phase 8 and stay disabled.
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { api, ok } from "@/api/client";
import { AnlageNScreen } from "@/components/anlageN";
import { ReturnEstimateSummary } from "@/components/returnEstimate";
import { AnlageVScreen } from "@/components/tax";
import { TaxInputsScreen } from "@/components/taxAngaben";
import { ANGABEN_PARAM, ANLAGE_N_PARAM, TaxTabs } from "@/components/taxYear";
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
  const owner = search.get("eigentuemer");
  const tab = search.get("tab");
  const form = tab === ANLAGE_N_PARAM ? "n" : tab === ANGABEN_PARAM ? "angaben" : "v";
  const year = /^\d{4}$/.test(yearParam) ? Number(yearParam) : null;

  return (
    <section>
      {year === null ? (
        <p className="muted">
          Kein gültiges Steuerjahr. <Link href="/tax">Zum aktuellen Jahr</Link>
        </p>
      ) : form === "angaben" ? (
        <TaxInputsScreen year={year} ownerParam={owner} propertyParam={requested} />
      ) : form === "n" ? (
        <AnlageNScreen year={year} ownerParam={owner} propertyParam={requested} />
      ) : (
        <AnlageV year={year} requested={requested} owner={owner} />
      )}
    </section>
  );
}

function AnlageV({ year, requested, owner }: { year: number; requested: string | null; owner: string | null }) {
  const properties = useLoad(loadProperties);
  return (
    <Loading state={properties}>
      {(list) => {
        const property = list.find((p) => p.id === requested) ?? list[0];
        const tabs = <TaxTabs current="v" state={{ year, propertyId: property && requested ? property.id : null, ownerId: owner }} />;
        const summary = <ReturnEstimateSummary year={year} />;
        if (!property) {
          return (
            <>
              <div className="page-head tax-head">
                <div>
                  <p className="eyebrow">Steuerjahr {year} · Anlage V (Entwurf)</p>
                  <h1>Anlage V</h1>
                </div>
              </div>
              {summary}
              {tabs}
              <p className="muted">
                Noch keine Immobilie. <Link href="/properties/new">Immobilie anlegen</Link>, dann entsteht hier der
                Entwurf der Anlage V.
              </p>
            </>
          );
        }
        return (
          <AnlageVScreen key={property.id} property={property} properties={list} year={year} summary={summary} tabs={tabs} />
        );
      }}
    </Loading>
  );
}
