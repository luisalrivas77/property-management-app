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

import { formatDate, formatMoney, formatPercent } from "./format";
import { notModelledLabel, RETURN_STATUS, WARNING_TEXT } from "./labels";
import { SourcesButton } from "./tax";
import { questionnaireHref, taxFormHref } from "./taxYear";
import { WarningActionView, WhatToDo } from "./todos";
import { useLoad, type Loaded } from "./useLoad";

export type ReturnEstimate = components["schemas"]["ReturnEstimate"];
type MissingItem = components["schemas"]["MissingItem"];

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
  const owners = useLoad(loadOwners);
  const properties = useLoad(loadProperties);
  if (state.data === undefined) {
    if (state.error == null) return null;
    return (
      <section className="card tax-estimate" aria-label="Gesamt">
        <p className="muted">Die Gesamtschätzung konnte gerade nicht berechnet werden.</p>
        <button type="button" className="secondary" onClick={state.reload}>
          Erneut versuchen
        </button>
      </section>
    );
  }
  if (state.data === null) {
    return (
      <section className="card tax-estimate" aria-label="Gesamt">
        <p className="muted">Für {year} gibt es noch kein Regelwerk, also keine Gesamtschätzung der Einkommensteuer.</p>
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
  if (pair === "iii/v") return "Steuerklassen III/V: die Erstattung oder Nachzahlung gleicht den Lohnsteuerabzug aus.";
  if (classes.includes("iv_factor") && classes.every((c) => c === "iv" || c === "iv_factor")) {
    return "Steuerklassen IV/IV mit Faktor: der Lohnsteuerabzug liegt schon nahe an der gemeinsamen Steuer; die Erstattung oder Nachzahlung gleicht den Rest aus.";
  }
  if (pair === "iv/iv") {
    return "Steuerklassen IV/IV: jeder zahlt Lohnsteuer wie allein veranlagt; die Erstattung oder Nachzahlung gleicht den Unterschied zur gemeinsamen Steuer aus.";
  }
  return null;
}

function ownerName(owners: Owner[], id: string | null | undefined): string {
  return owners.find((o) => o.id === id)?.full_name ?? "Eigentümer";
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
  const year = estimate.tax_year;
  const result = estimate.result;
  const headline = isZeroAmount(result)
    ? "Weder Erstattung noch Nachzahlung"
    : result.startsWith("-")
      ? "Voraussichtliche Erstattung"
      : "Voraussichtliche Nachzahlung";
  const filing = estimate.filing_required;
  const progress = estimate.questionnaire;
  const classes = taxClassSentence(estimate);
  const shown = (estimate.warnings ?? []).filter(
    (w) => w.code !== "return.filing_required" && w.code !== "return.questionnaire_open",
  );

  return (
    <section className="card tax-estimate" aria-label="Gesamt">
      <p className="eyebrow">Einkommensteuer {year} · Gesamt</p>
      <div className="row tax-estimate-head">
        <p className="tax-estimate-result">
          {headline}
          {isZeroAmount(result) ? null : (
            <strong className={result.startsWith("-") ? "tax-estimate-refund" : undefined}> {formatMoney(magnitude(result))}</strong>
          )}
        </p>
        <span className="badge">{RETURN_STATUS[estimate.status]}</span>
        <span className="spacer" />
        <SourcesButton refs={estimate.sources ?? []} title={`Gesamt ${year}`} />
      </div>
      <p className="tax-subline">
        {filing.required ? (
          <>
            Abgabe pflichtig ({(filing.reasons ?? []).join("; ")}), Frist {formatDate(filing.deadline)}
          </>
        ) : (
          <>Abgabe freiwillig ({(filing.reasons ?? []).join("; ")})</>
        )}
      </p>
      {classes ? <p className="tax-subline tax-estimate-classes">{classes}</p> : null}
      <p className="tax-subline">
        Jahresfragen {year}: {progress.answered} von {progress.total} beantwortet ·{" "}
        <Link href={questionnaireHref(year)}>{progress.answered < progress.total ? "Weiter" : "Ansehen"}</Link>
      </p>
      <details className="tax-estimate-details">
        <summary>So rechnet die Schätzung</summary>
        <Breakdown estimate={estimate} owners={owners} properties={properties} />
      </details>
      <Missing items={estimate.missing ?? []} owners={owners} year={year} />
      {(estimate.reminders ?? []).length > 0 ? (
        <div className="tax-estimate-block">
          <p className="tax-tracked-head">Von Hand in ELSTER</p>
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
                {warning.severity === "warning" ? "Prüfen" : "Hinweis"}
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
          Nicht berücksichtigt: {(estimate.not_modelled ?? []).map((code) => notModelledText(code, estimate)).join(", ")}
        </p>
      ) : null}
      {estimate.tariff.reused_from != null ? (
        <p className="muted tax-subline">Tarif {estimate.tariff.reused_from} (für {year} noch keiner veröffentlicht)</p>
      ) : null}
    </section>
  );
}

/** A "Nicht berücksichtigt" item; the außergewöhnliche Belastungen carry the household's
 * zumutbare Belastung (§33 Abs. 3, a reminder figure the API computes and subtracts nowhere). */
function notModelledText(code: string, estimate: ReturnEstimate): string {
  const label = notModelledLabel(code);
  return code === "extraordinary_burden" && estimate.extraordinary_threshold != null
    ? `${label} (zumutbare Belastung ${formatMoney(estimate.extraordinary_threshold)})`
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
  return (
    <li className={strong ? "tax-estimate-total" : undefined}>
      <span>{label}</span>
      {refs && refs.length > 0 ? (
        <SourcesButton refs={refs} title={title ?? (typeof label === "string" ? label : "Zeile")} propertyId={propertyId} />
      ) : null}
      <span className="num">{formatMoney(amount)}</span>
    </li>
  );
}

const MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];

