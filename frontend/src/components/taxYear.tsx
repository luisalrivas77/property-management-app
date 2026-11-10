"use client";

// The tab bar of Steuern › one tax year (phase-3.md §11, phase-4b.md §10): Anlage V, Anlage N,
// KAP and SO (disabled until phase 8), and the way into the Jahresfragen. Each tab is a URL:
// /tax/2026 (Anlage V, ?immobilie=…), /tax/2026?tab=anlage-n (?eigentuemer=…), and
// /tax/2026/fragen for the questionnaire. The chosen property and owner travel along, so
// switching back keeps them.
import Link from "next/link";

export type TaxForm = "v" | "n";

/** The value of ?tab= (the TabBar key) for the Anlage N tab. */
export const ANLAGE_N_PARAM = "anlage-n";

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

/** The URL of one form of the year. */
export function taxFormHref(form: TaxForm, state: TaxYearState): string {
  if (form === "v") return `/tax/${state.year}${query([["immobilie", state.propertyId]])}`;
  return `/tax/${state.year}${query([
    ["tab", ANLAGE_N_PARAM],
    ["eigentuemer", state.ownerId],
    ["immobilie", state.propertyId],
  ])}`;
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
  return (
    <nav aria-label="Formulare" className="tabs">
      <Link href={taxFormHref("v", state)} aria-current={current === "v" ? "page" : undefined} scroll={false}>
        Anlage V
      </Link>
      <Link href={taxFormHref("n", state)} aria-current={current === "n" ? "page" : undefined} scroll={false}>
        Anlage N
      </Link>
      <span className="tab-disabled" aria-disabled="true">
        KAP <span className="tab-note">ab Phase 8</span>
      </span>
      <span className="tab-disabled" aria-disabled="true">
        SO <span className="tab-note">ab Phase 8</span>
      </span>
      <Link href={questionnaireHref(state.year)} className="tax-tab-questions">
        Jahresfragen
      </Link>
    </nav>
  );
}
