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
import { type FormEvent, useCallback, useEffect, useId, useMemo, useState, type MouseEvent, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodeText } from "@/api/errors";
import type { components } from "@/api/types";
import type { AllocationFact, AnlageVDraft, DocumentChecklistItem, Owner, Property } from "@/api/schemas";
import { ChecklistTab } from "@/app/(app)/properties/[id]/ChecklistTab";
import { formatter, translate, useFormat, useT } from "@/i18n";
import { getLocale } from "@/i18n/locale";
import { taxGuide as messages } from "@/i18n/taxGuide";
import { todosText } from "@/i18n/todos";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { Icon } from "@/lib/icons";

import { DetailLayout, DetailPanel, useDetail } from "./DetailPanel";
import { ReadUpload } from "./documents";
import { decimalInput, toMoneyString } from "./input";
import { HOUSEHOLD_SERVICE_CLASS, REPLACEMENT_BENEFIT, WARNING_TEXT } from "./labels";
import { NoPayButton, noPayHint } from "./noPay";
import { isZeroAmount, ReturnEstimateSummary } from "./returnEstimate";
import { useReturnInputsSync } from "./returnInputsSync";
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
  noteEntry,
  requirementGuide,
  type TodoEntry,
  TodoList,
  warningEntry,
} from "./todos";
import {
  guidePointKey,
  type GuideStepParam,
  POINT_DETAIL_KIND,
  propertyAnchorHref,
  payInputsHref,
  questionnaireHref,
  taxFormHref,
  taxGuideHref,
  taxStepHref,
} from "./taxYear";
import { ActionDialog, Loading } from "./ui";
import { useLoad, type Loaded } from "./useLoad";

import "./tax.css";
import "./taxGuide.css";

type TaxGuide = components["schemas"]["TaxGuide"];
type GuideStep = components["schemas"]["GuideStep"];
type GuidePoint = components["schemas"]["GuidePoint"];
type GuideDocument = components["schemas"]["GuideDocument"];
type GuideFormResult = components["schemas"]["GuideFormResult"];
type BookedProposal = components["schemas"]["BookedProposal"];
type ReturnInputCreate = components["schemas"]["ReturnInputCreate"];
type T = ReturnType<typeof translate<typeof messages.de>>;

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
    // Without a month: the review step while another step is not done yet („noch nicht“).
    return <span className="badge badge-muted">{step.waiting_from ? t.stateWaiting(f.month(step.waiting_from)) : t.stateNotYet}</span>;
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

/** Why the year cannot be finished, when a source could not be read (`unavailable`): the
 *  rules for the year are missing (tax.no_rule_set), or some other part refused. */
