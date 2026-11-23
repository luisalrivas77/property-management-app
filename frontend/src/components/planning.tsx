"use client";

// Planung › Immobilie (phase-4.md §9, docs/mockups/05-performance.png): the actual years and
// the projection to a sale year of one property, its levers and its saved scenarios.
//
// - GET …/performance/actuals and …/performance/projection compute everything on every read
//   (D85, D86); every euro figure, rate and share on this screen is one the backend returned
//   (principle 1). The screen formats, links and lays out; it adds, subtracts and multiplies
//   nothing. The equity bars take their heights from the returned equity, for display only.
// - The levers are query parameters of the projection (D87): the ± buttons change the page's
//   URL (?wertsteigerung=, ?mieterhoehung=, ?verkauf=, ?szenario=) and the screen refetches;
//   nothing is written until "Szenario speichern" records a scenario row.
// - A property the backend cannot compute (a purchase year before the first rule set or
//   tariff: projection.no_rule_set, tariff.no_file) gets a plain notice, not an error.
// - Every figure opens its sources (principle 2): the year's, the tax effect's or the bridge
//   row's SourceRefs; a payment shows its bank line and Beleg, as on the Steuern screen.
// - The card „Nebenkostenpauschalen“ (D155 (5)): GET …/flat-rate-check, the costs when each
//   Pauschale was set against today, per tenancy its share and verdict. Since phase 10 §7.6 it
//   sits on the property page beside the tenancies (components/propertyFlatRate.tsx), no longer
//   above the actuals here; the actuals' Pauschale coverage links to it there.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type {
  AllocationFact,
  Assumption,
  BridgeRow,
  CostLevel,
  CostRate,
  FlatRateCheck,
  Levers,
  Projection,
  Property,
  SaleResult,
  Scenario,
  TaxEffect,
  TaxRateResult,
  TaxSourceRef,
  TenancyFlatRate,
  YearFigures,
} from "@/api/schemas";
import { formatter, getLocale, useT } from "@/i18n";
import { common } from "@/i18n/common";
import { flatRateText } from "@/i18n/flatRate";
import { propertyPlanning } from "@/i18n/propertyPlanning";

import { FLAT_RATE_CARD_ID, FlatRateActions, flatRateCardHref } from "./flatRate";

import { Field, FormBanner, Submit, useFormErrors } from "./form";
import { shiftDecimal, todayIso } from "./format";
import { ALLOCATION_CATEGORY, TAX_SOURCE_KIND } from "./labels";
import { linkFor } from "./tax";
import { payInputsHref } from "./taxYear";
import { ActionDialog, Loading, Notice } from "./ui";
import { useLoad, type Loaded } from "./useLoad";

import "./tax.css";

type Texts = (typeof propertyPlanning)["de"];

// Read-only figures in the chosen language; no input on this screen is prefilled from them, and
// German reads exactly as with ./format.
const formatMoney = (value: string | null | undefined) => formatter().money(value);
const formatDate = (value: string | null | undefined) => formatter().date(value);
const formatDateTime = (value: string | null | undefined) => formatter().dateTime(value);
const formatDecimal = (value: string | null | undefined, minFraction = 0) => formatter().decimal(value, minFraction);

/** A percentage figure: de „2,0 %“, en „2.0%“. */
function withPercent(text: string): string {
  return getLocale() === "en" ? `${text}%` : `${text} %`;
}

// --- Decimal strings, formatted (never computed) ----------------------------------------------

const ZERO = /^-?0*(\.0*)?$/;

function isZero(value: string): boolean {
  return ZERO.test(value);
}

function isNegative(value: string): boolean {
  return value.startsWith("-") && !isZero(value);
}

/** "+1.234,00 €", "-1.234,00 €", "0,00 €"; "–" without a value. */
function signedMoney(value: string | null | undefined): string {
  if (value == null) return "–";
  if (isZero(value)) return formatMoney(value.replace(/^-/, ""));
  return isNegative(value) ? formatMoney(value) : `+${formatMoney(value)}`;
}

/** The colour of a signed figure: a gain green, a loss brown. `lowerIsBetter` for the tax
 * effect, where a negative effect is a saving. */
function tone(value: string | null | undefined, lowerIsBetter = false): string | undefined {
  if (value == null || isZero(value)) return undefined;
  return isNegative(value) !== lowerIsBetter ? "planning-bad" : "planning-good";
}

/** A rate as a fraction ("0.020000", "0.0357") as a percentage: "2,0 %" with minFraction 1. */
function ratePercent(value: string, minFraction = 0): string {
  const percent = shiftDecimal(value, 2);
  return percent === null ? value : withPercent(formatDecimal(percent, minFraction));
}

/** A yearly return as a fraction ("0.032145") as a percentage rounded half-up to one place
 * ("3,2 %"), on the digits of the string, so no floating point is involved. */
function returnPercent(value: string | null | undefined): string {
  if (value == null) return "–";
  const percent = shiftDecimal(value, 2);
  const match = percent === null ? null : /^(-?)(\d+)(?:\.(\d+))?$/.exec(percent);
  if (!match) return value;
  const [, sign = "", int = "0", frac = ""] = match;
  const tenths = BigInt(int + (frac.charAt(0) || "0")) + (Number(frac.charAt(1) || "0") >= 5 ? 1n : 0n);
  const digits = tenths.toString().padStart(2, "0");
  const text = formatDecimal(`${digits.slice(0, -1)}.${digits.slice(-1)}`, 1);
  return withPercent(`${sign && tenths !== 0n ? "-" : ""}${text}`);
}

/** A percent the backend already rounded ("33.8") as "33,8 %". */
function percentText(value: string): string {
  return withPercent(formatDecimal(value, 1));
}

function addressLine(property: Property): string {
  const a = property.address;
  return `${a.street} ${a.house_number}, ${a.postal_code} ${a.city}`;
}

// --- Refusals ---------------------------------------------------------------------------------

type Refusal = "projection.no_rule_set" | "tariff.no_file";

const REFUSALS: readonly Refusal[] = ["projection.no_rule_set", "tariff.no_file"];

interface Refused {
  refused: Refusal;
}

function isRefused(value: object): value is Refused {
  return "refused" in value;
}

/** The call's data, or the refusal when the backend cannot compute this property. */
async function orRefused<T>(call: Promise<T>): Promise<T | Refused> {
  try {
    return await call;
  } catch (error) {
    if (error instanceof ApiError && error.body) {
      const codes = ruleCodes(error.body);
      const refusal = REFUSALS.find((code) => codes.includes(code));
      if (refusal) return { refused: refusal };
    }
    throw error;
  }
}

function RefusalNotice({ refusal }: { refusal: Refusal }) {
  const t = useT(propertyPlanning);
  return (
    <div className="notice notice-info" role="status">
      <p>{refusal === "tariff.no_file" ? t.refusalNoTariff : t.refusalNoRuleSet}</p>
    </div>
  );
}

