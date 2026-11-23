"use client";

// Steuern as one guided path of five steps (phase 10 §7.6, D290, mockup 22b). GET
// /tax/{year}/guide (rules/tax_guide.py) decides every step's state, „Jetzt dran“, the points
// and the Belege; this screen formats them and reuses the existing to-do actions (todos.tsx),
// input sections (taxInputs.tsx) and export cards (tax.tsx, anlageN.tsx). No figure is computed
// here: the results and the estimate are the forms' own, copied by the server.
//
// URLs (taxYear.tsx): /tax/<year> the start page; ?schritt=immobilie&immobilie=<id> the
// property's tax inputs; ?schritt=belege the year's Belege; ?schritt=pruefen „Prüfen und
// abgeben“, each point opening alone in the detail panel (#d-punkt-<key>). Eure Angaben stays
// ?tab=angaben and the Jahresfragen /tax/<year>/fragen.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, type MouseEvent, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import type { AllocationFact, AnlageVDraft, DocumentChecklistItem, Owner, Property } from "@/api/schemas";
import { ChecklistTab } from "@/app/(app)/properties/[id]/ChecklistTab";
import { translate, useFormat, useT } from "@/i18n";
import { taxGuide as messages } from "@/i18n/taxGuide";
import { todosText } from "@/i18n/todos";
import { Icon } from "@/lib/icons";

import { AnlageNHandOver } from "./anlageN";
import { DetailLayout, DetailPanel, useDetail } from "./DetailPanel";
import { ReadUpload } from "./documents";
import { WARNING_TEXT } from "./labels";
import { NoPayButton, noPayHint } from "./noPay";
import { isZeroAmount, ReturnEstimateSummary } from "./returnEstimate";
import { HandOverCard, LimitCard } from "./tax";
import {
  GrundsteuerSection,
  MovableAssetSection,
  PRICE_RENOVATION_ANCHOR,
  type TaxInput,
  UsefulLifeSection,
} from "./taxInputs";
import {
  checklistAction,
  checklistEntry,
  type GuideContext,
  requirementGuide,
  type TodoEntry,
  TodoList,
  warningEntry,
} from "./todos";
import {
  type GuideStepParam,
  payInputsHref,
  questionnaireHref,
  taxFormHref,
  taxGuideHref,
  taxStepHref,
} from "./taxYear";
import { Loading } from "./ui";
import { useLoad, type Loaded } from "./useLoad";

import "./tax.css";
import "./taxGuide.css";

type TaxGuide = components["schemas"]["TaxGuide"];
type GuideStep = components["schemas"]["GuideStep"];
type GuidePoint = components["schemas"]["GuidePoint"];
type GuideDocument = components["schemas"]["GuideDocument"];
type GuideFormResult = components["schemas"]["GuideFormResult"];
type T = ReturnType<typeof translate<typeof messages.de>>;

/** The anchors of the Immobilie step's inputs; a to-do's `#…` link (todos.tsx `onPage`) or a
 * reminder opens the step at one of them. */
export const PROPERTY_STEP_ANCHORS = ["tax-useful-life", "tax-movable-asset", "tax-grundsteuer", "tax-limit"] as const;

const loadProperties = () => ok(api.GET("/api/properties"));
const loadOwners = () => ok(api.GET("/api/owners"));

function useGuide(year: number): Loaded<TaxGuide> {
  const load = useCallback(() => ok(api.GET("/api/tax/{tax_year}/guide", { params: { path: { tax_year: year } } })), [year]);
  return useLoad(load);
}

/** What the to-do actions of one property need: its draft (null without a rule set) and the
 * payments the draft cites, by allocation id. */
interface PropertyContext {
  draft: AnlageVDraft | null;
  payments: Map<string, AllocationFact>;
}

async function loadPropertyContext(propertyId: string, year: number): Promise<PropertyContext> {
  const draft = await ok(
    api.GET("/api/properties/{property_id}/anlage-v", {
      params: { path: { property_id: propertyId }, query: { tax_year: year } },
    }),
  ).catch(() => null);
  if (!draft) return { draft: null, payments: new Map() };
  const rows = await ok(
    api.GET("/api/properties/{property_id}/anlage-v/sources", {
      params: { path: { property_id: propertyId }, query: { tax_year: year } },
    }),
  ).catch(() => []);
  return { draft, payments: new Map(rows.map((row) => [row.allocation_id, row])) };
}

/** The contexts of the given properties, keyed by id. */
function usePropertyContexts(year: number, propertyIds: readonly string[]): Loaded<Map<string, PropertyContext>> {
  const key = propertyIds.join(",");
  const load = useCallback(async () => {
    const ids = key ? key.split(",") : [];
    const loaded = await Promise.all(ids.map((id) => loadPropertyContext(id, year)));
    return new Map(ids.map((id, index) => [id, loaded[index] as PropertyContext]));
  }, [key, year]);
  return useLoad(load);
}

