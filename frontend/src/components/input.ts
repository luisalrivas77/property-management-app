// Text inputs → the API's string formats (§9.5). Each normaliser returns either the value to
// send or a fixed German message for the field. Messages never repeat what was typed. These
// are the "basic number formats" checks only; every domain rule stays with the API.
import type { Fraction } from "@/api/schemas";

import { shiftDecimal } from "./format";

export type Parsed<T> = { ok: true; value: T } | { ok: false; message: string };

const NOT_A_NUMBER = "Bitte eine Zahl eingeben, z. B. 1.400,50.";

/** German or plain input ("1.400,50", "1400.5", "3,6 %") → "1400.50"-style digits, or null.
 * `thousands`: a lone dot before exactly three digits is a thousands separator ("1.400"). */
function canonicalDecimal(raw: string, thousands: boolean): string | null {
  let text = raw.replace(/[\s €%]/g, "").replace(/m²$/, "");
  if (text.includes(",")) {
    if (text.split(",").length > 2) return null;
    text = text.replace(/\./g, "").replace(",", ".");
  } else if (thousands && /^\d{1,3}(\.\d{3})+$/.test(text)) {
    text = text.replace(/\./g, "");
  }
  return /^\d+(\.\d+)?$/.test(text) ? text : null;
}

function decimals(value: string): number {
  return value.split(".")[1]?.length ?? 0;
}

function padFraction(value: string, places: number): string {
  const [int, frac = ""] = value.split(".");
  return places > 0 ? `${int}.${frac.padEnd(places, "0")}` : (int ?? value);
}

/** Money: "1.400,00" / "1400" → "1400.00" (§9.5 toMoneyString). */
export function toMoneyString(raw: string): Parsed<string> {
  const value = canonicalDecimal(raw, true);
  if (value === null) return { ok: false, message: NOT_A_NUMBER };
  if (decimals(value) > 2) return { ok: false, message: "Höchstens zwei Nachkommastellen (Cent)." };
  return { ok: true, value: padFraction(value, 2) };
}

/** Money that may be negative (a document's total, e.g. a refund): "-12,50" → "-12.50". */
export function toSignedMoneyString(raw: string): Parsed<string> {
  const text = raw.trim();
  const negative = text.startsWith("-");
  const parsed = toMoneyString(negative ? text.slice(1) : text);
  if (!parsed.ok || !negative) return parsed;
  return { ok: true, value: /^0+(\.0+)?$/.test(parsed.value) ? parsed.value : `-${parsed.value}` };
}

/** A percentage → the fraction the API stores: "3,6 %" → "0.036" (§9.5 toRate).
 * `maxDecimals` is the API's precision of the fraction (6 for rates, 4 for a tax rate). */
export function toRate(raw: string, maxDecimals = 6): Parsed<string> {
  const percent = canonicalDecimal(raw, false);
  if (percent === null) return { ok: false, message: "Bitte einen Prozentsatz eingeben, z. B. 3,65." };
  const fraction = shiftDecimal(percent, -2);
  if (fraction === null) return { ok: false, message: NOT_A_NUMBER };
  if (decimals(fraction) > maxDecimals) {
    return { ok: false, message: `Höchstens ${maxDecimals - 2} Nachkommastellen in Prozent.` };
  }
  return { ok: true, value: fraction };
}

/** A plain decimal (area, rooms) with at most `maxDecimals` places: "68,4" → "68.4". */
export function toDecimalString(raw: string, maxDecimals: number): Parsed<string> {
  const value = canonicalDecimal(raw, false);
  if (value === null) return { ok: false, message: "Bitte eine Zahl eingeben, z. B. 68,40." };
  if (decimals(value) > maxDecimals) {
    return { ok: false, message: maxDecimals === 1 ? "Höchstens eine Nachkommastelle." : `Höchstens ${maxDecimals} Nachkommastellen.` };
  }
  return { ok: true, value };
}

/** "1/2" or "85/1000" → a Fraction (§9.5: shares as n/d). */
export function toFraction(raw: string): Parsed<Fraction> {
  const match = /^\s*(\d+)\s*\/\s*(\d+)\s*$/.exec(raw);
  if (!match) return { ok: false, message: "Als Bruch eingeben, z. B. 1/2." };
  const numerator = Number(match[1]);
  const denominator = Number(match[2]);
  if (numerator < 1 || denominator < 1 || !Number.isSafeInteger(numerator) || !Number.isSafeInteger(denominator)) {
    return { ok: false, message: "Zähler und Nenner müssen positive ganze Zahlen sein." };
  }
  return { ok: true, value: { numerator, denominator } };
}

/** A whole year, e.g. Baujahr or Steuerjahr. */
export function toYear(raw: string): Parsed<number> {
  if (!/^\s*\d{4}\s*$/.test(raw)) return { ok: false, message: "Bitte eine Jahreszahl eingeben, z. B. 1994." };
  return { ok: true, value: Number(raw.trim()) };
}

/** A fraction from the API ("0.036500") as the percentage to prefill an input: "3,65". */
export function percentInput(value: string | null | undefined): string {
  if (!value) return "";
  return (shiftDecimal(value, 2) ?? value).replace(".", ",");
}

/** A decimal from the API ("1400.50") as the text to prefill an input: "1400,50". */
export function decimalInput(value: string | null | undefined): string {
  return value ? value.replace(".", ",") : "";
}

/** Empty text → null (an optional field left blank). */
export function blankToNull(raw: string): string | null {
  const text = raw.trim();
  return text === "" ? null : text;
}
