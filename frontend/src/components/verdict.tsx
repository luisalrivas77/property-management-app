"use client";

// Finanzielle Gesundheit (phase 10 §7.6; Luis, 23 Nov 2026: „something that simply says financial
// health -> excellent, good, or bad … Then in a side panel the value can be explained“): the one
// word of GET /health's `verdict` and its three questions as cards, shared by Übersicht's tile
// panel and Haushalt's „Kennzahlen“. Every value, state, limit and text is the server's
// (rules/verdict); the browser only formats. A state is a dot in its colour with its meaning
// read out beside it, never a colour word.
import type { ReactNode } from "react";

import type { components } from "@/api/types";
import { type Translation, useT } from "@/i18n";
import { goalText } from "@/i18n/goal";
import { verdictText } from "@/i18n/verdict";

import { formatOneDecimal } from "./healthShared";
import { type PlanFormat, usePlanFormat } from "./planFormat";

import "./verdict.css";

export type Verdict = components["schemas"]["Verdict"];
export type VerdictPart = components["schemas"]["VerdictPart"];
type VerdictState = components["schemas"]["VerdictState"];
type VerdictWord = components["schemas"]["VerdictWord"];
type VerdictOrigin = components["schemas"]["VerdictOrigin"];
type MarkedFigure = components["schemas"]["MarkedFigure"];
type T = Translation<typeof verdictText.de>;
export type Mark = "green" | "amber" | "red" | "none";

export const STATE_MARK: Record<VerdictState, Mark> = { good: "green", ok: "amber", strained: "red", missing: "none" };
export const WORD_MARK: Record<VerdictWord, Mark> = { very_good: "green", good: "amber", strained: "red", too_little_data: "none" };

export function wordText(t: T, word: VerdictWord): string {
  switch (word) {
    case "very_good":
      return t.veryGood;
    case "good":
      return t.good;
    case "strained":
      return t.strained;
    case "too_little_data":
      return t.tooLittleData;
  }
}

export function stateText(t: T, state: VerdictState): string {
  switch (state) {
    case "good":
      return t.stateGood;
    case "ok":
      return t.stateOk;
    case "strained":
      return t.stateStrained;
    case "missing":
      return t.stateMissing;
  }
}

export function partLabel(t: T, figure: MarkedFigure): string {
  switch (figure) {
    case "savings_rate":
      return t.savingsRate;
    case "reserve":
      return t.reserve;
    case "debt_service_ratio":
      return t.debtService;
    default:
      return figure;
  }
}

/** A dot in the mark's colour (an empty ring for none); `label`, when given, is read out. */
export function VerdictDot({ mark, label }: { mark: Mark; label?: string }) {
  return (
    <>
      <span className="verdict-dot" data-mark={mark} aria-hidden="true" />
      {label ? <span className="visually-hidden">{label} </span> : null}
    </>
  );
}

/** One decimal as phase 9 shows it: German exactly as formatOneDecimal, English via p.short. */
function oneDecimal(p: PlanFormat, value: string | null | undefined): string {
  return p.locale === "de" ? formatOneDecimal(value) : p.short(value);
}

/** Zero or below: the server's gap reads as reached. */
function noGap(value: string): boolean {
  return value.startsWith("-") || /^0*(\.0*)?$/.test(value);
}

function partValue(g: Translation<typeof goalText.de>, p: PlanFormat, part: VerdictPart): string {
  // The reserve always as „X € von Y €“ (§7.13 (d) 2); the gap under it is the server's.
  if (part.figure === "reserve") {
    if (part.amount == null) return "–";
    return part.target != null ? g.reserveOf(p.euro(part.amount), p.euro(part.target)) : p.euro(part.amount);
  }
  if (part.no_loan) return "–";
  return part.value != null ? p.ratio(part.value) : "–";
}

interface LimitShown {
  text: string;
  origin: VerdictOrigin | null | undefined;
}