function propertyLabel(properties: readonly Property[], id: string | null | undefined, t: T): string {
  return properties.find((p) => p.id === id)?.label ?? t.unknownProperty;
}

function ownerName(owners: readonly Owner[], id: string | null | undefined): string {
  return owners.find((o) => o.id === id)?.full_name ?? "";
}

function abs(value: string): string {
  return value.startsWith("-") ? value.slice(1) : value;
}

// --- the screen ----------------------------------------------------------------------------

export function TaxGuideScreen({
  year,
  step,
  propertyParam,
}: {
  year: number;
  step: GuideStepParam | null;
  propertyParam: string | null;
}) {
  const guide = useGuide(year);
  const properties = useLoad(loadProperties);
  const owners = useLoad(loadOwners);
  return (
    <Loading state={guide}>
      {(data) => {
        const ps = properties.data ?? [];
        const os = owners.data ?? [];
        const onChanged = guide.reload;
        if (step === "immobilie") {
          return <PropertyStep guide={data} properties={ps} propertyParam={propertyParam} onChanged={onChanged} />;
        }
        if (step === "belege") {
          return <DocumentsStep guide={data} properties={ps} owners={os} onChanged={onChanged} />;
        }
        if (step === "pruefen") {
          return <ReviewStep guide={data} properties={ps} owners={os} onChanged={onChanged} />;
        }
        return <StartPage guide={data} properties={ps} />;
      }}
    </Loading>
  );
}

// --- the start page --------------------------------------------------------------------------

interface StepView {
  title: string;
  sub: string;
  href: string;
}

function stepView(step: GuideStep, year: number, properties: readonly Property[], t: T): StepView {
  switch (step.kind) {
    case "angaben":
      return {
        title: t.stepAngaben,
        sub: t.stepAngabenSub,
        href: taxFormHref("angaben", { year, propertyId: null, ownerId: null }),
      };
    case "property":
      return {
        title: t.stepProperty(propertyLabel(properties, step.property_id, t)),
        sub: t.stepPropertySub,
        href: taxStepHref(year, "immobilie", step.property_id ?? null),
      };
    case "questions":
      return {
        title: t.stepQuestions,
        sub: step.total ? t.stepQuestionsSub(step.done ?? 0, step.total) : t.stepQuestionsNone,
        href: questionnaireHref(year),
      };
    case "documents":
      return {
        title: t.stepBelege,
        sub: step.total ? t.stepBelegeSub(step.done ?? 0, step.total) : t.stepBelegeNone,
        href: taxStepHref(year, "belege"),
      };
    default:
      return { title: t.stepReview, sub: t.stepReviewSub, href: taxStepHref(year, "pruefen") };
  }
}

function StepState({ step }: { step: GuideStep }) {
  const t = useT(messages);
  const f = useFormat();
  if (step.state === "done") {
    return <span className="badge">{step.kind === "review" ? t.stateReady : t.stateDone}</span>;
  }
  if (step.state === "waiting") {
    return <span className="badge badge-muted">{step.waiting_from ? t.stateWaiting(f.month(step.waiting_from)) : t.stateWaiting("…")}</span>;
  }
  return <span className="badge badge-warn">{t.stateOpen(step.open)}</span>;
}

function EstimateLine({ guide, year }: { guide: TaxGuide; year: number }) {
  const t = useT(messages);
  const f = useFormat();
  const estimate = guide.estimate;
  let text: string;
  if (!estimate) text = t.noEstimate;
  else if (isZeroAmount(estimate.result)) text = t.neither;
  else if (estimate.result.startsWith("-")) text = t.refund(f.money(abs(estimate.result)));
  else text = t.backPayment(f.money(estimate.result));
  return (
    <Link href={taxStepHref(year, "pruefen")} className="tax-guide-estimate">
      {text}
      {estimate ? <span className="muted"> · {estimate.status === "certified" ? t.estimateCertified : t.estimateRough}</span> : null} ›
    </Link>
  );
}

