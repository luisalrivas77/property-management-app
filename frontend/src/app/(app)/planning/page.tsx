"use client";

// Planung left the menu in phase 10 wave 3 (a) (§7.9 (a), D288): its Budget and Reserve are
// Haushalt's tabs, „Anlegen“ is the foot of Haushalt › Budget, „Vermögen“ is on Anlagen ›
// Übersicht and Immobilie on Anlagen › Immobilie. This page only sends an old link (a bookmark,
// a to-do the server wrote under rules/plan/, phase 9's ?tab=haushalt) to where its place is now,
// keeping the property and its levers and the old hash's group of „Annahmen“ (lib/planningRoutes).
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { LoadingLine } from "@/components/ui";
import { planningTarget } from "@/lib/planningRoutes";

export default function PlanningPage() {
  return (
    <Suspense fallback={<LoadingLine />}>
      <PlanningRedirect />
    </Suspense>
  );
}

function PlanningRedirect() {
  const search = useSearchParams();
  const router = useRouter();
  const query = search.toString();
  useEffect(() => {
    router.replace(planningTarget(new URLSearchParams(query), window.location.hash));
  }, [router, query]);
  return <LoadingLine />;
}
