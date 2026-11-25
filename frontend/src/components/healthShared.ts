// Phase 9's display helpers (docs/phases/phase-9.md §14): the key figures and the household
// plan show whole euros and one decimal, rounded half away from zero exactly as the backend's
// sentences do (rules/health/bands.py format_money, format_percent, format_months,
// format_multiple), so a card and the sentence beneath it never disagree by a rounding. All of
// it works on decimal strings through BigInt, never a JS number.
import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { localized } from "@/i18n";

import { formatDecimal } from "./format";
import { type Parsed, toDecimalString, toRate } from "./input";

export type Health = components["schemas"]["Health"];
export type HealthTodo = components["schemas"]["HealthTodo"];
export type Light = components["schemas"]["Light"];
export type MarkedFigure = components["schemas"]["MarkedFigure"];
export type BandLimits = components["schemas"]["BandLimits"];
export type BandProposal = components["schemas"]["BandProposal"];
export type HouseholdScenario = components["schemas"]["HouseholdScenario"];
export type HouseholdLevers = components["schemas"]["HouseholdLevers"];
export type HouseholdPlan = components["schemas"]["HouseholdPlan"];
export type HealthMonthView = components["schemas"]["HealthMonthView"];
export type NetWorthPoint = components["schemas"]["NetWorthPoint"];

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/** A decimal string rounded half away from zero to `places`: ("1139.085", 2) → "1139.09",
 * ("-0.4", 0) → "0". Null for a non-decimal. */