function StartPage({ guide, properties }: { guide: TaxGuide; properties: readonly Property[] }) {
  const t = useT(messages);
  const f = useFormat();
  const year = guide.tax_year;
  const total = guide.steps.length;
  const thisYear = new Date().getFullYear();
  const years = [...new Set([thisYear - 2, thisYear - 1, thisYear, year])].sort((a, b) => a - b);
  const next = guide.next_step != null ? guide.steps[guide.next_step] : null;
  const nextView = next ? stepView(next, year, properties, t) : null;
  let now: string;
  if (guide.ready) now = t.nowReady;
  else if (next?.kind === "questions") now = t.nowQuestions(next.open);
  else if (next?.kind === "documents") now = t.nowBelege(next.open);
  else if (next) now = t.nowOpen(next.open);
  else now = t.nowWaiting(guide.waiting_from ? f.month(guide.waiting_from) : "…");
  return (
    <div className="stack tax-guide">
      <div className="page-head tax-head">
        <div>
          <p className="eyebrow">{t.backLabel}</p>
          <h1>{t.eyebrow(year)}</h1>
        </div>
        <span className="spacer" />
        <nav className="tax-year-chips" aria-label={t.backLabel}>
          {years.map((y) => (
            <Link key={y} href={taxGuideHref(y)} aria-current={y === year ? "page" : undefined} className="tax-chip">
              {y}
            </Link>
          ))}
        </nav>
      </div>
      <section className="card tax-guide-head" aria-label={t.progress(guide.steps_done, total)}>
        <strong>{t.progress(guide.steps_done, total)}</strong>
        <div
          className="tax-bar"
          role="progressbar"
          aria-label={t.progress(guide.steps_done, total)}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={guide.steps_done}
        >
          <span style={{ width: total > 0 ? `${Math.min(100, (guide.steps_done / total) * 100)}%` : "0%" }} />
        </div>
        <p className="tax-guide-facts">
          <span>{t.deadline(f.date(guide.filing_deadline))}</span>
          <EstimateLine guide={guide} year={year} />
        </p>
        {(guide.unavailable ?? []).length > 0 ? <p className="muted">{t.unavailable}</p> : null}
      </section>
      <section className="card tax-guide-now" aria-labelledby="tax-guide-now">
        <h2 id="tax-guide-now">{t.nowTitle}</h2>
        <p>{now}</p>
        {nextView ? (
          <Link href={nextView.href} className="button-link">
            {t.nowOpenStep}: {nextView.title}
          </Link>
        ) : null}
      </section>
      <section aria-labelledby="tax-guide-steps">
        <h2 id="tax-guide-steps" className="visually-hidden">
          {t.stepsLabel}
        </h2>
        <ol className="detail-rows tax-guide-steps">
          {guide.steps.map((step, index) => {
            const view = stepView(step, year, properties, t);
            return (
              <li key={`${step.kind}-${step.property_id ?? ""}`}>
                <Link href={view.href} className="detail-row" aria-current={index === guide.next_step ? "step" : undefined}>
                  <span className="tax-guide-number" aria-hidden="true">
                    {index + 1}
                  </span>
                  <span className="detail-row-text">
                    <span className="detail-row-title">{view.title}</span>
                    <span className="detail-row-sub">{view.sub}</span>
                  </span>
                  <StepState step={step} />
                  <Icon name="chevronRight" size={18} />
                </Link>
              </li>
            );
          })}
        </ol>
      </section>
      <p className="tax-guide-forms">
        {t.formsLine} <Link href={taxFormHref("v", { year, propertyId: null, ownerId: null })}>Anlage V</Link> ·{" "}
        <Link href={taxFormHref("n", { year, propertyId: null, ownerId: null })}>Anlage N</Link> ·{" "}
        <span className="muted">{t.kapSoLater}</span>
      </p>
    </div>
  );
}

// --- one step's frame -------------------------------------------------------------------------

function StepHead({ guide, kind, propertyId, title }: { guide: TaxGuide; kind: GuideStep["kind"]; propertyId?: string | null; title: string }) {
  const t = useT(messages);
  const index = guide.steps.findIndex((s) => s.kind === kind && (kind !== "property" || s.property_id === propertyId));
  const step = index >= 0 ? guide.steps[index] : null;
  return (
    <div className="page-head tax-head">
      <div>
        <p className="eyebrow">
          <Link href={taxGuideHref(guide.tax_year)}>{t.back}</Link>
          {index >= 0 ? <> · {t.stepNumber(index + 1, guide.steps.length)}</> : null}
        </p>
        <h1>{title}</h1>
      </div>
      <span className="spacer" />
      {step ? <StepState step={step} /> : null}
    </div>
  );
}

// --- points as to-do entries ------------------------------------------------------------------

interface PointScope {
  year: number;
  properties: readonly Property[];
  owners: readonly Owner[];
  contexts: Map<string, PropertyContext> | undefined;
  /** Every checklist item the guide holds for a property (a deposit split names the tenancies by them). */
  items: (propertyId: string) => DocumentChecklistItem[];
  onChanged: () => void;
}

function pointKey(point: GuidePoint): string {
  const params = point.warning?.params ?? {};
  const sorted = Object.keys(params)
    .sort()
    .map((k) => `${k}=${params[k]}`)
    .join("&");
  return `${point.code}~${point.property_id ?? ""}~${point.owner_id ?? ""}~${point.item?.scope.id ?? ""}~${sorted}`;
}

function pointContext(point: GuidePoint, scope: PointScope, t: T): string | undefined {
  if (point.property_id) return propertyLabel(scope.properties, point.property_id, t);
  if (point.owner_id) return ownerName(scope.owners, point.owner_id) || undefined;
  return undefined;
}

