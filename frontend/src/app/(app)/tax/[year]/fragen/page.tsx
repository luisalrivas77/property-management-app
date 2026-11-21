"use client";

// Steuern › Jahresfragen for one tax year (phase-4b.md §10, D110): the yearly questions, one
// per screen. The flow itself is components/questionnaire.tsx. ?frage=<key> (with
// &eigentuemer=<id> or &immobilie=<id>) opens it at that question, as the Anlage N tab links.
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { QuestionnaireFlow } from "@/components/questionnaire";
import { LoadingLine } from "@/components/ui";
import { useT } from "@/i18n";
import { pages } from "@/i18n/pages";

export default function QuestionnairePage() {
  return (
    <Suspense fallback={<LoadingLine />}>
      <Questionnaire />
    </Suspense>
  );
}

function Questionnaire() {
  const { year: yearParam } = useParams<{ year: string }>();
  const search = useSearchParams();
  const year = /^\d{4}$/.test(yearParam) ? Number(yearParam) : null;
  const t = useT(pages);
  if (year === null) {
    return (
      <p className="muted">
        {t.invalidYear} <Link href="/tax">{t.currentYear}</Link>
      </p>
    );
  }
  const key = search.get("frage");
  const start = key ? { key, ownerId: search.get("eigentuemer"), propertyId: search.get("immobilie") } : null;
  return <QuestionnaireFlow key={`${year}:${key ?? ""}:${start?.ownerId ?? ""}:${start?.propertyId ?? ""}`} year={year} start={start} />;
}
