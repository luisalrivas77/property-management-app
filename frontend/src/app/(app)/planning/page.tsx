"use client";

// Planung in five sections (phase 10 D270, mockup 17-C2): ?section=budget (the default),
// reserve, anlegen, vermoegen or immobilie. Phase 9's links (?tab=haushalt, from rules/plan/ and
// other screens) land on Budget. The property's Planung (phase-4.md §9) moved to Anlagen ›
// Immobilie (phase 10 §7.6, D288): ?section=immobilie and a link that names a property
// (?immobilie=…) are sent there with the property and its levers.
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { HouseholdPlanScreen, PLAN_SECTIONS, type PlanSection } from "@/components/householdPlan";
import { LoadingLine } from "@/components/ui";

/** The query parameters Anlagen › Immobilie reads: the property and the projection's levers. */
const PROPERTY_PARAMS = ["immobilie", "szenario", "wertsteigerung", "mieterhoehung", "verkauf"] as const;

function propertyPlanningHref(search: URLSearchParams): string {
  const query = new URLSearchParams({ tab: "immobilie" });
  for (const name of PROPERTY_PARAMS) {
    const value = search.get(name);
    if (value !== null) query.set(name, value);
  }
  return `/assets?${query.toString()}`;
}

export default function PlanningPage() {
  return (
    <Suspense fallback={<LoadingLine />}>
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
  const router = useRouter();
  const params = new URLSearchParams(search.toString());
  const section = sectionOf(params);
  const target = section === "immobilie" ? propertyPlanningHref(params) : null;
  useEffect(() => {
    if (target) router.replace(target);
  }, [router, target]);
  if (target) return <LoadingLine />;
  return (
    <section>
      <HouseholdPlanScreen section={section} property={null} />
    </section>
  );
}
