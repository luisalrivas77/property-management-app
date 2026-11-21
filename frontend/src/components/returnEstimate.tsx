"use client";

// The Gesamt card of Steuern › one tax year (phase-4b.md §10, D101): the household's whole
// return as GET /api/tax/return-estimate computes it on every read — refund or back-payment,
// the §46 filing check with its deadline, the Jahresfragen progress, and a fold-out with the
// chain from the Einkünfte to the result. Every euro figure is the backend's (principle 1);
// this card formats, names and links, it adds nothing up. Each line of the fold-out opens its
// own sources (OwnerIncome.sources, PropertyIncome.sources, ReturnEstimate.line_sources); the
// headline button opens all of them at once (ReturnEstimate.sources, their union).
import Link from "next/link";
import { useCallback, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type { Owner, Property, TaxSourceRef } from "@/api/schemas";
import type { components } from "@/api/types";
import { translate, useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { returnEstimate as messages } from "@/i18n/returnEstimate";

import { notModelledLabel, RETURN_STATUS, WARNING_TEXT } from "./labels";
import { SourcesButton } from "./tax";
import { questionnaireHref, taxFormHref } from "./taxYear";
import { WarningActionView, WhatToDo } from "./todos";
import { useLoad, type Loaded } from "./useLoad";

export type ReturnEstimate = components["schemas"]["ReturnEstimate"];
type MissingItem = components["schemas"]["MissingItem"];
type T = Translation<(typeof messages)["de"]>;

/** "0", "0.00", "-0.0": a decimal string that is zero. */
export function isZeroAmount(value: string | null | undefined): boolean {
  return value == null || /^-?0*(\.0*)?$/.test(value);
}

/** "-2426.00" → "2426.00": the amount of a refund, whose direction the words already say. */
function magnitude(value: string): string {
  return value.replace(/^-/, "");
}

/** The household's estimate of `year`; null when the year has no rule set (tax.no_rule_set). */
async function loadEstimate(year: number): Promise<ReturnEstimate | null> {
  try {
    return await ok(api.GET("/api/tax/return-estimate", { params: { query: { tax_year: year } } }));
  } catch (error) {
    if (error instanceof ApiError && error.body && ruleCodes(error.body).includes("tax.no_rule_set")) return null;
    throw error;
  }
}

export function useReturnEstimate(year: number): Loaded<ReturnEstimate | null> {
  const load = useCallback(() => loadEstimate(year), [year]);
  return useLoad(load);
}

const loadOwners = () => ok(api.GET("/api/owners"));
const loadProperties = () => ok(api.GET("/api/properties"));

/** The Gesamt card loading its own estimate (the Anlage V tab, which never changes it). */
export function ReturnEstimateSummary({ year }: { year: number }) {
  const estimate = useReturnEstimate(year);
  return <ReturnEstimateCard year={year} state={estimate} />;
}

export function ReturnEstimateCard({ year, state }: { year: number; state: Loaded<ReturnEstimate | null> }) {
  const t = useT(messages);
  const owners = useLoad(loadOwners);
  const properties = useLoad(loadProperties);
  if (state.data === undefined) {
    if (state.error == null) return null;
    return (
      <section className="card tax-estimate" aria-label={t.total}>
        <p className="muted">{t.failed}</p>
        <button type="button" className="secondary" onClick={state.reload}>
          {t.retry}
        </button>
      </section>
    );
  }
  if (state.data === null) {
    return (
      <section className="card tax-estimate" aria-label={t.total}>
        <p className="muted">{t.noRuleSet(year)}</p>
      </section>
    );
  }
  return (
    <EstimateView
      estimate={state.data}
      owners={owners.data ?? []}
      properties={properties.data ?? []}
      onChanged={state.reload}
    />
  );
}

/** Why a refund or back-payment arises, from the two owners' Steuerklassen (Q-O18): the class
 * sets only the monthly Lohnsteuer, never the year's tax, so this is a sentence and no figure.
 * Null unless both owners have a class and the pair is one of the married combinations. */
export function taxClassSentence(estimate: ReturnEstimate): string | null {
  const classes = (estimate.income_n ?? []).map((i) => i.tax_class).filter((c): c is NonNullable<typeof c> => c != null);
  if (classes.length !== 2) return null;
  const pair = [...classes].sort().join("/");
  const t = translate(messages);
  if (pair === "iii/v") return t.classesIiiV;
  if (classes.includes("iv_factor") && classes.every((c) => c === "iv" || c === "iv_factor")) {
    return t.classesIvFactor;
  }
  if (pair === "iv/iv") {
    return t.classesIvIv;
  }
  return null;
}

function ownerName(owners: Owner[], id: string | null | undefined, t: T): string {
  return owners.find((o) => o.id === id)?.full_name ?? t.owner;
}

function EstimateView({
  estimate,
  owners,
  properties,
  onChanged,
}: {
  estimate: ReturnEstimate;
  owners: Owner[];
  properties: Property[];
  onChanged: () => void;
}) {
  const t = useT(messages);
  const f = useFormat();
  const year = estimate.tax_year;
  const result = estimate.result;
  const headline = isZeroAmount(result) ? t.neither : result.startsWith("-") ? t.refund : t.backPayment;
  const filing = estimate.filing_required;
  const progress = estimate.questionnaire;
  const classes = taxClassSentence(estimate);
  const shown = (estimate.warnings ?? []).filter(
    (w) => w.code !== "return.filing_required" && w.code !== "return.questionnaire_open",
  );

  return (
    <section className="card tax-estimate" aria-label={t.total}>
      <p className="eyebrow">{t.eyebrow(year)}</p>
      <div className="row tax-estimate-head">
        <p className="tax-estimate-result">
          {headline}
          {isZeroAmount(result) ? null : (
            <strong className={result.startsWith("-") ? "tax-estimate-refund" : undefined}> {f.money(magnitude(result))}</strong>
          )}
        </p>
        <span className="badge">{RETURN_STATUS[estimate.status]}</span>
        <span className="spacer" />
        <SourcesButton refs={estimate.sources ?? []} title={t.sourcesTitle(year)} />
      </div>
      <p className="tax-subline">
        {filing.required
          ? t.filingRequired((filing.reasons ?? []).join("; "), f.date(filing.deadline))
          : t.filingVoluntary((filing.reasons ?? []).join("; "))}
      </p>
      {classes ? <p className="tax-subline tax-estimate-classes">{classes}</p> : null}
      <p className="tax-subline">
        {t.questions(year, progress.answered, progress.total)}{" "}
        <Link href={questionnaireHref(year)}>{progress.answered < progress.total ? t.continue : t.view}</Link>
      </p>
      <details className="tax-estimate-details">
        <summary>{t.howCalculated}</summary>
        <Breakdown estimate={estimate} owners={owners} properties={properties} />
      </details>
      <Missing items={estimate.missing ?? []} owners={owners} year={year} />
      {(estimate.reminders ?? []).length > 0 ? (
        <div className="tax-estimate-block">
          <p className="tax-tracked-head">{t.byHand}</p>
          <ul className="tax-estimate-list">
            {(estimate.reminders ?? []).map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {shown.length > 0 ? (
        <ul className="tax-estimate-list">
          {shown.map((warning, index) => (
            <li key={`${warning.code}-${index}`}>
              <span className={warning.severity === "warning" ? "badge badge-warn" : "badge badge-info"}>
                {warning.severity === "warning" ? t.check : t.note}
              </span>{" "}
              {WARNING_TEXT[warning.code](warning.params ?? {})}
              <WhatToDo warning={warning} />
              <WarningActionView warning={warning} ctx={{ year }} onChanged={onChanged} />
            </li>
          ))}
        </ul>
      ) : null}
      {(estimate.not_modelled ?? []).length > 0 ? (
        <p className="muted tax-subline">
          {t.notConsidered((estimate.not_modelled ?? []).map((code) => notModelledText(code, estimate, t, f)).join(", "))}
        </p>
      ) : null}
      {estimate.tariff.reused_from != null ? (
        <p className="muted tax-subline">{t.tariffReused(estimate.tariff.reused_from, year)}</p>
      ) : null}
    </section>
  );
}

/** A "Nicht berücksichtigt" item; the außergewöhnliche Belastungen carry the household's
 * zumutbare Belastung (§33 Abs. 3, a reminder figure the API computes and subtracts nowhere). */
function notModelledText(code: string, estimate: ReturnEstimate, t: T, f: Formatter): string {
  const label = notModelledLabel(code);
  return code === "extraordinary_burden" && estimate.extraordinary_threshold != null
    ? t.reasonableBurden(label, f.money(estimate.extraordinary_threshold))
    : label;
}

function Row({
  label,
  amount,
  strong,
  refs,
  title,
  propertyId,
}: {
  label: ReactNode;
  amount: string;
  strong?: boolean;
  refs?: TaxSourceRef[];
  title?: string;
  propertyId?: string;
}) {
  const t = useT(messages);
  const f = useFormat();
  return (
    <li className={strong ? "tax-estimate-total" : undefined}>
      <span>{label}</span>
      {refs && refs.length > 0 ? (
        <SourcesButton refs={refs} title={title ?? (typeof label === "string" ? label : t.line)} propertyId={propertyId} />
      ) : null}
      <span className="num">{f.money(amount)}</span>
    </li>
  );
}

/** "2026-03" → "März 2026" (the API sends the birth month only, never the day). */
function monthName(born: string, f: Formatter): string {
  return /^(\d{4})-(\d{2})$/.test(born) ? f.month(born) : born;
}

/** "Kind ab März 2026 (10 Monate)" for a child born in the year, else "Kind geb. Mai 2019
 * (12 Monate)"; children with no month in the year are left out, as the rule leaves them. */
function childrenLabel(estimate: ReturnEstimate, t: T, f: Formatter): string {
  const counted = (estimate.children ?? []).filter((child) => child.months > 0);
  const parts = counted.map((child) =>
    child.born_month.startsWith(`${estimate.tax_year}-`)
      ? t.childFrom(monthName(child.born_month, f), child.months)
      : t.childBorn(monthName(child.born_month, f), child.months),
  );
  return parts.length > 0 ? parts.join(", ") : t.childrenMonths(estimate.assessed.children?.months ?? 0);
}

/** The chain from the Einkünfte to the result, every figure as the API sent it. */
function Breakdown({ estimate, owners, properties }: { estimate: ReturnEstimate; owners: Owner[]; properties: Property[] }) {
  const t = useT(messages);
  const f = useFormat();
  const a = estimate.assessed;
  const progression = a.progression;
  const children = a.children;
  const withheld = estimate.withheld;
  const lines = estimate.line_sources;
  const propertyLabel = (id: string) => properties.find((p) => p.id === id)?.label ?? t.property;
  // An owner without pay: none recorded, or „Kein Arbeitslohn“ said for the year (D195).
  const noPayText = (n: { status: string; no_pay_declared?: boolean }) =>
    n.status !== "none" ? "" : n.no_pay_declared ? t.noPayDeclared : t.noPayNone;
  return (
    <div className="stack tax-estimate-breakdown">
      <div>
        <p className="tax-tracked-head">{t.income}</p>
        <ul className="tax-estimate-rows">
          {(estimate.income_n ?? []).map((n) => (
            <Row
              key={n.owner_id}
              label={`Anlage N ${ownerName(owners, n.owner_id, t)}${noPayText(n)}`}
              amount={n.result}
              refs={n.sources}
            />
          ))}
          {(estimate.income_v ?? []).map((v) => (
            <Row
              key={v.property_id}
              label={`Anlage V ${propertyLabel(v.property_id)}`}
              amount={v.result}
              refs={v.sources}
              propertyId={v.property_id}
            />
          ))}
          <Row label={t.totalIncome} amount={estimate.total_income} strong />
        </ul>
      </div>
      <div>
        <p className="tax-tracked-head">{t.special}</p>
        <ul className="tax-estimate-rows">
          <Row label={t.vorsorge} amount={estimate.vorsorge_total} refs={lines?.vorsorge} />
          {estimate.special_allowance_used ? (
            <Row
              label={
                isZeroAmount(estimate.church_tax_paid) && isZeroAmount(estimate.donations)
                  ? t.specialAllowance
                  : t.specialAllowanceHigher
              }
              amount={estimate.special_allowance ?? "0"}
              refs={lines?.special_allowance}
              title={t.specialAllowance}
            />
          ) : (
            <>
              {!isZeroAmount(estimate.church_tax_paid) ? <Row label={t.churchPaid} amount={estimate.church_tax_paid} refs={lines?.church_tax_paid} /> : null}
              {!isZeroAmount(estimate.donations) ? <Row label={t.donations} amount={estimate.donations} refs={lines?.donations} /> : null}
            </>
          )}
          {!isZeroAmount(estimate.childcare) ? <Row label={t.childcare} amount={estimate.childcare} refs={lines?.childcare} /> : null}
          <Row label={t.specialTotal} amount={estimate.special_expenses} strong />
        </ul>
      </div>
      <ul className="tax-estimate-rows">
        <Row label={t.zve} amount={estimate.zve} strong />
        <Row
          label={
            progression
              ? t.incomeTaxProgression(f.percent(progression.rate), f.money(progression.benefits))
              : t.incomeTax
          }
          amount={a.est}
          refs={lines?.income_tax}
          title={t.incomeTax}
        />
        {children ? (
          <li className="tax-estimate-note">
            <span>
              {children.favourable === "kindergeld"
                ? t.kindergeldBetter(childrenLabel(estimate, t, f), f.money(children.kindergeld), f.money(children.saving))
                : t.allowancesBetter(
                    childrenLabel(estimate, t, f),
                    f.money(children.allowances),
                    f.money(children.kindergeld),
                    f.money(children.saving),
                  )}
            </span>
            <SourcesButton refs={lines?.children ?? []} title={t.children} />
          </li>
        ) : null}
        <Row label={t.credit35a} amount={a.credit_35a} refs={lines?.credit_35a} />
        {!isZeroAmount(a.credit_35a) ? <Row label={t.afterCredit} amount={a.est_final} /> : null}
        <Row label={t.soli} amount={a.soli} refs={lines?.surcharges} />
        <Row label={t.church} amount={a.church} refs={lines?.surcharges} />
        <Row label={t.assessed} amount={a.total} strong />
        <Row
          label={t.withheld}
          amount={withheld.total}
          refs={lines?.withheld}
          title={t.withheldTitle}
        />
        <Row
          label={estimate.result.startsWith("-") ? t.resultRefund : t.resultBackPayment}
          amount={estimate.result}
          strong
        />
      </ul>
    </div>
  );
}

/** What the estimate still lacks: open questions (one line), certificates and benefit letters. */
function Missing({ items, owners, year }: { items: MissingItem[]; owners: Owner[]; year: number }) {
  const t = useT(messages);
  if (items.length === 0) return null;
  const questions = items.filter((item) => item.kind === "question").length;
  const others = items.filter((item) => item.kind !== "question");
  return (
    <div className="tax-estimate-block">
      <p className="tax-tracked-head">{t.missing}</p>
      <ul className="tax-estimate-list">
        {questions > 0 ? (
          <li>
            {t.questionsOpen(questions)}{" "}
            <Link href={questionnaireHref(year)}>{t.answer}</Link>
          </li>
        ) : null}
        {others.map((item, index) => (
          <li key={`${item.kind}-${item.owner_id ?? ""}-${index}`}>
            {item.kind === "document"
              ? t.certificateMissing(year, ownerName(owners, item.owner_id, t))
              : t.benefitMissing(ownerName(owners, item.owner_id, t))}
            {" · "}
            <Link
              href={`${taxFormHref(item.kind === "document" ? "n" : "angaben", { year, propertyId: null, ownerId: item.owner_id ?? null })}#${
                item.kind === "document" ? "tax-n-documents" : "eingabe-replacement_income"
              }`}
            >
              {item.kind === "document" ? t.uploadWhenThere : t.enter}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
