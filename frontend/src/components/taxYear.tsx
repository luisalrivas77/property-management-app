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
import { detailHref } from "@/lib/detailHash";

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

/** The anchors of the property's tax inputs, on the Anlage V page until D290 and on the
 * Immobilie step since: a to-do link or an old bookmark to one of them opens that step. */
export const PROPERTY_STEP_ANCHORS: readonly string[] = ["tax-useful-life", "tax-movable-asset", "tax-grundsteuer", "tax-limit"];

/** The Immobilie step's URL for a `#…` link to one of PROPERTY_STEP_ANCHORS, else null. */
export function propertyAnchorHref(year: number, propertyId: string | null, href: string): string | null {
  if (!href.startsWith("#") || !PROPERTY_STEP_ANCHORS.includes(href.slice(1))) return null;
  return `${taxStepHref(year, "immobilie", propertyId)}${href}`;
}

/** The detail-panel kind of a point on Prüfen und abgeben (#d-punkt-<key>). */
export const POINT_DETAIL_KIND = "punkt";

/** What names one point of the guide (GET /tax/{year}/guide, `GuidePoint`): its code, where it
 * belongs and the warning's params. The form views link to the same key, so both agree. */
export interface GuidePointKeyParts {
  code: string;
  property_id?: string | null;
  owner_id?: string | null;
  /** The checklist item's scope id, for a point that is a checklist item. */
  item_scope_id?: string | null;
  params?: { [key: string]: string } | null;
  /** The warning's sources: two payments with equal params are two points (rules/tax_guide.py). */
  refs?: readonly { kind: string; id?: string | null }[] | null;
}

/** A short, stable digest (FNV-1a, 32 bit) of the refs, so the URL stays readable. */
function refsDigest(refs: GuidePointKeyParts["refs"]): string {
  if (!refs || refs.length === 0) return "";
  const text = refs
    .map((r) => `${r.kind}:${r.id ?? ""}`)
    .sort()
    .join(",");
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

export function guidePointKey(point: GuidePointKeyParts): string {
  const params = point.params ?? {};
  const sorted = Object.keys(params)
    .sort()
    .map((k) => `${k}=${params[k]}`)
    .join("&");
  return `${point.code}~${point.property_id ?? ""}~${point.owner_id ?? ""}~${point.item_scope_id ?? ""}~${sorted}~${refsDigest(point.refs)}`;
}

/** Prüfen und abgeben with one point open in the panel. */
export function guidePointHref(year: number, point: GuidePointKeyParts): string {
  return `${taxStepHref(year, "pruefen")}${detailHref(POINT_DETAIL_KIND, guidePointKey(point))}`;
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
