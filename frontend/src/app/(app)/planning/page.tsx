"use client";

// Planung in five sections (phase 10 D270, mockup 17-C2): ?section=budget (the default),
// reserve, anlegen, vermoegen or immobilie. Phase 9's links (?tab=haushalt, from rules/plan/ and
// other screens) land on Budget; a link that names a property (?immobilie=…) without a section
// opens Immobilie, the property's Planung (phase-4.md §9), whose levers live in the URL too.
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { api, ok } from "@/api/client";
import { HouseholdPlanScreen, PLAN_SECTIONS, type PlanSection } from "@/components/householdPlan";
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

function sectionOf(search: URLSearchParams): PlanSection {
  const named = search.get("section");
  if (named && (PLAN_SECTIONS as readonly string[]).includes(named)) return named as PlanSection;
  if (search.get("tab") === "haushalt") return "budget";
  if (search.get("immobilie") !== null) return "immobilie";
  return "budget";
}

function Planning() {
  const search = useSearchParams();
  const section = sectionOf(new URLSearchParams(search.toString()));
  return (
    <section>
      <HouseholdPlanScreen
        section={section}
        property={section === "immobilie" ? <PropertyPlanning requested={search.get("immobilie")} /> : null}
      />
    </section>
  );
}

function PropertyPlanning({ requested }: { requested: string | null }) {
  const properties = useLoad(loadProperties);

  return (
    <Loading state={properties}>
      {(list) => {
        if (list.length === 0) {
          return (
            <p className="muted">
              Noch keine Immobilie. <Link href="/properties/new">Immobilie anlegen</Link>, dann entsteht hier die Planung bis zum
              Verkauf.
            </p>
          );
        }
        const property = list.find((p) => p.id === requested) ?? list[0];
        if (!property) return null;
        return <PlanningScreen key={property.id} property={property} properties={list} />;
      }}
    </Loading>
  );
}