/** "2026-03" → "März 2026" (the API sends the birth month only, never the day). */
function monthName(born: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(born);
  return match ? `${MONTH_NAMES[Number(match[2]) - 1] ?? match[2]} ${match[1]}` : born;
}

/** "Kind ab März 2026 (10 Monate)" for a child born in the year, else "Kind geb. Mai 2019
 * (12 Monate)"; children with no month in the year are left out, as the rule leaves them. */
function childrenLabel(estimate: ReturnEstimate): string {
  const counted = (estimate.children ?? []).filter((child) => child.months > 0);
  const parts = counted.map((child) => {
    const months = `${child.months} ${child.months === 1 ? "Monat" : "Monate"}`;
    return child.born_month.startsWith(`${estimate.tax_year}-`)
      ? `Kind ab ${monthName(child.born_month)} (${months})`
      : `Kind geb. ${monthName(child.born_month)} (${months})`;
  });
  return parts.length > 0 ? parts.join(", ") : `Kinder (${estimate.assessed.children?.months ?? 0} Monate)`;
}

/** The chain from the Einkünfte to the result, every figure as the API sent it. */
function Breakdown({ estimate, owners, properties }: { estimate: ReturnEstimate; owners: Owner[]; properties: Property[] }) {
  const a = estimate.assessed;
  const progression = a.progression;
  const children = a.children;
  const withheld = estimate.withheld;
  const lines = estimate.line_sources;
  const propertyLabel = (id: string) => properties.find((p) => p.id === id)?.label ?? "Immobilie";
  return (
    <div className="stack tax-estimate-breakdown">
      <div>
        <p className="tax-tracked-head">Einkünfte</p>
        <ul className="tax-estimate-rows">
          {(estimate.income_n ?? []).map((n) => (
            <Row
              key={n.owner_id}
              label={`Anlage N ${ownerName(owners, n.owner_id)}${n.status === "none" ? " (kein Arbeitslohn erfasst)" : ""}`}
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
          <Row label="Summe der Einkünfte" amount={estimate.total_income} strong />
        </ul>
      </div>
      <div>
        <p className="tax-tracked-head">Sonderausgaben</p>
        <ul className="tax-estimate-rows">
          <Row label="Vorsorgeaufwendungen" amount={estimate.vorsorge_total} refs={lines?.vorsorge} />
          {estimate.special_allowance_used ? (
            <Row
              label={
                isZeroAmount(estimate.church_tax_paid) && isZeroAmount(estimate.donations)
                  ? "Sonderausgaben-Pauschbetrag"
                  : "Sonderausgaben-Pauschbetrag (höher als Kirchensteuer und Spenden)"
              }
              amount={estimate.special_allowance ?? "0"}
              refs={lines?.special_allowance}
              title="Sonderausgaben-Pauschbetrag"
            />
          ) : (
            <>
              {!isZeroAmount(estimate.church_tax_paid) ? <Row label="Kirchensteuer gezahlt" amount={estimate.church_tax_paid} refs={lines?.church_tax_paid} /> : null}
              {!isZeroAmount(estimate.donations) ? <Row label="Spenden" amount={estimate.donations} refs={lines?.donations} /> : null}
            </>
          )}
          {!isZeroAmount(estimate.childcare) ? <Row label="Kinderbetreuungskosten" amount={estimate.childcare} refs={lines?.childcare} /> : null}
          <Row label="Sonderausgaben gesamt" amount={estimate.special_expenses} strong />
        </ul>
      </div>
      <ul className="tax-estimate-rows">
        <Row label="Zu versteuerndes Einkommen (zvE)" amount={estimate.zve} strong />
        <Row
          label={
            progression
              ? `Einkommensteuer mit Progressionsvorbehalt (besonderer Steuersatz ${formatPercent(progression.rate)} auf Lohnersatzleistungen ${formatMoney(progression.benefits)})`
              : "Einkommensteuer"
          }
          amount={a.est}
          refs={lines?.income_tax}
          title="Einkommensteuer"
        />
        {children ? (
          <li className="tax-estimate-note">
            <span>
              {children.favourable === "kindergeld"
                ? `${childrenLabel(estimate)}: Kindergeld ${formatMoney(children.kindergeld)} günstiger als die Freibeträge (Ersparnis ${formatMoney(children.saving)})`
                : `${childrenLabel(estimate)}: Freibeträge ${formatMoney(children.allowances)} günstiger als das Kindergeld ${formatMoney(children.kindergeld)} (Ersparnis ${formatMoney(children.saving)})`}
            </span>
            <SourcesButton refs={lines?.children ?? []} title="Kinder" />
          </li>
        ) : null}
        <Row label="Steuerermäßigung § 35a" amount={a.credit_35a} refs={lines?.credit_35a} />
        {!isZeroAmount(a.credit_35a) ? <Row label="Einkommensteuer nach Ermäßigung" amount={a.est_final} /> : null}
        <Row label="Solidaritätszuschlag" amount={a.soli} refs={lines?.surcharges} />
        <Row label="Kirchensteuer" amount={a.church} refs={lines?.surcharges} />
        <Row label="Festzusetzen" amount={a.total} strong />
        <Row
          label="Einbehalten (Lohnsteuer, Soli, Kirchensteuer laut Nr. 4–7)"
          amount={withheld.total}
          refs={lines?.withheld}
          title="Einbehalten"
        />
        <Row
          label={estimate.result.startsWith("-") ? "Ergebnis (Erstattung)" : "Ergebnis (Nachzahlung)"}
          amount={estimate.result}
          strong
        />
      </ul>
    </div>
  );
}

/** What the estimate still lacks: open questions (one line), certificates and benefit letters. */
function Missing({ items, owners, year }: { items: MissingItem[]; owners: Owner[]; year: number }) {
  if (items.length === 0) return null;
  const questions = items.filter((item) => item.kind === "question").length;
  const others = items.filter((item) => item.kind !== "question");
  return (
    <div className="tax-estimate-block">
      <p className="tax-tracked-head">Es fehlt noch</p>
      <ul className="tax-estimate-list">
        {questions > 0 ? (
          <li>
            {questions} {questions === 1 ? "Jahresfrage" : "Jahresfragen"} offen ·{" "}
            <Link href={questionnaireHref(year)}>beantworten</Link>
          </li>
        ) : null}
        {others.map((item, index) => (
          <li key={`${item.kind}-${item.owner_id ?? ""}-${index}`}>
            {item.kind === "document"
              ? `Lohnsteuerbescheinigung ${year} ${ownerName(owners, item.owner_id)}: kommt meist bis Ende Februar ${year + 1}`
              : `Bescheid über Lohnersatzleistungen ${ownerName(owners, item.owner_id)} noch nicht eingetragen`}
            {" · "}
            <Link
              href={`${taxFormHref("n", { year, propertyId: null, ownerId: item.owner_id ?? null })}#${
                item.kind === "document" ? "tax-n-documents" : "eingabe-replacement_income"
              }`}
            >
              {item.kind === "document" ? "hochladen, sobald sie da ist" : "eintragen"}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
