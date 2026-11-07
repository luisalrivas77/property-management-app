// Display formatting in German (§9.5). Money, rates, areas and rooms arrive as decimal strings
// ("1400.50", "0.036500") and are formatted by moving digits around, never through a JS
// number, so nothing is rounded on the way to the screen.
import type { Fraction } from "@/api/schemas";

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/** Move the decimal point of a decimal string: shiftDecimal("0.0365", 2) = "3.65". Trailing
 * fraction zeros and leading integer zeros are dropped. Returns null for a non-decimal. */
export function shiftDecimal(value: string, places: number): string | null {
  const match = DECIMAL.exec(value);
  if (!match) return null;
  const [, sign, int = "", frac = ""] = match;
  const digits = int + frac;
  const point = int.length + places;
  let intPart: string;
  let fracPart: string;
  if (point <= 0) {
    intPart = "0";
    fracPart = "0".repeat(-point) + digits;
  } else if (point >= digits.length) {
    intPart = digits + "0".repeat(point - digits.length);
    fracPart = "";
  } else {
    intPart = digits.slice(0, point);
    fracPart = digits.slice(point);
  }
  intPart = intPart.replace(/^0+(?=\d)/, "");
  fracPart = fracPart.replace(/0+$/, "");
  const isZero = /^0+$/.test(intPart + fracPart);
  return `${sign && !isZero ? "-" : ""}${intPart}${fracPart ? `.${fracPart}` : ""}`;
}

function groupThousands(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

/** "1234.5" → "1.234,5"; `minFraction` pads the fraction ("1400" → "1.400,00" with 2). */
export function formatDecimal(value: string | null | undefined, minFraction = 0): string {
  if (value == null || value === "") return "–";
  const match = DECIMAL.exec(value);
  if (!match) return value;
  const [, sign, int = "", frac = ""] = match;
  const fraction = frac.padEnd(minFraction, "0");
  return `${sign}${groupThousands(int)}${fraction ? `,${fraction}` : ""}`;
}

/** "1400.5" → "1.400,50 €". */
export function formatMoney(value: string | null | undefined): string {
  if (value == null || value === "") return "–";
  return `${formatDecimal(value, 2)} €`;
}

/** A rate stored as a fraction ("0.036500") as a percentage: "3,65 %". */
export function formatPercent(value: string | null | undefined): string {
  if (value == null || value === "") return "–";
  const percent = shiftDecimal(value, 2);
  return percent === null ? value : `${formatDecimal(percent)} %`;
}

/** "68.40" → "68,40 m²". */
export function formatArea(value: string | null | undefined): string {
  if (value == null || value === "") return "–";
  return `${formatDecimal(value, 2)} m²`;
}

/** An ISO date ("2024-03-15") as "15.03.2024", without going through Date (no time zones). */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "–";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : value;
}

const dateTime = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short" });

/** A timestamp (recorded_at, created_at) in local time. */
export function formatDateTime(value: string | null | undefined): string {
  return value ? dateTime.format(new Date(value)) : "–";
}

/** {numerator: 1, denominator: 2} → "1/2". A share read from a document as a decimal per
 * thousand is stored exact and unreduced over a power of ten of at least 10000 and is shown
 * as the document wrote it: 5618/100000 → "56,18/1.000" (log10(denominator) − 3 decimals).
 * The text round-trips through toFraction, so it also prefills inputs. */
export function formatFraction(value: Fraction | null | undefined): string {
  if (!value) return "–";
  const { numerator, denominator } = value;
  const power = /^10{4,}$/.test(String(denominator)) ? String(denominator).length - 1 : 0;
  if (power === 0) return `${numerator}/${denominator}`;
  const places = power - 3;
  const digits = String(numerator).padStart(places + 1, "0");
  const int = digits.slice(0, digits.length - places);
  const frac = digits.slice(digits.length - places);
  return `${groupThousands(int)},${frac}/1.000`;
}

/** A share as a percentage, exact from the integers: up to three decimals rounded half-up,
 * trailing zeros dropped, German comma (1/2 → "50 %", 5618/100000 → "5,618 %",
 * 1/3 → "33,333 %"). BigInt, so no floating-point digits appear. */
export function formatSharePercent(value: Fraction): string {
  const numerator = BigInt(value.numerator);
  const denominator = BigInt(value.denominator);
  // Percent × 1000, rounded half-up: (2·n·100·1000 + d) / (2·d).
  const scaled = (2n * numerator * 100000n + denominator) / (2n * denominator);
  const int = (scaled / 1000n).toString();
  const frac = (scaled % 1000n).toString().padStart(3, "0").replace(/0+$/, "");
  return `${groupThousands(int)}${frac ? `,${frac}` : ""} %`;
}

/** A share for reading, with its percentage: "56,18/1.000 (5,618 %)", "1/2 (50 %)". Inputs
 * are prefilled with formatFraction instead. */
export function formatShare(value: Fraction | null | undefined): string {
  if (!value) return "–";
  if (value.denominator <= 0) return formatFraction(value);
  return `${formatFraction(value)} (${formatSharePercent(value)})`;
}

/** Today's local date as "YYYY-MM-DD" (the API's date format). */
export function todayIso(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** A short stand-in for a hash or id: the first characters. */
export function shortId(value: string, length = 8): string {
  return value.replace(/-/g, "").slice(0, length);
}
