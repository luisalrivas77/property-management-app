"use client";

// The old Jahresfragen address (phase-4b.md §10, D110), kept since the questions moved into
// Steuern › Eure Angaben's topics and the Immobilie step (phase 10 §7.8 (b), D294).
// /tax/<year>/fragen?frage=<key>[&eigentuemer=<id>|&immobilie=<id>] opens that question in its
// topic, a property question in the Immobilie step; without a question it opens the first open
// topic, else the topic list. If the year's topics cannot be read (the guide has none), the old
// flow is shown here as before, so no question is ever out of reach.
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect } from "react";

import { api, ok } from "@/api/client";
import { isTopic } from "@/components/angaben";
import { QuestionnaireFlow } from "@/components/questionnaire";
import { angabenTopicHref, taxFormHref, taxStepHref } from "@/components/taxYear";
import { LoadingLine } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { angaben } from "@/i18n/angaben";
import { pages } from "@/i18n/pages";

export default function QuestionnairePage() {
  return (
    <Suspense fallback={<LoadingLine />}>
      <Questionnaire />
    </Suspense>
  );
}

async function loadTarget(year: number) {
  const [guide, view] = await Promise.all([
    ok(api.GET("/api/tax/{tax_year}/guide", { params: { path: { tax_year: year } } })),
    ok(api.GET("/api/tax/questionnaire", { params: { query: { tax_year: year } } })),
  ]);
  return { guide, view };
}

/** Where an old /fragen link lands now, or null to show the old flow. */
function fragenTarget(
  year: number,
  data: Awaited<ReturnType<typeof loadTarget>>,
  at: { key: string | null; ownerId: string | null; propertyId: string | null },
): string | null {
  if (data.guide.topics == null) return null;
  if (at.key) {
    const question = data.view.questions.find((q) => q.key === at.key);
    if (question?.scope === "property") {
      const step = taxStepHref(year, "immobilie", at.propertyId);
      return `${step}${step.includes("?") ? "&" : "?"}frage=${encodeURIComponent(at.key)}`;
    }
    const topic = question?.topic;
    if (isTopic(topic)) return angabenTopicHref(year, topic, { ownerId: at.ownerId, frage: at.key });
  }
  const open = data.guide.topics.find((topic) => topic.state === "open");
  return open ? angabenTopicHref(year, open.topic) : taxFormHref("angaben", { year, propertyId: null, ownerId: null });
}

function Questionnaire() {
  const { year: yearParam } = useParams<{ year: string }>();
  const year = /^\d{4}$/.test(yearParam) ? Number(yearParam) : null;
  const t = useT(pages);
  if (year === null) {
    return (
      <p className="muted">
        {t.invalidYear} <Link href="/tax">{t.currentYear}</Link>
      </p>
    );
  }
  return <Forward year={year} />;
}

function Forward({ year }: { year: number }) {
  const search = useSearchParams();
  const router = useRouter();
  const a = useT(angaben);
  const key = search.get("frage");
  const ownerId = search.get("eigentuemer");
  const propertyId = search.get("immobilie");
  const load = useCallback(() => loadTarget(year), [year]);
  const state = useLoad(load);
  const target = state.data ? fragenTarget(year, state.data, { key, ownerId, propertyId }) : undefined;
  useEffect(() => {
    if (target) router.replace(target);
  }, [target, router]);
  // The old flow when the topics cannot be read, or the target cannot be loaded at all.
  if (target === null || state.error != null) {
    const start = key ? { key, ownerId, propertyId } : null;
    return <QuestionnaireFlow key={`${year}:${key ?? ""}:${ownerId ?? ""}:${propertyId ?? ""}`} year={year} start={start} />;
  }
  return <p className="muted">{a.opening}</p>;
}