// --- Levers in the URL ------------------------------------------------------------------------

interface LeverParams {
  scenario: string | null;
  valueGrowth: string | null;
  rentGrowth: string | null;
  saleYear: number | null;
}

const NO_LEVERS: LeverParams = { scenario: null, valueGrowth: null, rentGrowth: null, saleYear: null };

const PARAM = {
  scenario: "szenario",
  valueGrowth: "wertsteigerung",
  rentGrowth: "mieterhoehung",
  saleYear: "verkauf",
} as const;

/** A Rate as the API takes it: 0 ≤ r < 1, at most six places. */
const RATE = /^0(\.\d{1,6})?$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function readParams(search: { get(name: string): string | null }): LeverParams {
  const rate = (name: string) => {
    const value = search.get(name);
    return value !== null && RATE.test(value) ? value : null;
  };
  const scenario = search.get(PARAM.scenario);
  const saleYear = search.get(PARAM.saleYear);
  return {
    scenario: scenario !== null && UUID.test(scenario) ? scenario : null,
    valueGrowth: rate(PARAM.valueGrowth),
    rentGrowth: rate(PARAM.rentGrowth),
    saleYear: saleYear !== null && /^\d{4}$/.test(saleYear) ? Number(saleYear) : null,
  };
}

function planningHref(propertyId: string | null, params: LeverParams): string {
  // Anlagen › Immobilie (phase 10 §7.6, D288; Planung's Immobilie section until then): the tab
  // stays in the URL with the levers.
  const query = new URLSearchParams({ tab: "immobilie" });
  if (propertyId) query.set("immobilie", propertyId);
  if (params.scenario) query.set(PARAM.scenario, params.scenario);
  if (params.valueGrowth !== null) query.set(PARAM.valueGrowth, params.valueGrowth);
  if (params.rentGrowth !== null) query.set(PARAM.rentGrowth, params.rentGrowth);
  if (params.saleYear !== null) query.set(PARAM.saleYear, String(params.saleYear));
  return `/assets?${query.toString()}`;
}

const RATE_PLACES = 6;
const RATE_STEP = 5000; // millionths: 0,5 Prozentpunkte
const RATE_MAX = 200000; // 20 %

/** A lever rate one step up or down, in whole millionths of the decimal string (no floating
 * point); null when the step would leave 0 … 20 %. This is a lever the user sets, not a money
 * figure. */
function stepRate(value: string, direction: 1 | -1): string | null {
  const micros = shiftDecimal(value, RATE_PLACES);
  if (micros === null || !/^\d+$/.test(micros)) return null;
  const next = Number(micros) + direction * RATE_STEP;
  if (!Number.isSafeInteger(next) || next < 0 || next > RATE_MAX) return null;
  return shiftDecimal(String(next), -RATE_PLACES);
}

// --- Loading that keeps the last result -------------------------------------------------------

/** Like useLoad, but the previous result stays on screen while the levers change (a new
 * `load`), so the screen does not blank on every ± click. */