/** One point as a to-do entry, with the action todos.tsx gives its code. */
function pointEntry(point: GuidePoint, scope: PointScope, t: T): TodoEntry | null {
  const key = pointKey(point);
  const context = pointContext(point, scope, t);
  if (point.code === "anlage_n.no_pay" && point.owner_id) {
    const n = translate(todosText);
    return {
      key,
      title: n.noPayTitle(scope.year),
      text: n.noPayText + noPayHint(),
      action: (
        <>
          <ReadUpload label={n.uploadPayslip} onUploaded={scope.onChanged} />
          <Link href={payInputsHref(scope.year, point.owner_id)}>{n.typeGrossPay}</Link>
          <NoPayButton ownerId={point.owner_id} year={scope.year} onDone={scope.onChanged} />
        </>
      ),
      context,
    };
  }
  const property = point.property_id ? scope.contexts?.get(point.property_id) : undefined;
  if (point.warning) {
    const ctx: GuideContext = {
      year: scope.year,
      propertyId: point.property_id ?? undefined,
      ownerId: point.owner_id ?? undefined,
      draft: property?.draft ?? undefined,
      payments: property?.payments,
    };
    return (
      warningEntry(point.warning, ctx, key, scope.onChanged, context) ?? {
        key,
        title: WARNING_TEXT[point.warning.code](point.warning.params ?? {}),
        text: null,
        action: null,
        context,
      }
    );
  }
  if (point.item && point.property_id) {
    return checklistEntry(
      point.item,
      scope.year,
      { propertyId: point.property_id, payments: property?.payments, items: scope.items(point.property_id), onChanged: scope.onChanged },
      context,
    );
  }
  return null;
}

function usePointScope(guide: TaxGuide, properties: readonly Property[], owners: readonly Owner[], onChanged: () => void) {
  const ids = useMemo(
    () => guide.steps.filter((s) => s.kind === "property" && s.property_id).map((s) => s.property_id as string),
    [guide.steps],
  );
  const contexts = usePropertyContexts(guide.tax_year, ids);
  const reloadContexts = contexts.reload;
  const changed = useCallback(() => {
    reloadContexts();
    onChanged();
  }, [reloadContexts, onChanged]);
  const scope: PointScope = {
    year: guide.tax_year,
    properties,
    owners,
    contexts: contexts.data,
    items: (propertyId) => [
      ...(guide.documents ?? []).filter((d) => d.property_id === propertyId && d.item).map((d) => d.item as DocumentChecklistItem),
      ...(guide.points ?? []).filter((p) => p.property_id === propertyId && p.item).map((p) => p.item as DocumentChecklistItem),
    ],
    onChanged: changed,
  };
  return { scope, contexts };
}

/** A step's own points as „Was noch fehlt“ (Eure Angaben and Immobilie). */
function StepPoints({ entries, empty, loading, titleId }: { entries: TodoEntry[]; empty: string; loading: boolean; titleId: string }) {
  return <TodoList titleId={titleId} entries={entries} empty={empty} loading={loading} />;
}

/** The points of Eure Angaben (phase 10 D290), at the top of Steuern › Eure Angaben. */
export function AngabenPoints({ year }: { year: number }) {
  const t = useT(messages);
  const guide = useGuide(year);
  const properties = useLoad(loadProperties);
  const owners = useLoad(loadOwners);
  const reload = guide.reload;
  const data = guide.data;
  if (!data) return null;
  return <AngabenPointList guide={data} properties={properties.data ?? []} owners={owners.data ?? []} onChanged={reload} empty={t.pointsNone} />;
}

function AngabenPointList({
  guide,
  properties,
  owners,
  onChanged,
  empty,
}: {
  guide: TaxGuide;
  properties: readonly Property[];
  owners: readonly Owner[];
  onChanged: () => void;
  empty: string;
}) {
  const t = useT(messages);
  const { scope } = usePointScope(guide, properties, owners, onChanged);
  const entries = (guide.points ?? [])
    .filter((p) => p.step === "angaben")
    .map((p) => pointEntry(p, scope, t))
    .filter((e): e is TodoEntry => e !== null);
  if (entries.length === 0) return null;
  return <StepPoints titleId="tax-angaben-points" entries={entries} empty={empty} loading={false} />;
}

// --- the Immobilie step ----------------------------------------------------------------------

function liveRows(rows: readonly TaxInput[]): TaxInput[] {
  const superseded = new Set(rows.map((r) => r.supersedes_id).filter(Boolean));
  return rows.filter((r) => !superseded.has(r.id) && !r.withdrawn);
}

