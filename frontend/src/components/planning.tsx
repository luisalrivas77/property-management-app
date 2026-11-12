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
// - Above the actuals, the card „Nebenkostenpauschalen“ (D155 (5)): GET …/flat-rate-check, the
//   costs when each Pauschale was set against today, per tenancy its share and verdict; it
//   needs no rule set, so it shows even when the actuals are refused.
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

import { FLAT_RATE_CARD_ID, FLAT_RATE_NOT_ADVICE, FlatRateActions } from "./flatRate";

import { Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatDate, formatDateTime, formatDecimal, formatMoney, shiftDecimal, todayIso } from "./format";
import { ALLOCATION_CATEGORY, TAX_SOURCE_KIND } from "./labels";
import { linkFor } from "./tax";
import { ActionDialog, Loading, Notice } from "./ui";
import { useLoad, type Loaded } from "./useLoad";

import "./tax.css";

const DISCLAIMER = "Eine Planungsschätzung, keine Steuer- oder Anlageberatung.";

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
  return percent === null ? value : `${formatDecimal(percent, minFraction)} %`;
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
  return `${sign && tenths !== 0n ? "-" : ""}${text} %`;
}

/** A percent the backend already rounded ("33.8") as "33,8 %". */
function percentText(value: string): string {
  return `${formatDecimal(value, 1)} %`;
}

function addressLine(property: Property): string {
  const a = property.address;
  return `${a.street} ${a.house_number}, ${a.postal_code} ${a.city}`;
}

// --- Refusals ---------------------------------------------------------------------------------

type Refusal = "projection.no_rule_set" | "tariff.no_file";

const REFUSALS: readonly Refusal[] = ["projection.no_rule_set", "tariff.no_file"];

