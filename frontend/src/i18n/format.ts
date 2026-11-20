// Numbers and dates in the chosen language (phase 10 D284). The same rules as
// src/components/format.ts, which stays German and unchanged for the screens not yet moved:
// money, rates and areas arrive as decimal strings and are formatted by moving digits around,
// never through a JS number, so nothing is rounded on the way to the screen; a date ("2026-03-15")
// is read without going through Date, so no time zone can move it by a day.
//
//   const f = useFormat();
//   f.money("1400.5")   // de „1.400,50 €“   en „€1,400.50“
//   f.percent("0.0365") // de „3,65 %“       en „3.65%“
//   f.date("2026-03-15")// de „15.03.2026“   en „15 Mar 2026“
//   f.month("2026-07")  // de „Juli 2026“    en „July 2026“
import { useMemo } from "react";

import { shiftDecimal } from "@/components/format";

import { LOCALE_TAG, type Locale } from "./core";
import { getLocale, useLocale } from "./locale";

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

const SEPARATORS: Record<Locale, { group: string; decimal: string }> = {
  de: { group: ".", decimal: "," },
  en: { group: ",", decimal: "." },
};

const MONTHS: Record<Locale, readonly string[]> = {
  de: ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
  en: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
};
const MONTHS_SHORT_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export interface Formatter {
  readonly locale: Locale;
  /** "1234.5" → de „1.234,5“, en „1,234.5“; `minFraction` pads the fraction. */
  decimal(value: string | null | undefined, minFraction?: number): string;
  /** "1400.5" → de „1.400,50 €“, en „€1,400.50“. */
  money(value: string | null | undefined): string;
  /** A fraction ("0.0365") as a percentage: de „3,65 %“, en „3.65%“. */
  percent(value: string | null | undefined): string;
  /** "68.4" → de „68,40 m²“, en „68.40 m²“. */
  area(value: string | null | undefined): string;
  /** An ISO date: de „15.03.2026“, en „15 Mar 2026“. */
  date(value: string | null | undefined): string;
  /** A month ("2026-07" or "2026-07-01"): de „Juli 2026“, en „July 2026“. */
  month(value: string | null | undefined): string;
  /** A timestamp in local time, medium date and short time. */
  dateTime(value: string | null | undefined): string;
  /** A whole count ("3") with grouping: de „1.200“, en „1,200“. */
  count(value: number): string;
}

const DASH = "–";

export function formatterFor(locale: Locale): Formatter {
  const { group, decimal: point } = SEPARATORS[locale];
  const dateTimeFormat = new Intl.DateTimeFormat(LOCALE_TAG[locale], { dateStyle: "medium", timeStyle: "short" });

  function decimal(value: string | null | undefined, minFraction = 0): string {
    if (value == null || value === "") return DASH;
    const match = DECIMAL.exec(value);
    if (!match) return value;
    const [, sign, int = "", frac = ""] = match;
    const fraction = frac.padEnd(minFraction, "0");
    const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, group);
    return `${sign}${grouped}${fraction ? `${point}${fraction}` : ""}`;
  }

  return {
    locale,
    decimal,
    money(value) {
      if (value == null || value === "") return DASH;
      const text = decimal(value, 2);
      if (locale === "de") return `${text} €`;
      return text.startsWith("-") ? `-€${text.slice(1)}` : `€${text}`;
    },
    percent(value) {
      if (value == null || value === "") return DASH;
      const percent = shiftDecimal(value, 2);
      if (percent === null) return value;
      return locale === "de" ? `${decimal(percent)} %` : `${decimal(percent)}%`;
    },
    area(value) {
      if (value == null || value === "") return DASH;
      return `${decimal(value, 2)} m²`;
    },
    date(value) {
      if (!value) return DASH;
      const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
      if (!match) return value;
      const [, year, month, day] = match;
      if (locale === "de") return `${day}.${month}.${year}`;
      return `${Number(day)} ${MONTHS_SHORT_EN[Number(month) - 1] ?? month} ${year}`;
    },
    month(value) {
      if (!value) return DASH;
      const match = /^(\d{4})-(\d{2})/.exec(value);
      const name = match ? MONTHS[locale][Number(match[2]) - 1] : undefined;
      return match && name ? `${name} ${match[1]}` : value;
    },
    dateTime(value) {
      return value ? dateTimeFormat.format(new Date(value)) : DASH;
    },
    count(value) {
      return decimal(String(Math.trunc(value)));
    },
  };
}

const cache = new Map<Locale, Formatter>();

/** The formatter for a language, built once. */
export function formatter(locale: Locale = getLocale()): Formatter {
  let found = cache.get(locale);
  if (!found) {
    found = formatterFor(locale);
    cache.set(locale, found);
  }
  return found;
}

/** The formatter for the chosen language, in a component. */
export function useFormat(): Formatter {
  const locale = useLocale();
  return useMemo(() => formatter(locale), [locale]);
}
