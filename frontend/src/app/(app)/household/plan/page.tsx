"use client";

// „Euer Plan“ (phase 10 §7.11 (h), D306; mockup 19i) under Haushalt › Ziel: the household's plan as
// seven cards, „Für euch“ and „Zum Teilen“ (?ansicht=teilen). The screen is
// components/householdPlanPage.tsx; this route only wraps it for useSearchParams.
import { Suspense } from "react";

import { PlanPageView } from "@/components/householdPlanPage";

export default function HouseholdPlanPage() {
  return (
    <Suspense fallback={<p className="muted">…</p>}>
      <PlanPageView />
    </Suspense>
  );
}