/** Opens the `<details>` the URL's hash names, now and on every hash change. */
function useOpenAnchor() {
  useEffect(() => {
    function open() {
      const id = window.location.hash.slice(1);
      if (!id) return;
      const target = document.getElementById(id);
      if (target instanceof HTMLDetailsElement) {
        target.open = true;
        target.scrollIntoView({ block: "start" });
      }
    }
    open();
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, []);
}

function InputRow({
  id,
  title,
  value,
  why,
  change,
  children,
}: {
  id?: string;
  title: string;
  value: ReactNode;
  why: string;
  /** A link instead of the inline form (the input lives on another page). */
  change?: { href: string };
  children?: ReactNode;
}) {
  const t = useT(messages);
  return (
    <li className="tax-guide-input">
      <div className="tax-guide-input-head">
        <span className="detail-row-text">
          <span className="detail-row-title">{title}</span>
          <span className="detail-row-sub">{value}</span>
        </span>
        <details className="tax-guide-why">
          <summary aria-label={t.rowWhyLabel(title)}>{t.rowWhy}</summary>
          <p className="muted">{why}</p>
        </details>
        {change ? <Link href={change.href}>{t.rowChange}</Link> : null}
      </div>
      {children ? (
        <details id={id} className="tax-guide-change">
          <summary>{t.rowChange}</summary>
          <div className="stack">{children}</div>
        </details>
      ) : null}
    </li>
  );
}

function PropertyStep({
  guide,
  properties,
  propertyParam,
  onChanged,
}: {
  guide: TaxGuide;
  properties: readonly Property[];
  propertyParam: string | null;
  onChanged: () => void;
}) {
  const t = useT(messages);
  const steps = guide.steps.filter((s) => s.kind === "property");
  const propertyId = steps.find((s) => s.property_id === propertyParam)?.property_id ?? propertyParam ?? steps[0]?.property_id ?? null;
  const property = properties.find((p) => p.id === propertyId);
  if (!propertyId) {
    return (
      <div className="stack tax-guide">
        <StepHead guide={guide} kind="property" title={t.stepProperty(t.unknownProperty)} />
        <p className="muted">{t.propertyEmpty}</p>
      </div>
    );
  }
  return (
    <div className="stack tax-guide">
      <StepHead guide={guide} kind="property" propertyId={propertyId} title={t.stepProperty(property?.label ?? t.unknownProperty)} />
      <p className="muted">{t.propertyIntro}</p>
      <PropertyStepBody key={propertyId} guide={guide} properties={properties} propertyId={propertyId} property={property} onChanged={onChanged} />
    </div>
  );
}

function PropertyStepBody({
  guide,
  properties,
  propertyId,
  property,
  onChanged,
}: {
  guide: TaxGuide;
  properties: readonly Property[];
  propertyId: string;
  property: Property | undefined;
  onChanged: () => void;
}) {
  const t = useT(messages);
  const f = useFormat();
  const year = guide.tax_year;
  const { scope, contexts } = usePointScope(guide, properties, [], onChanged);
  const loadInputs = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/tax-inputs", { params: { path: { property_id: propertyId }, query: {} } })),
    [propertyId],
  );
  const inputs = useLoad(loadInputs);
  const reloadInputs = inputs.reload;
  const changed = useCallback(() => {
    reloadInputs();
    scope.onChanged();
  }, [reloadInputs, scope]);
  useOpenAnchor();

  const entries = (guide.points ?? [])
    .filter((p) => p.step === "property" && p.property_id === propertyId)
    .map((p) => pointEntry(p, scope, t))
    .filter((e): e is TodoEntry => e !== null);
  const live = liveRows(inputs.data ?? []);
  const of = (kind: TaxInput["kind"]) => live.filter((r) => r.kind === kind);
  const usefulLife = of("useful_life")[0];
  const movable = of("movable_asset");
  const renovation = of("price_renovation")[0];
  const schedules = of("grundsteuer_schedule");
  const schedule = schedules.find((r) => r.tax_year === year) ?? schedules[0];
  const draft = contexts.data?.get(propertyId)?.draft ?? null;
  const limit = draft?.limit_15 ?? null;
  const value = (v: ReactNode) => (inputs.loading && !inputs.data ? "…" : v);

  return (
    <>
      <StepPoints titleId="tax-step-points" entries={entries} empty={t.propertyEmpty} loading={contexts.loading && !contexts.data} />
      <section className="card stack" aria-labelledby="tax-guide-inputs">
        <h2 id="tax-guide-inputs">{t.inputsTitle}</h2>
        <ul className="tax-guide-inputs">
          <InputRow
            id="tax-useful-life"
            title={t.rowUsefulLife}
            why={t.usefulLifeWhy}
            value={value(
              usefulLife && "years" in usefulLife.value ? t.usefulLifeYears(String(usefulLife.value.years)) : t.usefulLifeStatutory,
            )}
          >
            <UsefulLifeSection propertyId={propertyId} defaultYear={year} onChanged={changed} />
          </InputRow>
          <InputRow
            id="tax-movable-asset"
            title={t.rowMovableAsset}
            why={t.movableAssetWhy}
            value={value(
              movable.length > 0
                ? movable.map((r) => ("amount" in r.value && "useful_life_years" in r.value ? f.money(r.value.amount) : "")).join(", ")
                : t.none,
            )}
          >
            <MovableAssetSection propertyId={propertyId} purchaseYear={draft?.header.transfer_date ? Number(draft.header.transfer_date.slice(0, 4)) : year} onChanged={changed} />
          </InputRow>
          <InputRow
            title={t.rowPriceRenovation}
            why={t.priceRenovationWhy}
            value={value(renovation && "amount_net" in renovation.value ? f.money(renovation.value.amount) : t.noneClaimed)}
            change={{
              href: `${taxFormHref("angaben", { year, propertyId, ownerId: null })}#${PRICE_RENOVATION_ANCHOR}`,
            }}
          />
          <InputRow
            id="tax-limit"
            title={t.rowLimit}
            why={t.limitWhy}
            value={limit && limit.limit != null ? t.limitUsed(f.money(limit.used_net), f.money(limit.limit)) : t.limitNone}
          >
            <LimitCard limit={limit} />
          </InputRow>
          <InputRow
            id="tax-grundsteuer"
            title={t.rowGrundsteuer}
            why={t.grundsteuerWhy}
            value={value(schedule && "annual_amount" in schedule.value ? f.money(schedule.value.annual_amount) : t.notEntered)}
          >
            <GrundsteuerSection propertyId={propertyId} defaultYear={year} />
          </InputRow>
          <InputRow
            title={t.rowHandover}
            why={t.handoverWhy}
            value={property ? f.date(property.purchase_date) : "…"}
            change={{ href: `/properties/${propertyId}?tab=kauf` }}
          />
        </ul>
        {!draft && contexts.data ? <p className="muted">{t.noDraft}</p> : null}
      </section>
    </>
  );
}

