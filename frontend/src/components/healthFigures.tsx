"use client";

// The household's key figures on Übersicht (phase 9 §14, D235–D243, D250; docs/mockups/
// 13-overview-figures.png before the limits are confirmed, 13b after): the card
// „Liquiditätsreserve“, the card „Ampel-Grenzen festlegen“ (each proposed limit with its origin,
// editable; „Übernehmen“ stores them in a new version of the default household scenario), the
// block „Kennzahlen“ with each figure's mark beside its sentences, the split of the assets
// (never marked) and „Vermögen im Verlauf“ (GET /health/net-worth-series). Every figure is the
// server's (GET /health); a mark and its sentences come only from confirmed limits, so nothing
// is coloured before „Übernehmen“. Every figure opens what it is made of, and every gap is a
// to-do with its button (principle 7); a month-end to-do (`/?month_end=…`) is the button
// „Monatsabschluss festhalten“ itself.
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type FormEvent, type ReactNode, Suspense, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { ApiError, ruleCodes } from "@/api/errors";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { formatDate, formatMonth } from "./format";
import {
  type BandLimits,
  type BandProposal,
  bandsConfirmedBy,
  COLOUR_CLASS,
  COLOUR_LABEL,
  currentDefault,
  FIGURE_LABEL,
  FIGURES,
  formatEuro,
  formatOneDecimal,
  formatRatio,
  formatSignedEuro,
  type Health,
  type HealthTodo,
  HIGHER_IS_BETTER,
  type HouseholdScenario,
  type Light,
  leversOf,
  limitText,
  loadMemberNames,
  loadScenarioHistory,
  longDate,
  type MarkedFigure,
  monthEnd,
  monthEndOf,
  monthRange,
  type NetWorthPoint,
  REFERENCE_POINTS,
  saveDefaultScenario,
  toMultiple,
  toSignedRate,
} from "./healthShared";
import { Field } from "./form";
import { percentInput } from "./input";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

import "./health.css";

type Account = components["schemas"]["Account"];
type SnapshotResult = components["schemas"]["SnapshotResult"];

interface HealthData {
  health: Health;
  series: NetWorthPoint[];
  scenarios: HouseholdScenario[];
  names: Map<string, string>;
  accounts: Map<string, Account>;
}

async function loadHealth(): Promise<HealthData> {
  const [health, series, scenarios, names, accounts] = await Promise.all([
    ok(api.GET("/api/health")),
    ok(api.GET("/api/health/net-worth-series")),
    loadScenarioHistory(),
    loadMemberNames(),
    ok(api.GET("/api/accounts")),
  ]);
  return { health, series, scenarios, names, accounts: new Map(accounts.map((account) => [account.id, account])) };
}

/** The Übersicht's phase 9 block; one request set for all of it. */
export function HealthSection() {
  return (
    <Suspense fallback={null}>
      <HealthBlock />
    </Suspense>
  );
}

function HealthBlock() {
  const state = useLoad(loadHealth);
  const requested = useSearchParams().get("month_end");
  return (
    <>
      {requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) ? <MonthEndCard day={requested} onDone={state.reload} /> : null}
      <Loading state={state}>{(data) => <HealthFigures data={data} onChanged={state.reload} />}</Loading>
    </>
  );
}

function HealthFigures({ data, onChanged }: { data: HealthData; onChanged: () => void }) {
  const { health, scenarios } = data;
  const [editing, setEditing] = useState(false);
  const proposals = health.proposals ?? [];
  const todos = health.todos ?? [];
  const current = currentDefault(scenarios);
  const confirmed = (current?.levers?.bands ?? []).length > 0;
  const showCard = !confirmed || editing;
  // The bands' own to-do is the card itself on this page.
  const otherTodos = todos.filter((todo) => todo.code !== "bands_unconfirmed");
  return (
    <>
      <ReserveCard data={data} onChanged={onChanged} />
      {showCard ? (
        <BandsCard
          health={health}
          proposals={proposals}
          current={current}
          confirmed={confirmed}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
          onCancel={confirmed ? () => setEditing(false) : null}
        />
      ) : (
        <div className="notice notice-info row health-confirmed" role="note">
          <p>Eure Ampel-Grenzen sind übernommen. Jede Farbe misst eine Kennzahl an eurer eigenen Grenze, nicht an einer Regel von außen.</p>
          <span className="spacer" />
          <button type="button" className="link" onClick={() => setEditing(true)}>
            Grenzen ändern
          </button>
        </div>
      )}
      <FiguresCard
        data={data}
        confirmed={confirmed}
        current={current}
        todos={otherTodos}
        onChanged={onChanged}
        onEditBands={() => setEditing(true)}
      />
      <SeriesCard series={data.series} onChanged={onChanged} />
    </>
  );
}

