"use client";

// Planung › Haushalt (phase 9 §14, D244–D248; docs/mockups/12-household-plan.png, 12b the
// projection, 12c fewer than three closed months): what the household can invest per month,
// the reserve, the paths to the reserve target, how the number is made, and the net worth on
// the rates a member typed. Every figure is the server's (GET /household-plan) and is computed
// with the saved default scenario: a lever changed on this page is a draft until
// „Szenario speichern“ stores it as a new version of the default (D248, append-only), and the
// figures follow on the next read. No figure is computed here; no row of the paths is
// highlighted (D246); the rate fields are empty with no placeholder until a member types (D247).
import Link from "next/link";
import { type FormEvent, type ReactNode, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { formatDate, formatMonth } from "./format";
import { Field } from "./form";
import { HealthTodoItem } from "./healthFigures";
import {
  currentDefault,
  fillLevers,
  formatEuro,
  formatOneDecimal,
  formatRatio,
  formatSignedEuro,
  type Health,
  type HealthMonthView,
  type HealthTodo,
  type HouseholdLevers,
  type HouseholdPlan,
  type HouseholdScenario,
  loadMemberNames,
  loadScenarioHistory,
  longDate,
  monthEnd,
  monthName,
  monthRange,
  saveDefaultScenario,
  toSignedRate,
} from "./healthShared";
import { percentInput, toSignedMoneyString } from "./input";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

import "./health.css";

type Account = components["schemas"]["Account"];
type SavingsPlan = components["schemas"]["SavingsPlan"];
type SurplusKey = components["schemas"]["SurplusKey"];
type IncomeChangeNote = components["schemas"]["IncomeChangeNote"];
type ProjectionPoint = components["schemas"]["ProjectionPoint"];

interface PlanData {
  plan: HouseholdPlan;
  health: Health;
  scenarios: HouseholdScenario[];
  accounts: Account[];
  names: Map<string, string>;
  months: HealthMonthView[];
}

async function loadPlan(): Promise<PlanData> {
  const [plan, health, scenarios, accounts, names] = await Promise.all([
    ok(api.GET("/api/household-plan")),
    ok(api.GET("/api/health")),
    loadScenarioHistory(),
    ok(api.GET("/api/accounts")),
    loadMemberNames(),
  ]);
  const closedMonths = health.window.months ?? [];
  const first = closedMonths[0];
  const last = closedMonths[closedMonths.length - 1];
  const months = first && last ? await ok(api.GET("/api/health/months", { params: { query: { from: first, to: last } } })) : [];
  return { plan, health, scenarios, accounts, names, months };
}

/** The tab „Haushalt“ of Planung (`/planning?tab=haushalt`). */
export function HouseholdPlanScreen() {
  const state = useLoad(loadPlan);
  return (
    <>
      <div className="page-head planning-head">
        <div>
          <p className="eyebrow">Planung{state.data ? ` · Stand ${formatDate(state.data.plan.as_of)}` : ""}</p>
          <h1>Wie viel könnt ihr anlegen?</h1>
        </div>
      </div>
      <nav aria-label="Bereiche" className="tabs">
        <Link href="/planning" scroll={false}>
          Immobilie
        </Link>
        <Link href="/planning?tab=haushalt" aria-current="page" scroll={false}>
          Haushalt
        </Link>
      </nav>
      <div className="stack planning">
        <Loading state={state}>
          {(data) =>
            data.health.window.enough && data.plan.surplus ? (
              <PlanBody key={data.plan.scenario_id ?? "defaults"} data={data} onSaved={state.reload} />
            ) : (
              <TooFewMonths data={data} onChanged={state.reload} />
            )
          }
        </Loading>
      </div>
    </>
  );
}

/** The page's own to-dos point back at a section of this page; every other one is a link. */
function PlanTodos({ todos, onChanged }: { todos: HealthTodo[]; onChanged: () => void }) {
  const local = todos.map((todo) =>
    todo.href === "/planning?tab=haushalt"
      ? { ...todo, href: todo.code === "rate_missing" ? "#annahme" : "#weitere-annahmen" }
      : todo,
  );
  if (local.length === 0) return null;
  return (
    <ul className="health-todos">
      {local.map((todo, index) =>
        todo.href.startsWith("#") ? (
          <li key={`${todo.code}-${index}`} className="health-todo">
            <span>{todo.text}</span>
            <a href={todo.href} className="button-link secondary">
              {todo.action}
            </a>
          </li>
        ) : (
          <HealthTodoItem key={`${todo.code}-${index}`} todo={todo} onChanged={onChanged} />
        ),
      )}
    </ul>
  );
}

// --- 12c: fewer than three closed months ---------------------------------------------------

function TooFewMonths({ data, onChanged }: { data: PlanData; onChanged: () => void }) {
  const closed = data.health.window.months ?? [];
  const count = closed.length;
  const last = closed[count - 1];
  const next = last ? nextMonth(last) : null;
  const todos = [...(data.plan.todos ?? [])].filter((todo) => todo.code !== "too_few_months");
  return (
    <section className="card stack" aria-labelledby="too-few-title">
      <h2 id="too-few-title">Noch zu wenige abgeschlossene Monate</h2>
      <p>
        {count === 0
          ? "Bisher ist kein Monat abgeschlossen."
          : `Bisher ${count === 1 ? "ist 1 Monat" : `sind ${count} Monate`} abgeschlossen: ${closed.map(formatMonth).join(", ")}.`}{" "}
        Die Zahlen dieser Seite erscheinen ab 3 abgeschlossenen Monaten am Stück; mit weniger wäre ein Durchschnitt zu unsicher.
      </p>
      <p className="muted">{Math.min(count, 3)} von 3 Monaten</p>
      <span className="health-progress" aria-hidden="true">
        <span style={{ width: `${(Math.min(count, 3) / 3) * 100}%` }} />
      </span>
      <p className="muted">
        Ein Monat ist abgeschlossen, wenn jedes Konto bis zu seinem letzten Tag eingelesen ist, keine Buchung des Monats mehr in „Zu
        prüfen“ wartet und jede Überweisung zwischen euren Konten ihre Gegenbuchung hat.
      </p>
      {todos.length > 0 ? (
        <>
          <h3>{next ? `Das hält den ${formatMonth(next)} noch offen` : "Das hält die Monate noch offen"}</h3>
          <PlanTodos todos={todos} onChanged={onChanged} />
        </>
      ) : null}
      <p className="muted">
        Sobald 3 Monate abgeschlossen sind, stehen hier anlegbarer Betrag, Reserve, die Wege zum Reserveziel und die Rechnung zu eurem
        Vermögen.
      </p>
    </section>
  );
}

function nextMonth(month: string): string {
  const year = Number(month.slice(0, 4));
  const mon = Number(month.slice(5, 7));
  return mon === 12 ? `${year + 1}-01` : `${year}-${String(mon + 1).padStart(2, "0")}`;
}

// --- the page with its figures ----------------------------------------------------------------

function surplusRow(plan: HouseholdPlan, key: SurplusKey) {
  return plan.surplus?.rows.find((row) => row.key === key) ?? null;
}

function planName(plan: SavingsPlan | undefined, accounts: Account[]): string {
  if (!plan) return "Sparplan";
  const account = plan.destination_account_id ? accounts.find((a) => a.id === plan.destination_account_id) : undefined;
  return account?.label ?? plan.destination_payee ?? (plan.asset_class === "crypto" ? "Krypto" : "Fonds und Wertpapiere");
}

function PlanBody({ data, onSaved }: { data: PlanData; onSaved: () => void }) {
  const { plan } = data;
  const closedMonths = data.health.window.months ?? [];
  const newest = plan.newest_closed ?? closedMonths[closedMonths.length - 1] ?? "";
  const [draft, setDraft] = useState<HouseholdLevers>(() => fillLevers(plan.levers));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const saved = JSON.stringify(fillLevers(plan.levers));
  const dirty = JSON.stringify(draft) !== saved;
  const current = currentDefault(data.scenarios);
  const plans = plan.plans ?? [];
  const reserve = plan.reserve;
  const investable = surplusRow(plan, "investable");
  const free = surplusRow(plan, "free_cash");
  const todos = (plan.todos ?? []).filter((todo) => todo.code !== "rate_missing");

  async function save() {
    setBusy(true);
    setError(null);
    try {
      // The limits of the key figures stay as the default holds them (they are set on Übersicht).
      await saveDefaultScenario({ ...draft, bands: current?.levers?.bands ?? draft.bands ?? [] }, current);
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const saveBar = (
    <div className="stack">
      {error != null ? <ErrorNotice error={error} /> : null}
      <div className="row">
        <button type="button" disabled={busy || !dirty} onClick={() => void save()}>
          Szenario speichern
        </button>
        <span className="muted">Gespeichert als neue Fassung eures Szenarios; die alte bleibt im Verlauf.</span>
      </div>
    </div>
  );

  return (
    <>
      <div className="planning-kpis">
        <div className="planning-kpi planning-kpi-main">
          <p className="planning-kpi-label">Anlegbar pro Monat</p>
          <p className="planning-kpi-value">{formatEuro(investable?.amount)}</p>
          <p className="planning-kpi-sub">
            frei verfügbar {formatEuro(free?.amount)} minus {formatOneDecimal(plan.levers.buffer_percent).replace(/,0$/, "")} % Puffer
          </p>
        </div>
        <div className="planning-kpi">
          <p className="planning-kpi-label">Schon angelegt pro Monat</p>
          <p className="planning-kpi-value">{formatEuro(plan.already_invested ?? "0")}</p>
          <p className="planning-kpi-sub">
            {plans.length > 0
              ? plans.map((p) => `${planName(p, data.accounts)} ${formatEuro(p.monthly)}`).join(" · ")
              : "kein Sparplan erkannt"}
          </p>
          {plan.investable_left != null ? (
            <p className="planning-kpi-sub">danach bleiben {formatEuro(plan.investable_left)} im Monat</p>
          ) : null}
        </div>
        <div className="planning-kpi">
          <p className="planning-kpi-label">Reserve heute</p>
          <p className="planning-kpi-value">{formatEuro(reserve?.amount)}</p>
          <p className="planning-kpi-sub">
            {reserve?.months != null ? `reicht für ${formatOneDecimal(reserve.months)} Monate` : "ohne Bedarf keine Monate"}
          </p>
        </div>
        <div className="planning-kpi">
          <p className="planning-kpi-label">Bis zum Reserveziel fehlen</p>
          <p className="planning-kpi-value">{formatEuro(reserve?.gap)}</p>
          <p className="planning-kpi-sub">
            Ziel {formatEuro(reserve?.target)} = {formatOneDecimal(plan.levers.reserve_target_months).replace(/,0$/, "")} Monate Bedarf
          </p>
        </div>
      </div>
      <p className="muted">
        Basis: {closedMonths.length} Monate, {monthRange(closedMonths)}. Die Durchschnitte und der Bedarf kommen aus diesen {closedMonths.length}{" "}
        abgeschlossenen Monaten. Die Wege zum Reserveziel beginnen beim Monatsabschluss am {longDate(newest ? monthEnd(newest) : null)}.{" "}
        <Link href={`/household?monat=${newest}`}>Monate ansehen</Link>
      </p>
      <PlanTodos todos={todos} onChanged={onSaved} />

      <Levers data={data} draft={draft} setDraft={setDraft} />
      <MoreAssumptions data={data} draft={draft} setDraft={setDraft} />
      {dirty ? (
        <p className="notice notice-warn" role="status">
          Geändert, noch nicht gespeichert; die Zahlen auf dieser Seite rechnen mit der gespeicherten Fassung.
        </p>
      ) : null}
      {saveBar}

      <Paths data={data} />
      <HowItIsMade data={data} />
      <ProjectionCard data={data} draft={draft} setDraft={setDraft} dirty={dirty} saveBar={saveBar} onChanged={onSaved} />
    </>
  );
}

type SetDraft = (update: (previous: HouseholdLevers) => HouseholdLevers) => void;

// --- Stellschrauben -------------------------------------------------------------------------

/** "6" → 12 half-months and back, for the ± of the reserve target (0 to 24 in 0.5 steps). */
function halves(value: string): number {
  return Math.round(Number(value) * 2);
}

function Levers({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const reserve = data.plan.reserve;
  const regular = surplusRow(data.plan, "mean_regular_spending");
  const rental = surplusRow(data.plan, "mean_rental_cash");
  const target = halves(draft.reserve_target_months);
  const buffer = Number(draft.buffer_percent);
  const setTarget = (next: number) =>
    setDraft((previous) => ({ ...previous, reserve_target_months: String(Math.max(0, Math.min(48, next)) / 2) }));
  const setBuffer = (next: number) =>
    setDraft((previous) => ({ ...previous, buffer_percent: String(Math.max(0, Math.min(50, next))) }));
  const flatShortfall = rental && rental.amount.startsWith("-") ? rental.amount.slice(1) : null;
  return (
    <section className="card stack" aria-labelledby="levers-title">
      <h2 id="levers-title">Stellschrauben</h2>
      <div className="planning-levers">
        <div className="planning-lever">
          <p className="planning-kpi-label">Reserveziel</p>
          <div className="planning-lever-control">
            <button type="button" className="secondary" aria-label="Reserveziel verringern" disabled={target <= 0} onClick={() => setTarget(target - 1)}>
              −
            </button>
            <span className="planning-lever-value" aria-live="polite">
              {formatOneDecimal(String(target / 2)).replace(/,0$/, "")} Monate
            </span>
            <button type="button" className="secondary" aria-label="Reserveziel erhöhen" disabled={target >= 48} onClick={() => setTarget(target + 1)}>
              +
            </button>
          </div>
          <p className="planning-lever-sub">
            in Monaten eures Bedarfs von {formatEuro(reserve?.need)}: Ø regelmäßige Ausgaben {formatEuro(regular?.amount)}
            {flatShortfall ? ` plus ${formatEuro(flatShortfall)}, die die Wohnung im Monat nicht selbst trägt` : ""}
          </p>
        </div>
        <div className="planning-lever">
          <p className="planning-kpi-label">Puffer für Unvorhergesehenes</p>
          <div className="planning-lever-control">
            <button type="button" className="secondary" aria-label="Puffer verringern" disabled={buffer <= 0} onClick={() => setBuffer(buffer - 5)}>
              −
            </button>
            <span className="planning-lever-value" aria-live="polite">
              {formatOneDecimal(draft.buffer_percent).replace(/,0$/, "")} %
            </span>
            <button type="button" className="secondary" aria-label="Puffer erhöhen" disabled={buffer >= 50} onClick={() => setBuffer(buffer + 5)}>
              +
            </button>
          </div>
          <p className="planning-lever-sub">so viel vom frei verfügbaren Geld bleibt unverplant</p>
        </div>
      </div>
    </section>
  );
}

// --- Weitere Annahmen -----------------------------------------------------------------------

const INCOME_NOTE: Record<IncomeChangeNote, string> = {
  parental_leave: "Elternzeit",
  job_change: "Jobwechsel",
  part_time: "Teilzeit",
  other: "Sonstiges",
};

function MoreAssumptions({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const [open, setOpen] = useState(true);
  return (
    <section className="card stack" id="weitere-annahmen" aria-labelledby="more-title">
      <div className="planning-card-head">
        <h2 id="more-title">Weitere Annahmen</h2>
        <span className="spacer" />
        <button type="button" className="link" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          {open ? "zuklappen ▴" : "aufklappen ▾"}
        </button>
      </div>
      {open ? (
        <>
          <IncomeChanges draft={draft} setDraft={setDraft} />
          <OneOffs data={data} draft={draft} setDraft={setDraft} />
          <ReserveAccountsBlock data={data} draft={draft} setDraft={setDraft} />
          <SavingsPlans data={data} draft={draft} setDraft={setDraft} />
        </>
      ) : null}
    </section>
  );
}

function IncomeChanges({ draft, setDraft }: { draft: HouseholdLevers; setDraft: SetDraft }) {
  const ids = useId();
  const [adding, setAdding] = useState(false);
  const [amount, setAmount] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [note, setNote] = useState<IncomeChangeNote>("other");
  const [errors, setErrors] = useState<Partial<Record<"amount" | "from" | "to", string>>>({});
  const changes = draft.income_changes ?? [];

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = toSignedMoneyString(amount);
    const found: typeof errors = {};
    if (!parsed.ok) found.amount = parsed.message;
    if (!/^\d{4}-\d{2}$/.test(from)) found.from = "Bitte einen Monat wählen.";
    if (to !== "" && (!/^\d{4}-\d{2}$/.test(to) || to < from)) found.to = "Das Ende liegt am oder nach dem Beginn.";
    setErrors(found);
    if (!parsed.ok || Object.keys(found).length > 0) return;
    setDraft((previous) => ({
      ...previous,
      income_changes: [...(previous.income_changes ?? []), { monthly_net: parsed.value, from_month: from, to_month: to || null, note_code: note }],
    }));
    setAdding(false);
    setAmount("");
    setFrom("");
    setTo("");
  }

  return (
    <div className="stack">
      <h3>Einkommen ändert sich</h3>
      {changes.length === 0 ? (
        <p className="muted">Keine Änderung eingetragen. Für eine Zeit mit mehr oder weniger Netto im Monat, mit Beginn und Ende.</p>
      ) : (
        <ul className="household-rows">
          {changes.map((change, index) => (
            <li key={`${change.from_month}-${index}`}>
              <span>
                {INCOME_NOTE[change.note_code]}: ab {formatMonth(change.from_month)}
                {change.to_month ? ` bis ${formatMonth(change.to_month)}` : ", ohne Ende"}
              </span>
              <span>
                {formatSignedEuro(change.monthly_net)} im Monat{" "}
                <button
                  type="button"
                  className="link"
                  onClick={() =>
                    setDraft((previous) => ({ ...previous, income_changes: (previous.income_changes ?? []).filter((_, i) => i !== index) }))
                  }
                >
                  Entfernen
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <form className="stack" onSubmit={add} aria-label="Änderung des Einkommens">
          <div className="health-limit-fields">
            <Field id={`${ids}-amount`} label="Netto im Monat mehr (+) oder weniger (−)" hint="z. B. −600,00" errors={errors.amount ? [errors.amount] : []}>
              <input type="text" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} />
            </Field>
            <Field id={`${ids}-from`} label="ab Monat" errors={errors.from ? [errors.from] : []}>
              <input type="month" value={from} onChange={(event) => setFrom(event.target.value)} />
            </Field>
            <Field id={`${ids}-to`} label="bis Monat (leer: ohne Ende)" errors={errors.to ? [errors.to] : []}>
              <input type="month" value={to} onChange={(event) => setTo(event.target.value)} />
            </Field>
            <Field id={`${ids}-note`} label="Anlass">
              <select value={note} onChange={(event) => setNote(event.target.value as IncomeChangeNote)}>
                {(Object.keys(INCOME_NOTE) as IncomeChangeNote[]).map((code) => (
                  <option key={code} value={code}>
                    {INCOME_NOTE[code]}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="row">
            <button type="submit">Hinzufügen</button>
            <button type="button" className="secondary" onClick={() => setAdding(false)}>
              Abbrechen
            </button>
          </div>
        </form>
      ) : (
        <p>
          <button type="button" className="secondary" onClick={() => setAdding(true)}>
            Änderung eintragen
          </button>
        </p>
      )}
    </div>
  );
}

// An accepted refund names its tax year in its source, "return_estimate:2026" (PR #434 review D);
// a bare "return_estimate" was saved before the year was written and names none.
const RETURN_ESTIMATE = "return_estimate";

function isReturnEstimate(source: string | undefined): boolean {
  return source === RETURN_ESTIMATE || (source ?? "").startsWith(`${RETURN_ESTIMATE}:`);
}

function estimateYear(source: string | undefined): number | null {
  return (source ?? "").startsWith(`${RETURN_ESTIMATE}:`) ? Number(source!.slice(RETURN_ESTIMATE.length + 1)) : null;
}

function OneOffs({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const ids = useId();
  const months = (data.plan.months ?? []).map((m) => m.month);
  const proposals = data.plan.proposed_one_offs ?? [];
  const oneOffs = draft.one_offs ?? [];
  // a proposal is hidden once the draft holds a one-off of its tax year, or a bare one (no year)
  const bareTaken = oneOffs.some((o) => o.source === RETURN_ESTIMATE);
  const takenYears = new Set(oneOffs.map((o) => estimateYear(o.source)).filter((year) => year !== null));
  const newestClosed = data.plan.newest_closed;
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [adding, setAdding] = useState(false);
  const [month, setMonth] = useState("");
  const [amount, setAmount] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"month" | "amount", string>>>({});

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = toSignedMoneyString(amount);
    const found: typeof errors = {};
    if (!parsed.ok) found.amount = parsed.message;
    if (month === "") found.month = "Bitte einen Monat wählen.";
    setErrors(found);
    if (!parsed.ok || month === "") return;
    setDraft((previous) => ({ ...previous, one_offs: [...(previous.one_offs ?? []), { month, amount: parsed.value, source: "typed" }] }));
    setAdding(false);
    setMonth("");
    setAmount("");
  }

  const monthSelect = (id: string, value: string, onChange: (value: string) => void) => (
    <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">Monat wählen …</option>
      {months.map((m) => (
        <option key={m} value={m}>
          {formatMonth(m)}
        </option>
      ))}
    </select>
  );

  return (
    <div className="stack">
      <h3>Einmalige Beträge</h3>
      {!bareTaken
        ? proposals.map((proposal, index) => {
            if (takenYears.has(proposal.tax_year)) return null;
            const id = `${ids}-proposal-${index}`;
            const picked = chosen[index] ?? "";
            return (
              <div key={proposal.tax_year} className="health-plan-row">
                <p>
                  Einkommensteuer {proposal.tax_year}: erwartete {proposal.amount.startsWith("-") ? "Nachzahlung" : "Erstattung"}{" "}
                  <span className="badge badge-info">Vorschlag</span> <strong>{formatSignedEuro(proposal.amount)}</strong>{" "}
                  <span className="muted">
                    aus der Schätzung unter <Link href={`/tax/${proposal.tax_year}`}>Steuern › Gesamt</Link>
                  </span>
                </p>
                <span className="row">
                  <label htmlFor={id}>Monat</label>
                  {monthSelect(id, picked, (value) => setChosen((previous) => ({ ...previous, [index]: value })))}
                  <button
                    type="button"
                    className="secondary"
                    disabled={picked === ""}
                    onClick={() =>
                      setDraft((previous) => ({
                        ...previous,
                        one_offs: [
                          ...(previous.one_offs ?? []),
                          { month: picked, amount: proposal.amount, source: `${RETURN_ESTIMATE}:${proposal.tax_year}` },
                        ],
                      }))
                    }
                  >
                    Übernehmen
                  </button>
                </span>
                <p className="muted">
                  Wählt den Monat, in dem ihr das Geld auf dem Konto erwartet. Bis ihr den Vorschlag übernehmt, rechnet die App ohne diesen
                  Betrag.
                </p>
              </div>
            );
          })
        : null}
      {oneOffs.length > 0 ? (
        <ul className="household-rows">
          {oneOffs.map((oneOff, index) => (
            <li key={`${oneOff.month}-${index}`}>
              <span>
                {formatMonth(oneOff.month)}
                {isReturnEstimate(oneOff.source)
                  ? ` · Einkommensteuer${estimateYear(oneOff.source) !== null ? ` ${estimateYear(oneOff.source)}` : ""}, aus der Schätzung`
                  : ""}
                {newestClosed && oneOff.month <= newestClosed ? " · Monat abgeschlossen, zählt schon in den gemessenen Monaten" : ""}
              </span>
              <span>
                {formatSignedEuro(oneOff.amount)}{" "}
                <button
                  type="button"
                  className="link"
                  onClick={() => setDraft((previous) => ({ ...previous, one_offs: (previous.one_offs ?? []).filter((_, i) => i !== index) }))}
                >
                  Entfernen
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : proposals.length === 0 ? (
        <p className="muted">Kein einmaliger Betrag eingetragen.</p>
      ) : null}
      {adding ? (
        <form className="stack" onSubmit={add} aria-label="Einmaliger Betrag">
          <div className="health-limit-fields">
            <Field id={`${ids}-month`} label="Monat" errors={errors.month ? [errors.month] : []}>
              {monthSelect(`${ids}-month`, month, setMonth)}
            </Field>
            <Field id={`${ids}-amount`} label="Betrag (+ Einnahme, − Ausgabe)" hint="z. B. −2.400,00" errors={errors.amount ? [errors.amount] : []}>
              <input type="text" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} />
            </Field>
          </div>
          <div className="row">
            <button type="submit">Hinzufügen</button>
            <button type="button" className="secondary" onClick={() => setAdding(false)}>
              Abbrechen
            </button>
          </div>
        </form>
      ) : (
        <p>
          <button type="button" className="secondary" onClick={() => setAdding(true)}>
            Betrag eintragen
          </button>
        </p>
      )}
    </div>
  );
}

const RESERVE_KINDS = new Set<Account["kind"]>(["bank_current", "bank_savings", "credit_card"]);

function ReserveAccountsBlock({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const ids = useId();
  const rows = data.plan.reserve?.accounts ?? [];
  // The default set: the accounts the server lists as default or removed (D237).
  const byDefault = new Set(rows.filter((row) => row.origin !== "added").map((row) => row.account_id));
  const added = new Set(draft.reserve_accounts?.added ?? []);
  const removed = new Set(draft.reserve_accounts?.removed ?? []);
  const accounts = data.accounts.filter((account) => RESERVE_KINDS.has(account.kind));

  function toggle(accountId: string, on: boolean) {
    setDraft((previous) => {
      const nextAdded = new Set(previous.reserve_accounts?.added ?? []);
      const nextRemoved = new Set(previous.reserve_accounts?.removed ?? []);
      if (byDefault.has(accountId)) {
        if (on) nextRemoved.delete(accountId);
        else nextRemoved.add(accountId);
      } else if (on) nextAdded.add(accountId);
      else nextAdded.delete(accountId);
      return { ...previous, reserve_accounts: { added: [...nextAdded], removed: [...nextRemoved] } };
    });
  }

  return (
    <div className="stack">
      <h3>Konten der Reserve</h3>
      <ul className="household-rows">
        {accounts.map((account) => {
          const counted = byDefault.has(account.id) ? !removed.has(account.id) : added.has(account.id);
          const row = rows.find((r) => r.account_id === account.id);
          const id = `${ids}-${account.id}`;
          return (
            <li key={account.id}>
              <span>
                <input id={id} type="checkbox" checked={counted} onChange={(event) => toggle(account.id, event.target.checked)} />{" "}
                <label htmlFor={id}>{account.label}</label>{" "}
                <span className="muted">
                  {account.purpose === "rental" && !counted
                    ? "zählt nicht: das Geld der Wohnung für ihre nächsten Raten"
                    : account.kind === "credit_card"
                      ? "Schulden auf der Karte mindern die Reserve"
                      : counted
                        ? "zählt zur Reserve"
                        : "zählt nicht zur Reserve"}
                </span>
              </span>
              <span>{row ? formatEuro(row.balance) : ""}</span>
            </li>
          );
        })}
      </ul>
      <p className="muted">Reserve laut gespeicherter Fassung: {formatEuro(data.plan.reserve?.amount)}</p>
    </div>
  );
}

function SavingsPlans({ data, draft, setDraft }: { data: PlanData; draft: HouseholdLevers; setDraft: SetDraft }) {
  const ids = useId();
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const overrides = draft.plan_overrides ?? [];
  const plans = data.plan.plans ?? [];
  const known = new Set(plans.map((p) => p.key));
  // A plan the saved scenario leaves out is not in the plan any more; it is listed from its override.
  const dropped = overrides.filter((o) => o.dropped && !known.has(o.plan_key));

  function setOverride(planKey: string, next: { monthly?: string | null; dropped: boolean } | null) {
    setDraft((previous) => {
      const rest = (previous.plan_overrides ?? []).filter((o) => o.plan_key !== planKey);
      return { ...previous, plan_overrides: next ? [...rest, { plan_key: planKey, ...next }] : rest };
    });
  }

  return (
    <div className="stack">
      <h3>Sparpläne</h3>
      {plans.length === 0 && dropped.length === 0 ? (
        <p className="muted">Kein Sparplan erkannt: kein Ziel bekam in jedem der letzten drei Monate Geld von euch.</p>
      ) : null}
      {plans.map((p) => {
        const override = overrides.find((o) => o.plan_key === p.key);
        const id = `${ids}-${p.key}`;
        return (
          <div key={p.key} className="health-plan-row">
            <p>
              <strong>{planName(p, data.accounts)}</strong>{" "}
              <span className="muted">
                erkannt aus euren Überweisungen: {joinMonths(p.months)}
                {p.overridden ? " · Betrag von euch geändert" : ""}
              </span>
            </p>
            <span className="row">
              <span>
                {override?.dropped ? "nicht mitgerechnet" : `${formatEuro(override?.monthly ?? p.monthly)} im Monat`}
              </span>
              {editing === p.key ? (
                <span className="row">
                  <label htmlFor={id}>Betrag im Monat</label>
                  <input id={id} type="text" inputMode="decimal" value={value} onChange={(event) => setValue(event.target.value)} />
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => {
                      const parsed = toSignedMoneyString(value);
                      if (!parsed.ok || parsed.value.startsWith("-")) {
                        setMessage(parsed.ok ? "Bitte einen Betrag ab 0 eingeben." : parsed.message);
                        return;
                      }
                      setOverride(p.key, { monthly: parsed.value, dropped: false });
                      setEditing(null);
                      setMessage(null);
                    }}
                  >
                    Übernehmen
                  </button>
                </span>
              ) : (
                <>
                  <button
                    type="button"
                    className="link"
                    onClick={() => {
                      setEditing(p.key);
                      setValue((override?.monthly ?? p.monthly).replace(".", ","));
                    }}
                  >
                    Betrag ändern
                  </button>
                  {override?.dropped ? (
                    <button type="button" className="link" onClick={() => setOverride(p.key, null)}>
                      Wieder mitrechnen
                    </button>
                  ) : (
                    <button type="button" className="link" onClick={() => setOverride(p.key, { dropped: true })}>
                      Nicht mitrechnen
                    </button>
                  )}
                </>
              )}
            </span>
            {editing === p.key && message ? <p className="field-errors">{message}</p> : null}
          </div>
        );
      })}
      {dropped.map((o) => (
        <div key={o.plan_key} className="health-plan-row">
          <p className="muted">Ein Sparplan wird nicht mitgerechnet.</p>
          <button type="button" className="link" onClick={() => setOverride(o.plan_key, null)}>
            Wieder mitrechnen
          </button>
        </div>
      ))}
    </div>
  );
}

function joinMonths(months: readonly string[]): string {
  const sorted = [...months].sort();
  const year = sorted[sorted.length - 1]?.slice(0, 4) ?? "";
  const sameYear = sorted.every((m) => m.startsWith(year));
  const names = sorted.map((m) => (sameYear ? monthName(m) : formatMonth(m)));
  const text = names.length > 1 ? `${names.slice(0, -1).join(", ")} und ${names[names.length - 1]}` : (names[0] ?? "");
  return sameYear && year ? `${text} ${year}` : text;
}

// --- Wege zum Reserveziel --------------------------------------------------------------------

function Paths({ data }: { data: PlanData }) {
  const { plan } = data;
  const paths = plan.paths ?? [];
  const plans = plan.plans ?? [];
  const later = plan.flat_change;
  const target = plan.reserve?.target;
  const others = plan.paths_others ?? [];
  const name = (key: string) => planName(plans.find((p) => p.key === key), data.accounts);
  if (paths.length === 0) return null;

  function label(key: string, paused: string[]): { title: string; sub: string } {
    const running = plans.filter((p) => !paused.includes(p.key));
    const runningText =
      running.length === 0
        ? ""
        : `${running.map((p) => `${planName(p, data.accounts)} ${formatEuro(p.monthly)}`).join(", ")} ${running.length === 1 ? "läuft" : "laufen"} weiter`;
    if (key === "reserve_first") {
      return { title: "Reserve zuerst", sub: plans.length === 1 ? "Sparplan pausiert" : plans.length === 2 ? "beide Sparpläne pausiert" : "alle Sparpläne pausiert" };
    }
    if (key === "as_is") return { title: "Alles weiter wie bisher", sub: "jeder Sparplan läuft weiter" };
    return { title: `Sparplan ${name(key.slice("pause:".length))} pausiert`, sub: runningText };
  }

  return (
    <section className="card stack" aria-labelledby="paths-title">
      <h2 id="paths-title">Wege zum Reserveziel</h2>
      <p className="muted">
        Jeder Weg mit denselben Zahlen, nach dem Monat sortiert, in dem die Reserve {formatEuro(target)} erreicht. Die App rechnet; welcher
        Weg passt, entscheidet ihr. Start: {formatEuro(plan.reserve?.amount)} am {longDate(plan.newest_closed ? monthEnd(plan.newest_closed) : null)}.
      </p>
      <div className="table-wrap">
        <table className="planning-table">
          <thead>
            <tr>
              <th scope="col">Weg</th>
              <th scope="col" className="num">
                Zur Reserve
              </th>
              <th scope="col">Ziel erreicht</th>
            </tr>
          </thead>
          <tbody>
            {paths.map((path) => {
              const { title, sub } = label(path.key, path.paused ?? []);
              const after = later?.paths.find((p) => p.key === path.key);
              return (
                <tr key={path.key}>
                  <th scope="row">
                    {title}
                    {sub ? <span className="muted"> · {sub}</span> : null}
                  </th>
                  <td className="num">
                    {formatEuro(path.to_reserve_first_month)} im Monat
                    {after && later ? (
                      <span className="muted">
                        <br />
                        ab {formatMonth(later.month)}: {formatEuro(after.to_reserve)} im Monat
                      </span>
                    ) : null}
                  </td>
                  <td>
                    {path.reached ? (
                      <>
                        {formatMonth(path.reached)}
                        {path.months_needed != null ? <span className="muted"> · in {path.months_needed} Monaten</span> : null}
                      </>
                    ) : path.used_up ? (
                      <>nicht erreicht; die Reserve ist im {formatMonth(path.used_up)} aufgebraucht</>
                    ) : (
                      <>nicht in {Math.round(plan.levers.horizon_months / 12)} Jahren; Reserve dann {formatEuro(path.reserve_at_horizon)}</>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {others.length > 0 ? <p className="muted">Weitere Sparpläne ohne eigene Zeile: {others.map(name).join(", ")}.</p> : null}
      {plan.paths_on_rental_account ? (
        <p className="muted">
          Davon kommen {formatEuro(plan.paths_on_rental_account)} im Monat zuerst auf dem Mietkonto an, das nicht zur Reserve zählt.
        </p>
      ) : null}
      {later ? (
        <p className="muted">
          Ab {formatMonth(later.month)} rechnet die App für die Wohnung mit ihrem Szenario: {formatSignedEuro(later.flat)} im Monat statt
          {later.before_is_mean ? " im Schnitt " : " "}
          {formatSignedEuro(later.before)}. Jeder Weg nimmt an, dass Geld, das in keinen Sparplan geht, auf einem Konto der Reserve landet.
        </p>
      ) : (
        <p className="muted">Jeder Weg nimmt an, dass Geld, das in keinen Sparplan geht, auf einem Konto der Reserve landet.</p>
      )}
    </section>
  );
}

// --- So entsteht die Zahl -----------------------------------------------------------------

const SURPLUS_LABEL: Record<SurplusKey, string> = {
  mean_income: "Ø Einnahmen der privaten Konten",
  mean_regular_spending: "Ø regelmäßige Ausgaben",
  mean_rental_cash: "Ø Wohnung nach Zins und Tilgung",
  free_cash: "Frei verfügbar pro Monat",
  buffer: "Puffer",
  investable: "Anlegbar pro Monat",
  median_free_cash: "Mittlerer Monat (Median), frei verfügbar",
  lowest_free_cash: "Schwächster Monat, frei verfügbar",
  one_off_spending_left_out: "Nicht im Schnitt: Einmalausgaben",
};

/** The household month's field each row is the mean (or median, lowest, sum) of. */
const SURPLUS_FIELD: Partial<Record<SurplusKey, keyof components["schemas"]["HouseholdMonth"]>> = {
  mean_income: "private_income",
  mean_regular_spending: "regular_spending",
  mean_rental_cash: "rental_cash",
  free_cash: "free_cash",
  median_free_cash: "free_cash",
  lowest_free_cash: "free_cash",
  one_off_spending_left_out: "one_off_spending",
};

function HowItIsMade({ data }: { data: PlanData }) {
  const { plan } = data;
  const rows = plan.surplus?.rows ?? [];
  const closedMonths = data.health.window.months ?? [];
  const rental = data.accounts.find((account) => account.purpose === "rental");
  const first = closedMonths[0];
  const last = closedMonths[closedMonths.length - 1];
  return (
    <section className="card stack" aria-labelledby="made-title">
      <h2 id="made-title">So entsteht die Zahl</h2>
      <p className="muted">
        Jede Zeile öffnet ihre Monate und Buchungen. Basis: {closedMonths.length} abgeschlossene Monate, {monthRange(closedMonths)}.
      </p>
      <div className="stack">
        {rows.map((row) => {
          const field = SURPLUS_FIELD[row.key];
          const title =
            row.key === "buffer"
              ? `Puffer ${formatOneDecimal(plan.levers.buffer_percent).replace(/,0$/, "")} %`
              : row.key === "lowest_free_cash" && row.month
                ? `${SURPLUS_LABEL[row.key]}: ${formatMonth(row.month)}`
                : SURPLUS_LABEL[row.key];
          const amount = row.key === "buffer" ? formatSignedEuro(`-${row.amount.replace(/^-/, "")}`) : formatSignedEuro(row.amount);
          if (!field) {
            return (
              <div key={row.key} className="health-plan-row">
                <p>
                  <strong>{title}</strong>
                </p>
                <span>{amount}</span>
              </div>
            );
          }
          return (
            <details key={row.key} className="health-sources">
              <summary>
                <span className="health-plan-row">
                  <span>{title}</span>
                  <span>{amount}</span>
                </span>
              </summary>
              <ul className="household-rows">
                {data.months.map((view) => (
                  <li key={view.month}>
                    <Link href={row.key === "mean_rental_cash" && rental ? rentalHref(rental.id, view.month) : `/household?monat=${view.month}`}>
                      {formatMonth(view.month)}
                    </Link>
                    <span>{formatSignedEuro(view.household[field] as string)}</span>
                  </li>
                ))}
              </ul>
              {row.key === "mean_rental_cash" && rental && first && last ? (
                <p>
                  <Link href={`/accounts/${rental.id}?tab=umsaetze&von=${first}-01&bis=${monthEnd(last)}`}>
                    Alle Buchungen des Mietkontos dieser Monate
                  </Link>
                </p>
              ) : (
                <p className="muted">Jeder Monat öffnet die Buchungen der privaten Konten unter Haushalt.</p>
              )}
            </details>
          );
        })}
      </div>
    </section>
  );
}

function rentalHref(accountId: string, month: string): string {
  return `/accounts/${accountId}?tab=umsaetze&von=${month}-01&bis=${monthEnd(month)}`;
}

// --- Vermögen in einigen Jahren -------------------------------------------------------------

const CLASS_LABEL = { depot: "Fonds und Wertpapiere", crypto: "Krypto" } as const;

function ProjectionCard({
  data,
  draft,
  setDraft,
  dirty,
  saveBar,
  onChanged,
}: {
  data: PlanData;
  draft: HouseholdLevers;
  setDraft: SetDraft;
  dirty: boolean;
  saveBar: ReactNode;
  onChanged: () => void;
}) {
  const ids = useId();
  const projection = data.plan.projection;
  const points = projection?.points ?? [];
  const assumptions = projection?.assumptions ?? [];
  const flat = assumptions.find((a) => a.kind === "flat_growth");
  const rateTodos = (data.plan.todos ?? []).filter((todo) => todo.code === "rate_missing");
  const projectionTodos = projection?.todos ?? [];
  const [texts, setTexts] = useState({
    depot: percentText(draft.value_change?.depot),
    crypto: percentText(draft.value_change?.crypto),
    years: String(draft.net_worth_years),
  });
  const [errors, setErrors] = useState<Partial<Record<"depot" | "crypto" | "years", string>>>({});

  function apply(key: "depot" | "crypto" | "years", raw: string) {
    setTexts((previous) => ({ ...previous, [key]: raw }));
    if (key === "years") {
      const years = Number(raw);
      if (!/^\d{1,2}$/.test(raw.trim()) || years < 1 || years > 30) {
        setErrors((previous) => ({ ...previous, years: "Zwischen 1 und 30 Jahren." }));
        return;
      }
      setErrors((previous) => ({ ...previous, years: undefined }));
      setDraft((previous) => ({ ...previous, net_worth_years: years }));
      return;
    }
    if (raw.trim() === "") {
      setErrors((previous) => ({ ...previous, [key]: undefined }));
      setDraft((previous) => ({ ...previous, value_change: { ...previous.value_change, [key]: null } }));
      return;
    }
    const parsed = toSignedRate(raw, 3);
    if (!parsed.ok) {
      setErrors((previous) => ({ ...previous, [key]: parsed.message }));
      return;
    }
    const value = parsed.value;
    const tooLarge = Math.abs(Number(value)) > 0.5;
    setErrors((previous) => ({ ...previous, [key]: tooLarge ? "Zwischen −50 % und 50 % pro Jahr." : undefined }));
    if (!tooLarge) setDraft((previous) => ({ ...previous, value_change: { ...previous.value_change, [key]: value } }));
  }

  const last = points[points.length - 1];
  const rateLine = (kind: "depot_rate" | "crypto_rate", label: string) => {
    const assumption = assumptions.find((a) => a.kind === kind);
    if (!assumption) return null;
    const by = assumption.created_by ? data.names.get(assumption.created_by) : undefined;
    return (
      <li key={kind}>
        {label}: {formatRatio(assumption.value)} pro Jahr, eingetragen{by ? ` von ${by}` : ""} am {longDate(assumption.created_at)}.
      </li>
    );
  };

  return (
    <section className="card stack" id="annahme" aria-labelledby="projection-title">
      <h2 id="projection-title">Vermögen in einigen Jahren: eure Annahme</h2>
      <p className="muted">
        Eine Rechnung auf eure eigenen Annahmen, keine Vorhersage. Die App schlägt keine Wertänderung vor; ihr tragt sie selbst ein, je eine
        für Fonds und Wertpapiere und eine für Krypto.
      </p>
      <fieldset className="health-limit">
        <legend>Angenommene Wertänderung pro Jahr</legend>
        <div className="health-limit-fields">
          {(["depot", "crypto"] as const).map((key) => (
            <Field key={key} id={`${ids}-${key}`} label={`${CLASS_LABEL[key]} (% pro Jahr)`} errors={errors[key] ? [errors[key] as string] : []}>
              <input
                type="text"
                inputMode="decimal"
                className="health-limit-input"
                value={texts[key]}
                onChange={(event) => apply(key, event.target.value)}
              />
            </Field>
          ))}
          <Field id={`${ids}-years`} label="Jahre" errors={errors.years ? [errors.years] : []}>
            <input
              type="text"
              inputMode="numeric"
              className="health-limit-input"
              value={texts.years}
              onChange={(event) => apply("years", event.target.value)}
            />
          </Field>
        </div>
        <p className="muted">
          Wohnung{flat ? ` ${formatRatio(flat.value)} pro Jahr, aus ihrem Szenario` : ": Wertänderung aus ihrem Szenario"}{" "}
          <Link href={flat?.property_id ? `/planning?immobilie=${flat.property_id}` : "/planning"}>im Szenario der Wohnung ändern</Link>
        </p>
      </fieldset>
      {dirty ? saveBar : null}
      {points.length === 0 ? (
        <>
          {rateTodos.length > 0 ? <p className="health-reference-title">Annahme fehlt</p> : null}
          <PlanTodos todos={[...rateTodos, ...projectionTodos.filter((t) => t.code !== "rate_missing")]} onChanged={onChanged} />
        </>
      ) : (
        <>
          <ul className="muted">
            {rateLine("depot_rate", "Fonds und Wertpapiere")}
            {rateLine("crypto_rate", "Krypto")}
            <li>
              Wohnung: {flat ? `${formatRatio(flat.value)} pro Jahr aus ihrem Szenario` : "aus ihrem Szenario"}; Wert und Darlehen kommen aus
              der Projektion der Wohnung.
            </li>
            <li>
              Einnahmen, Ausgaben und Sparpläne bleiben beim Schnitt von {monthRange(data.health.window.months ?? [])}, mit den Änderungen aus
              eurem Szenario. Das Geld, das in keinen Sparplan geht, bleibt auf den Konten.
            </li>
            <li>Nominale Euro: vor Steuern auf Gewinne, vor Kosten, ohne Inflation.</li>
          </ul>
          {last ? (
            <p className="cashflow-value">
              Vermögen am {longDate(last.on)} nach eurer Annahme: {formatEuro(last.total)}
            </p>
          ) : null}
          {projection?.start ? (
            <p className="muted">
              Start ({longDate(projection.start.on)}): {formatEuro(projection.start.total)}
            </p>
          ) : null}
          <ProjectionTable start={projection?.start ?? null} points={points} />
          {projection?.ends_before_sale_year != null ? (
            <p className="muted">
              Die Rechnung endet {projection.ends_before_sale_year - 1}, weil das Szenario der Wohnung danach den Verkauf vorsieht.{" "}
              <Link href="/planning">Planung › Immobilie</Link>
            </p>
          ) : null}
          {projection?.used_up ? (
            <p className="muted">Das Geld auf den Konten ist im {formatMonth(projection.used_up)} aufgebraucht; danach bleibt die Rechnung bei 0 €.</p>
          ) : null}
          {last?.change ? <ChangeRows point={last} /> : null}
          <PlanTodos todos={projectionTodos} onChanged={onChanged} />
        </>
      )}
    </section>
  );
}

function percentText(value: string | null | undefined): string {
  if (value == null) return "";
  const negative = value.startsWith("-");
  return `${negative ? "-" : ""}${percentInput(negative ? value.slice(1) : value)}`;
}

function ProjectionTable({ start, points }: { start: ProjectionPoint | null; points: ProjectionPoint[] }) {
  const rows = start ? [start, ...points] : points;
  return (
    <div className="table-wrap">
      <table className="planning-table">
        <thead>
          <tr>
            <th scope="col">Stichtag</th>
            <th scope="col" className="num">
              Bankguthaben
            </th>
            <th scope="col" className="num">
              Fonds und Wertpapiere
            </th>
            <th scope="col" className="num">
              Krypto
            </th>
            <th scope="col" className="num">
              Wohnung
            </th>
            <th scope="col" className="num">
              Darlehen
            </th>
            <th scope="col" className="num">
              Kreditkarte
            </th>
            <th scope="col" className="num">
              Vermögen
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((point) => (
            <tr key={point.on}>
              <th scope="row">{formatDate(point.on)}</th>
              <td className="num">{formatEuro(point.bank)}</td>
              <td className="num">{formatEuro(point.depot)}</td>
              <td className="num">{formatEuro(point.crypto)}</td>
              <td className="num">{formatEuro(point.property)}</td>
              <td className="num">{formatEuro(negated(point.loans))}</td>
              <td className="num">{formatEuro(negated(point.other_liabilities))}</td>
              <td className="num">
                <strong>{formatEuro(point.total)}</strong>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A debt shown as a negative amount, whichever sign the server keeps it in. */
function negated(value: string): string {
  if (value.startsWith("-")) return value;
  return /^0+(\.0+)?$/.test(value) ? value : `-${value}`;
}

function ChangeRows({ point }: { point: ProjectionPoint }) {
  const change = point.change;
  if (!change) return null;
  return (
    <div className="stack">
      <h3>Woraus die Veränderung von {formatSignedEuro(change.total)} besteht</h3>
      <ul className="household-rows">
        <li>
          <span>Auf den Konten</span>
          <span>{formatSignedEuro(change.on_accounts)}</span>
        </li>
        <li>
          <span>In die Sparpläne eingezahlt</span>
          <span>{formatSignedEuro(change.paid_in)}</span>
        </li>
        <li>
          <span>Aus euren Annahmen</span>
          <span>{formatSignedEuro(change.from_assumptions)}</span>
        </li>
        <li>
          <span>Wert der Wohnung laut ihrem Szenario</span>
          <span>{formatSignedEuro(change.flat_value)}</span>
        </li>
        <li>
          <span>Darlehen getilgt</span>
          <span>{formatSignedEuro(change.loan_repaid)}</span>
        </li>
      </ul>
    </div>
  );
}
