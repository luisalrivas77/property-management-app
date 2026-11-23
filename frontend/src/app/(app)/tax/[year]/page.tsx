"use client";

// Steuern › one tax year (phase-3.md §11, phase-4b.md §10, phase 10 D290, mockup 22b).
// /tax/<year> alone is the guided start page; ?schritt=immobilie|belege|pruefen opens one step
// (?immobilie=… for the Immobilie step). The forms are views: Anlage V (?tab=anlage-v, or a
// legacy ?immobilie=… without a tab or step; the first property when absent), Anlage N
// (?tab=anlage-n, ?eigentuemer=…). Eure Angaben (?tab=angaben, ?eigentuemer=…; D281) holds the
// owners' and household's inputs. KAP and SO are tabs of phase 8 and stay disabled.
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { api, ok } from "@/api/client";
import { AnlageNScreen } from "@/components/anlageN";
import { ReturnEstimateSummary } from "@/components/returnEstimate";
import { AnlageVScreen } from "@/components/tax";
import { TaxInputsScreen } from "@/components/taxAngaben";
import { TaxGuideScreen } from "@/components/taxGuide";
import { ANGABEN_PARAM, ANLAGE_N_PARAM, ANLAGE_V_PARAM, type GuideStepParam, STEP_PARAM, TaxTabs } from "@/components/taxYear";
import { Loading, LoadingLine } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { pages } from "@/i18n/pages";

const loadProperties = () => ok(api.GET("/api/properties"));

export default function TaxYearPage() {
  return (
    <Suspense fallback={<LoadingLine />}>
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
  const stepParam = search.get(STEP_PARAM);
  const step: GuideStepParam | null = stepParam === "immobilie" || stepParam === "belege" || stepParam === "pruefen" ? stepParam : null;
  // /tax/<year> alone is the guided start page (D290); ?tab=anlage-v, or a legacy
  // ?immobilie=… without a tab or step, opens the Anlage V view.
  const form =
    tab === ANLAGE_N_PARAM
      ? "n"
      : tab === ANGABEN_PARAM
        ? "angaben"
        : tab === ANLAGE_V_PARAM || (requested && !step)
          ? "v"
          : "guide";
  const year = /^\d{4}$/.test(yearParam) ? Number(yearParam) : null;
  const t = useT(pages);

  return (
    <section>
      {year === null ? (
        <p className="muted">
          {t.invalidYear} <Link href="/tax">{t.currentYear}</Link>
        </p>
      ) : form === "angaben" ? (
        <TaxInputsScreen year={year} ownerParam={owner} propertyParam={requested} />
      ) : form === "guide" ? (
        <TaxGuideScreen year={year} step={step} propertyParam={requested} />
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
  const t = useT(pages);
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
                  <p className="eyebrow">{t.anlageVEyebrow(year)}</p>
                  <h1>Anlage V</h1>
                </div>
              </div>
              {summary}
              {tabs}
              <p className="muted">
                {t.noProperty} <Link href="/properties/new">{t.createProperty}</Link>
                {t.anlageVRest}
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