// --- to-dos -----------------------------------------------------------------------------

/** One to-do with its button or link; a month-end to-do captures the month-end at once. */
export function HealthTodoItem({ todo, onChanged }: { todo: HealthTodo; onChanged: () => void }) {
  const day = monthEndOf(todo.href);
  return (
    <li className="health-todo">
      <span>{todo.text}</span>
      {day !== null ? (
        <MonthEndButton day={day} label={todo.action} onDone={onChanged} />
      ) : todo.href === "/" ? (
        <a href="#ampel-grenzen" className="button-link secondary">
          {todo.action}
        </a>
      ) : (
        <Link href={todo.href} className="button-link secondary">
          {todo.action}
        </Link>
      )}
    </li>
  );
}

export function HealthTodoList({ todos, onChanged }: { todos: HealthTodo[]; onChanged: () => void }) {
  if (todos.length === 0) return null;
  return (
    <ul className="health-todos">
      {todos.map((todo, index) => (
        <HealthTodoItem key={`${todo.code}-${index}`} todo={todo} onChanged={onChanged} />
      ))}
    </ul>
  );
}

const SNAPSHOT_TEXT: Record<SnapshotResult["outcome"], (day: string) => string> = {
  written: (day) => `Monatsabschluss zum ${formatDate(day)} festgehalten.`,
  recaptured: (day) => `Monatsabschluss zum ${formatDate(day)} neu festgehalten: die Zahlen hatten sich seitdem geändert.`,
  unchanged: (day) => `Der Monatsabschluss zum ${formatDate(day)} ist schon mit genau diesen Zahlen festgehalten.`,
  empty: () => "Nichts festzuhalten: noch kein Konto, keine Anlage und keine Immobilie mit einem Wert.",
};

