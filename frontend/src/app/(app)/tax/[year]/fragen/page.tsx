"use client";

// Steuern › Jahresfragen for one tax year (phase-4b.md §10, D110): the yearly questions, one
// per screen. The flow itself is components/questionnaire.tsx. ?frage=<key> (with
// &eigentuemer=<id> or &immobilie=<id>) opens it at that question, as the Anlage N tab links.
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { QuestionnaireFlow } from "@/components/questionnaire";

export default function QuestionnairePage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <Questionnaire />
    </Suspense>
  );
}

function Questionnaire() {
  const { year: yearParam } = useParams<{ year: string }>();
  const search = useSearchParams();
  const year = /^\d{4}$/.test(yearParam) ? Number(yearParam) : null;
  if (year === null) {
    return (
      <p className="muted">
        Kein gültiges Steuerjahr. <Link href="/tax">Zum aktuellen Jahr</Link>
      </p>
    );
  }
  const key = search.get("frage");
  const start = key ? { key, ownerId: search.get("eigentuemer"), propertyId: search.get("immobilie") } : null;
  return <QuestionnaireFlow key={`${year}:${key ?? ""}:${start?.ownerId ?? ""}:${start?.propertyId ?? ""}`} year={year} start={start} />;
}
