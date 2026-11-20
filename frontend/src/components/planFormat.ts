// Planung's figures in the chosen language (phase 10 D284, step 4). German keeps phase 9's
// helpers exactly (healthShared, format.ts), so a German screen reads as before; English uses
// the same rounding (half away from zero on decimal strings, never a JS number) with the
// separators and month names of src/i18n/format.ts. Nothing is computed here, only shown.
import { useMemo } from "react";

import { formatter, type Locale, useLocale } from "@/i18n";

import { formatDate, formatMoney, formatMonth } from "./format";
import {
  formatEuro,
  formatOneDecimal,
  formatRatio,
  formatSignedEuro,
  longDate,
  monthName,
  monthRange,
  roundDecimal,
} from "./healthShared";

const MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export interface PlanFormat {
  locale: Locale;
  /** Whole euros: de „9.990 €“, en „€9,990“. */
  euro(value: string | null | undefined): string;
  /** Whole euros with a sign: de „+1.250 €“, en „+€1,250“. */
  signedEuro(value: string | null | undefined): string;
  /** Euros and cents: de „682,50 €“, en „€682.50“. */
  money(value: string | null | undefined): string;
  /** A ratio as a percent with one place: "0.7388" → de „73,9 %“, en „73.9%“. */
  ratio(value: string | null | undefined): string;
  /** A ratio as a whole percent: "0.2245" → de „22 %“, en „22%“. */
  wholePercent(value: string | null | undefined): string;
  /** A number with one place, a trailing „,0“ dropped: "6.0" → „6“, "6.5" → de „6,5“. */
  short(value: string | null | undefined): string;
  /** A percent with one place as stored ("50.0") → de „50 %“, en „50%“. */
  percentValue(value: string | null | undefined): string;
  month(value: string | null | undefined): string;
  monthName(month: string): string;
  monthRange(months: readonly string[]): string;
  longDate(day: string | null | undefined): string;
  date(day: string | null | undefined): string;
}

function enFormat(): PlanFormat {
  const f = formatter("en");
  const euro = (value: string | null | undefined) => {
    if (value == null || value === "") return "–";
    const rounded = roundDecimal(value, 0);
    if (rounded === null) return value;
    return rounded.startsWith("-") ? `-€${f.decimal(rounded.slice(1))}` : `€${f.decimal(rounded)}`;
  };
  const one = (value: string | null | undefined) => {
    if (value == null || value === "") return "–";
    const rounded = roundDecimal(value, 1);
    return rounded === null ? value : f.decimal(rounded).replace(/\.0$/, "");
  };
  const name = (month: string) => MONTHS_EN[Number(month.slice(5, 7)) - 1] ?? month;
  const percentOf = (value: string | null | undefined, places: number) => {
    if (value == null || value === "") return "–";
    const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
    if (!match) return value;
    const [, sign, int = "0", frac = ""] = match;
    const shifted = `${sign}${int}${frac.slice(0, 2).padEnd(2, "0")}${frac.length > 2 ? `.${frac.slice(2)}` : ""}`;
    const rounded = roundDecimal(shifted, places);
    return rounded === null ? value : `${f.decimal(rounded)}%`;
  };
  return {
    locale: "en",
    euro,
    signedEuro(value) {
      const text = euro(value);
      if (value == null || value === "" || text.startsWith("-") || text === "€0") return text;
      return `+${text}`;
    },
    money: (value) => f.money(value),
    ratio: (value) => percentOf(value, 1),
    wholePercent: (value) => percentOf(value, 0),
    short: one,
    percentValue: (value) => (value == null || value === "" ? "–" : `${one(value)}%`),
    month: (value) => f.month(value),
    monthName: name,
    monthRange(months) {
      const first = months[0];
      const last = months[months.length - 1];
      if (!first || !last) return "";
      if (first === last) return `${name(first)} ${first.slice(0, 4)}`;
      const sameYear = first.slice(0, 4) === last.slice(0, 4);
      return `${name(first)}${sameYear ? "" : ` ${first.slice(0, 4)}`} to ${name(last)} ${last.slice(0, 4)}`;
    },
    longDate(day) {
      if (!day) return "–";
      const [year, month, date] = day.slice(0, 10).split("-");
      if (!year || !month || !date) return day;
      return `${Number(date)} ${name(`${year}-${month}`)} ${year}`;
    },
    date: (day) => f.date(day),
  };
}

function deFormat(): PlanFormat {
  const one = (value: string | null | undefined) => formatOneDecimal(value).replace(/,0$/, "");
  return {
    locale: "de",
    euro: formatEuro,
    signedEuro: formatSignedEuro,
    money: formatMoney,
    ratio: formatRatio,
    wholePercent(value) {
      if (value == null || value === "") return "–";
      const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
      if (!match) return value;
      const [, sign, int = "0", frac = ""] = match;
      const shifted = `${sign}${int}${frac.slice(0, 2).padEnd(2, "0")}${frac.length > 2 ? `.${frac.slice(2)}` : ""}`;
      const rounded = roundDecimal(shifted, 0);
      return rounded === null ? value : `${formatter("de").decimal(rounded)} %`;
    },
    short: one,
    percentValue: (value) => (value == null || value === "" ? "–" : `${one(value)} %`),
    month: formatMonth,
    monthName,
    monthRange,
    longDate,
    date: formatDate,
  };
}

const FORMATS: Record<Locale, PlanFormat> = { de: deFormat(), en: enFormat() };

export function planFormat(locale: Locale): PlanFormat {
  return FORMATS[locale];
}

export function usePlanFormat(): PlanFormat {
  const locale = useLocale();
  return useMemo(() => FORMATS[locale], [locale]);
}
