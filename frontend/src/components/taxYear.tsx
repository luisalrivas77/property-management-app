"use client";

// The tab bar of Steuern › one tax year (phase-3.md §11, phase-4b.md §10, phase 10 D281): Eure
// Angaben (every input the forms read), Anlage V, Anlage N, KAP and SO (disabled until phase 8),
// and the way into the Jahresfragen. Each tab is a URL: /tax/2026?tab=angaben (?eigentuemer=…),
// /tax/2026?tab=anlage-v (Anlage V, ?immobilie=…; /tax/2026 alone is the guided start page since
// phase 10 D290), /tax/2026?tab=anlage-n (?eigentuemer=…), and
// /tax/2026/fragen for the questionnaire. The chosen property and owner travel along, so
// switching back keeps them.
import Link from "next/link";

import { useT } from "@/i18n";
import { tax as messages } from "@/i18n/tax";

export type TaxForm = "angaben" | "v" | "n";

/** The value of ?tab= (the TabBar key) for the Anlage N tab. */
export const ANLAGE_N_PARAM = "anlage-n";

/** The value of ?tab= for the Eure Angaben tab (D281). */
export const ANGABEN_PARAM = "angaben";

/** The value of ?tab= for the Anlage V view (phase 10 D290): /tax/<year> alone is the guided
 * start page since then; /tax/<year>?immobilie=… without a tab still opens the Anlage V view. */
export const ANLAGE_V_PARAM = "anlage-v";

/** ?schritt=… opens one step of the guided path (phase 10 D290): the property's tax inputs
 * (immobilie, with ?immobilie=…), the Belege (belege) and Prüfen und abgeben (pruefen). Eure
 * Angaben stays ?tab=angaben and the Jahresfragen /tax/<year>/fragen. */
export const STEP_PARAM = "schritt";
export type GuideStepParam = "immobilie" | "belege" | "pruefen";

/** The guided start page of a year (phase 10 D290). */
export function taxGuideHref(year: number): string {
  return `/tax/${year}`;
}

/** One step of the guided path; `propertyId` for the Immobilie step. */
export function taxStepHref(year: number, step: GuideStepParam, propertyId: string | null = null): string {
  return `/tax/${year}${query([
    [STEP_PARAM, step],
    ["immobilie", step === "immobilie" ? propertyId : null],
  ])}`;
}

export interface TaxYearState {
  year: number;
  /** ?immobilie=…, kept only when the user picked one. */
  propertyId: string | null;
  /** ?eigentuemer=…, kept only when the user picked one. */
  ownerId: string | null;
}

function query(params: [string, string | null][]): string {
  const parts = params.filter(([, value]) => value).map(([key, value]) => `${key}=${encodeURIComponent(value ?? "")}`);
  return parts.length > 0 ? `?${parts.join("&")}` : "";
}

/** The pre-fill of the Lohnersatzleistungen form from the payments booked as Elterngeld &
 * Lohnersatz (phase 6 D159 amended (8)): ?lohnersatz=<amount>&leistung=<benefit code>, carried
 * on the Anlage N URL until the form is saved or cancelled. Nothing is saved from the URL. */
export const BENEFIT_AMOUNT_PARAM = "lohnersatz";
export const BENEFIT_KIND_PARAM = "leistung";

export interface BenefitPrefill {
  /** A decimal string with a dot, e.g. "1287.00". */
  amount: string;
  /** A benefit code the form offers, else null (the person picks). */
  benefit: string | null;
}

/** The prefill read from the URL's params; null without a valid amount. */
export function benefitPrefill(get: (key: string) => string | null, benefits: readonly string[]): BenefitPrefill | null {
  const amount = get(BENEFIT_AMOUNT_PARAM);
  if (!amount || !/^\d{1,9}(\.\d{1,2})?$/.test(amount)) return null;
  const benefit = get(BENEFIT_KIND_PARAM);
  return { amount, benefit: benefit && benefits.includes(benefit) ? benefit : null };
}

