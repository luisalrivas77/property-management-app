// Text inputs → the API's string formats (§9.5). Each normaliser returns either the value to
// send or a fixed message for the field, German or English as chosen (phase 10 D284). Messages never repeat what was typed. These
// are the "basic number formats" checks only; every domain rule stays with the API.
import type { Fraction } from "@/api/schemas";

import { getLocale } from "@/i18n/locale";

import { shiftDecimal } from "./format";

export type Parsed<T> = { ok: true; value: T } | { ok: false; message: string };

const MESSAGES = {
  de: {
    notANumber: "Bitte eine Zahl eingeben, z. B. 1.400,50.",
    cents: "Höchstens zwei Nachkommastellen (Cent).",
    notAPercent: "Bitte einen Prozentsatz eingeben, z. B. 3,65.",
    percentPlaces: (n: number) => `Höchstens ${n} Nachkommastellen in Prozent.`,
    notADecimal: "Bitte eine Zahl eingeben, z. B. 68,40.",
    places: (n: number) => (n === 1 ? "Höchstens eine Nachkommastelle." : `Höchstens ${n} Nachkommastellen.`),
    notAShare: "Als Bruch oder Prozent eingeben, z. B. 1/2, 56,18/1.000 oder 5,618 %.",
    shareWhole: "Zähler und Nenner müssen positive ganze Zahlen sein.",
    shareAtMostOne: "Ein Anteil ist höchstens das Ganze (1/1 oder 100 %).",
    notAYear: "Bitte eine Jahreszahl eingeben, z. B. 1994.",
  },
  en: {
    notANumber: "Please enter a number, e.g. 1400.50.",
    cents: "At most two decimal places (cents).",
    notAPercent: "Please enter a percentage, e.g. 3.65.",
    percentPlaces: (n: number) => `At most ${n} decimal places in percent.`,
    notADecimal: "Please enter a number, e.g. 68.40.",
    places: (n: number) => (n === 1 ? "At most one decimal place." : `At most ${n} decimal places.`),
    notAShare: "Enter a fraction or a percentage, e.g. 1/2, 56.18/1000 or 5.618 %.",
    shareWhole: "Numerator and denominator must be positive whole numbers.",
    shareAtMostOne: "A share is at most the whole (1/1 or 100 %).",
    notAYear: "Please enter a year, e.g. 1994.",
  },
};
const m = () => MESSAGES[getLocale()];

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
  if (value === null) return { ok: false, message: m().notANumber };
  if (decimals(value) > 2) return { ok: false, message: m().cents };
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
  if (percent === null) return { ok: false, message: m().notAPercent };
  const fraction = shiftDecimal(percent, -2);
  if (fraction === null) return { ok: false, message: m().notANumber };
  if (decimals(fraction) > maxDecimals) {
    return { ok: false, message: m().percentPlaces(maxDecimals - 2) };
  }
  return { ok: true, value: fraction };
}

/** A plain decimal (area, rooms) with at most `maxDecimals` places: "68,4" → "68.4". */
export function toDecimalString(raw: string, maxDecimals: number): Parsed<string> {
  const value = canonicalDecimal(raw, false);
  if (value === null) return { ok: false, message: m().notADecimal };
  if (decimals(value) > maxDecimals) {
    return { ok: false, message: m().places(maxDecimals) };
  }
  return { ok: true, value };
}

/** One side of a share as [digits, decimals], by the backend's rule (extraction_parse
 * _share_number, the same as canonicalDecimal with thousands): a comma is the decimal mark and
 * dots group thousands; without a comma, dots grouping exactly three digits are thousands
 * ("1.000"), else a lone dot is the decimal mark ("56.18"). */
function shareNumber(raw: string, thousands: boolean): [string, number] | null {
  const value = canonicalDecimal(raw, thousands);
  if (value === null) return null;
  const [int = "", frac = ""] = value.split(".");
  return [int + frac, frac.length];
}

/** A share, exact and unreduced (§9.5: shares as n/d): "1/2", "85/1000", "56,18/1.000" or
 * "56.18/1000" → 5618/100000 (both sides scaled by the same power of ten, as the backend reads
 * a document); a percentage "5,618 %" → 5618/100000, "50 %" → 50/100. At most the whole. */
export function toFraction(raw: string): Parsed<Fraction> {
  const text = raw.trim();
  let numeratorDigits: string;
  let denominatorDigits: string;
  if (/^[^%]*%$/.test(text)) {
    // A percentage is at most 100, so a lone dot is its decimal mark ("5.618 %").
    const side = shareNumber(text, false);
    if (!side) return { ok: false, message: m().notAShare };
    [numeratorDigits] = side;
    denominatorDigits = `100${"0".repeat(side[1])}`;
  } else {
    const parts = text.split("/");
    if (parts.length !== 2 || text.includes("%")) return { ok: false, message: m().notAShare };
    const top = shareNumber(parts[0] ?? "", true);
    const bottom = shareNumber(parts[1] ?? "", true);
    if (!top || !bottom) return { ok: false, message: m().notAShare };
    const places = Math.max(top[1], bottom[1]);
    numeratorDigits = top[0] + "0".repeat(places - top[1]);
    denominatorDigits = bottom[0] + "0".repeat(places - bottom[1]);
  }
  const numerator = Number(numeratorDigits);
  const denominator = Number(denominatorDigits);
  if (numerator < 1 || denominator < 1 || !Number.isSafeInteger(numerator) || !Number.isSafeInteger(denominator)) {
    return { ok: false, message: m().shareWhole };
  }
  if (numerator > denominator) return { ok: false, message: m().shareAtMostOne };
  return { ok: true, value: { numerator, denominator } };
}

/** A whole year, e.g. Baujahr or Steuerjahr. */
export function toYear(raw: string): Parsed<number> {
  if (!/^\s*\d{4}\s*$/.test(raw)) return { ok: false, message: m().notAYear };
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
