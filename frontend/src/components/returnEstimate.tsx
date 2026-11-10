"use client";

// The Gesamt card of Steuern › one tax year (phase-4b.md §10, D101): the household's whole
// return as GET /api/tax/return-estimate computes it on every read — refund or back-payment,
// the §46 filing check with its deadline, the Jahresfragen progress, and a fold-out with the
// chain from the Einkünfte to the result. Every euro figure is the backend's (principle 1);
// this card formats, names and links, it adds nothing up. The estimate cites its sources as
// one list (ReturnEstimate.sources), so the card has one sources button, not one per figure.
import Link from "next/link";
import { useCallback, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type { Owner, Property } from "@/api/schemas";
import type { components } from "@/api/types";

import { formatDate, formatMoney, formatPercent } from "./format";
import { notModelledLabel, RETURN_STATUS, WARNING_TEXT } from "./labels";
import { SourcesButton } from "./tax";
import { questionnaireHref } from "./taxYear";
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
  return <EstimateView estimate={state.data} owners={owners.data ?? []} properties={properties.data ?? []} />;
}

function ownerName(owners: Owner[], id: string | null | undefined): string {
  return owners.find((o) => o.id === id)?.full_name ?? "Eigentümer";
}

function EstimateView({ estimate, owners, properties }: { estimate: ReturnEstimate; owners: Owner[]; properties: Property[] }) {
  const year = estimate.tax_year;
  const result = estimate.result;
  const headline = isZeroAmount(result)
    ? "Weder Erstattung noch Nachzahlung"
    : result.startsWith("-")
      ? "Voraussichtliche Erstattung"
      : "Voraussichtliche Nachzahlung";
  const filing = estimate.filing_required;
  const progress = estimate.questionnaire;
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
            </li>
          ))}
        </ul>
      ) : null}
      {(estimate.not_modelled ?? []).length > 0 ? (
        <p className="muted tax-subline">
          Nicht berücksichtigt: {(estimate.not_modelled ?? []).map(notModelledLabel).join(", ")}
        </p>
      ) : null}
      {estimate.tariff.reused_from != null ? (
        <p className="muted tax-subline">Tarif {estimate.tariff.reused_from} (für {year} noch keiner veröffentlicht)</p>
      ) : null}
    </section>
  );
}

function Row({ label, amount, strong }: { label: ReactNode; amount: string; strong?: boolean }) {
  return (
    <li className={strong ? "tax-estimate-total" : undefined}>
      <span>{label}</span>
      <span className="num">{formatMoney(amount)}</span>
    </li>
  );
}

/** The chain from the Einkünfte to the result, every figure as the API sent it. */
function Breakdown({ estimate, owners, properties }: { estimate: ReturnEstimate; owners: Owner[]; properties: Property[] }) {
  const a = estimate.assessed;
  const progression = a.progression;
  const children = a.children;
  const withheld = estimate.withheld;
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
            />
          ))}
          {(estimate.income_v ?? []).map((v) => (
            <Row key={v.property_id} label={`Anlage V ${propertyLabel(v.property_id)}`} amount={v.result} />
          ))}
          <Row label="Summe der Einkünfte" amount={estimate.total_income} strong />
        </ul>
      </div>
      <div>
        <p className="tax-tracked-head">Sonderausgaben</p>
        <ul className="tax-estimate-rows">
          <Row label="Vorsorgeaufwendungen" amount={estimate.vorsorge_total} />
          {!isZeroAmount(estimate.church_tax_paid) ? <Row label="Kirchensteuer gezahlt" amount={estimate.church_tax_paid} /> : null}
          {!isZeroAmount(estimate.donations) ? <Row label="Spenden" amount={estimate.donations} /> : null}
          {!isZeroAmount(estimate.childcare) ? <Row label="Kinderbetreuungskosten" amount={estimate.childcare} /> : null}
          <Row label="Sonderausgaben gesamt (mindestens der Pauschbetrag)" amount={estimate.special_expenses} strong />
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
        />
        {children ? (
          <li className="tax-estimate-note">
            {children.favourable === "kindergeld"
              ? `Kinder (${children.months} Monate): Kindergeld ${formatMoney(children.kindergeld)} günstiger als die Freibeträge (Ersparnis ${formatMoney(children.saving)})`
              : `Kinder (${children.months} Monate): Freibeträge ${formatMoney(children.allowances)} günstiger als das Kindergeld ${formatMoney(children.kindergeld)} (Ersparnis ${formatMoney(children.saving)})`}
          </li>
        ) : null}
        <Row label="Steuerermäßigung § 35a" amount={a.credit_35a} />
        {!isZeroAmount(a.credit_35a) ? <Row label="Einkommensteuer nach Ermäßigung" amount={a.est_final} /> : null}
        <Row label="Solidaritätszuschlag" amount={a.soli} />
        <Row label="Kirchensteuer" amount={a.church} />
        <Row label="Festzusetzen" amount={a.total} strong />
        <Row label="Einbehalten (Lohnsteuer, Soli, Kirchensteuer laut Nr. 4–7)" amount={withheld.total} />
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
          </li>
        ))}
      </ul>
    </div>
  );
}