function useStickyLoad<T>(load: () => Promise<T>): Loaded<T> {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<{ load: () => Promise<T>; version: number; data?: T; error: unknown } | null>(null);

  useEffect(() => {
    let cancelled = false;
    load().then(
      (data) => {
        if (!cancelled) setResult({ load, version, data, error: null });
      },
      (error: unknown) => {
        if (!cancelled) setResult((previous) => ({ load, version, data: previous?.data, error }));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [load, version]);

  const reload = useCallback(() => setVersion((n) => n + 1), []);
  const current = result?.load === load && result.version === version;
  return { data: result?.data, error: current ? (result?.error ?? null) : null, loading: !current, reload };
}

// --- The screen -------------------------------------------------------------------------------

export function PlanningScreen({
  property,
  properties,
  head,
}: {
  property: Property;
  properties: Property[];
  /** Cards above the actuals, given the projection once it is read (Anlagen › Immobilie). */
  head?: (projection: Projection | undefined) => ReactNode;
}) {
  const router = useRouter();
  const search = useSearchParams();
  const ids = useId();
  const params = readParams(search);
  const { scenario: scenarioId, valueGrowth, rentGrowth, saleYear } = params;
  const overridden = valueGrowth !== null || rentGrowth !== null || saleYear !== null;
  const multi = properties.length > 1;
  const [saved, setSaved] = useState<string | null>(null);
  const t = useT(propertyPlanning);

  const go = useCallback(
    (next: LeverParams) => router.replace(planningHref(multi ? property.id : null, next), { scroll: false }),
    [router, multi, property.id],
  );

  const loadActuals = useCallback(
    () =>
      orRefused(
        ok(api.GET("/api/properties/{property_id}/performance/actuals", { params: { path: { property_id: property.id } } })),
      ),
    [property.id],
  );
  const actuals = useLoad(loadActuals);
  const loadProjection = useCallback(
    () =>
      orRefused(
        ok(
          api.GET("/api/properties/{property_id}/performance/projection", {
            params: {
              path: { property_id: property.id },
              query: { scenario_id: scenarioId, value_growth: valueGrowth, rent_growth: rentGrowth, sale_year: saleYear },
            },
          }),
        ),
      ),
    [property.id, scenarioId, valueGrowth, rentGrowth, saleYear],
  );
  const projection = useStickyLoad(loadProjection);
  const loadScenarios = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/scenarios", { params: { path: { property_id: property.id } } })),
    [property.id],
  );
  const scenarios = useLoad(loadScenarios);

  const actualsRefused = actuals.data !== undefined && isRefused(actuals.data);
  const actualYears = actuals.data !== undefined && !isRefused(actuals.data) ? actuals.data.years : undefined;

  return (
    <>
      <div className="page-head planning-head">
        <div>
          <p className="eyebrow">{property.label}</p>
          <h2>{t.heading}</h2>
          <span className="muted">{addressLine(property)}</span>
        </div>
        <span className="spacer" />
        {multi ? (
          <label htmlFor={`${ids}-property`} className="inline">
            {t.property}
            <select
              id={`${ids}-property`}
              value={property.id}
              onChange={(event) => router.push(planningHref(event.target.value, NO_LEVERS))}
            >
              {properties.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <div className="stack planning">
        {head ? head(projection.data !== undefined && !isRefused(projection.data) ? projection.data : undefined) : null}
        <Loading state={actuals}>
          {(data) =>
            isRefused(data) ? (
              <RefusalNotice refusal={data.refused} />
            ) : (
              <ActualsCard years={data.years} propertyId={property.id} />
            )
          }
        </Loading>
        {actualsRefused ? null : (
          <>
            <Loading state={projection}>
              {(data) =>
                isRefused(data) ? (
                  <RefusalNotice refusal={data.refused} />
                ) : (
                  <ProjectionCard
                    projection={data}
                    actualYears={actualYears}
                    property={property}
                    busy={projection.loading}
                    overridden={overridden}
                    onLevers={(next) => go({ ...params, ...next })}
                    onReset={() => go({ ...NO_LEVERS, scenario: scenarioId })}
                  />
                )
              }
            </Loading>
            {projection.data === undefined && projection.error != null && (overridden || scenarioId !== null) ? (
              <div className="row">
                <button type="button" className="secondary" onClick={() => go(NO_LEVERS)}>
                  {t.resetAssumptions}
                </button>
              </div>
            ) : null}
            {projection.data !== undefined && !isRefused(projection.data) ? (
              <ScenariosCard
                propertyId={property.id}
                projection={projection.data}
                scenarios={scenarios}
                overridden={overridden}
                saved={saved}
                onShow={(id) => {
                  setSaved(null);
                  go({ ...NO_LEVERS, scenario: id });
                }}
                onSaved={(created) => {
                  setSaved(created.name);
                  scenarios.reload();
                  go({ ...NO_LEVERS, scenario: created.id });
                }}
              />
            ) : null}
          </>
        )}
        <p className="muted planning-footer">{t.disclaimer}</p>
      </div>
    </>
  );
}

// --- Actuals card ---------------------------------------------------------------------------

function sourceLabel(t: Texts, source: TaxEffect["source"]): string {
  return source === "bescheid" ? t.sourceBescheid : source === "override" ? t.sourceOverride : t.sourceEstimate;
}

/** "33,8 % effektiv", or the tariff's slope at a result of 0. */
function rateText(t: Texts, effect: TaxEffect): string | null {
  if (effect.effective_rate) return t.effectiveRate(percentText(effect.effective_rate));
  if (effect.slope) return t.marginalRate(percentText(effect.slope));
  return null;
}

function Figure({ label, value, valueClass, sub }: { label: string; value: ReactNode; valueClass?: string; sub?: ReactNode }) {
  return (
    <div className="planning-figure" role="group" aria-label={label}>
      <p className="planning-figure-label">{label}</p>
      <p className={valueClass ? `planning-figure-value ${valueClass}` : "planning-figure-value"}>{value}</p>
      {sub ? <p className="planning-figure-sub">{sub}</p> : null}
    </div>
  );
}

/** The current year as far as booked (D85: "bisher"), else the latest actual year. */
function shownYear(years: YearFigures[]): YearFigures | undefined {
  return years.find((y) => (y.flags ?? []).includes("so_far")) ?? years[years.length - 1];
}

function ActualsCard({ years, propertyId }: { years: YearFigures[]; propertyId: string }) {
  const t = useT(propertyPlanning);
  const year = shownYear(years);
  if (!year) {
    return (
      <section className="card">
        <h2>{t.actual}</h2>
        <p className="muted">{t.noActuals}</p>
      </section>
    );
  }
  const soFar = (year.flags ?? []).includes("so_far");
  const estimated = (year.flags ?? []).includes("tax_estimated");
  const effect = year.tax_effect;
  const title = soFar ? t.yearSoFar(year.year) : t.yearActual(year.year);
  return (
    <section className="card planning-actuals" aria-label={title}>
      <div className="planning-card-head">
        <h2>{title}</h2>
        <span className="muted">{t.actualsSub}</span>
        <span className="spacer" />
        {(year.sources ?? []).length > 0 ? (
          <SourcesDialog label={t.sources} title={title} refs={year.sources ?? []} propertyId={propertyId} />
        ) : null}
      </div>
      <div className="planning-figures">
        <Figure label={t.rent} value={signedMoney(year.rent_in)} />
        <Figure label={t.spending} value={formatMoney(year.cash_out)} sub={t.spendingSub} />
        <Figure
          label={estimated ? t.taxEffectEstimated : t.taxEffect}
          value={effect ? signedMoney(effect.effect) : "—"}
          valueClass={effect ? tone(effect.effect, true) : undefined}
          sub={
            effect ? (
              [
                isZero(effect.effect) ? null : isNegative(effect.effect) ? t.lessTax : t.moreTax,
                year.taxable_result != null ? t.result(signedMoney(year.taxable_result)) : null,
                rateText(t, effect),
              ]
                .filter(Boolean)
                .join(" · ")
            ) : (
              <Link href={payInputsHref(year.year)}>{t.enterIncome}</Link>
            )
          }
        />
        <Figure label={t.netCash} value={signedMoney(year.net_cash)} valueClass={tone(year.net_cash)} />
        <Figure label={t.principal} value={signedMoney(year.principal_repaid)} sub={t.principalSub} />
      </div>
      <p className="muted planning-subline">
        {t.umlagen(formatMoney(year.umlagen_in), formatMoney(year.umlagen_out), signedMoney(year.umlagen_net))}
        {isZero(year.other_income) ? null : t.otherIncome(formatMoney(year.other_income))}
      </p>
      {year.flat_rate_gap_monthly != null && year.flat_rate_months != null ? (
        <FlatRateCoverage year={year} gap={year.flat_rate_gap_monthly} months={year.flat_rate_months} propertyId={propertyId} />
      ) : null}
    </section>
  );
}

/** The Pauschale coverage (D91, D92): flat-rate charges received against the umlagefähige costs
 * paid (the flat's electricity included), per month the Pauschalen were in force. Information
 * only. Whether a Pauschale may rise is the card „Nebenkostenpauschalen“ (D155), which compares
 * the costs when each Pauschale was set with today and needs no second year; it sits on the
 * property page since phase 10 §7.6, so the link goes there. */
function FlatRateCoverage({ year, gap, months, propertyId }: { year: YearFigures; gap: string; months: number; propertyId: string }) {
  const t = useT(propertyPlanning);
  const verdict = isNegative(gap) ? t.coverageOver : isZero(gap) ? t.coverageExact : t.coverageCovered;
  return (
    <p className="planning-subline planning-flat-rate">
      <strong>{t.coverageLabel}</strong> {t.coverageText(formatMoney(year.flat_rate_in), formatMoney(year.umlagen_out))}{" "}
      <span className={tone(gap)}>{t.coverageGap(signedMoney(gap), months)}</span>
      . {verdict}{" "}
      <span className="muted">
        {t.coverageLinkBefore} <Link href={flatRateCardHref(propertyId)}>{t.coverageLink}</Link>.
      </span>
    </p>
  );
}

// --- Nebenkostenpauschalen card (D155) --------------------------------------------------------

function basisText(t: Texts, basis: CostRate["basis"]): string {
  switch (basis) {
    case "monthly_payment":
      return t.basisMonthly;
    case "yearly_premium":
      return t.basisYearly;
    case "schedule":
      return t.basisSchedule;
    case "twelve_months":
      return t.basisTwelveMonths;
    default:
      return t.basisNone;
  }
}

/** A rate with its sources and how it was found. */
function RateCell({ rate, date, propertyId }: { rate: CostRate | undefined; date: string; propertyId: string }) {
  const t = useT(propertyPlanning);
  if (!rate) return <td className="num">–</td>;
  const label = ALLOCATION_CATEGORY[rate.category];
  return (
    <td className="num">
      <SourcesDialog
        label={formatMoney(rate.monthly)}
        title={t.rateOn(label, formatDate(date))}
        refs={rate.sources ?? []}
        propertyId={propertyId}
      />
      <span className="tax-line-sub">
        {basisText(t, rate.basis)}
        {rate.from_later ? t.firstPaymentAfter : ""}
      </span>
    </td>
  );
}

function rateOf(level: CostLevel | undefined, category: CostRate["category"]): CostRate | undefined {
  return level?.rates.find((rate) => rate.category === category);
}

/** „kann steigen, frühestens ab 01.04.2027“, „senken ab 01.01.2027“ or why not. */
function verdictText(t: Texts, tenancy: TenancyFlatRate, raiseAbove: string): string {
  if (tenancy.recorded_from) {
    return t.verdictRecorded(formatDate(tenancy.recorded_from), formatMoney(tenancy.recorded_pauschale));
  }
  if (tenancy.verdict === "raise") return t.verdictRaise(formatDate(tenancy.earliest_start));
  if (tenancy.verdict === "lower") return t.verdictLower(formatDate(tenancy.fell_on));
  return isZero(tenancy.rise) ? t.verdictUnchanged : t.verdictBelow(formatMoney(raiseAbove));
}

/** Per date a Pauschale was set: the costs then and now per category; per tenancy its share
 * of the change and the verdict; the coverage of the shown year; settlements as hints. Every
 * figure is the check's own (principle 1); nothing here adds or splits. Shown on the property
 * page (components/propertyFlatRate.tsx) since phase 10 §7.6. */
export function FlatRateCard({
  check,
  actualYears,
  propertyId,
}: {
  check: FlatRateCheck;
  actualYears: YearFigures[] | undefined;
  propertyId: string;
}) {
  const t = useT(propertyPlanning);
  const flatRate = useT(flatRateText);
  const tenancies = check.tenancies ?? [];
  if (tenancies.length === 0) return null;
  const levels = check.levels ?? [];
  const levelOn = (date: string) => levels.find((level) => level.as_of === date);
  const now = levelOn(check.as_of);
  const setDates = [...new Set(tenancies.map((tenancy) => tenancy.set_on))].sort();
  const year = actualYears ? shownYear(actualYears) : undefined;
  const gap = year?.flat_rate_gap_monthly ?? null;
  const settlements = check.settlements ?? [];
  const title = t.flatRateTitle;
  return (
    <section className="card" id={FLAT_RATE_CARD_ID} aria-label={title}>
      <div className="planning-card-head">
        <h2>{title}</h2>
        <span className="muted">{t.flatRateSub(formatDate(check.as_of))}</span>
      </div>
      {setDates.map((setOn) => {
        const group = tenancies.filter((tenancy) => tenancy.set_on === setOn);
        const first = group[0];
        if (!first) return null;
        const then = levelOn(setOn);
        return (
          <div key={setOn} className="stack">
            <h3>
              {t.setOn(formatDate(setOn), group.map((tenancy) => tenancy.unit_label).join(", "))}
            </h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{t.colCostType}</th>
                    <th scope="col" className="num">
                      {t.colOn(formatDate(setOn))}
                    </th>
                    <th scope="col" className="num">
                      {t.colToday}
                    </th>
                    <th scope="col" className="num">
                      {t.colChange}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {first.changes
                    .filter((change) => change.category !== "electricity")
                    .map((change) => (
                      <tr key={change.category}>
                        <th scope="row">{ALLOCATION_CATEGORY[change.category]}</th>
                        <RateCell rate={rateOf(then, change.category)} date={setOn} propertyId={propertyId} />
                        <RateCell rate={rateOf(now, change.category)} date={check.as_of} propertyId={propertyId} />
                        <td className={["num", tone(change.change, true)].filter(Boolean).join(" ")}>{signedMoney(change.change)}</td>
                      </tr>
                    ))}
                  <tr className="planning-actual-row">
                    <th scope="row">{t.operatingTotal}</th>
                    <td className="num">{then ? formatMoney(then.betrkv) : "–"}</td>
                    <td className="num">{now ? formatMoney(now.betrkv) : "–"}</td>
                    <td className="num">
                      <strong>{signedMoney(first.flat_rise)}</strong>
                    </td>
                  </tr>
                  {first.changes
                    .filter((change) => change.category === "electricity")
                    .map((change) => (
                      <tr key={change.category}>
                        <th scope="row">
                          {ALLOCATION_CATEGORY[change.category]}
                          <span className="tax-line-sub">{t.electricityNote}</span>
                        </th>
                        <RateCell rate={rateOf(then, change.category)} date={setOn} propertyId={propertyId} />
                        <RateCell rate={rateOf(now, change.category)} date={check.as_of} propertyId={propertyId} />
                        <td className="num">{signedMoney(change.change)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
      <h3>{t.perRoom}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">{t.colRoom}</th>
              <th scope="col" className="num">
                {t.colFlatRate}
              </th>
              <th scope="col" className="num">
                {t.colShare}
              </th>
              <th scope="col" className="num">
                {t.colShareOfChange}
              </th>
              <th scope="col">{t.colResult}</th>
            </tr>
          </thead>
          <tbody>
            {tenancies.map((tenancy) => (
              <tr key={tenancy.tenancy_id}>
                <th scope="row">
                  <Link href={`/tenancies/${tenancy.tenancy_id}`}>{tenancy.unit_label}</Link>
                  <span className="tax-line-sub">
                    {t.since(formatDate(tenancy.set_on), tenancy.set_by === "start")}
                  </span>
                </th>
                <td className="num">{formatMoney(tenancy.pauschale)}</td>
                <td className="num">{tenancy.share_percent != null ? percentText(tenancy.share_percent) : "–"}</td>
                <td className={["num", tone(tenancy.rise, true)].filter(Boolean).join(" ")}>{signedMoney(tenancy.rise)}</td>
                <td>
                  {verdictText(t, tenancy, check.raise_above)}
                  {tenancy.verdict !== "none" ? (
                    <div className="row todo-actions">
                      <FlatRateActions tenancyId={tenancy.tenancy_id} propertyId={propertyId} withCard={false} />
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted planning-subline">{t.betrkvNote(formatMoney(check.raise_above))}</p>
      {gap != null && year ? (
        <p className="planning-subline">{t.yearCoverage(year.year, isNegative(gap), signedMoney(gap))}</p>
      ) : null}
      {settlements.length > 0 ? (
        <ul className="planning-subline">
          {settlements.map((hint) => (
            <li key={hint.allocation_id}>
              {t.settlementHint(
                formatDate(hint.booking_date),
                ALLOCATION_CATEGORY[hint.category],
                isNegative(hint.amount),
                formatMoney(hint.amount.replace(/^-/, "")),
              )}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="muted planning-subline">
        {t.notLegalAdvice} {flatRate.notAdvice}
      </p>
    </section>
  );
}

// --- Projection card ------------------------------------------------------------------------

function ProjectionCard({
  projection,
  actualYears,
  property,
  busy,
  overridden,
  onLevers,
  onReset,
}: {
  projection: Projection;
  actualYears: YearFigures[] | undefined;
  property: Property;
  busy: boolean;
  overridden: boolean;
  onLevers: (next: Partial<LeverParams>) => void;
  onReset: () => void;
}) {
  const t = useT(propertyPlanning);
  const levers = projection.scenario;
  const sale = projection.sale;
  const thisYear = Number(todayIso().slice(0, 4));
  const purchaseYear = property.purchase_date ? Number(property.purchase_date.slice(0, 4)) : null;
  const first = projection.years[0];

  return (
    <section className="card planning-projection" aria-labelledby="planning-projection-title">
      <div className="planning-card-head">
        <h2 id="planning-projection-title">{t.projectionTitle}</h2>
        <span className="spacer" />
        <span className="muted" role="status">
          {busy ? t.computing : t.leversHint}
        </span>
      </div>
      <div className="planning-levers">
        <RateLever
          label={t.valueGrowthLabel}
          name={t.valueGrowth}
          value={levers.value_growth}
          disabled={busy}
          onChange={(next) => onLevers({ valueGrowth: next })}
        />
        <RateLever
          label={t.rentGrowthLabel}
          name={t.rentGrowth}
          value={levers.rent_growth}
          disabled={busy}
          onChange={(next) => onLevers({ rentGrowth: next })}
        />
        <TaxLever year={first} propertyId={property.id} />
        <div className="planning-lever" role="group" aria-label={t.saleAfter}>
          <p className="planning-lever-label">{t.saleAfter}</p>
          <div className="planning-lever-control">
            <button
              type="button"
              aria-label={t.sellEarlier}
              disabled={busy || levers.sale_year - 1 <= thisYear}
              onClick={() => onLevers({ saleYear: levers.sale_year - 1 })}
            >
              −
            </button>
            <span className="planning-lever-value">
              {purchaseYear !== null ? t.years(levers.sale_year - purchaseYear) : levers.sale_year}
            </span>
            <button
              type="button"
              aria-label={t.sellLater}
              disabled={busy || levers.sale_year + 1 > 2100}
              onClick={() => onLevers({ saleYear: levers.sale_year + 1 })}
            >
              +
            </button>
          </div>
          <p className="planning-lever-sub">{t.saleOn(formatDate(sale.sale_date))}</p>
        </div>
      </div>
      {overridden ? (
        <p className="muted planning-subline">
          {t.changedUnsaved}{" "}
          <button type="button" className="link" onClick={onReset}>
            {t.reset}
          </button>
        </p>
      ) : null}
      <SaleBanner sale={sale} propertyId={property.id} />
      <SaleKpis sale={sale} property={property} saleYear={levers.sale_year} />
      <div className="planning-split">
        <EquityBars years={projection.years} />
        <Bridge sale={sale} propertyId={property.id} />
      </div>
      <YearTable actualYears={actualYears ?? []} projection={projection} propertyId={property.id} />
      <AssumptionsFooter projection={projection} />
    </section>
  );
}

function RateLever({
  label,
  name,
  value,
  disabled,
  onChange,
}: {
  label: string;
  name: string;
  value: string;
  disabled: boolean;
  onChange: (next: string) => void;
}) {
  const t = useT(propertyPlanning);
  const lower = stepRate(value, -1);
  const higher = stepRate(value, 1);
  return (
    <div className="planning-lever" role="group" aria-label={label}>
      <p className="planning-lever-label">{label}</p>
      <div className="planning-lever-control">
        <button type="button" aria-label={t.lowerLever(name)} disabled={disabled || lower === null} onClick={() => lower && onChange(lower)}>
          −
        </button>
        <span className="planning-lever-value">{ratePercent(value, 1)}</span>
        <button
          type="button"
          aria-label={t.raiseLever(name)}
          disabled={disabled || higher === null}
          onClick={() => higher && onChange(higher)}
        >
          +
        </button>
      </div>
    </div>
  );
}

/**
 * Steuerwirkung as the derived effective rate of the first projected year, with its source
 * chip ("Schätzung 2026 · 81.993,00 € zvE", from GET /api/tax-rate, which states the zvE the
 * projection does not carry) linking to the income inputs, and a pencil to set an override
 * there. Not a ± lever: the rate follows from income (D82), a manual rate is an income input.
 */
function TaxLever({ year, propertyId }: { year: YearFigures | undefined; propertyId: string }) {
  const t = useT(propertyPlanning);
  const effect = year?.tax_effect ?? null;
  const taxYear = year?.year ?? null;
  const result = year?.taxable_result ?? null;
  const payHref = payInputsHref(taxYear ?? Number(todayIso().slice(0, 4)));
  const loadRate = useCallback(
    (): Promise<TaxRateResult | null> =>
      taxYear === null || result === null
        ? Promise.resolve(null)
        : ok(
            api.GET("/api/tax-rate", { params: { query: { tax_year: taxYear, result, property_id: propertyId } } }),
          ).catch(() => null),
    [taxYear, result, propertyId],
  );
  const rate = useLoad(loadRate);
  const zve = rate.data?.zve ?? null;
  const chip = effect
    ? zve
      ? `${sourceLabel(t, zve.source)} ${zve.tax_year}${zve.amount != null ? t.chipZve(formatMoney(zve.amount)) : ""}${zve.carried_forward ? t.carriedForward : ""}`
      : sourceLabel(t, effect.source)
    : null;
  const value = effect
    ? effect.effective_rate
      ? percentText(effect.effective_rate)
      : effect.slope
        ? percentText(effect.slope)
        : "—"
    : "—";
  return (
    <div className="planning-lever" role="group" aria-label={t.taxEffect}>
      <p className="planning-lever-label">
        {t.taxEffect}
        {taxYear !== null ? ` ${taxYear}` : ""}
        {effect && !effect.effective_rate && effect.slope ? t.marginalSuffix : effect ? t.effectiveSuffix : ""}
      </p>
      <div className="planning-lever-control">
        <span className="planning-lever-value">{value}</span>
        <Link href={payHref} className="planning-lever-edit" aria-label={t.setRateManually} title={t.setRateManually}>
          ✎
        </Link>
      </div>
      <p className="planning-lever-sub">
        {chip ? (
          <Link href={payHref} className="badge" title={t.chipTitle}>
            {chip}
          </Link>
        ) : (
          <>
            — · <Link href={payHref}>{t.enterIncome}</Link>
          </>
        )}
      </p>
    </div>
  );
}

/** The §23 check: the sale date against the first tax-free day (D88). */
function SaleBanner({ sale, propertyId }: { sale: SaleResult; propertyId: string }) {
  const t = useT(propertyPlanning);
  if (sale.tax_free_from == null) {
    return (
      <div className="notice notice-info" role="status">
        <p>
          {t.noContractDate} <Link href={`/properties/${propertyId}?tab=uebersicht`}>{t.enterInMasterData}</Link>
        </p>
      </div>
    );
  }
  const saleDate = formatDate(sale.sale_date);
  const freeFrom = formatDate(sale.tax_free_from);
  if (!sale.taxable) {
    return (
      <div className="notice notice-ok planning-banner" role="status">
        <p>{t.taxFreeSale(saleDate, freeFrom)}</p>
      </div>
    );
  }
  const loss = sale.gain != null && isNegative(sale.gain);
  return (
    <div className="notice notice-warn planning-banner" role="status">
      <p>
        {t.taxableSale(
          saleDate,
          freeFrom,
          formatMoney(sale.afa_claimed),
          isZero(sale.afa_claimed_movable) ? null : formatMoney(sale.afa_claimed_movable),
        )}
      </p>
      <p>
        {sale.gain == null
          ? null
          : loss
            ? t.saleLoss(formatMoney(sale.gain))
            : t.saleGain(formatMoney(sale.gain))}
        {sale.freigrenze_applied ? t.freigrenze : null}
        {t.saleTax(formatMoney(sale.sale_tax), freeFrom)}
      </p>
    </div>
  );
}

function SaleKpis({ sale, property, saleYear }: { sale: SaleResult; property: Property; saleYear: number }) {
  const t = useT(propertyPlanning);
  const cashFlow = (sale.bridge ?? []).find((row) => row.key === "cash_flow");
  return (
    <div className="planning-kpis">
      <div className="planning-kpi" role="group" aria-label={t.salePrice(saleYear)}>
        <p className="planning-kpi-label">{t.salePrice(saleYear)}</p>
        <p className="planning-kpi-value">{formatMoney(sale.value)}</p>
        <p className="planning-kpi-sub">{t.purchasePrice(formatMoney(property.purchase_price))}</p>
      </div>
      <div className="planning-kpi" role="group" aria-label={t.cashAfterTax}>
        <p className="planning-kpi-label">{t.cashAfterTax}</p>
        <p className={`planning-kpi-value ${tone(cashFlow?.amount) ?? ""}`}>{signedMoney(cashFlow?.amount)}</p>
        <p className="planning-kpi-sub">{t.allYears}</p>
      </div>
      <div className="planning-kpi" role="group" aria-label={t.proceeds}>
        <p className="planning-kpi-label">{t.proceeds}</p>
        <p className="planning-kpi-value">{signedMoney(sale.net_proceeds)}</p>
        <p className="planning-kpi-sub">{t.proceedsSub(formatMoney(sale.sale_tax), formatMoney(sale.loan_balance))}</p>
      </div>
      <div className="planning-kpi planning-kpi-main" role="group" aria-label={t.profitTotal}>
        <p className="planning-kpi-label">{t.profitTotal}</p>
        <p className="planning-kpi-value">{signedMoney(sale.profit)}</p>
        <p className="planning-kpi-sub">
          {t.perYearOn(returnPercent(sale.cagr), formatMoney(sale.initial_equity))}
          {sale.irr != null ? t.irr(returnPercent(sale.irr)) : ""}
        </p>
      </div>
    </div>
  );
}

/** Equity (value − remaining loan, as returned) per projected year; the last bar is the sale
 * year. The heights are the returned figures relative to the largest, for display only. */
function EquityBars({ years }: { years: YearFigures[] }) {
  const t = useT(propertyPlanning);
  const largest = Math.max(0, ...years.map((y) => (y.equity == null ? 0 : Number(y.equity))));
  return (
    <figure className="planning-equity">
      <p className="planning-section-title">{t.equityTitle}</p>
      <ul className="planning-bars">
        {years.map((year, index) => {
          const equity = year.equity == null ? 0 : Number(year.equity);
          const height = largest > 0 && equity > 0 ? (equity / largest) * 100 : 0;
          const last = index === years.length - 1;
          return (
            <li key={year.year} title={`${year.year}: ${formatMoney(year.equity)}`}>
              <span className={last ? "planning-bar planning-bar-sale" : "planning-bar"} style={{ height: `${height}%` }} />
              <span className="planning-bar-year" aria-hidden="true">
                {year.year}
              </span>
              <span className="visually-hidden">
                {year.year}: {formatMoney(year.equity)}
              </span>
            </li>
          );
        })}
      </ul>
      <figcaption className="muted">{t.equityCaption}</figcaption>
    </figure>
  );
}

function bridgeLabel(t: Texts, key: BridgeRow["key"]): string {
  switch (key) {
    case "appreciation":
      return t.bridgeAppreciation;
    case "loan_paid_down":
      return t.bridgeLoanPaidDown;
    case "cash_flow":
      return t.bridgeCashFlow;
    case "buying_costs":
      return t.bridgeBuyingCosts;
    case "selling_costs":
      return t.bridgeSellingCosts;
    default:
      return t.bridgeSaleTax;
  }
}

/** Where the profit comes from (D89): the backend's bridge rows, which sum to the profit. */
function Bridge({ sale, propertyId }: { sale: SaleResult; propertyId: string }) {
  const t = useT(propertyPlanning);
  const rows = sale.bridge ?? [];
  return (
    <section className="planning-bridge" aria-labelledby="planning-bridge-title">
      <p id="planning-bridge-title" className="planning-section-title">
        {t.bridgeTitle}
      </p>
      <ul>
        {rows.map((row) => {
          const label =
            row.key === "sale_tax" && !sale.taxable ? `${bridgeLabel(t, row.key)}${t.taxFreeAfterTen}` : bridgeLabel(t, row.key);
          return (
            <li key={row.key}>
              <span>{label}</span>
              <span className={`num ${tone(row.amount) ?? ""}`}>
                <SourcesDialog label={signedMoney(row.amount)} title={label} refs={row.sources ?? []} propertyId={propertyId} />
              </span>
            </li>
          );
        })}
        <li className="planning-bridge-total">
          <span>{t.profitTotal}</span>
          <span className="num">{signedMoney(sale.profit)}</span>
        </li>
      </ul>
    </section>
  );
}

// --- Year table -----------------------------------------------------------------------------

interface TableRow {
  figures: YearFigures;
  badge: string | null;
  sale: boolean;
}

function YearTable({ actualYears, projection, propertyId }: { actualYears: YearFigures[]; projection: Projection; propertyId: string }) {
  const t = useT(propertyPlanning);
  const saleYear = projection.scenario.sale_year;
  const rows: TableRow[] = [
    ...actualYears.map((figures) => ({
      figures,
      badge: (figures.flags ?? []).includes("so_far") ? t.badgeSoFar : t.actual,
      sale: false,
    })),
    ...projection.years.map((figures) => {
      const sale = figures.year === saleYear;
      return { figures, badge: sale ? t.badgeSale : null, sale };
    }),
  ];
  return (
    <div className="table-wrap planning-table">
      <table>
        <caption className="visually-hidden">{t.tableCaption}</caption>
        <thead>
          <tr>
            <th scope="col">{t.colYear}</th>
            <th scope="col" className="num">
              {t.rent}
            </th>
            <th scope="col" className="num">
              {t.colInterest}
            </th>
            <th scope="col" className="num">
              {t.colTaxResult}
            </th>
            <th scope="col" className="num">
              {t.taxEffect}
            </th>
            <th scope="col" className="num">
              {t.cashAfterTax}
            </th>
            <th scope="col" className="num">
              {t.colValue}
            </th>
            <th scope="col" className="num">
              {t.colLoan}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ figures: y, badge, sale }) => {
            const refs = y.sources ?? [];
            const cell = (column: string, text: string, className?: string, cellRefs: TaxSourceRef[] = refs) => (
              <td className={className ? `num ${className}` : "num"}>
                {text === "–" || text === "—" ? (
                  text
                ) : (
                  <SourcesDialog
                    label={text}
                    title={`${column} ${y.year}`}
                    refs={cellRefs}
                    propertyId={propertyId}
                    buttonClass="link planning-cell"
                  />
                )}
              </td>
            );
            return (
              <tr key={`${y.kind}-${y.year}`} className={y.kind === "projected" ? undefined : "planning-actual-row"}>
                <td>
                  <strong>{y.year}</strong>
                  {badge ? <span className={sale ? "badge" : "badge badge-muted"}>{badge}</span> : null}
                </td>
                {cell(t.rent, formatMoney(y.rent_in))}
                {cell(t.colInterest, formatMoney(y.interest))}
                {cell(t.colTaxResult, signedMoney(y.taxable_result))}
                {cell(
                  t.taxEffect,
                  y.tax_effect ? signedMoney(y.tax_effect.effect) : "—",
                  tone(y.tax_effect?.effect, true),
                  y.tax_effect?.sources ?? [],
                )}
                {cell(t.cashAfterTax, signedMoney(y.net_cash), `planning-strong ${tone(y.net_cash) ?? ""}`)}
                {cell(t.colValue, y.value == null ? "–" : formatMoney(y.value))}
                {cell(t.colLoan, y.loan_balance == null ? "–" : formatMoney(y.loan_balance))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// --- Assumptions ------------------------------------------------------------------------------

function assumptionText(t: Texts, assumption: Assumption): string {
  const p: Record<string, string> = assumption.params ?? {};
  switch (assumption.code) {
    case "sale.on_31_december":
      return t.asmSaleOn31December(p.year ?? "");
    case "tax.no_child_allowances":
      return t.asmNoChildAllowances;
    case "draft.form_provisional":
      return t.asmFormProvisional((p.years ?? "").split(",").join(", "));
    case "tariff.reused":
      return t.asmTariffReused(p.year ?? "?", p.from ?? "?");
    case "sale.no_contract_date":
      return t.asmNoContractDate;
    case "sale.tax_without_income":
      return t.asmTaxWithoutIncome;
    default:
      return assumption.code;
  }
}

function leverLines(t: Texts, levers: Levers): string[] {
  const followUp = (levers.loans ?? []).filter((loan) => loan.follow_up_rate != null);
  return [
    t.upkeep(formatMoney(levers.upkeep_per_year), ratePercent(levers.cost_inflation, 1)),
    t.sellingCosts(ratePercent(levers.selling_costs_rate)),
    t.vacancy(formatDecimal(levers.vacancy_months), levers.today_value != null ? formatMoney(levers.today_value) : t.thePurchasePrice),
    followUp.length > 0
      ? t.followUp(followUp.map((loan) => ratePercent(loan.follow_up_rate ?? "0", 1)).join(", "))
      : t.followUpDefault,
  ];
}

function AssumptionsFooter({ projection }: { projection: Projection }) {
  const t = useT(propertyPlanning);
  const lines = [
    ...leverLines(t, projection.scenario),
    ...(projection.assumptions ?? []).map((assumption) => assumptionText(t, assumption)),
  ];
  return (
    <div className="planning-assumptions" aria-label={t.assumptions}>
      <p className="planning-section-title">{t.assumptions}</p>
      <ul>
        {lines.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
      <p className="muted">
        {t.ruleSet(projection.rule_set.version, projection.tariff.version)}
        {projection.tariff.reused_from != null ? t.reusedFrom(String(projection.tariff.reused_from)) : ""}
      </p>
    </div>
  );
}

// --- Scenarios ------------------------------------------------------------------------------

function ScenariosCard({
  propertyId,
  projection,
  scenarios,
  overridden,
  saved,
  onShow,
  onSaved,
}: {
  propertyId: string;
  projection: Projection;
  scenarios: Loaded<Scenario[]>;
  overridden: boolean;
  saved: string | null;
  onShow: (id: string) => void;
  onSaved: (created: Scenario) => void;
}) {
  const t = useT(propertyPlanning);
  const currentDefault = scenarios.data?.find((s) => s.is_default);
  return (
    <section className="card stack planning-scenarios" aria-labelledby="planning-scenarios-title">
      <div className="planning-card-head">
        <h2 id="planning-scenarios-title">{t.scenarios}</h2>
        <span className="spacer" />
        <ActionDialog label={t.saveScenario} title={t.saveScenario} buttonClass="">
          {(close) => (
            <SaveScenarioForm
              propertyId={propertyId}
              levers={projection.scenario}
              currentDefault={currentDefault}
              onSaved={(created) => {
                close();
                onSaved(created);
              }}
            />
          )}
        </ActionDialog>
      </div>
      {saved ? <Notice>{t.scenarioSaved(saved)}</Notice> : null}
      <Loading state={scenarios}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">{t.noScenario}</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">{t.colName}</th>
                    <th scope="col" className="num">
                      {t.valueGrowth}
                    </th>
                    <th scope="col" className="num">
                      {t.rentGrowth}
                    </th>
                    <th scope="col" className="num">
                      {t.colSale}
                    </th>
                    <th scope="col">{t.colSaved}</th>
                    <th scope="col">
                      <span className="visually-hidden">{t.colAction}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((scenario) => {
                    const shown = projection.scenario_id === scenario.id;
                    return (
                      <tr key={scenario.id}>
                        <td>
                          {scenario.name}
                          {scenario.is_default ? <span className="badge">{t.badgeDefault}</span> : null}
                          {shown ? (
                            <span className="badge badge-muted">{overridden ? t.shownChanged : t.shown}</span>
                          ) : null}
                        </td>
                        <td className="num">{ratePercent(scenario.levers.value_growth, 1)}</td>
                        <td className="num">{ratePercent(scenario.levers.rent_growth, 1)}</td>
                        <td className="num">{scenario.levers.sale_year}</td>
                        <td>{formatDateTime(scenario.recorded_at)}</td>
                        <td className="actions">
                          <button
                            type="button"
                            className="link"
                            onClick={() => onShow(scenario.id)}
                            disabled={shown && !overridden}
                          >
                            {t.show}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
    </section>
  );
}

const SCENARIO_KEYS = ["name", "is_default", "levers"] as const;

/** Records the levers on screen (the projection's, overrides included) as a new scenario row
 * (D87). "Als Standard" with a default already in force supersedes that row: the server keeps
 * one default per property (scenario.not_current otherwise). */
function SaveScenarioForm({
  propertyId,
  levers,
  currentDefault,
  onSaved,
}: {
  propertyId: string;
  levers: Levers;
  currentDefault: Scenario | undefined;
  onSaved: (created: Scenario) => void;
}) {
  const t = useT(propertyPlanning);
  const c = useT(common);
  const ids = useId();
  const [name, setName] = useState("");
  const [asDefault, setAsDefault] = useState(false);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(SCENARIO_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const trimmed = name.trim();
    if (trimmed === "") return form.invalid({ name: [t.nameRequired] });
    if (trimmed.length > 100) return form.invalid({ name: [t.nameTooLong] });
    setBusy(true);
    form.clear();
    try {
      const created = await ok(
        api.POST("/api/properties/{property_id}/scenarios", {
          params: { path: { property_id: propertyId } },
          body: {
            property_id: propertyId,
            name: trimmed,
            is_default: asDefault,
            levers,
            supersedes_id: asDefault && currentDefault ? currentDefault.id : null,
            withdrawn: false,
          },
        }),
      );
      onSaved(created);
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <p className="muted">
        {t.saveIntro(ratePercent(levers.value_growth, 1), ratePercent(levers.rent_growth, 1), levers.sale_year)}
      </p>
      <Field id={`${ids}-name`} label={t.colName} errors={form.of("name")}>
        <input type="text" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} autoComplete="off" required />
      </Field>
      <div className="field">
        <label className="inline" htmlFor={`${ids}-default`}>
          <input id={`${ids}-default`} type="checkbox" checked={asDefault} onChange={(e) => setAsDefault(e.target.checked)} />
          {t.asDefault}
        </label>
        {currentDefault ? (
          <p className="muted hint">{t.replacesDefault(currentDefault.name)}</p>
        ) : (
          <p className="muted hint">{t.defaultHint}</p>
        )}
      </div>
      <div className="row">
        <Submit busy={busy}>{c.save}</Submit>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- Sources --------------------------------------------------------------------------------

/** Every confirmed payment of the property, for the payment rows of a sources list (the
 * Steuern screen's GET …/anlage-v/sources; it lists every year's payments, so today's year is
 * asked). Without it a payment shows its kind and amount only. */
function loadPayments(propertyId: string): Promise<Map<string, AllocationFact>> {
  const year = Number(todayIso().slice(0, 4));
  return ok(
    api.GET("/api/properties/{property_id}/anlage-v/sources", {
      params: { path: { property_id: propertyId }, query: { tax_year: year } },
    }),
  ).then(
    (rows) => new Map(rows.map((row) => [row.allocation_id, row])),
    () => new Map<string, AllocationFact>(),
  );
}

/** A figure that opens its sources; a figure without sources is plain text. */
function SourcesDialog({
  label,
  title,
  refs,
  propertyId,
  buttonClass = "link tax-sources-button",
}: {
  label: string;
  title: string;
  refs: TaxSourceRef[];
  propertyId: string;
  buttonClass?: string;
}) {
  const t = useT(propertyPlanning);
  if (refs.length === 0) return <>{label}</>;
  return (
    <ActionDialog label={label} title={t.sourcesOf(title)} buttonClass={buttonClass}>
      {() => <SourceList refs={refs} propertyId={propertyId} />}
    </ActionDialog>
  );
}

function SourceList({ refs, propertyId }: { refs: TaxSourceRef[]; propertyId: string }) {
  const hasPayments = refs.some((ref) => ref.kind === "allocation");
  const load = useCallback(
    () => (hasPayments ? loadPayments(propertyId) : Promise.resolve(new Map<string, AllocationFact>())),
    [hasPayments, propertyId],
  );
  const payments = useLoad(load);
  const c = useT(common);
  return (
    <>
      {hasPayments && payments.data === undefined ? <p className="muted">{c.loading}</p> : null}
      <ul className="tax-sources">
        {refs.map((source, index) => (
          <li key={`${source.kind}-${source.id ?? "file"}-${index}`}>
            {source.kind === "allocation" ? (
              <PaymentSource source={source} fact={source.id ? payments.data?.get(source.id) : undefined} />
            ) : (
              <OtherSource source={source} propertyId={propertyId} />
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

/** A payment: date, category, amount, masked counterparty and period, with its bank line and
 * Beleg; no name or purpose text (D5). */
function PaymentSource({ source, fact }: { source: TaxSourceRef; fact: AllocationFact | undefined }) {
  const t = useT(propertyPlanning);
  if (!fact) {
    return (
      <>
        <span>{TAX_SOURCE_KIND[source.kind]}</span>
        {source.amount != null ? <span className="num">{formatMoney(source.amount)}</span> : null}
      </>
    );
  }
  const period = fact.service_period ? t.period(formatDate(fact.service_period.start), formatDate(fact.service_period.end)) : null;
  const details = [fact.counterparty_iban_masked, period].filter(Boolean).join(" · ");
  return (
    <div className="tax-payment">
      <p className="tax-payment-head">
        <span>{formatDate(fact.booking_date)}</span>
        <span>{ALLOCATION_CATEGORY[fact.category]}</span>
        <span className="num">{formatMoney(source.amount ?? fact.amount)}</span>
      </p>
      {details ? <p className="tax-line-sub">{details}</p> : null}
      <p className="tax-payment-links">
        {fact.transaction_id ? <Link href={`/inbox?umsatz=${fact.transaction_id}`}>{t.openBankLine}</Link> : null}
        {fact.document_id ? <Link href={`/documents/${fact.document_id}`}>{t.openReceipt}</Link> : null}
      </p>
    </div>
  );
}

function OtherSource({ source, propertyId }: { source: TaxSourceRef; propertyId: string }) {
  const link = linkFor(source, propertyId); // an income input links to Eure Angaben there
  return (
    <>
      <span>{TAX_SOURCE_KIND[source.kind]}</span>
      {source.amount != null ? <span className="num">{formatMoney(source.amount)}</span> : null}
      {link ? <Link href={link.href}>{link.label}</Link> : null}
    </>
  );
}