/** The limits a part was measured against, each with where it came from. */
export function limitsShown(t: T, p: PlanFormat, part: VerdictPart): LimitShown[] {
  if (part.figure === "reserve") {
    const out: LimitShown[] = [{ text: t.reserveGood(part.target ? p.euro(part.target) : null), origin: null }];
    if (part.strained != null) {
      out.push({
        text: t.reserveStrained(part.strained_amount ? p.euro(part.strained_amount) : null, p.ratio(part.strained)),
        origin: part.strained_origin,
      });
    }
    return out;
  }
  const higher = part.figure === "savings_rate";
  const out: LimitShown[] = [];
  if (part.good != null) out.push({ text: higher ? t.goodFrom(p.ratio(part.good)) : t.goodUpTo(p.ratio(part.good)), origin: part.good_origin });
  if (part.strained != null) {
    out.push({
      text: higher ? t.strainedBelow(p.ratio(part.strained)) : t.strainedAbove(p.ratio(part.strained)),
      origin: part.strained_origin,
    });
  }
  return out;
}

/** The word with its dot and the line naming the weakest point. */
export function VerdictWordLine({ verdict, big = false }: { verdict: Verdict; big?: boolean }) {
  const t = useT(verdictText);
  return (
    <div className={big ? "verdict-word verdict-word-big" : "verdict-word"}>
      <p className="verdict-word-text">
        <VerdictDot mark={WORD_MARK[verdict.word]} />
        {wordText(t, verdict.word)}
      </p>
      <p className="muted">{verdict.line}</p>
    </div>
  );
}

/** One question as a card: the figure, its dot and headline, the limits used (each a „Standard-
 * Grenze“ or „eure Grenze“), why it matters, and the reference behind the limits. `children`
 * holds what the figure is made of, where a screen shows it. */
export function VerdictPartCard({ part, children }: { part: VerdictPart; children?: ReactNode }) {
  const t = useT(verdictText);
  const g = useT(goalText);
  const p = usePlanFormat();
  const label = partLabel(t, part.figure);
  const limits = part.state === "missing" || part.no_loan ? [] : limitsShown(t, p, part);
  return (
    <div className="verdict-card health-figure" role="group" aria-label={label} data-state={part.state}>
      <p className="verdict-question">{part.question}</p>
      <p className="verdict-figure">
        <span className="verdict-label">{label}</span>
        <span className="verdict-value">{partValue(g, p, part)}</span>
      </p>
      {part.figure === "reserve" && part.gap != null ? (
        <p className="verdict-gap">{noGap(part.gap) ? g.reserveReached : g.reserveStill(p.euro(part.gap))}</p>
      ) : null}
      {part.figure === "reserve" && part.months != null && part.target_months != null ? (
        <p className="muted">
          {t.reserveMonths(oneDecimal(p, part.months))} · {t.reserveTarget(p.short(part.target_months))}
        </p>
      ) : null}
      <p className="verdict-headline">
        <VerdictDot mark={STATE_MARK[part.state]} label={stateText(t, part.state)} />
        <span>{part.headline}</span>
      </p>
      {limits.length > 0 ? (
        <ul className="verdict-limits">
          {limits.map((limit) => (
            <li key={limit.text}>
              {limit.text}
              {limit.origin ? (
                <span className="badge badge-muted verdict-origin">{limit.origin === "household" ? t.yourLimit : t.standardLimit}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      <p>{part.why}</p>
      <details className="verdict-reference">
        <summary>{t.reference}</summary>
        <p className="muted">{part.reference}</p>
      </details>
      {children}
    </div>
  );
}

/** The three cards in the server's order. */
export function VerdictCards({ parts, children }: { parts: VerdictPart[]; children?: (part: VerdictPart) => ReactNode }) {
  return (
    <div className="verdict-cards">
      {parts.map((part) => (
        <VerdictPartCard key={part.figure} part={part}>
          {children?.(part)}
        </VerdictPartCard>
      ))}
    </div>
  );
}