const REFUSAL_TEXT: Record<Refusal, string> = {
  "projection.no_rule_set":
    "Für das Kaufjahr dieser Immobilie gibt es noch kein Regelwerk. Ohne Regelwerk lassen sich weder die Ist-Zahlen noch eine Prognose berechnen.",
  "tariff.no_file":
    "Für das Kaufjahr dieser Immobilie ist kein Einkommensteuertarif hinterlegt. Ohne Tarif lassen sich weder die Ist-Zahlen noch eine Prognose berechnen.",
};

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
  return (
    <div className="notice notice-info" role="status">
      <p>{REFUSAL_TEXT[refusal]}</p>
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
  const query = new URLSearchParams();
  if (propertyId) query.set("immobilie", propertyId);
  if (params.scenario) query.set(PARAM.scenario, params.scenario);
  if (params.valueGrowth !== null) query.set(PARAM.valueGrowth, params.valueGrowth);
  if (params.rentGrowth !== null) query.set(PARAM.rentGrowth, params.rentGrowth);
  if (params.saleYear !== null) query.set(PARAM.saleYear, String(params.saleYear));
  const text = query.toString();
  return text ? `/planning?${text}` : "/planning";
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

export function PlanningScreen({ property, properties }: { property: Property; properties: Property[] }) {
  const router = useRouter();
  const search = useSearchParams();
  const ids = useId();
  const params = readParams(search);
  const { scenario: scenarioId, valueGrowth, rentGrowth, saleYear } = params;
  const overridden = valueGrowth !== null || rentGrowth !== null || saleYear !== null;
  const multi = properties.length > 1;
  const [saved, setSaved] = useState<string | null>(null);

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
  const loadFlatRate = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/flat-rate-check", { params: { path: { property_id: property.id } } })),
    [property.id],
  );
  const flatRate = useLoad(loadFlatRate);
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
  const hasFlatRateCard = (flatRate.data?.tenancies ?? []).length > 0;

  return (
    <>
      <div className="page-head planning-head">
        <div>
          <p className="eyebrow">Planung · {property.label}</p>
          <h1>Rechnet sich die Immobilie?</h1>
          <span className="muted">{addressLine(property)}</span>
        </div>
        <span className="spacer" />
        {multi ? (
          <label htmlFor={`${ids}-property`} className="inline">
            Immobilie
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
      <nav aria-label="Bereiche" className="tabs">
        <Link href={planningHref(multi ? property.id : null, NO_LEVERS)} aria-current="page" scroll={false}>
          Immobilie
        </Link>
        <span className="tab-disabled" aria-disabled="true">
          Haushalt <span className="tab-note">ab Phase 9</span>
        </span>
      </nav>
      <div className="stack planning">
        {flatRate.data !== undefined || flatRate.error != null ? (
          <Loading state={flatRate}>
            {(check) => <FlatRateCard check={check} actualYears={actualYears} propertyId={property.id} />}
          </Loading>
        ) : null}
        <Loading state={actuals}>
          {(data) =>
            isRefused(data) ? (
              <RefusalNotice refusal={data.refused} />
            ) : (
              <ActualsCard years={data.years} propertyId={property.id} flatRateCard={hasFlatRateCard} />
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
                  Annahmen zurücksetzen
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
        <p className="muted planning-footer">{DISCLAIMER}</p>
      </div>
    </>
  );
}

// --- Actuals card ---------------------------------------------------------------------------

const SOURCE_LABEL: Record<TaxEffect["source"], string> = {
  estimate: "Schätzung",
  bescheid: "Bescheid",
  override: "manuell",
};

/** "33,8 % effektiv", or the tariff's slope at a result of 0. */
function rateText(effect: TaxEffect): string | null {
  if (effect.effective_rate) return `${percentText(effect.effective_rate)} effektiv`;
  if (effect.slope) return `Grenzsteuersatz ${percentText(effect.slope)}`;
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

function ActualsCard({ years, propertyId, flatRateCard }: { years: YearFigures[]; propertyId: string; flatRateCard: boolean }) {
  const year = shownYear(years);
  if (!year) {
    return (
      <section className="card">
        <h2>Ist</h2>
        <p className="muted">Noch keine Ist-Zahlen: die Immobilie ist noch nicht übergeben.</p>
      </section>
    );
  }
  const soFar = (year.flags ?? []).includes("so_far");
  const estimated = (year.flags ?? []).includes("tax_estimated");
  const effect = year.tax_effect;
  const title = soFar ? `${year.year} bisher · Ist` : `${year.year} · Ist`;
  return (
    <section className="card planning-actuals" aria-label={title}>
      <div className="planning-card-head">
        <h2>{title}</h2>
        <span className="muted">aus bestätigten Buchungen · Umlagen als durchlaufender Posten</span>
        <span className="spacer" />
        {(year.sources ?? []).length > 0 ? (
          <SourcesDialog label="Quellen" title={title} refs={year.sources ?? []} propertyId={propertyId} />
        ) : null}
      </div>
      <div className="planning-figures">
        <Figure label="Kaltmiete" value={signedMoney(year.rent_in)} />
        <Figure label="Ausgaben" value={formatMoney(year.cash_out)} sub="Zinsen, Tilgung, laufende Kosten und Umlagen" />
        <Figure
          label={estimated ? "Steuerwirkung (geschätzt)" : "Steuerwirkung"}
          value={effect ? signedMoney(effect.effect) : "—"}
          valueClass={effect ? tone(effect.effect, true) : undefined}
          sub={
            effect ? (
              [
                isZero(effect.effect) ? null : isNegative(effect.effect) ? "weniger Steuer" : "mehr Steuer",
                year.taxable_result != null ? `Ergebnis ${signedMoney(year.taxable_result)}` : null,
                rateText(effect),
              ]
                .filter(Boolean)
                .join(" · ")
            ) : (
              <Link href="/owners">Einkommen eintragen</Link>
            )
          }
        />
        <Figure label="Netto-Cash" value={signedMoney(year.net_cash)} valueClass={tone(year.net_cash)} />
        <Figure label="Tilgung" value={signedMoney(year.principal_repaid)} sub="wird Eigenkapital" />
      </div>
      <p className="muted planning-subline">
        Umlagen: {formatMoney(year.umlagen_in)} erhalten, {formatMoney(year.umlagen_out)} bezahlt, Saldo{" "}
        {signedMoney(year.umlagen_net)} (im Netto-Cash enthalten)
        {isZero(year.other_income) ? null : <> · sonstige Einnahmen {formatMoney(year.other_income)}</>}
      </p>
      {year.flat_rate_gap_monthly != null && year.flat_rate_months != null ? (
        <FlatRateCoverage year={year} gap={year.flat_rate_gap_monthly} months={year.flat_rate_months} linked={flatRateCard} />
      ) : null}
    </section>
  );
}

/** The Pauschale coverage (D91, D92): flat-rate charges received against the umlagefähige costs
 * paid (the flat's electricity included), per month the Pauschalen were in force. Information
 * only. Whether a Pauschale may rise is the card „Nebenkostenpauschalen“ (D155), which compares
 * the costs when each Pauschale was set with today and needs no second year; `linked` when that
 * card is on the screen. */
function FlatRateCoverage({ year, gap, months, linked }: { year: YearFigures; gap: string; months: number; linked: boolean }) {
  const verdict = isNegative(gap)
    ? "Die Kosten liegen über den Pauschalen."
    : isZero(gap)
      ? "Die Pauschalen decken die Kosten genau."
      : "Die Pauschalen decken die Kosten.";
  return (
    <p className="planning-subline planning-flat-rate">
      <strong>Pauschalen-Check:</strong> {formatMoney(year.flat_rate_in)} Nebenkostenpauschalen erhalten,{" "}
      {formatMoney(year.umlagen_out)} umlagefähige Kosten bezahlt, Differenz{" "}
      <span className={tone(gap)}>
        {signedMoney(gap)} pro Monat, über {months === 1 ? "1 Monat" : `${months} Monate`}
      </span>
      . {verdict}{" "}
      {linked ? (
        <span className="muted">
          Ob eine Pauschale steigen kann oder sinken muss, zeigt <a href={`#${FLAT_RATE_CARD_ID}`}>Nebenkostenpauschalen</a>.
        </span>
      ) : null}
    </p>
  );
}

// --- Nebenkostenpauschalen card (D155) --------------------------------------------------------

const RATE_BASIS: Record<CostRate["basis"], string> = {
  monthly_payment: "Monatszahlung",
  yearly_premium: "Jahresbeitrag ÷ 12",
  schedule: "Grundsteuerbescheid ÷ 12",
  twelve_months: "Zahlungen der letzten 12 Monate ÷ 12",
  none: "nichts gebucht",
};

const ELECTRICITY_NOTE =
  "Strom ist keine Betriebskostenart nach § 2 BetrKV; ob § 560 BGB eine Erhöhung für den mit der Pauschale abgegoltenen Strom erlaubt, ist nicht geklärt. Diese Zeile löst deshalb nichts aus.";

/** A rate with its sources and how it was found. */
function RateCell({ rate, date, propertyId }: { rate: CostRate | undefined; date: string; propertyId: string }) {
  if (!rate) return <td className="num">–</td>;
  const label = ALLOCATION_CATEGORY[rate.category];
  return (
    <td className="num">
      <SourcesDialog
        label={formatMoney(rate.monthly)}
        title={`${label} am ${formatDate(date)}`}
        refs={rate.sources ?? []}
        propertyId={propertyId}
      />
      <span className="tax-line-sub">
        {RATE_BASIS[rate.basis]}
        {rate.from_later ? ", erste Zahlung danach" : ""}
      </span>
    </td>
  );
}

function rateOf(level: CostLevel | undefined, category: CostRate["category"]): CostRate | undefined {
  return level?.rates.find((rate) => rate.category === category);
}

/** „kann steigen, frühestens ab 01.04.2027“, „senken ab 01.01.2027“ or why not. */
function verdictText(tenancy: TenancyFlatRate, raiseAbove: string): string {
  if (tenancy.recorded_from) {
    return `Änderung ab ${formatDate(tenancy.recorded_from)} erfasst (${formatMoney(tenancy.recorded_pauschale)})`;
  }
  if (tenancy.verdict === "raise") return `kann steigen, frühestens ab ${formatDate(tenancy.earliest_start)}`;
  if (tenancy.verdict === "lower") return `senken ab ${formatDate(tenancy.fell_on)}`;
  return isZero(tenancy.rise) ? "unverändert" : `Anstieg bis ${formatMoney(raiseAbove)} im Monat: keine Erhöhung`;
}

/** Per date a Pauschale was set: the costs then and now per category; per tenancy its share
 * of the change and the verdict; the coverage of the shown year; settlements as hints. Every
 * figure is the check's own (principle 1); nothing here adds or splits. */
function FlatRateCard({
  check,
  actualYears,
  propertyId,
}: {
  check: FlatRateCheck;
  actualYears: YearFigures[] | undefined;
  propertyId: string;
}) {
  const tenancies = check.tenancies ?? [];
  if (tenancies.length === 0) return null;
  const levels = check.levels ?? [];
  const levelOn = (date: string) => levels.find((level) => level.as_of === date);
  const now = levelOn(check.as_of);
  const setDates = [...new Set(tenancies.map((t) => t.set_on))].sort();
  const year = actualYears ? shownYear(actualYears) : undefined;
  const gap = year?.flat_rate_gap_monthly ?? null;
  const settlements = check.settlements ?? [];
  const title = "Nebenkostenpauschalen";
  return (
    <section className="card" id={FLAT_RATE_CARD_ID} aria-label={title}>
      <div className="planning-card-head">
        <h2>{title}</h2>
        <span className="muted">Kosten bei Festlegung der Pauschale und heute ({formatDate(check.as_of)}), pro Monat</span>
      </div>
      {setDates.map((setOn) => {
        const group = tenancies.filter((t) => t.set_on === setOn);
        const first = group[0];
        if (!first) return null;
        const then = levelOn(setOn);
        return (
          <div key={setOn} className="stack">
            <h3>
              Festgelegt am {formatDate(setOn)}: {group.map((t) => t.unit_label).join(", ")}
            </h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Kostenart (ganze Wohnung)</th>
                    <th scope="col" className="num">
                      am {formatDate(setOn)}
                    </th>
                    <th scope="col" className="num">
                      heute
                    </th>
                    <th scope="col" className="num">
                      Änderung
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
                    <th scope="row">Betriebskosten zusammen</th>
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
                          <span className="tax-line-sub">{ELECTRICITY_NOTE}</span>
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
      <h3>Je Zimmer</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Zimmer</th>
              <th scope="col" className="num">
                Pauschale
              </th>
              <th scope="col" className="num">
                Anteil
              </th>
              <th scope="col" className="num">
                Anteil an der Änderung
              </th>
              <th scope="col">Ergebnis</th>
            </tr>
          </thead>
          <tbody>
            {tenancies.map((tenancy) => (
              <tr key={tenancy.tenancy_id}>
                <th scope="row">
                  <Link href={`/tenancies/${tenancy.tenancy_id}`}>{tenancy.unit_label}</Link>
                  <span className="tax-line-sub">
                    seit {formatDate(tenancy.set_on)} ({tenancy.set_by === "start" ? "Mietbeginn" : "Mietänderung"})
                  </span>
                </th>
                <td className="num">{formatMoney(tenancy.pauschale)}</td>
                <td className="num">{tenancy.share_percent != null ? percentText(tenancy.share_percent) : "–"}</td>
                <td className={["num", tone(tenancy.rise, true)].filter(Boolean).join(" ")}>{signedMoney(tenancy.rise)}</td>
                <td>
                  {verdictText(tenancy, check.raise_above)}
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
      <p className="muted planning-subline">
        Gezählt werden nur Betriebskosten nach § 2 BetrKV (umlagefähiges Hausgeld, Grundsteuer, Versicherung). Die Änderung der
        Wohnung wird nach den Pauschalen verteilt; ein Zimmer kann steigen, wenn sein Anteil mehr als{" "}
        {formatMoney(check.raise_above)} im Monat beträgt, und muss sinken, sobald die Kosten fallen.
      </p>
      {gap != null && year ? (
        <p className="planning-subline">
          {year.year}: Die Pauschalen decken die Kosten{isNegative(gap) ? " nicht" : ""} ({signedMoney(gap)} pro Monat) — das
          allein erlaubt keine Erhöhung.
        </p>
      ) : null}
      {settlements.length > 0 ? (
        <ul className="planning-subline">
          {settlements.map((hint) => (
            <li key={hint.allocation_id}>
              {formatDate(hint.booking_date)}, {ALLOCATION_CATEGORY[hint.category]}:{" "}
              {isNegative(hint.amount) ? "Erstattung" : "Nachzahlung"} {formatMoney(hint.amount.replace(/^-/, ""))} aus einer
              Abrechnung, keine Monatsrate; die nächste Rate kann sich ändern.
            </li>
          ))}
        </ul>
      ) : null}
      <p className="muted planning-subline">Eine Information, keine Rechtsberatung: {FLAT_RATE_NOT_ADVICE}</p>
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
  const levers = projection.scenario;
  const sale = projection.sale;
  const thisYear = Number(todayIso().slice(0, 4));
  const purchaseYear = property.purchase_date ? Number(property.purchase_date.slice(0, 4)) : null;
  const first = projection.years[0];

  return (
    <section className="card planning-projection" aria-labelledby="planning-projection-title">
      <div className="planning-card-head">
        <h2 id="planning-projection-title">Prognose bis zum Verkauf</h2>
        <span className="spacer" />
        <span className="muted" role="status">
          {busy ? "Berechnet …" : "Annahmen ändern: alles darunter rechnet neu"}
        </span>
      </div>
      <div className="planning-levers">
        <RateLever
          label="Wertsteigerung pro Jahr"
          name="Wertsteigerung"
          value={levers.value_growth}
          disabled={busy}
          onChange={(next) => onLevers({ valueGrowth: next })}
        />
        <RateLever
          label="Mieterhöhung pro Jahr"
          name="Mieterhöhung"
          value={levers.rent_growth}
          disabled={busy}
          onChange={(next) => onLevers({ rentGrowth: next })}
        />
        <TaxLever year={first} propertyId={property.id} />
        <div className="planning-lever" role="group" aria-label="Verkauf nach">
          <p className="planning-lever-label">Verkauf nach</p>
          <div className="planning-lever-control">
            <button
              type="button"
              aria-label="Früher verkaufen"
              disabled={busy || levers.sale_year - 1 <= thisYear}
              onClick={() => onLevers({ saleYear: levers.sale_year - 1 })}
            >
              −
            </button>
            <span className="planning-lever-value">
              {purchaseYear !== null ? `${levers.sale_year - purchaseYear} Jahren` : levers.sale_year}
            </span>
            <button
              type="button"
              aria-label="Später verkaufen"
              disabled={busy || levers.sale_year + 1 > 2100}
              onClick={() => onLevers({ saleYear: levers.sale_year + 1 })}
            >
              +
            </button>
          </div>
          <p className="planning-lever-sub">Verkauf am {formatDate(sale.sale_date)}</p>
        </div>
      </div>
      {overridden ? (
        <p className="muted planning-subline">
          Annahmen geändert, nicht gespeichert.{" "}
          <button type="button" className="link" onClick={onReset}>
            Zurücksetzen
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
  const lower = stepRate(value, -1);
  const higher = stepRate(value, 1);
  return (
    <div className="planning-lever" role="group" aria-label={label}>
      <p className="planning-lever-label">{label}</p>
      <div className="planning-lever-control">
        <button type="button" aria-label={`${name} senken`} disabled={disabled || lower === null} onClick={() => lower && onChange(lower)}>
          −
        </button>
        <span className="planning-lever-value">{ratePercent(value, 1)}</span>
        <button
          type="button"
          aria-label={`${name} erhöhen`}
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
  const effect = year?.tax_effect ?? null;
  const taxYear = year?.year ?? null;
  const result = year?.taxable_result ?? null;
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
      ? `${SOURCE_LABEL[zve.source]} ${zve.tax_year}${zve.amount != null ? ` · ${formatMoney(zve.amount)} zvE` : ""}${zve.carried_forward ? " (fortgeschrieben)" : ""}`
      : SOURCE_LABEL[effect.source]
    : null;
  const value = effect
    ? effect.effective_rate
      ? percentText(effect.effective_rate)
      : effect.slope
        ? percentText(effect.slope)
        : "—"
    : "—";
  return (
    <div className="planning-lever" role="group" aria-label="Steuerwirkung">
      <p className="planning-lever-label">
        Steuerwirkung{taxYear !== null ? ` ${taxYear}` : ""}
        {effect && !effect.effective_rate && effect.slope ? " (Grenzsteuersatz)" : effect ? " (effektiv)" : ""}
      </p>
      <div className="planning-lever-control">
        <span className="planning-lever-value">{value}</span>
        <Link href="/owners" className="planning-lever-edit" aria-label="Steuersatz manuell setzen" title="Steuersatz manuell setzen">
          ✎
        </Link>
      </div>
      <p className="planning-lever-sub">
        {chip ? (
          <Link href="/owners" className="badge" title="Einkommen und Steuersatz auf der Seite Eigentümer">
            {chip}
          </Link>
        ) : (
          <>
            — · <Link href="/owners">Einkommen eintragen</Link>
          </>
        )}
      </p>
    </div>
  );
}

/** The §23 check: the sale date against the first tax-free day (D88). */
function SaleBanner({ sale, propertyId }: { sale: SaleResult; propertyId: string }) {
  if (sale.tax_free_from == null) {
    return (
      <div className="notice notice-info" role="status">
        <p>
          Ohne das Datum des notariellen Kaufvertrags lässt sich die Zehnjahresfrist (§ 23 EStG) nicht prüfen.{" "}
          <Link href={`/properties/${propertyId}?tab=stammdaten`}>In den Stammdaten eintragen</Link>
        </p>
      </div>
    );
  }
  const saleDate = formatDate(sale.sale_date);
  const freeFrom = formatDate(sale.tax_free_from);
  if (!sale.taxable) {
    return (
      <div className="notice notice-ok planning-banner" role="status">
        <p>
          Verkauf am {saleDate} liegt nach dem {freeFrom}: mehr als zehn Jahre seit dem notariellen Kauf, der Gewinn ist
          steuerfrei (§ 23 EStG). Den Verkaufsvertrag nicht vor dem {freeFrom} unterschreiben.
        </p>
      </div>
    );
  }
  const loss = sale.gain != null && isNegative(sale.gain);
  return (
    <div className="notice notice-warn planning-banner" role="status">
      <p>
        Verkauf am {saleDate} liegt vor dem {freeFrom}: innerhalb von zehn Jahren seit dem notariellen Kauf, ein Gewinn ist
        steuerpflichtig (§ 23 EStG). Die abgeschriebene AfA ({formatMoney(sale.afa_claimed)}
        {isZero(sale.afa_claimed_movable) ? null : <>, davon Küche {formatMoney(sale.afa_claimed_movable)}</>}) wird dem
        Gewinn hinzugerechnet.
      </p>
      <p>
        {sale.gain == null
          ? null
          : loss
            ? `Verlust ${formatMoney(sale.gain)}: nicht verrechenbar. `
            : `Gewinn ${formatMoney(sale.gain)}. `}
        {sale.freigrenze_applied ? "Freigrenze von 1.000 € je Ehegatte berücksichtigt. " : null}
        Steuer beim Verkauf: {formatMoney(sale.sale_tax)}. Ab dem {freeFrom} wäre der Verkauf steuerfrei.
      </p>
    </div>
  );
}

function SaleKpis({ sale, property, saleYear }: { sale: SaleResult; property: Property; saleYear: number }) {
  const cashFlow = (sale.bridge ?? []).find((row) => row.key === "cash_flow");
  return (
    <div className="planning-kpis">
      <div className="planning-kpi" role="group" aria-label={`Verkaufspreis ${saleYear}`}>
        <p className="planning-kpi-label">Verkaufspreis {saleYear}</p>
        <p className="planning-kpi-value">{formatMoney(sale.value)}</p>
        <p className="planning-kpi-sub">Kaufpreis {formatMoney(property.purchase_price)}</p>
      </div>
      <div className="planning-kpi" role="group" aria-label="Cash nach Steuern">
        <p className="planning-kpi-label">Cash nach Steuern</p>
        <p className={`planning-kpi-value ${tone(cashFlow?.amount) ?? ""}`}>{signedMoney(cashFlow?.amount)}</p>
        <p className="planning-kpi-sub">alle Jahre, nach Steuern</p>
      </div>
      <div className="planning-kpi" role="group" aria-label="Erlös beim Verkauf">
        <p className="planning-kpi-label">Erlös beim Verkauf</p>
        <p className="planning-kpi-value">{signedMoney(sale.net_proceeds)}</p>
        <p className="planning-kpi-sub">
          nach Verkaufskosten, Steuer ({formatMoney(sale.sale_tax)}) und Restschuld ({formatMoney(sale.loan_balance)})
        </p>
      </div>
      <div className="planning-kpi planning-kpi-main" role="group" aria-label="Gewinn gesamt">
        <p className="planning-kpi-label">Gewinn gesamt</p>
        <p className="planning-kpi-value">{signedMoney(sale.profit)}</p>
        <p className="planning-kpi-sub">
          ≈ {returnPercent(sale.cagr)} pro Jahr auf {formatMoney(sale.initial_equity)} Eigenkapital
          {sale.irr != null ? ` · interner Zinsfuß ${returnPercent(sale.irr)}` : ""}
        </p>
      </div>
    </div>
  );
}

/** Equity (value − remaining loan, as returned) per projected year; the last bar is the sale
 * year. The heights are the returned figures relative to the largest, for display only. */
function EquityBars({ years }: { years: YearFigures[] }) {
  const largest = Math.max(0, ...years.map((y) => (y.equity == null ? 0 : Number(y.equity))));
  return (
    <figure className="planning-equity">
      <p className="planning-section-title">Eigenkapital in der Immobilie, Jahr für Jahr</p>
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
      <figcaption className="muted">Marktwert minus Restschuld. Der letzte Balken ist das Verkaufsjahr.</figcaption>
    </figure>
  );
}

const BRIDGE_LABEL: Record<BridgeRow["key"], string> = {
  appreciation: "Wertsteigerung",
  loan_paid_down: "Getilgtes Darlehen",
  cash_flow: "Cashflow nach Steuern",
  buying_costs: "Kaufnebenkosten",
  selling_costs: "Verkaufskosten",
  sale_tax: "Steuer beim Verkauf",
};

/** Where the profit comes from (D89): the backend's bridge rows, which sum to the profit. */
function Bridge({ sale, propertyId }: { sale: SaleResult; propertyId: string }) {
  const rows = sale.bridge ?? [];
  return (
    <section className="planning-bridge" aria-labelledby="planning-bridge-title">
      <p id="planning-bridge-title" className="planning-section-title">
        Woher der Gewinn kommt
      </p>
      <ul>
        {rows.map((row) => {
          const label =
            row.key === "sale_tax" && !sale.taxable ? `${BRIDGE_LABEL[row.key]} (nach 10 Jahren steuerfrei)` : BRIDGE_LABEL[row.key];
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
          <span>Gewinn gesamt</span>
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
}

function YearTable({ actualYears, projection, propertyId }: { actualYears: YearFigures[]; projection: Projection; propertyId: string }) {
  const saleYear = projection.scenario.sale_year;
  const rows: TableRow[] = [
    ...actualYears.map((figures) => ({ figures, badge: (figures.flags ?? []).includes("so_far") ? "bisher" : "Ist" })),
    ...projection.years.map((figures) => ({ figures, badge: figures.year === saleYear ? "Verkauf" : null })),
  ];
  return (
    <div className="table-wrap planning-table">
      <table>
        <caption className="visually-hidden">Jahr für Jahr: Ist-Jahre und Prognose bis zum Verkauf</caption>
        <thead>
          <tr>
            <th scope="col">Jahr</th>
            <th scope="col" className="num">
              Kaltmiete
            </th>
            <th scope="col" className="num">
              Zinsen
            </th>
            <th scope="col" className="num">
              Steuerliches Ergebnis
            </th>
            <th scope="col" className="num">
              Steuerwirkung
            </th>
            <th scope="col" className="num">
              Cash nach Steuern
            </th>
            <th scope="col" className="num">
              Wert
            </th>
            <th scope="col" className="num">
              Darlehen
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ figures: y, badge }) => {
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
                  {badge ? <span className={badge === "Verkauf" ? "badge" : "badge badge-muted"}>{badge}</span> : null}
                </td>
                {cell("Kaltmiete", formatMoney(y.rent_in))}
                {cell("Zinsen", formatMoney(y.interest))}
                {cell("Steuerliches Ergebnis", signedMoney(y.taxable_result))}
                {cell(
                  "Steuerwirkung",
                  y.tax_effect ? signedMoney(y.tax_effect.effect) : "—",
                  tone(y.tax_effect?.effect, true),
                  y.tax_effect?.sources ?? [],
                )}
                {cell("Cash nach Steuern", signedMoney(y.net_cash), `planning-strong ${tone(y.net_cash) ?? ""}`)}
                {cell("Wert", y.value == null ? "–" : formatMoney(y.value))}
                {cell("Darlehen", y.loan_balance == null ? "–" : formatMoney(y.loan_balance))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// --- Assumptions ------------------------------------------------------------------------------

const ASSUMPTION_TEXT: Record<string, (params: Record<string, string>) => string> = {
  "sale.on_31_december": (p) =>
    `Verkauf, Übergabe und Zahlung des Kaufpreises am 31.12.${p.year ?? ""}; ein Kaufpreis, der erst im Januar fließt, zählt steuerlich im Folgejahr.`,
  "tax.no_child_allowances": () => "Kinderfreibeträge und Progressionsvorbehalt nicht berücksichtigt.",
  "draft.form_provisional": (p) =>
    `Für ${(p.years ?? "").split(",").join(", ")} ist das Formular noch nicht veröffentlicht; Zeilen und Kennzahlen sind vorläufig.`,
  "tariff.reused": (p) => `Für ${p.year ?? "?"} gibt es noch keinen Einkommensteuertarif; gerechnet wird mit dem Tarif ${p.from ?? "?"}.`,
  "sale.no_contract_date": () => "Ohne Datum des Kaufvertrags ist die Zehnjahresfrist (§ 23 EStG) nicht geprüft.",
  "sale.tax_without_income": () =>
    "Ohne eingetragenes Einkommen ist die Steuer auf einen Verkaufsgewinn nicht berechnet (0 €).",
};

function assumptionText(assumption: Assumption): string {
  return ASSUMPTION_TEXT[assumption.code]?.(assumption.params ?? {}) ?? assumption.code;
}

function leverLines(levers: Levers): string[] {
  const followUp = (levers.loans ?? []).filter((loan) => loan.follow_up_rate != null);
  return [
    `Instandhaltung ${formatMoney(levers.upkeep_per_year)} pro Jahr, Kostensteigerung ${ratePercent(levers.cost_inflation, 1)} pro Jahr.`,
    `Verkaufskosten ${ratePercent(levers.selling_costs_rate)} des Verkaufspreises.`,
    `Leerstand ${formatDecimal(levers.vacancy_months)} Monate pro Jahr; Wert heute: ${
      levers.today_value != null ? formatMoney(levers.today_value) : "der Kaufpreis"
    }.`,
    followUp.length > 0
      ? `Anschlusszins nach der Zinsbindung: ${followUp.map((loan) => ratePercent(loan.follow_up_rate ?? "0", 1)).join(", ")}.`
      : "Anschlusszins nach der Zinsbindung: wie im Darlehen hinterlegt, sonst der heutige Zins.",
  ];
}

function AssumptionsFooter({ projection }: { projection: Projection }) {
  const lines = [...leverLines(projection.scenario), ...(projection.assumptions ?? []).map(assumptionText)];
  return (
    <div className="planning-assumptions" aria-label="Annahmen">
      <p className="planning-section-title">Annahmen</p>
      <ul>
        {lines.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
      <p className="muted">
        Regelwerk {projection.rule_set.version} · Tarif {projection.tariff.version}
        {projection.tariff.reused_from != null ? ` (von ${projection.tariff.reused_from})` : ""}
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
  const currentDefault = scenarios.data?.find((s) => s.is_default);
  return (
    <section className="card stack planning-scenarios" aria-labelledby="planning-scenarios-title">
      <div className="planning-card-head">
        <h2 id="planning-scenarios-title">Szenarien</h2>
        <span className="spacer" />
        <ActionDialog label="Szenario speichern" title="Szenario speichern" buttonClass="">
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
      {saved ? <Notice>Szenario „{saved}“ gespeichert.</Notice> : null}
      <Loading state={scenarios}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">
              Noch kein Szenario gespeichert. Ohne Szenario rechnet die Prognose mit den Standardannahmen und dem ersten Jahr
              nach der Zehnjahresfrist als Verkaufsjahr.
            </p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col" className="num">
                      Wertsteigerung
                    </th>
                    <th scope="col" className="num">
                      Mieterhöhung
                    </th>
                    <th scope="col" className="num">
                      Verkauf
                    </th>
                    <th scope="col">Gespeichert</th>
                    <th scope="col">
                      <span className="visually-hidden">Aktion</span>
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
                          {scenario.is_default ? <span className="badge">Standard</span> : null}
                          {shown ? (
                            <span className="badge badge-muted">{overridden ? "angezeigt, geändert" : "angezeigt"}</span>
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
                            Anzeigen
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
  const ids = useId();
  const [name, setName] = useState("");
  const [asDefault, setAsDefault] = useState(false);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(SCENARIO_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const trimmed = name.trim();
    if (trimmed === "") return form.invalid({ name: ["Bitte einen Namen eingeben."] });
    if (trimmed.length > 100) return form.invalid({ name: ["Höchstens 100 Zeichen."] });
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
        Gespeichert werden die Annahmen, wie sie gerade angezeigt sind: Wertsteigerung {ratePercent(levers.value_growth, 1)},
        Mieterhöhung {ratePercent(levers.rent_growth, 1)}, Verkauf {levers.sale_year}.
      </p>
      <Field id={`${ids}-name`} label="Name" errors={form.of("name")}>
        <input type="text" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} autoComplete="off" required />
      </Field>
      <div className="field">
        <label className="inline" htmlFor={`${ids}-default`}>
          <input id={`${ids}-default`} type="checkbox" checked={asDefault} onChange={(e) => setAsDefault(e.target.checked)} />
          Als Standard für diese Immobilie
        </label>
        {currentDefault ? (
          <p className="muted hint">Ersetzt das bisherige Standard-Szenario „{currentDefault.name}“; es erscheint dann nicht mehr in der Liste.</p>
        ) : (
          <p className="muted hint">Das Standard-Szenario zeigt die Planung beim Öffnen.</p>
        )}
      </div>
      <div className="row">
        <Submit busy={busy}>Speichern</Submit>
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
  if (refs.length === 0) return <>{label}</>;
  return (
    <ActionDialog label={label} title={`Quellen: ${title}`} buttonClass={buttonClass}>
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
  return (
    <>
      {hasPayments && payments.data === undefined ? <p className="muted">Lädt …</p> : null}
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
  if (!fact) {
    return (
      <>
        <span>{TAX_SOURCE_KIND[source.kind]}</span>
        {source.amount != null ? <span className="num">{formatMoney(source.amount)}</span> : null}
      </>
    );
  }
  const period = fact.service_period ? `Zeitraum ${formatDate(fact.service_period.start)} – ${formatDate(fact.service_period.end)}` : null;
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
        {fact.transaction_id ? <Link href={`/inbox?umsatz=${fact.transaction_id}`}>Bankzeile öffnen</Link> : null}
        {fact.document_id ? <Link href={`/documents/${fact.document_id}`}>Beleg öffnen</Link> : null}
      </p>
    </div>
  );
}

function OtherSource({ source, propertyId }: { source: TaxSourceRef; propertyId: string }) {
  const link =
    linkFor(source, propertyId) ?? (source.kind === "income_input" ? { href: "/owners", label: "Einkommen ansehen" } : null);
  return (
    <>
      <span>{TAX_SOURCE_KIND[source.kind]}</span>
      {source.amount != null ? <span className="num">{formatMoney(source.amount)}</span> : null}
      {link ? <Link href={link.href}>{link.label}</Link> : null}
    </>
  );
}