export function roundDecimal(value: string, places: number): string | null {
  const match = DECIMAL.exec(value.trim());
  if (!match) return null;
  const [, sign, int = "0", frac = ""] = match;
  let digits: bigint;
  if (frac.length <= places) {
    digits = BigInt(int + frac.padEnd(places, "0"));
  } else {
    const all = BigInt(int + frac);
    const divisor = 10n ** BigInt(frac.length - places);
    digits = all / divisor;
    if (2n * (all % divisor) >= divisor) digits += 1n;
  }
  const text = digits.toString().padStart(places + 1, "0");
  const whole = places > 0 ? text.slice(0, -places) : text;
  const fraction = places > 0 ? text.slice(-places) : "";
  const negative = sign === "-" && digits !== 0n;
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

/** Whole euros: "9990.40" → "9.990 €"; "–" without a value. */
export function formatEuro(value: string | null | undefined): string {
  if (value == null || value === "") return "–";
  const rounded = roundDecimal(value, 0);
  return rounded === null ? value : `${formatDecimal(rounded)} €`;
}

/** Whole euros with a sign: "+1.250 €", "-11.770 €", "0 €". */
export function formatSignedEuro(value: string | null | undefined): string {
  if (value == null || value === "") return "–";
  const rounded = roundDecimal(value, 0);
  if (rounded === null) return value;
  return `${rounded.startsWith("-") || rounded === "0" ? "" : "+"}${formatDecimal(rounded)} €`;
}

/** A ratio as a percent with one decimal: "0.7388" → "73,9 %". */
export function formatRatio(value: string | null | undefined): string {
  if (value == null || value === "") return "–";
  const match = DECIMAL.exec(value.trim());
  if (!match) return value;
  const [, sign, int = "0", frac = ""] = match;
  // × 100 by moving the point, then one decimal.
  const shifted = `${sign}${int}${frac.slice(0, 2).padEnd(2, "0")}${frac.length > 2 ? `.${frac.slice(2)}` : ""}`;
  const rounded = roundDecimal(shifted, 1);
  return rounded === null ? value : `${formatDecimal(rounded)} %`;
}

/** One decimal: months ("3.75" → "3,8") or a multiple ("3.20" → "3,2"). */
export function formatOneDecimal(value: string | null | undefined): string {
  if (value == null || value === "") return "–";
  const rounded = roundDecimal(value, 1);
  return rounded === null ? value : formatDecimal(rounded);
}

/** A typed percentage that may be negative ("-1,5", "−1,5 %") → the fraction ("-0.015"). */
export function toSignedRate(raw: string, maxDecimals: number): Parsed<string> {
  const text = raw.trim().replace(/^−/, "-");
  const negative = text.startsWith("-");
  const parsed = toRate(negative ? text.slice(1) : text, maxDecimals);
  if (!parsed.ok || !negative) return parsed;
  return { ok: true, value: /^0+(\.0+)?$/.test(parsed.value) ? parsed.value : `-${parsed.value}` };
}

/** A typed multiple ("3,5") → "3.5", at most `maxDecimals` places. */
export function toMultiple(raw: string, maxDecimals: number): Parsed<string> {
  return toDecimalString(raw, maxDecimals);
}

const MONTH_NAMES = [
  "Januar",
  "Februar",
  "März",
  "April",
  "Mai",
  "Juni",
  "Juli",
  "August",
  "September",
  "Oktober",
  "November",
  "Dezember",
];

/** "2027-05" → "Mai". */
export function monthName(month: string): string {
  return MONTH_NAMES[Number(month.slice(5, 7)) - 1] ?? month;
}

/** The window as words: ["2027-03", "2027-04", "2027-05"] → "März bis Mai 2027";
 * across a year end "November 2026 bis Januar 2027"; one month "Mai 2027". */
export function monthRange(months: readonly string[]): string {
  const first = months[0];
  const last = months[months.length - 1];
  if (!first || !last) return "";
  if (first === last) return `${monthName(first)} ${first.slice(0, 4)}`;
  const sameYear = first.slice(0, 4) === last.slice(0, 4);
  return `${monthName(first)}${sameYear ? "" : ` ${first.slice(0, 4)}`} bis ${monthName(last)} ${last.slice(0, 4)}`;
}

/** "2027-05-31" → "31. Mai 2027". */
export function longDate(day: string | null | undefined): string {
  if (!day) return "–";
  const [year, month, date] = day.slice(0, 10).split("-");
  if (!year || !month || !date) return day;
  return `${Number(date)}. ${monthName(`${year}-${month}`)} ${year}`;
}

/** The last day of a month: "2027-02" → "2027-02-28". */
export function monthEnd(month: string): string {
  const year = Number(month.slice(0, 4));
  const mon = Number(month.slice(5, 7));
  const last = new Date(Date.UTC(year, mon, 0)).getUTCDate();
  return `${month}-${String(last).padStart(2, "0")}`;
}

export const FIGURE_LABEL: Record<MarkedFigure, string> = localized<MarkedFigure>(
  {
    reserve: "Liquiditätsreserve",
    savings_rate: "Sparquote",
    ltv: "Beleihungsauslauf",
    debt_service_ratio: "Schuldendienstquote",
    debt_ratio: "Verschuldungsgrad",
    debt_to_income: "Schulden zu Jahreseinkommen",
  },
  {
    reserve: "Cash reserve",
    savings_rate: "Savings rate",
    ltv: "Loan-to-value",
    debt_service_ratio: "Debt service ratio",
    debt_ratio: "Debt ratio",
    debt_to_income: "Debt to annual income",
  },
);

/** The order of the card „Ampel-Grenzen festlegen“ (mockup 13). */
export const FIGURES: MarkedFigure[] = ["reserve", "savings_rate", "ltv", "debt_service_ratio", "debt_ratio", "debt_to_income"];

/** Which way is better, as bands.yaml has it ("higher": red below, green from). */
export const HIGHER_IS_BETTER: Record<MarkedFigure, boolean> = {
  reserve: true,
  savings_rate: true,
  ltv: false,
  debt_service_ratio: false,
  debt_ratio: false,
  debt_to_income: false,
};

export const COLOUR_LABEL: Record<components["schemas"]["LightColour"], string> = localized<components["schemas"]["LightColour"]>(
  {
    green: "Grün",
    amber: "Gelb",
    red: "Rot",
  },
  {
    green: "Green",
    amber: "Amber",
    red: "Red",
  },
);

export const COLOUR_CLASS: Record<components["schemas"]["LightColour"], string> = {
  green: "badge health-mark",
  amber: "badge badge-warn health-mark",
  red: "badge badge-danger health-mark",
};

/** A limit as typed and shown: a ratio as a percent, the reserve's red as a percent of the
 * target, Schulden zu Jahreseinkommen as a multiple. */
export function limitText(figure: MarkedFigure, value: string | null | undefined): string {
  if (value == null) return "–";
  return figure === "debt_to_income" ? `${formatOneDecimal(value)}fach` : formatRatio(value);
}

/** The Bezugspunkte of mockup 13 in German, per figure: what each source says and what it does
 * not. bands.yaml holds them in English for the validator; these are their screen text, kept in
 * step with that file by hand. */
export const REFERENCE_POINTS: Record<MarkedFigure, string[]> = localized<MarkedFigure, string[]>(
  {
  reserve: [
    "Keiner. Eine Grenze für die Reserve eines einzelnen Haushalts setzt keine Quelle, die Hausbuch gelesen hat; die Hälfte eures Ziels ist ein Vorschlag.",
  ],
  savings_rate: [
    "Statistisches Bundesamt, Sparquote der privaten Haushalte: wie viel alle Haushalte in Deutschland im Schnitt sparen. Ein Vergleich, keine Grenze für einen einzelnen Haushalt; deshalb steht hier keine Zahl daraus.",
  ],
  ltv: [
    "§ 14 Pfandbriefgesetz: Eine Pfandbriefbank darf Kredite bis 60 % des Beleihungswerts als Deckung nutzen. Das ist eine Regel für Banken, keine Grenze für einen Haushalt, und der Beleihungswert ist nicht die Schätzung, mit der Hausbuch rechnet.",
    "Bundesbank, Finanzstabilitätsbericht: Die Aufsicht berichtet, wie viele Kredite über dem Wert der Immobilie liegen (über 100 %). Keine Grenze für einen einzelnen Haushalt.",
  ],
  debt_service_ratio: [
    "Ratgeber der Verbraucherzentrale und von Stiftung Warentest: 30 bis 40 % des Nettoeinkommens für die Rate eines Kredits fürs eigene Wohnen. Gemeint für selbst genutztes Eigentum, nicht für eine vermietete Wohnung und nicht genau für diese Zahl.",
  ],
  debt_ratio: ["Keiner gefunden."],
  debt_to_income: ["Keiner gefunden."],
  },
  {
    reserve: [
      "None. No source Hausbuch has read sets a limit for a single household's reserve; half of your target is a suggestion.",
    ],
    savings_rate: [
      "Statistisches Bundesamt (Federal Statistical Office), Sparquote der privaten Haushalte (household saving ratio): how much all households in Germany save on average. A comparison, not a limit for a single household; so no figure from it is shown here.",
    ],
    ltv: [
      "§ 14 Pfandbriefgesetz (Covered Bond Act): a covered-bond bank may use loans up to 60% of the Beleihungswert (lending value) as cover. That is a rule for banks, not a limit for a household, and the Beleihungswert is not the estimate Hausbuch works with.",
      "Bundesbank, Finanzstabilitätsbericht (financial stability review): the supervisors report how many loans exceed the property's value (above 100%). Not a limit for a single household.",
    ],
    debt_service_ratio: [
      "Guides by the Verbraucherzentrale (consumer advice centre) and Stiftung Warentest: 30 to 40% of net income for the instalment of a loan for your own home. Meant for an owner-occupied home, not for a rented flat and not exactly for this figure.",
    ],
    debt_ratio: ["None found."],
    debt_to_income: ["None found."],
  },
);

/** Whether a to-do's link captures a month-end, which the screens turn into the button
 * „Monatsabschluss festhalten“: `/assets?month_end=YYYY-MM-DD` (Anlagen › Übersicht holds the
 * month-end card since phase 10 §7.6), or `/?month_end=…` as rules/health and rules/plan still
 * write it (those folders feed rule_version(), so their href stays). */
export function monthEndOf(href: string): string | null {
  const match = /^\/(?:assets)?\?month_end=(\d{4}-\d{2}-\d{2})$/.exec(href);
  return match?.[1] ?? null;
}

// --- household scenarios (D248): append-only rows, the default in force read from the history

/** Every row, history included, so the default's earlier versions can be followed. */
export function loadScenarioHistory(): Promise<HouseholdScenario[]> {
  return ok(api.GET("/api/household-scenarios", { params: { query: { include_history: true } } }));
}

/** The default scenario in force: a default row neither withdrawn nor superseded. */
export function currentDefault(rows: readonly HouseholdScenario[]): HouseholdScenario | null {
  const superseded = new Set(rows.map((row) => row.supersedes_id).filter((id): id is string => id != null));
  return rows.find((row) => row.is_default && !row.withdrawn && !superseded.has(row.id)) ?? null;
}

function bandsKey(bands: readonly BandLimits[] | undefined): string {
  return JSON.stringify(
    [...(bands ?? [])]
      .map((band) => [band.figure, band.green ?? null, band.red ?? null] as const)
      .sort((a, b) => a[0].localeCompare(b[0])),
  );
}

/** The row that first stored the default's current limits: the default followed back through
 * the rows it superseded while their limits stay the same (who confirmed them, and when). */
export function bandsConfirmedBy(rows: readonly HouseholdScenario[], current: HouseholdScenario): HouseholdScenario {
  const byId = new Map(rows.map((row) => [row.id, row]));
  const key = bandsKey(current.levers?.bands);
  let row = current;
  let previous = row.supersedes_id ? byId.get(row.supersedes_id) : undefined;
  while (previous && bandsKey(previous.levers?.bands) === key) {
    row = previous;
    previous = row.supersedes_id ? byId.get(row.supersedes_id) : undefined;
  }
  return row;
}

/** „Szenario speichern“ and „Übernehmen“: a new version of the default with these levers,
 * superseding the one in force, or the first default („Standard“) when there is none. */
export function saveDefaultScenario(levers: HouseholdLevers, current: HouseholdScenario | null): Promise<HouseholdScenario> {
  return ok(
    api.POST("/api/household-scenarios", {
      body: {
        name: current?.name ?? "Standard",
        is_default: true,
        levers,
        supersedes_id: current?.id ?? null,
        withdrawn: false,
      },
    }),
  );
}

/** The levers of a scenario with every default filled in, or HouseholdLevers' own defaults for
 * a household without one (6 months, 10 %, 60 months, 10 years). */
export function leversOf(scenario: HouseholdScenario | null): HouseholdLevers {
  return fillLevers(scenario?.levers);
}

/** Levers with every optional part present, so a screen can edit them without checks. */
export function fillLevers(levers: HouseholdLevers | null | undefined): HouseholdLevers {
  return {
    reserve_target_months: levers?.reserve_target_months ?? "6",
    buffer_percent: levers?.buffer_percent ?? "10",
    horizon_months: levers?.horizon_months ?? 60,
    reserve_accounts: { added: levers?.reserve_accounts?.added ?? [], removed: levers?.reserve_accounts?.removed ?? [] },
    plan_overrides: levers?.plan_overrides ?? [],
    income_changes: levers?.income_changes ?? [],
    one_offs: levers?.one_offs ?? [],
    value_change: {
      depot: levers?.value_change?.depot ?? null,
      crypto: levers?.value_change?.crypto ?? null,
      // Phase 10 §7.11 (D304): the reserve's yearly rate, kept on every save (no screen sets it yet).
      reserve: levers?.value_change?.reserve ?? null,
    },
    net_worth_years: levers?.net_worth_years ?? 10,
    bands: levers?.bands ?? [],
    // D263: kept on every save, from Übersicht's limits too; mean_at_save is the server's.
    spending_changes: levers?.spending_changes ?? [],
    // Phase 10 D274: the household's own budget targets and class moves, kept on every save.
    budget_targets: {
      needs_max: levers?.budget_targets?.needs_max ?? null,
      wants_max: levers?.budget_targets?.wants_max ?? null,
      saving_min: levers?.budget_targets?.saving_min ?? null,
      housing_max: levers?.budget_targets?.housing_max ?? null,
    },
    budget_class_overrides: levers?.budget_class_overrides ?? {},
    // Phase 10 §7.12 (D307): the household's one-off switches per category, kept on every save
    // (no screen sets them yet; a save without them would drop them).
    one_off_overrides: levers?.one_off_overrides ?? {},
    // Phase 10 §7.9 (d): the goal and its chosen scenario, kept on every save (a save without
    // them would drop the goal).
    goal: levers?.goal ?? null,
    goal_choice: levers?.goal_choice ?? null,
    // Phase 10 §7.11 (a) 2 (D301): the household's investment split, kept on every save (no
    // screen sets it yet; a save without it would drop it).
    investment_split: levers?.investment_split ?? null,
    // Every key, so a lever added to the schema and not carried here fails the typecheck instead
    // of being dropped by a save.
  } satisfies Required<HouseholdLevers>;
}

/** Each member's display name by user id, for „eingetragen von …“. */
export async function loadMemberNames(): Promise<Map<string, string>> {
  const members = await ok(api.GET("/api/auth/members"));
  return new Map(members.map((member) => [member.user.id, member.user.display_name]));
}