/** „Monatsabschluss festhalten“ for a past month-end (POST /net-worth/snapshots {as_of}, D241). */
export function MonthEndButton({ day, label, onDone }: { day: string; label: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<SnapshotResult | null>(null);
  async function capture() {
    setBusy(true);
    setError(null);
    try {
      setResult(await ok(api.POST("/api/net-worth/snapshots", { body: { as_of: day } })));
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="health-month-end">
      <button type="button" className="secondary" disabled={busy} onClick={() => void capture()}>
        {label}
      </button>
      {result ? <span role="status">{SNAPSHOT_TEXT[result.outcome](result.as_of)}</span> : null}
      {error != null ? <ErrorNotice error={error} /> : null}
    </span>
  );
}

/** The card a month-end to-do of Planung opens (`/?month_end=YYYY-MM-DD`). */
function MonthEndCard({ day, onDone }: { day: string; onDone: () => void }) {
  return (
    <section className="card stack notice-info" aria-labelledby="month-end-title">
      <h2 id="month-end-title">Monatsabschluss zum {longDate(day)}</h2>
      <p>
        Für diesen Tag ist noch kein Monatsabschluss gespeichert. Hausbuch hält ihn am nächsten Morgen selbst fest; mit dem Knopf sofort.
      </p>
      <p>
        <MonthEndButton day={day} label="Monatsabschluss festhalten" onDone={onDone} />
      </p>
    </section>
  );
}

// --- the mark ---------------------------------------------------------------------------

function MarkPill({ light }: { light: Light | undefined }) {
  if (!light?.colour) return null;
  return <span className={COLOUR_CLASS[light.colour]}>{COLOUR_LABEL[light.colour]}</span>;
}

/** „eigene Grenze: rot unter 0,0 %, keine Grenze für Grün“ beside a marked figure. */
function LimitLine({ figure, light, target }: { figure: MarkedFigure; light: Light | undefined; target?: string }) {
  if (!light?.colour) return null;
  const higher = HIGHER_IS_BETTER[figure];
  if (figure === "reserve") {
    return (
      <p className="muted">
        eigene Grenze: grün ab eurem Ziel{target ? ` von ${formatEuro(target)}` : ""}, rot unter {limitText(figure, light.red)} des Ziels
      </p>
    );
  }
  const red = light.red != null ? `rot ${higher ? "unter" : "über"} ${limitText(figure, light.red)}` : "keine Grenze für Rot";
  const green = light.green != null ? `grün ${higher ? "ab" : "bis"} ${limitText(figure, light.green)}` : "keine Grenze für Grün";
  return <p className="muted">eigene Grenze: {higher ? `${red}, ${green}` : `${green}, ${red}`}</p>;
}

function Texts({ light }: { light: Light | undefined }) {
  const texts = light?.texts ?? [];
  if (texts.length === 0) return null;
  return (
    <div className="health-texts">
      {texts.map((text) => (
        <p key={text}>{text}</p>
      ))}
    </div>
  );
}

// --- Liquiditätsreserve -------------------------------------------------------------------

const RESERVE_ORIGIN: Record<components["schemas"]["ReserveOrigin"], string> = {
  default: "zählt zur Reserve",
  added: "von euch hinzugefügt",
  removed: "von euch herausgenommen",
};

function ReserveCard({ data, onChanged }: { data: HealthData; onChanged: () => void }) {
  const reserve = data.health.reserve;
  const light = data.health.marks?.reserve;
  return (
    <section className="card stack" aria-labelledby="reserve-title">
      <h2 id="reserve-title">Liquiditätsreserve</h2>
      {reserve ? (
        <>
          <p className="cashflow-value">
            {formatEuro(reserve.amount)} <MarkPill light={light} />
          </p>
          <p className="muted">
            {reserve.months != null
              ? `reicht für ${formatOneDecimal(reserve.months)} Monate eures Bedarfs von ${formatEuro(reserve.need)} im Monat`
              : "Ohne Bedarf im Monat lässt sich die Reserve nicht in Monaten angeben."}
          </p>
          <p className="muted">
            eigenes Ziel: {formatEuro(reserve.target)} = {formatOneDecimal(reserve.target_months).replace(/,0$/, "")} Monate ·{" "}
            <Link href="/planning?tab=haushalt">ändern</Link>
          </p>
          <LimitLine figure="reserve" light={light} target={reserve.target} />
          <Texts light={light} />
          <details className="health-sources">
            <summary>Woraus die Reserve besteht</summary>
            <ul className="household-rows">
              {(reserve.accounts ?? []).map((row) => (
                <li key={row.account_id}>
                  <span>
                    <Link href={`/accounts/${row.account_id}`}>{data.accounts.get(row.account_id)?.label ?? "Konto"}</Link>{" "}
                    <span className="muted">
                      {RESERVE_ORIGIN[row.origin]} · Stand {formatDate(row.as_of)}
                    </span>
                  </span>
                  <span>{formatEuro(row.balance)}</span>
                </li>
              ))}
            </ul>
            <p className="muted">
              Der Bedarf ist der Schnitt der regelmäßigen Ausgaben plus das, was die Wohnung im Monat nicht selbst trägt, aus den
              abgeschlossenen Monaten {monthRange(data.health.window.months ?? [])}.{" "}
              <Link href="/planning?tab=haushalt">Konten der Reserve ändern</Link>
            </p>
          </details>
          <HealthTodoList todos={reserve.todos ?? []} onChanged={onChanged} />
        </>
      ) : (
        <p className="muted">
          Die Reserve erscheint, sobald drei Monate in Folge abgeschlossen sind; was dafür fehlt, steht unter „Kennzahlen“.
        </p>
      )}
    </section>
  );
}

// --- Ampel-Grenzen festlegen ---------------------------------------------------------------

interface LimitDraft {
  green: string;
  red: string;
}

/** A limit from the API as typed text: a ratio in percent ("0.3" → "30,0"), the reserve's red as
 * a percent of the target, Schulden zu Jahreseinkommen as a multiple. */
function limitInput(figure: MarkedFigure, value: string | null | undefined): string {
  if (value == null) return "";
  if (figure === "debt_to_income") return value.replace(".", ",");
  const percent = percentInput(value);
  return percent.includes(",") ? percent : `${percent},0`;
}

function parseLimit(figure: MarkedFigure, raw: string): { ok: true; value: string | null } | { ok: false; message: string } {
  if (raw.trim() === "") return { ok: true, value: null };
  return figure === "debt_to_income" ? toMultiple(raw, 4) : toSignedRate(raw, 4);
}

function draftsFrom(bands: BandLimits[], proposals: BandProposal[]): Record<MarkedFigure, LimitDraft> {
  const out = {} as Record<MarkedFigure, LimitDraft>;
  for (const figure of FIGURES) {
    const band = bands.find((b) => b.figure === figure);
    const proposal = proposals.find((p) => p.figure === figure);
    out[figure] = band
      ? { green: limitInput(figure, band.green), red: limitInput(figure, band.red) }
      : bands.length > 0
        ? { green: "", red: "" }
        : { green: limitInput(figure, proposal?.green?.value), red: limitInput(figure, proposal?.red?.value) };
  }
  return out;
}

const FIGURE_INTRO: Record<MarkedFigure, string> = {
  reserve: "",
  savings_rate:
    "Rot, wenn ihr mehr ausgegeben als eingenommen habt. Eine Grenze für Grün schlägt Hausbuch nicht vor; ihr könnt eine eintragen.",
  ltv: "Wie viel die Kredite der Wohnung von ihrem geschätzten Wert ausmachen.",
  debt_service_ratio: "Welcher Teil eures Einkommens für Zins und Tilgung an die Bank geht.",
  debt_ratio: "Hausbuch schlägt keine Grenze vor; ohne eure Zahl bleibt diese Kennzahl ohne Farbe.",
  debt_to_income: "Hausbuch schlägt keine Grenze vor; ohne eure Zahl bleibt diese Kennzahl ohne Farbe.",
};

/** A refusal of the default scenario the limits are saved in (`household_scenario.*`, PR #434
 * review D): the fix is on Planung › Haushalt, where the scenario's other levers are. */
function scenarioRefused(error: unknown): boolean {
  return error instanceof ApiError && error.body !== null && ruleCodes(error.body).some((code) => code.startsWith("household_scenario."));
}

function BandsCard({
  health,
  proposals,
  current,
  confirmed,
  onSaved,
  onCancel,
}: {
  health: Health;
  proposals: BandProposal[];
  current: HouseholdScenario | null;
  confirmed: boolean;
  onSaved: () => void;
  onCancel: (() => void) | null;
}) {
  const ids = useId();
  const [drafts, setDrafts] = useState(() => draftsFrom(current?.levers?.bands ?? [], proposals));
  const [messages, setMessages] = useState<Partial<Record<string, string>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const initial = draftsFrom(current?.levers?.bands ?? [], proposals);
  const target = health.reserve?.target ?? null;
  const targetMonths = current?.levers?.reserve_target_months ?? health.reserve?.target_months ?? "6";

  function set(figure: MarkedFigure, side: keyof LimitDraft, value: string) {
    setDrafts((previous) => ({ ...previous, [figure]: { ...previous[figure], [side]: value } }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found: Partial<Record<string, string>> = {};
    const bands: BandLimits[] = [];
    for (const figure of FIGURES) {
      const draft = drafts[figure];
      const green = figure === "reserve" ? ({ ok: true, value: null } as const) : parseLimit(figure, draft.green);
      const red = parseLimit(figure, draft.red);
      if (!green.ok) found[`${figure}.green`] = green.message;
      if (!red.ok) found[`${figure}.red`] = red.message;
      if (green.ok && red.ok && (green.value !== null || red.value !== null)) {
        bands.push({ figure, green: green.value, red: red.value });
      }
    }
    setMessages(found);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    setError(null);
    try {
      await saveDefaultScenario({ ...leversOf(current), bands }, current);
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  function originText(figure: MarkedFigure): string {
    const draft = drafts[figure];
    if (draft.green.trim() === "" && draft.red.trim() === "") return "noch keine Grenze";
    const proposal = proposals.find((p) => p.figure === figure);
    const same = draft.green === initial[figure].green && draft.red === initial[figure].red;
    if (!confirmed && proposal && same) {
      const source = proposal.red?.origin === "source" || proposal.green?.origin === "source";
      return source ? "Grenze aus einer Quelle, Vorschlag von Hausbuch" : "eigene Grenze, Vorschlag von Hausbuch";
    }
    return "eigene Grenze";
  }

  function field(figure: MarkedFigure, side: keyof LimitDraft, label: string, unit: string, hint?: string) {
    const message = messages[`${figure}.${side}`];
    return (
      <Field id={`${ids}-${figure}-${side}`} label={`${label} (${unit})`} hint={hint} errors={message ? [message] : []}>
        <input
          type="text"
          inputMode="decimal"
          className="health-limit-input"
          value={drafts[figure][side]}
          onChange={(event) => set(figure, side, event.target.value)}
        />
      </Field>
    );
  }

  const reserveRed = parseLimit("reserve", drafts.reserve.red);
  const reserveRedEuro =
    target && reserveRed.ok && reserveRed.value !== null ? formatEuro(multiply(target, reserveRed.value)) : null;

  return (
    <section className="card stack" id="ampel-grenzen" aria-labelledby={`${ids}-title`}>
      <h2 id={`${ids}-title`}>{confirmed ? "Ampel-Grenzen ändern" : "Ampel-Grenzen festlegen"}</h2>
      <p>
        Grün, Gelb und Rot zeigt die App erst, wenn ihr eure eigenen Grenzen übernommen habt. Bis dahin hat keine Kennzahl eine Farbe.
        Hausbuch schlägt die Werte unten vor; jeder lässt sich ändern oder leeren.
      </p>
      <form className="stack" onSubmit={(event) => void submit(event)} aria-label="Ampel-Grenzen">
        {FIGURES.map((figure) => (
          <fieldset key={figure} className="health-limit">
            <legend>{FIGURE_LABEL[figure]}</legend>
            {figure === "reserve" ? (
              <p className="muted">
                Grün ab eurem eigenen Reserveziel von {formatOneDecimal(targetMonths).replace(/,0$/, "")} Monaten
                {target ? ` (${formatEuro(target)})` : ""}, das ihr unter <Link href="/planning?tab=haushalt">Planung</Link> einstellt.
              </p>
            ) : (
              <p className="muted">{FIGURE_INTRO[figure]}</p>
            )}
            <div className="health-limit-fields">
              {figure === "reserve" ? (
                field("reserve", "red", "Rot unter", "% des Ziels", reserveRedEuro ? `= ${reserveRedEuro}` : undefined)
              ) : HIGHER_IS_BETTER[figure] ? (
                <>
                  {field(figure, "red", "Rot unter", "%")}
                  {field(figure, "green", "Grün ab", "%", drafts[figure].green.trim() === "" ? "leer: ohne Grün" : undefined)}
                </>
              ) : (
                <>
                  {field(figure, "green", "Grün bis", figure === "debt_to_income" ? "fach" : "%")}
                  {field(figure, "red", "Rot über", figure === "debt_to_income" ? "fach" : "%")}
                </>
              )}
            </div>
            <p>
              <span className="badge badge-muted">{originText(figure)}</span>
            </p>
            <div className="health-reference">
              <p className="health-reference-title">Bezugspunkt</p>
              {REFERENCE_POINTS[figure].map((text) => (
                <p key={text} className="muted">
                  {text}
                </p>
              ))}
            </div>
          </fieldset>
        ))}
        {error != null ? <ErrorNotice error={error} /> : null}
        {scenarioRefused(error) ? (
          <p className="muted" role="note">
            Die Grenzen werden im Standard-Szenario gespeichert, und dessen Annahmen lassen sich so gerade nicht speichern. Bitte das
            Szenario unter <Link href="/planning?tab=haushalt">Planung › Haushalt</Link> prüfen und dort speichern; danach lassen sich
            die Grenzen hier übernehmen.
          </p>
        ) : null}
        <div className="row">
          <button type="submit" disabled={busy}>
            Übernehmen
          </button>
          {onCancel ? (
            <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
              Abbrechen
            </button>
          ) : null}
          <span className="muted">Gespeichert in eurem Szenario; ändern könnt ihr sie jederzeit mit „Grenzen ändern“.</span>
        </div>
      </form>
    </section>
  );
}

/** a × b for two decimal strings, exact (the reserve's red limit in euros, for display). */
function multiply(a: string, b: string): string {
  const parse = (value: string): [bigint, number] => {
    const negative = value.startsWith("-");
    const [int = "0", frac = ""] = value.replace(/^-/, "").split(".");
    const digits = BigInt(int + frac);
    return [negative ? -digits : digits, frac.length];
  };
  const [x, xs] = parse(a);
  const [y, ys] = parse(b);
  const product = x * y;
  const scale = xs + ys;
  const negative = product < 0n;
  const text = (negative ? -product : product).toString().padStart(scale + 1, "0");
  const whole = scale > 0 ? text.slice(0, -scale) : text;
  const fraction = scale > 0 ? text.slice(-scale) : "";
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

// --- Kennzahlen -------------------------------------------------------------------------

const SPLIT_LABEL: Record<string, string> = {
  property: "Immobilien",
  depot: "Fonds und Wertpapiere",
  bank: "Bankguthaben",
  crypto: "Krypto",
};

function FiguresCard({
  data,
  confirmed,
  current,
  todos,
  onChanged,
  onEditBands,
}: {
  data: HealthData;
  confirmed: boolean;
  current: HouseholdScenario | null;
  todos: HealthTodo[];
  onChanged: () => void;
  onEditBands: () => void;
}) {
  const { health } = data;
  const marks = health.marks ?? {};
  const months = health.window.months ?? [];
  const range = monthRange(months);
  const newest = months[months.length - 1];
  const by = current && confirmed ? bandsConfirmedBy(data.scenarios, current) : null;
  const rate = health.savings_rate;
  const debt = health.debt;
  const ltvRow = (debt?.properties ?? []).reduce<components["schemas"]["PropertyLtv"] | null>(
    (best, row) => (row.ltv != null && (best?.ltv == null || Number(row.ltv) > Number(best.ltv)) ? row : best),
    null,
  );
  const gap = (figure: components["schemas"]["DebtFigure"]) => debt?.gaps?.find((g) => g.figure === figure)?.reason ?? null;
  const split = Object.entries(health.split ?? {}).sort(([, a], [, b]) => Number(b) - Number(a));
  const annualised = debt && debt.months < 12 ? `; hochgerechnet aus ${debt.months} Monaten` : "";

  return (
    <section className="card stack" aria-labelledby="figures-title">
      <div className="planning-card-head">
        <h2 id="figures-title">Kennzahlen</h2>
        <span className="spacer" />
        {confirmed ? (
          <button type="button" className="link" onClick={onEditBands}>
            Grenzen ändern
          </button>
        ) : (
          <a href="#ampel-grenzen" className="link">
            Grenzen festlegen
          </a>
        )}
      </div>
      <p className="muted">
        {range ? `${range} · ` : ""}
        {newest ? `Bestände am ${longDate(monthEnd(newest))} · ` : ""}
        {confirmed
          ? `Farben nach euren eigenen Grenzen${
              by ? `, übernommen${by.created_by ? ` von ${data.names.get(by.created_by) ?? "einem Mitglied"}` : ""} am ${longDate(by.created_at)}` : ""
            }`
          : "ohne Farben, bis ihr eure Grenzen übernehmt"}
      </p>
      <HealthTodoList todos={todos} onChanged={onChanged} />
      {rate ? (
        <Figure title="Sparquote" value={formatRatio(rate.rate)} light={marks.savings_rate}>
          <ul className="household-rows">
            <li>
              <span>ohne Einmalausgaben</span>
              <span>{formatRatio(rate.rate_regular)}</span>
            </li>
            <li>
              <span>davon Tilgung</span>
              <span>{formatEuro(rate.principal)}</span>
            </li>
            <li>
              <span>ohne Tilgung</span>
              <span>{formatRatio(rate.rate_without_principal)}</span>
            </li>
          </ul>
          <p className="muted">
            Wie viel von eurem Einkommen übrig blieb: Einkommen minus Ausgaben, geteilt durch das Einkommen. {range}. Die Tilgung des
            Darlehens zählt als gespart, weil sie eure Schulden senkt.
          </p>
          <LimitLine figure="savings_rate" light={marks.savings_rate} />
          <Texts light={marks.savings_rate} />
          <details className="health-sources">
            <summary>Monate ansehen</summary>
            <ul className="household-rows">
              {(rate.months ?? []).map((row) => (
                <li key={row.month}>
                  <Link href={`/household?monat=${row.month}`}>{formatMonth(row.month)}</Link>
                  <span>{formatRatio(row.rate)}</span>
                </li>
              ))}
            </ul>
            <p className="muted">
              Gespart {formatEuro(rate.saved)} von {formatEuro(rate.income)} Einkommen; Einmalausgaben {formatEuro(rate.one_off_spending)}.
            </p>
          </details>
        </Figure>
      ) : null}
      {debt ? (
        <>
          <Figure
            title="Schuldendienstquote"
            value={debt.debt_service_ratio != null ? formatRatio(debt.debt_service_ratio) : "–"}
            light={marks.debt_service_ratio}
          >
            <p className="muted">
              Zins und Tilgung geteilt durch euer Einkommen vor diesen Zahlungen{annualised} ({formatEuro(debt.debt_service_year)} von{" "}
              {formatEuro(debt.income_year)} im Jahr).
            </p>
            {gap("debt_service_ratio") ? <p className="muted">{gap("debt_service_ratio")}</p> : null}
            <LimitLine figure="debt_service_ratio" light={marks.debt_service_ratio} />
            <Texts light={marks.debt_service_ratio} />
            <details className="health-sources">
              <summary>Woraus die Zahl besteht</summary>
              <ul className="household-rows">
                <li>
                  <span>Zinsen in {range}</span>
                  <span>{formatEuro(debt.interest)}</span>
                </li>
                <li>
                  <span>Tilgung in {range}</span>
                  <span>{formatEuro(debt.principal)}</span>
                </li>
              </ul>
              <p className="muted">
                Aus den Buchungen des Mietkontos; <Link href="/planning">Planung › Immobilie</Link> zeigt die Raten je Jahr.
              </p>
            </details>
          </Figure>
          <Figure title="Beleihungsauslauf" value={ltvRow?.ltv != null ? formatRatio(ltvRow.ltv) : "–"} light={marks.ltv}>
            <p className="muted">
              Die Kredite der Wohnung geteilt durch ihren geschätzten Wert
              {ltvRow ? ` (${formatEuro(ltvRow.loan_balance)} von ${formatEuro(ltvRow.estimate)})` : ""}.
            </p>
            {gap("ltv") ? <p className="muted">{gap("ltv")}</p> : null}
            <LimitLine figure="ltv" light={marks.ltv} />
            <Texts light={marks.ltv} />
            <details className="health-sources">
              <summary>Woraus die Zahl besteht</summary>
              <ul className="household-rows">
                {(debt.properties ?? []).map((row) => (
                  <li key={row.property_id}>
                    <span>
                      <Link href={`/properties/${row.property_id}`}>Immobilie</Link>{" "}
                      <span className="muted">
                        Wert {row.estimate_source === "scenario" ? "aus ihrem Szenario" : "nach dem Kaufpreis"}{" "}
                        {formatEuro(row.estimate)}, Darlehen {formatEuro(row.loan_balance)}
                      </span>
                    </span>
                    <span>{formatRatio(row.ltv)}</span>
                  </li>
                ))}
              </ul>
            </details>
          </Figure>
          <Figure
            title="Verschuldungsgrad"
            value={debt.debt_ratio != null ? formatRatio(debt.debt_ratio) : "–"}
            light={marks.debt_ratio}
          >
            <p className="muted">
              Alle eure Schulden geteilt durch alles, was ihr besitzt ({formatEuro(debt.liabilities)} von {formatEuro(debt.gross_assets)}).
            </p>
            {gap("debt_ratio") ? <p className="muted">{gap("debt_ratio")}</p> : null}
            <LimitLine figure="debt_ratio" light={marks.debt_ratio} />
            <Texts light={marks.debt_ratio} />
            <details className="health-sources">
              <summary>Woraus die Zahl besteht</summary>
              <ul className="household-rows">
                <li>
                  <span>Alle Schulden am Monatsende</span>
                  <span>{formatEuro(debt.liabilities)}</span>
                </li>
                <li>
                  <span>Alles, was ihr besitzt</span>
                  <span>{formatEuro(debt.gross_assets)}</span>
                </li>
              </ul>
              <p className="muted">
                Die Beträge kommen aus dem Monatsabschluss; jede Position steht unter <Link href="/assets">Anlagen</Link> und{" "}
                <Link href="/accounts">Konten</Link>.
              </p>
            </details>
          </Figure>
          <Figure
            title="Schulden zu Jahreseinkommen"
            value={debt.debt_to_income != null ? formatOneDecimal(debt.debt_to_income) : "–"}
            light={marks.debt_to_income}
          >
            <p className="muted">Alle eure Schulden geteilt durch euer Einkommen eines Jahres{annualised}.</p>
            {gap("debt_to_income") ? <p className="muted">{gap("debt_to_income")}</p> : null}
            <LimitLine figure="debt_to_income" light={marks.debt_to_income} />
            <Texts light={marks.debt_to_income} />
            <details className="health-sources">
              <summary>Woraus die Zahl besteht</summary>
              <ul className="household-rows">
                <li>
                  <span>Alle Schulden am Monatsende</span>
                  <span>{formatEuro(debt.liabilities)}</span>
                </li>
                <li>
                  <span>Einkommen eines Jahres{annualised ? `, hochgerechnet aus ${debt.months} Monaten` : ""}</span>
                  <span>{formatEuro(debt.income_year)}</span>
                </li>
              </ul>
              <p className="muted">
                Die Monate stehen unter „Sparquote › Monate ansehen“; die Schulden unter <Link href="/loans">Darlehen</Link> und{" "}
                <Link href="/accounts">Konten</Link>.
              </p>
            </details>
          </Figure>
          {!confirmed ? null : (["debt_ratio", "debt_to_income"] as const).some((f) => !current?.levers?.bands?.some((b) => b.figure === f)) ? (
            <p className="muted">
              Verschuldungsgrad und Schulden zu Jahreseinkommen bleiben ohne Farbe, bis ihr eine Grenze eintragt.{" "}
              <button type="button" className="link" onClick={onEditBands}>
                Grenze eintragen
              </button>
            </p>
          ) : null}
        </>
      ) : null}
      {split.length > 0 ? (
        <div className="stack health-split">
          <h3>Aufteilung des Vermögens</h3>
          <p className="muted">
            Welcher Teil von allem, was ihr besitzt{debt ? ` (${formatEuro(debt.gross_assets)})` : ""}, in welcher Anlageklasse steckt.
            {newest ? ` Am ${longDate(monthEnd(newest))}.` : ""} Eine Aufteilung ist keine Bewertung und bekommt nie eine Farbe.
          </p>
          <ul className="health-split-rows">
            {split.map(([key, share]) => (
              <li key={key}>
                <span>{SPLIT_LABEL[key] ?? key}</span>
                <span className="household-share" aria-hidden="true">
                  <span style={{ width: `${Math.max(0, Math.min(100, Number(share) * 100))}%` }} />
                </span>
                <span>{formatRatio(share)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function Figure({
  title,
  value,
  light,
  children,
}: {
  title: string;
  value: string;
  light: Light | undefined;
  children: ReactNode;
}) {
  return (
    <div className="health-figure" role="group" aria-label={title}>
      <p className="health-figure-label">{title}</p>
      <p className="health-figure-value">
        {value} <MarkPill light={light} />
      </p>
      {children}
    </div>
  );
}

// --- Vermögen im Verlauf ------------------------------------------------------------------

function SeriesCard({ series, onChanged }: { series: NetWorthPoint[]; onChanged: () => void }) {
  if (series.length === 0) return null;
  const totals = series.map((point) => (point.total != null ? Number(point.total) : 0));
  const largest = Math.max(1, ...totals.map(Math.abs));
  const todos = series.map((point) => point.todo).filter((todo): todo is HealthTodo => todo != null);
  return (
    <section className="card stack" aria-labelledby="series-title">
      <h2 id="series-title">Vermögen im Verlauf</h2>
      <p className="muted">
        Aus den gespeicherten Monatsabschlüssen. „Gespart“ ist Einkommen minus Ausgaben des Monats; der Rest der Veränderung kommt aus
        Kursen, dem geschätzten Wert der Wohnung und Sonstigem.
      </p>
      <ul className="planning-bars" aria-hidden="true">
        {series.map((point, index) => (
          <li key={point.month_end}>
            <span
              className={index === series.length - 1 ? "planning-bar planning-bar-sale" : "planning-bar"}
              style={{ height: `${(Math.abs(totals[index] ?? 0) / largest) * 100}%` }}
            />
            <span className="planning-bar-year">{longDate(point.month_end).replace(/ \d{4}$/, "")}</span>
          </li>
        ))}
      </ul>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Monat</th>
              <th scope="col" className="num">
                Vermögen am Monatsende
              </th>
              <th scope="col" className="num">
                Veränderung
              </th>
              <th scope="col" className="num">
                davon gespart
              </th>
              <th scope="col" className="num">
                Wertänderungen und Sonstiges
              </th>
            </tr>
          </thead>
          <tbody>
            {series.map((point) => (
              <tr key={point.month_end}>
                <th scope="row">
                  <Link href={`/household?monat=${point.month_end.slice(0, 7)}`}>{formatMonth(point.month_end.slice(0, 7))}</Link>
                </th>
                <td className="num">{point.total != null ? formatEuro(point.total) : "–"}</td>
                <td className="num">{point.change != null ? formatSignedEuro(point.change) : "–"}</td>
                <td className="num">{point.saved != null ? formatEuro(point.saved) : "–"}</td>
                <td className="num">{point.other != null ? formatSignedEuro(point.other) : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <HealthTodoList todos={todos} onChanged={onChanged} />
    </section>
  );
}