function unavailableText(guide: TaxGuide, t: T): string | null {
  const codes = guide.unavailable ?? [];
  if (codes.length === 0) return null;
  return codes.some((c) => c.endsWith(":tax.no_rule_set")) ? t.nowUnavailable(guide.tax_year) : t.nowUnavailableOther(guide.tax_year);
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
  // Nothing to do now, yet the year cannot be computed: say so instead of „Alles ist geklärt“.
  else now = unavailableText(guide, t) ?? t.nowWaiting(guide.waiting_from ? f.month(guide.waiting_from) : "…");
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
        {(guide.unavailable ?? []).length > 0 && next ? <p className="muted">{t.unavailable}</p> : null}
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

/** `listed`: the points the page lists, when it lists more than the step's own (Prüfen und
 * abgeben lists every point, so its badge counts the same list). */
function StepHead({
  guide,
  kind,
  propertyId,
  title,
  listed,
}: {
  guide: TaxGuide;
  kind: GuideStep["kind"];
  propertyId?: string | null;
  title: string;
  listed?: number;
}) {
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
      {listed ? <span className="badge badge-warn">{t.stateOpen(listed)}</span> : step ? <StepState step={step} /> : null}
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
  return guidePointKey({
    code: point.code,
    property_id: point.property_id,
    owner_id: point.owner_id,
    item_scope_id: point.item?.scope.id,
    // A proposal is named by its payments (digested, so no payee lands in the URL) and its
    // amount: a row that covers part of it gives a new point, and a fresh card.
    params: point.proposal
      ? { group: point.proposal.group, difference: point.proposal.difference }
      : point.refused
        ? { refused: point.refused }
        : point.warning?.params,
    refs: point.proposal ? point.proposal.allocation_ids.map((id) => ({ kind: "allocation", id })) : point.warning?.refs,
  });
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
  if (point.code === "anlage_n.benefit_letter" && point.owner_id) {
    // Announced in the Jahresfragen, not typed yet (the estimate's `benefit_letter`): the
    // Lohnersatzleistungen form of Eure Angaben, as the Gesamt card's „Es fehlt noch“ links it.
    return {
      key,
      title: t.benefitLetterTitle,
      text: t.benefitLetterText,
      action: (
        <Link href={`${taxFormHref("angaben", { year: scope.year, propertyId: null, ownerId: point.owner_id })}#eingabe-replacement_income`}>
          {t.benefitLetterAction}
        </Link>
      ),
      context,
    };
  }
  if (point.proposal) return proposalEntry(point.proposal, key, scope, t, context);
  if (point.code === "return_input.refused") {
    // An input the rules refuse (rules/tax_guide.py REFUSAL_CODES): the rule's own sentence,
    // and the tab where the input is changed.
    return {
      key,
      title: (point.refused ? ruleCodeText(point.refused) : null) ?? t.refusedTitle,
      text: t.refusedText,
      action: (
        <Link href={taxFormHref("angaben", { year: scope.year, propertyId: null, ownerId: point.owner_id ?? null })}>{t.refusedAction}</Link>
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
    // A note of a form that carries an action is listed with it (rules/tax_guide.py NOTE_STEP).
    const entry = point.note
      ? noteEntry(point.warning, ctx, key, scope.onChanged, context)
      : warningEntry(point.warning, ctx, key, scope.onChanged, context);
    return (
      entry ?? {
        key,
        title: WARNING_TEXT[point.warning.code](point.warning.params ?? {}),
        text: null,
        action: null,
        context,
      }
    );
  }
  if (point.item && point.property_id) {
    const checklistCtx = {
      propertyId: point.property_id,
      payments: property?.payments,
      items: scope.items(point.property_id),
      onChanged: scope.onChanged,
    };
    // The server counts this point, so it is never dropped: an item todos.tsx lists no to-do for
    // keeps its requirement's title and action.
    return (
      checklistEntry(point.item, scope.year, checklistCtx, context) ?? {
        key,
        title: requirementGuide(point.item.code).title(point.item, scope.year),
        text: requirementGuide(point.item.code).hint,
        action: checklistAction(point.item, checklistCtx),
        context,
      }
    );
  }
  return null;
}

// --- bookings that propose a tax input (phase 10 §7.8 (a), D293) ----------------------------

/** A proposal as a card: what it is and the amount, its explanation behind „Warum?“, and the
 * buttons. Nothing counts before „Übernehmen“ (principle 3), which posts the server's payload
 * with the fields the bookings cannot say. */
function proposalEntry(proposal: BookedProposal, key: string, scope: PointScope, t: T, context: string | undefined): TodoEntry {
  const amount = formatter(getLocale()).money(proposal.difference);
  const payee = proposal.payee ?? null;
  const [title, text] =
    proposal.group === "benefit"
      ? [t.proposalBenefit(amount), t.proposalBenefitText]
      : proposal.group === "donation"
        ? [t.proposalDonation(payee, amount), t.proposalDonationText]
        : proposal.group === "childcare"
          ? [t.proposalChildcare(payee, amount), t.proposalChildcareText]
          : [t.proposalServices(payee, amount), t.proposalServicesText];
  return {
    key,
    title,
    text,
    // Keyed by the point, so the „taken“ state never passes to the proposal that moves into its
    // place.
    action: <ProposalAction key={key} proposal={proposal} owners={scope.owners} onChanged={scope.onChanged} />,
    context,
  };
}

/** The benefits a booking can be: Kurzarbeitergeld is on the wage certificate and never
 * proposed from the account (phase 6 D159 amended (8)). */
const BOOKED_BENEFITS = (Object.keys(REPLACEMENT_BENEFIT) as (keyof typeof REPLACEMENT_BENEFIT)[]).filter(
  (b) => b !== "kurzarbeitergeld",
);
/** The 409 constraint of a row taken from a card the guide no longer shows (D293 review). */
const PROPOSAL_STALE = "return_input.proposal_stale";
const SERVICE_CLASSES = Object.keys(HOUSEHOLD_SERVICE_CLASS) as (keyof typeof HOUSEHOLD_SERVICE_CLASS)[];

function ProposalAction({ proposal, owners, onChanged }: { proposal: BookedProposal; owners: readonly Owner[]; onChanged: () => void }) {
  const t = useT(messages);
  // Once saved, „Übernehmen“ stays off until the guide has reloaded without this proposal: the
  // server takes a second identical row for these kinds, which would count the sum twice.
  const [taken, setTaken] = useState(false);
  return (
    <>
      <ActionDialog label={taken ? t.saved : t.take} title={t.takeTitle} buttonClass="" disabled={taken}>
        {(close) => (
          <ProposalForm
            proposal={proposal}
            owners={owners}
            onDone={() => {
              setTaken(true);
              close();
              onChanged();
            }}
            onStale={() => {
              // The card was out of date (taken already, or the bookings changed): the server
              // wrote nothing, so reload the guide and show the proposals as they are now.
              close();
              onChanged();
            }}
            onCancel={close}
          />
        )}
      </ActionDialog>
      <PaymentsDialog proposal={proposal} />
      {proposal.receipt_needed ? <ReadUpload label={t.uploadReceipt} onUploaded={onChanged} /> : null}
      {proposal.group === "household_services" ? <ReadUpload label={t.uploadInvoice} onUploaded={onChanged} /> : null}
    </>
  );
}

/** „Zahlungen ansehen“: the payments the proposal sums, by date, payee and amount (never the
 * purpose text, which the server does not send). */
function PaymentsDialog({ proposal }: { proposal: BookedProposal }) {
  const t = useT(messages);
  const f = useFormat();
  const lines = proposal.lines ?? [];
  if (lines.length === 0) return null;
  return (
    <ActionDialog label={t.showPayments(lines.length)} title={t.paymentsTitle} buttonClass="link">
      {() => (
        <ul className="tax-guide-payments">
          {lines.map((line) => (
            <li key={line.allocation_id} className="row">
              <span>{f.date(line.booking_date)}</span>
              <span>{line.payee ?? t.noPayee}</span>
              <span className="spacer" />
              <span>{f.money(line.amount)}</span>
            </li>
          ))}
        </ul>
      )}
    </ActionDialog>
  );
}

function ProposalForm({
  proposal,
  owners,
  onDone,
  onStale,
  onCancel,
}: {
  proposal: BookedProposal;
  owners: readonly Owner[];
  onDone: () => void;
  onStale: () => void;
  onCancel: () => void;
}) {
  const t = useT(messages);
  const f = useFormat();
  const ids = useId();
  const needs = new Set(proposal.needs ?? []);
  const services = proposal.group === "household_services";
  // §35a: the bank line is the whole invoice, so the labour share starts empty and is typed from
  // the invoice; every other group starts at the booked amount, which the person may change.
  const [amount, setAmount] = useState(services ? "" : decimalInput(proposal.difference));
  const [owner, setOwner] = useState(proposal.owner_id ?? "");
  const [benefit, setBenefit] = useState(proposal.benefit ?? "");
  const [child, setChild] = useState("");
  const [serviceClass, setServiceClass] = useState("");
  const [recipient, setRecipient] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setProblem(null);
    setError(null);
    const parsed = toMoneyString(amount);
    if (!parsed.ok) {
      setProblem(parsed.message);
      return;
    }
    if (
      (needs.has("owner") && !owner) ||
      (needs.has("benefit") && !benefit) ||
      (needs.has("child") && !child) ||
      (needs.has("service_class") && !serviceClass) ||
      (needs.has("recipient") && !recipient.trim())
    ) {
      setProblem(t.missingField);
      return;
    }
    const payload = proposal.payload as { value?: Record<string, unknown> } & Record<string, unknown>;
    const value: Record<string, unknown> = { ...(payload.value ?? {}) };
    if (services) value.labour_cost = parsed.value;
    else value.amount = parsed.value;
    if (needs.has("benefit")) value.benefit = benefit;
    if (needs.has("child")) value.child_id = child;
    if (needs.has("service_class")) value.service_class = serviceClass;
    if (needs.has("recipient")) value.recipient_label = recipient.trim();
    // What the card showed: the server refuses the row (409) unless the proposal still matches.
    const from_proposal = {
      group: proposal.group,
      allocation_ids: proposal.allocation_ids,
      difference: proposal.difference,
    };
    const body = {
      ...payload,
      value,
      from_proposal,
      ...(needs.has("owner") ? { owner_id: owner } : {}),
    } as unknown as ReturnInputCreate;
    // Off while the request runs; the card's button stays off after it (ProposalAction).
    setBusy(true);
    try {
      await ok(api.POST("/api/return-inputs", { body }));
      onDone();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409 && caught.body?.constraint === PROPOSAL_STALE) {
        onStale();
        return;
      }
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={(event) => void submit(event)}>
      {needs.has("owner") ? (
        <label htmlFor={`${ids}-owner`}>
          {t.ownerAsk}
          <select id={`${ids}-owner`} value={owner} onChange={(event) => setOwner(event.target.value)}>
            <option value="">{t.pick}</option>
            {owners.map((o) => (
              <option key={o.id} value={o.id}>
                {o.full_name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {needs.has("benefit") ? (
        <label htmlFor={`${ids}-benefit`}>
          {t.benefitAsk}
          <select id={`${ids}-benefit`} value={benefit} onChange={(event) => setBenefit(event.target.value)}>
            <option value="">{t.pick}</option>
            {BOOKED_BENEFITS.map((b) => (
              <option key={b} value={b}>
                {REPLACEMENT_BENEFIT[b]}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {needs.has("child") ? (
        <label htmlFor={`${ids}-child`}>
          {t.childAsk}
          <select id={`${ids}-child`} value={child} onChange={(event) => setChild(event.target.value)}>
            <option value="">{t.pick}</option>
            {(proposal.child_choices ?? []).map((c, index) => (
              <option key={c.id} value={c.id}>
                {t.childOption(index + 1, f.month(c.born_on.slice(0, 7)))}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {needs.has("service_class") ? (
        <label htmlFor={`${ids}-class`}>
          {t.serviceClassAsk}
          <select id={`${ids}-class`} value={serviceClass} onChange={(event) => setServiceClass(event.target.value)}>
            <option value="">{t.pick}</option>
            {SERVICE_CLASSES.map((c) => (
              <option key={c} value={c}>
                {HOUSEHOLD_SERVICE_CLASS[c]}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {needs.has("recipient") ? (
        <label htmlFor={`${ids}-recipient`}>
          {t.recipientAsk}
          <input id={`${ids}-recipient`} value={recipient} maxLength={120} onChange={(event) => setRecipient(event.target.value)} />
        </label>
      ) : null}
      <label htmlFor={`${ids}-amount`}>
        {services ? t.labourCost : t.amount}
        <input id={`${ids}-amount`} inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} />
      </label>
      {services ? <p className="muted">{t.billSum(f.money(proposal.difference))}</p> : null}
      {proposal.group === "childcare" ? <p className="muted">{t.childcareHint}</p> : null}
      {problem ? (
        <p className="notice notice-error" role="alert">
          {problem}
        </p>
      ) : null}
      <ErrorNotice error={error} />
      <div className="row">
        <button type="submit" disabled={busy}>
          {busy ? t.saving : t.take}
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          {t.cancel}
        </button>
      </div>
    </form>
  );
}

/** The ids of the properties whose draft the shown points or Belege need (their to-do actions
 * read the draft and its payments), so a step drafts no property it does not show. */
function propertyIdsOf(points: readonly GuidePoint[], documents: readonly GuideDocument[] = []): string[] {
  const ids = new Set<string>();
  for (const p of points) if (p.property_id) ids.add(p.property_id);
  for (const d of documents) if (d.property_id) ids.add(d.property_id);
  return [...ids].sort();
}

function usePointScope(
  guide: TaxGuide,
  properties: readonly Property[],
  owners: readonly Owner[],
  onChanged: () => void,
  propertyIds: readonly string[],
) {
  const idKey = propertyIds.join(",");
  const ids = useMemo(() => (idKey ? idKey.split(",") : []), [idKey]);
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
  // A proposal taken over here adds a row to the blocks below, and a row typed there ends a
  // proposal here (components/returnInputsSync.ts).
  const changed = useReturnInputsSync(guide.reload);
  const data = guide.data;
  if (!data) return null;
  return <AngabenPointList guide={data} properties={properties.data ?? []} owners={owners.data ?? []} onChanged={changed} empty={t.pointsNone} />;
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
  const points = (guide.points ?? []).filter((p) => p.step === "angaben");
  const { scope } = usePointScope(guide, properties, owners, onChanged, propertyIdsOf(points));
  const t = useT(messages);
  const entries = points
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

/** Opens the `<details>` the URL's hash names: on arrival, on a hash change, and on a click of a
 * same-page `#…` link (a Next `<Link>` moves the hash by pushState, which fires no hashchange). */
function useOpenAnchor() {
  useEffect(() => {
    function openId(id: string) {
      if (!id || id.startsWith("d-")) return;
      const target = document.getElementById(id);
      if (target instanceof HTMLDetailsElement) {
        target.open = true;
        target.scrollIntoView({ block: "start" });
      }
    }
    function fromHash() {
      openId(window.location.hash.slice(1));
    }
    function onClick(event: globalThis.MouseEvent) {
      const anchor = (event.target as Element | null)?.closest?.("a");
      const href = anchor?.getAttribute("href");
      if (href && href.startsWith("#")) openId(decodeURIComponent(href.slice(1)));
    }
    fromHash();
    window.addEventListener("hashchange", fromHash);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("hashchange", fromHash);
      document.removeEventListener("click", onClick, true);
    };
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
  change?: { href: string; label?: string };
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
        {change ? <Link href={change.href}>{change.label ?? t.rowChange}</Link> : null}
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
  // This property's draft (the 15 % limit, the kitchen's purchase year) and its points' actions.
  const { scope, contexts } = usePointScope(guide, properties, [], onChanged, [propertyId]);
  const loadInputs = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/tax-inputs", { params: { path: { property_id: propertyId }, query: {} } })),
    [propertyId],
  );
  const inputs = useLoad(loadInputs);
  const reloadInputs = inputs.reload;
  const scopeChanged = scope.onChanged;
  const changed = useCallback(() => {
    reloadInputs();
    scopeChanged();
  }, [reloadInputs, scopeChanged]);
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
  // The repair and renovation elections are answered on the payment (allocations.tsx), so this
  // row counts them and leads to the cost lines; the comparable rent is entered per flat.
  const elections = live.filter(
    (r) => (r.kind === "spread_82b" || r.kind === "renovation_treatment" || r.kind === "exclude_from_15pct") && (r.tax_year == null || r.tax_year <= year),
  );
  const marketRents = of("market_rent");
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
            title={t.rowElections}
            why={t.electionsWhy}
            value={value(t.electionsValue(elections.length))}
            change={{ href: taxFormHref("v", { year, propertyId, ownerId: null }), label: t.viewCosts }}
          />
          <InputRow
            title={t.rowMarketRent}
            why={t.marketRentWhy}
            value={value(
              marketRents.length > 0
                ? t.marketRentValue(
                    marketRents.map((r) => ("cold_rent_per_m2" in r.value ? f.money(r.value.cold_rent_per_m2) : "")).join(", "),
                  )
                : t.notEntered,
            )}
            change={{ href: `/properties/${propertyId}`, label: t.toUnits }}
          />
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
  const docs = guide.documents ?? [];
  const proofPoints = (guide.points ?? []).filter((p) => p.code === "payment_proof.missing");
  // The full checklists below load on their own: the counter reloads them after any change here.
  const [refresh, setRefresh] = useState(0);
  const changed = useCallback(() => {
    setRefresh((n) => n + 1);
    onChanged();
  }, [onChanged]);
  const { scope } = usePointScope(guide, properties, owners, changed, propertyIdsOf(proofPoints, docs));
  const missing = docs.filter((d) => d.status !== "present");
  const present = docs.filter((d) => d.status === "present");
  const context = (d: GuideDocument) =>
    d.property_id ? propertyLabel(properties, d.property_id, t) : d.owner_id ? ownerName(owners, d.owner_id) : "";
  // The bills that still need their proof of payment (a review point, D143): a Beleg too, so
  // they are uploaded here as well.
  const proofs = proofPoints
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
          <ChecklistTab propertyId={id} year={year} refresh={refresh} payments={scope.contexts?.get(id)?.payments} onChanged={scope.onChanged} />
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
        {/* „fertig“ only under a result: a form with no draft for the year is not done */}
        {form.open > 0 ? (
          <span className="badge badge-warn">{t.formOpen(form.open)}</span>
        ) : form.result == null ? (
          <span className="badge badge-muted">{t.stateNotYet}</span>
        ) : (
          <span className="badge">{t.formDone}</span>
        )}
        <Icon name="chevronRight" size={18} />
      </Link>
    </li>
  );
}

const POINT_KIND = POINT_DETAIL_KIND;

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
  const points = guide.points ?? [];
  const { scope } = usePointScope(guide, properties, owners, onChanged, propertyIdsOf(points));
  const keys: string[] = points.map(pointKey);
  const openIndex = detail.id ? keys.indexOf(detail.id) : -1;
  const openSteps = guide.steps.filter((s) => s.kind !== "review" && s.state !== "done");
  // A note or a proposal a person may rightly leave is listed but never counted (GuidePoint.blocking).
  const blocking = points.filter((p) => p.blocking !== false).length;
  const stillOpen = blocking + openSteps.filter((s) => s.kind === "questions" || s.kind === "documents").reduce((n, s) => n + s.open, 0);
  // Nothing left to clear, yet a part of the year cannot be computed: say that, not „0 Punkte“.
  const elsterWhy = (stillOpen === 0 ? unavailableText(guide, t) : null) ?? t.elsterNotYet(stillOpen);
  // The open point, remembered: once it is cleared (the guide reloads without it), „Nächster
  // Punkt“ offers the next point of the same kind on the same property (a payment's twin, which
  // the reload may have moved), else the point that moved into its place, so the flow never
  // dead-ends.
  const [last, setLast] = useState<{ index: number; code: string; propertyId: string | null }>({ index: 0, code: "", propertyId: null });
  const opened = openIndex >= 0 ? points[openIndex] : undefined;
  // Kept during render, not in an effect: React re-renders at once with the new value.
  if (opened && (openIndex !== last.index || opened.code !== last.code || (opened.property_id ?? null) !== last.propertyId)) {
    setLast({ index: openIndex, code: opened.code, propertyId: opened.property_id ?? null });
  }
  const twin = points.findIndex((p) => p.code === last.code && (p.property_id ?? null) === last.propertyId);
  const nextIndex =
    points.length === 0
      ? -1
      : openIndex < 0
        ? twin >= 0
          ? twin
          : Math.min(last.index, points.length - 1)
        : points.length > 1
          ? (openIndex + 1) % points.length
          : -1;
  const nextKey = nextIndex >= 0 ? keys[nextIndex] : undefined;

  const panel = detail.id ? (
    <PointPanel
      point={points[openIndex] ?? null}
      index={openIndex}
      total={points.length}
      scope={scope}
      onClose={detail.close}
      onNext={nextKey !== undefined ? () => detail.open(nextKey) : null}
    />
  ) : null;

  return (
    <DetailLayout panel={panel}>
      <div className="stack tax-guide">
        <StepHead guide={guide} kind="review" title={t.stepReview} listed={blocking} />
        <p className="muted">{t.reviewIntro}</p>
        <section aria-labelledby="tax-guide-points" className="stack">
          <h2 id="tax-guide-points">{blocking > 0 ? t.pointsTitle(blocking) : points.length > 0 ? t.suggestionsTitle : t.pointsNone}</h2>
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
                    <p key={form.owner_id}>
                      {/* the form view decides whether the sheet can be made (a „Kein Arbeitslohn“ year makes none) */}
                      <Link href={`${taxFormHref("n", { year, propertyId: null, ownerId: form.owner_id })}#tax-n-handover`}>
                        {t.anlageNSheetOf(ownerName(owners, form.owner_id))}
                      </Link>
                    </p>
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
                <p>{elsterWhy}</p>
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
              <p className="muted">
                {t.elsterAnyway}{" "}
                <Link href={`${taxFormHref("v", { year, propertyId: null, ownerId: null })}#tax-handover-title`}>Anlage V</Link>
                {" · "}
                <Link href={taxFormHref("n", { year, propertyId: null, ownerId: null })}>Anlage N</Link>
              </p>
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
    const propertyId = point?.property_id ?? null;
    // An input of the Immobilie step opens there; any other anchor (the field sheet) is the
    // Anlage V view's.
    router.push(
      propertyAnchorHref(scope.year, propertyId, href) ?? `${taxFormHref("v", { year: scope.year, propertyId, ownerId: null })}${href}`,
    );
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
        <div className="stack tax-guide-point">
          <p className="muted">{t.pointGone}</p>
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
      )}
    </DetailPanel>
  );
}
