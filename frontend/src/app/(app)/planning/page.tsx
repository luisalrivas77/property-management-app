"use client";

// Planung (phase-4.md §9, docs/mockups/05-performance.png): the property is ?immobilie=… (the
// first one when absent; the selector shows with more than one). The levers of the projection
// live in the URL too, so the ± buttons only change query parameters and the screen refetches.
// ?tab=haushalt is the household plan (phase-9.md §14, mockups 12, 12b, 12c).
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { api, ok } from "@/api/client";
import { HouseholdPlanScreen } from "@/components/householdPlan";
import { PlanningScreen } from "@/components/planning";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const loadProperties = () => ok(api.GET("/api/properties"));

export default function PlanningPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <Planning />
    </Suspense>
  );
}

function Planning() {
  const search = useSearchParams();
  if (search.get("tab") === "haushalt") {
    return (
      <section>
        <HouseholdPlanScreen />
      </section>
    );
  }
  return <PropertyPlanning requested={search.get("immobilie")} />;
}

function PropertyPlanning({ requested }: { requested: string | null }) {
  const properties = useLoad(loadProperties);

  return (
    <section>
      <Loading state={properties}>
        {(list) => {
          if (list.length === 0) {
            return (
              <p className="muted">
                Noch keine Immobilie. <Link href="/properties/new">Immobilie anlegen</Link>, dann entsteht hier die Planung
                bis zum Verkauf.
              </p>
            );
          }
          const property = list.find((p) => p.id === requested) ?? list[0];
          if (!property) return null;
          return <PlanningScreen key={property.id} property={property} properties={list} />;
        }}
      </Loading>
    </section>
  );
}