// --- the Belege step -------------------------------------------------------------------------

function documentTitle(doc: GuideDocument, year: number, t: T): string {
  if (doc.item) return requirementGuide(doc.item.code).title(doc.item, year);
  return t.wageCertificate;
}

function DocumentWhen({ doc }: { doc: GuideDocument }) {
  const t = useT(messages);
  const f = useFormat();
  if (doc.status === "missing" && doc.due_on) return <span className="badge badge-warn">{t.dueSince(f.month(doc.due_on))}</span>;
  if (doc.usual_from && doc.usual_to && doc.usual_from.slice(0, 7) !== doc.usual_to.slice(0, 7)) {
    return <span className="badge badge-muted">{t.usualBetween(f.month(doc.usual_from), f.month(doc.usual_to))}</span>;
  }
  if (doc.usual_from) return <span className="badge badge-muted">{t.usualIn(f.month(doc.usual_from))}</span>;
  return null;
}

function DocumentsStep({
  guide,
  properties,
  owners,
  onChanged,
}: {
  guide: TaxGuide;
  properties: readonly Property[];
  owners: readonly Owner[];
  onChanged: () => void;
}) {
  const t = useT(messages);
  const year = guide.tax_year;
  const { scope } = usePointScope(guide, properties, owners, onChanged);
  const docs = guide.documents ?? [];
  const missing = docs.filter((d) => d.status !== "present");
  const present = docs.filter((d) => d.status === "present");
  const context = (d: GuideDocument) =>
    d.property_id ? propertyLabel(properties, d.property_id, t) : d.owner_id ? ownerName(owners, d.owner_id) : "";
  // The bills that still need their proof of payment (a review point, D143): a Beleg too, so
  // they are uploaded here as well.
  const proofs = (guide.points ?? [])
    .filter((p) => p.code === "payment_proof.missing")
    .map((p) => pointEntry(p, scope, t))
    .filter((e): e is TodoEntry => e !== null);
  const stepPropertyIds = guide.steps.filter((s) => s.kind === "property" && s.property_id).map((s) => s.property_id as string);

  return (
    <div className="stack tax-guide">
      <StepHead guide={guide} kind="documents" title={t.stepBelege} />
      <p className="muted">{t.belegeIntro}</p>
      <section className="card stack" aria-labelledby="tax-guide-missing">
        <h2 id="tax-guide-missing">{t.missingTitle}</h2>
        {missing.length === 0 ? (
          <p className="muted">{t.noneMissing}</p>
        ) : (
          <ul className="tax-guide-docs">
            {missing.map((doc) => (
              <li key={`${doc.kind}-${doc.property_id ?? doc.owner_id ?? ""}-${doc.item?.scope.id ?? ""}`} className="tax-guide-doc">
                <div className="tax-guide-doc-head">
                  <span className="detail-row-text">
                    <span className="detail-row-title">
                      {documentTitle(doc, year, t)}
                      {context(doc) ? <span className="muted"> · {context(doc)}</span> : null}
                    </span>
                    {doc.sender ? <span className="detail-row-sub">{t.sender(doc.sender)}</span> : null}
                  </span>
                  <DocumentWhen doc={doc} />
                </div>
                <div className="row todo-actions">
                  {doc.item && doc.property_id
                    ? checklistAction(doc.item, {
                        propertyId: doc.property_id,
                        payments: scope.contexts?.get(doc.property_id)?.payments,
                        items: scope.items(doc.property_id),
                        onChanged: scope.onChanged,
                      })
                    : <ReadUpload label={t.uploadWageCertificate} onUploaded={scope.onChanged} />}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      {proofs.length > 0 ? <TodoList titleId="tax-guide-proofs" entries={proofs} empty={t.noneMissing} /> : null}
      <section className="card stack" aria-labelledby="tax-guide-present">
        <h2 id="tax-guide-present">{t.presentTitle}</h2>
        {present.length === 0 ? (
          <p className="muted">{t.nonePresent}</p>
        ) : (
          <ul className="tax-guide-present">
            {present.map((doc) => (
              <li key={`${doc.kind}-${doc.property_id ?? doc.owner_id ?? ""}-${doc.item?.scope.id ?? ""}`}>
                <span aria-hidden="true">✓</span> {documentTitle(doc, year, t)}
                {context(doc) ? <span className="muted"> · {context(doc)}</span> : null}
              </li>
            ))}
          </ul>
        )}
        <ReadUpload label={t.uploadBeleg} onUploaded={scope.onChanged} />
      </section>
      {stepPropertyIds.map((id) => (
        <details key={id} className="card tax-guide-all">
          <summary>{t.allDocumentsOf(propertyLabel(properties, id, t))}</summary>
          <ChecklistTab propertyId={id} year={year} payments={scope.contexts?.get(id)?.payments} onChanged={scope.onChanged} />
        </details>
      ))}
    </div>
  );
}

// --- Prüfen und abgeben ----------------------------------------------------------------------

function FormCard({ form, year, properties, owners }: { form: GuideFormResult; year: number; properties: readonly Property[]; owners: readonly Owner[] }) {
  const t = useT(messages);
  const f = useFormat();
  const title =
    form.form === "anlage_v" ? t.anlageVOf(propertyLabel(properties, form.property_id, t)) : t.anlageNOf(ownerName(owners, form.owner_id));
  const href =
    form.form === "anlage_v"
      ? taxFormHref("v", { year, propertyId: form.property_id ?? null, ownerId: null })
      : taxFormHref("n", { year, propertyId: null, ownerId: form.owner_id ?? null });
  return (
    <li>
      <Link href={href} className="detail-row">
        <span className="detail-row-text">
          <span className="detail-row-title">{title}</span>
          <span className="detail-row-sub">{form.result != null ? f.money(form.result) : t.formNoResult}</span>
        </span>
        {form.open > 0 ? <span className="badge badge-warn">{t.formOpen(form.open)}</span> : <span className="badge">{t.formDone}</span>}
        <Icon name="chevronRight" size={18} />
      </Link>
    </li>
  );
}

const POINT_KIND = "punkt";

function ReviewStep({
  guide,
  properties,
  owners,
  onChanged,
}: {
  guide: TaxGuide;
  properties: readonly Property[];
  owners: readonly Owner[];
  onChanged: () => void;
}) {
  const t = useT(messages);
  const year = guide.tax_year;
  const detail = useDetail(POINT_KIND);
  const { scope } = usePointScope(guide, properties, owners, onChanged);
  const points = guide.points ?? [];
  const keys: string[] = points.map(pointKey);
  const openIndex = detail.id ? keys.indexOf(detail.id) : -1;
  const openSteps = guide.steps.filter((s) => s.kind !== "review" && s.state !== "done");

  const panel = detail.id ? (
    <PointPanel
      point={points[openIndex] ?? null}
      index={openIndex}
      total={points.length}
      scope={scope}
      onClose={detail.close}
      onNext={openIndex >= 0 && points.length > 1 ? () => detail.open(keys[(openIndex + 1) % points.length] ?? "") : null}
    />
  ) : null;

  return (
    <DetailLayout panel={panel}>
      <div className="stack tax-guide">
        <StepHead guide={guide} kind="review" title={t.stepReview} />
        <p className="muted">{t.reviewIntro}</p>
        <section aria-labelledby="tax-guide-points" className="stack">
          <h2 id="tax-guide-points">{points.length > 0 ? t.pointsTitle(points.length) : t.pointsNone}</h2>
          {points.length > 0 ? (
            <ul className="detail-rows">
              {points.map((point) => {
                const entry = pointEntry(point, scope, t);
                return (
                  <li key={pointKey(point)}>
                    <a {...detail.linkProps(pointKey(point))} className="detail-row">
                      <span className="detail-row-text">
                        <span className="detail-row-title">{entry?.title ?? point.code}</span>
                        <span className="detail-row-sub">
                          {t.formLabel(point.form)}
                          {entry?.context ? ` · ${entry.context}` : ""}
                        </span>
                      </span>
                      <Icon name="chevronRight" size={18} />
                    </a>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </section>
        <section aria-labelledby="tax-guide-forms" className="stack">
          <h2 id="tax-guide-forms">{t.formsTitle}</h2>
          <ul className="detail-rows">
            {(guide.forms ?? []).map((form) => (
              <FormCard key={`${form.form}-${form.property_id ?? form.owner_id ?? ""}`} form={form} year={year} properties={properties} owners={owners} />
            ))}
          </ul>
        </section>
        <section aria-labelledby="tax-guide-result" className="stack">
          <h2 id="tax-guide-result">{t.resultTitle}</h2>
          <ReturnEstimateSummary year={year} />
          <p className="muted">{guide.ready ? t.resultFinal : t.resultChanges}</p>
        </section>
        <section className="card stack" aria-labelledby="tax-guide-elster">
          <h2 id="tax-guide-elster">{guide.ready ? t.readyTitle : t.elsterTitle}</h2>
          {guide.ready ? (
            <ol className="tax-guide-ready">
              <li>
                <strong>{t.ready1}</strong>
                <p className="muted">{t.ready1Text}</p>
                {(guide.forms ?? []).map((form) =>
                  form.form === "anlage_v" && form.property_id ? (
                    <HandOverCard key={form.property_id} propertyId={form.property_id} taxYear={year} titleId={`tax-guide-v-${form.property_id}`} />
                  ) : form.form === "anlage_n" && form.owner_id && form.result != null ? (
                    <AnlageNHandOver key={form.owner_id} ownerId={form.owner_id} year={year} ready declared={false} titleId={`tax-guide-n-${form.owner_id}`} />
                  ) : null,
                )}
              </li>
              <li>
                <strong>{t.ready2}</strong>
                <p className="muted">{t.ready2Text}</p>
              </li>
              <li>
                <strong>{t.ready3}</strong>
                <p className="muted">{t.ready3Text}</p>
              </li>
            </ol>
          ) : (
            <>
              <button type="button" disabled aria-describedby="tax-guide-elster-why">
                {t.elsterButton}
              </button>
              <div id="tax-guide-elster-why">
                <p>{t.elsterNotYet(points.length + openSteps.filter((s) => s.kind === "questions" || s.kind === "documents").reduce((n, s) => n + s.open, 0))}</p>
                {openSteps.length > 0 ? (
                  <p>
                    {t.elsterStepsOpen}{" "}
                    {openSteps.map((s, i) => {
                      const view = stepView(s, year, properties, t);
                      return (
                        <span key={`${s.kind}-${s.property_id ?? ""}`}>
                          {i > 0 ? " · " : null}
                          <Link href={view.href}>{view.title}</Link>
                        </span>
                      );
                    })}
                  </p>
                ) : null}
              </div>
              <p className="muted">{t.elsterAnyway}</p>
            </>
          )}
        </section>
      </div>
    </DetailLayout>
  );
}

/** One point, alone (phase 10 D290): its title, what the rule found, where it belongs, the
 * action, „Warum?“, „Später“ and „Nächster Punkt“. A to-do's `#tax-…` link (an anchor of the
 * Anlage V page until D290) goes to the Immobilie step, where those inputs live now. */
function PointPanel({
  point,
  index,
  total,
  scope,
  onClose,
  onNext,
}: {
  point: GuidePoint | null;
  index: number;
  total: number;
  scope: PointScope;
  onClose: () => void;
  onNext: (() => void) | null;
}) {
  const t = useT(messages);
  const n = useT(todosText);
  const router = useRouter();
  const entry = point ? pointEntry(point, scope, t) : null;
  const sentence = point?.warning ? WARNING_TEXT[point.warning.code](point.warning.params ?? {}) : point?.item ? requirementGuide(point.item.code).hint : null;

  function retarget(event: MouseEvent<HTMLDivElement>) {
    const anchor = (event.target as HTMLElement).closest("a");
    const href = anchor?.getAttribute("href");
    if (!anchor || !href || !href.startsWith("#") || href.startsWith("#d-")) return;
    event.preventDefault();
    router.push(`${taxStepHref(scope.year, "immobilie", point?.property_id ?? null)}${href}`);
  }

  return (
    <DetailPanel title={entry?.title ?? t.pointGone} backLabel={t.stepReview} onClose={onClose}>
      {point && entry ? (
        <div className="stack tax-guide-point">
          <p className="muted">
            {t.pointOf(index + 1, total)} · {t.formLabel(point.form)}
            {entry.context ? ` · ${entry.context}` : ""}
          </p>
          {sentence && sentence !== entry.title ? <p>{sentence}</p> : null}
          {entry.action ? (
            <div className="row todo-actions" onClickCapture={retarget}>
              {entry.action}
            </div>
          ) : null}
          {entry.text ? (
            <details className="todo-why">
              <summary>{n.why}</summary>
              <p className="todo-text">{entry.text}</p>
            </details>
          ) : null}
          <div className="row">
            <button type="button" className="secondary" onClick={onClose}>
              {t.later}
            </button>
            {onNext ? (
              <button type="button" onClick={onNext}>
                {t.nextPoint}
              </button>
            ) : null}
          </div>
        </div>
      ) : (
        <p className="muted">{t.pointGone}</p>
      )}
    </DetailPanel>
  );
}