/** Whether a current to-do return.benefit_booked of the loaded estimate offers `prefill`'s
 * amount (phase 6 D159 amended (8)). A pre-fill no to-do backs, such as a Back-button entry
 * after the amount was saved, is dropped, so the same amount is never offered twice. The owner
 * is not compared: the to-do's owner is only a suggestion, and the pre-fill is kept on the
 * owner switch. */
export function benefitPrefillBacked(
  prefill: BenefitPrefill,
  warnings: readonly { code: string; params?: { [key: string]: string } | null }[],
): boolean {
  return warnings.some((w) => w.code === "return.benefit_booked" && w.params?.difference === prefill.amount);
}

/** `href` (an Anlage N URL, which always has a query) with the prefill's params added. */
export function withBenefitPrefill(href: string, prefill: BenefitPrefill | null): string {
  if (!prefill) return href;
  const at = href.indexOf("#");
  const path = at < 0 ? href : href.slice(0, at);
  const hash = at < 0 ? "" : href.slice(at);
  const extra = query([
    [BENEFIT_AMOUNT_PARAM, prefill.amount],
    [BENEFIT_KIND_PARAM, prefill.benefit],
  ]).slice(1);
  return `${path}${path.includes("?") ? "&" : "?"}${extra}${hash}`;
}

/** The URL of one form of the year; „angaben“ is the Eure Angaben tab (D281). */
export function taxFormHref(form: TaxForm, state: TaxYearState): string {
  if (form === "v") {
    return `/tax/${state.year}${query([
      ["tab", ANLAGE_V_PARAM],
      ["immobilie", state.propertyId],
    ])}`;
  }
  return `/tax/${state.year}${query([
    ["tab", form === "n" ? ANLAGE_N_PARAM : ANGABEN_PARAM],
    ["eigentuemer", state.ownerId],
    ["immobilie", state.propertyId],
  ])}`;
}

/** Steuern › Eure Angaben › Arbeitslohn of a year (phase 10 D281): where the expected gross pay,
 * a Bescheid's zvE, a typed rate and „Kein Arbeitslohn“ are entered (Eigentümer › Einkommen until
 * then), on the given owner when the caller knows one. */
export function payInputsHref(year: number, ownerId: string | null = null): string {
  return `${taxFormHref("angaben", { year, propertyId: null, ownerId })}#arbeitslohn`;
}

/** One answer slot of the Jahresfragen: the question's key and whose it is. */
export interface QuestionTarget {
  key: string;
  ownerId?: string | null;
  propertyId?: string | null;
}

/** The Jahresfragen flow of the year (built in the questionnaire step, phase-4b.md §10), opened
 * at one question when `target` is given (?frage=…&eigentuemer=…&immobilie=…). */
export function questionnaireHref(year: number, target?: QuestionTarget): string {
  const base = `/tax/${year}/fragen`;
  if (!target) return base;
  const query = new URLSearchParams({ frage: target.key });
  if (target.ownerId) query.set("eigentuemer", target.ownerId);
  if (target.propertyId) query.set("immobilie", target.propertyId);
  return `${base}?${query.toString()}`;
}

export function TaxTabs({ current, state }: { current: TaxForm; state: TaxYearState }) {
  const t = useT(messages);
  return (
    <nav aria-label={t.forms} className="tabs">
      <Link href={taxGuideHref(state.year)} scroll={false}>
        {t.allSteps}
      </Link>
      <Link href={taxFormHref("angaben", state)} aria-current={current === "angaben" ? "page" : undefined} scroll={false}>
        {t.inputsTab}
      </Link>
      <Link href={taxFormHref("v", state)} aria-current={current === "v" ? "page" : undefined} scroll={false}>
        Anlage V
      </Link>
      <Link href={taxFormHref("n", state)} aria-current={current === "n" ? "page" : undefined} scroll={false}>
        Anlage N
      </Link>
      <span className="tab-disabled" aria-disabled="true">
        KAP <span className="tab-note">{t.fromPhase8}</span>
      </span>
      <span className="tab-disabled" aria-disabled="true">
        SO <span className="tab-note">{t.fromPhase8}</span>
      </span>
      <Link href={questionnaireHref(state.year)} className="tax-tab-questions">
        {t.questionnaire}
      </Link>
    </nav>
  );
}
