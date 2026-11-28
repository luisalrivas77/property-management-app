"use client";

// The fixed payments inside Notwendiges and Wünsche (phase 10 §7.10 (c), D300; mockup 19d frame
// 6, 19h frame 2), shown in the Budget's class panel. The card „Regelmäßige Zahlungen“ on
// Haushalt › Monat (§7.9 (b)) went with D300: the recurring payments are the Budget's expected
// payments now. Every amount is the server's (GET /household-budget's `fixed`, `fixed_monthly`
// and `variable`); the browser formats it and adds nothing up (D269).
//
// - A fixed row is keyed by its series' oldest payment (`BudgetFixedPayment.key` ==
//   `RecurringSeries.sources[0]`); GET /household/recurring gives the series' own key and
//   interval, which POST /household/recurring-marks takes.
// - A row that may have ended asks „Vielleicht beendet?“: „Ja, beendet“ posts `hidden` (it then
//   counts in the variable part), „Läuft weiter“ only closes the question. „Ausgeblendete
//   anzeigen“ lists this class's hidden series with „Wieder anzeigen“ (`shown`). Neither answer
//   changes the class's figure (D296 (4)).
// - Every answer shows at once (optimistic, Luis 24 Nov 2026): the row takes its new state before
//   the POST, a double tap is ignored, and a failure puts it back and shows the error.
import { useRef, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT, type Formatter } from "@/i18n";
import { monthBudget } from "@/i18n/monthBudget";
import { recurring as catalogue } from "@/i18n/recurring";
import { ErrorNotice } from "@/lib/ErrorNotice";

import "./householdRecurring.css";

type RecurringPayments = components["schemas"]["RecurringPayments"];
type RecurringSeries = components["schemas"]["RecurringSeries"];
type BudgetLine = components["schemas"]["BudgetLine"];
type BudgetFixedPayment = components["schemas"]["BudgetFixedPayment"];
type Messages = (typeof catalogue)["de"];

function isNegative(value: string): boolean {
  return value.trim().startsWith("-");
}

function unsigned(value: string): string {
  return value.trim().replace(/^[-+]/, "");
}

/** A series' id: the id of its oldest payment, as the Budget's fixed row carries it. */
function seriesId(series: RecurringSeries): string {
  return series.sources?.[0] ?? series.lines?.[0]?.allocation_id ?? `${series.interval}-${series.label}`;
}

function intervalLabel(t: Messages, interval: RecurringSeries["interval"]): string {
  return interval === "monthly" ? t.monthly : interval === "quarterly" ? t.quarterly : t.yearly;
}

/** „+5,00 € seit Juli 2026“ / „−6,00 € seit Juli 2026“: the server's change, its sign as words. */
function changeShort(t: Messages, f: Formatter, change: string | null | undefined, since: string | null | undefined): string | null {
  if (change == null || since == null) return null;
  const amount = f.money(unsigned(change));
  return isNegative(change) ? t.changeDown(amount, f.month(since)) : t.changeUp(amount, f.month(since));
}

async function loadRecurring(): Promise<RecurringPayments> {
  return ok(api.GET("/api/household/recurring"));
}

async function postSeriesMark(series: RecurringSeries, answer: "hidden" | "shown"): Promise<void> {
  await ok(api.POST("/api/household/recurring-marks", { body: { key: series.key, interval: series.interval, answer } }));
}

function withId(ids: ReadonlySet<string>, id: string, on: boolean): ReadonlySet<string> {
  const next = new Set(ids);
  if (on) next.add(id);
  else next.delete(id);
  return next;
}

/** „Feste Zahlungen (erwartet)“, „Schwankend“ and the sum line for one class (need or want). */
export function FixedPayments({ line, onChanged }: { line: BudgetLine; onChanged: () => void }) {
  const t = useT(catalogue);
  const m = useT(monthBudget);
  const f = useFormat();
  const state = useLoad(loadRecurring);
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const inFlight = useRef(new Set<string>()); // a double tap is ignored before the row is disabled
  // Per series: hidden just now (before the reload drops it), shown just now, „Läuft weiter“.
  const [hiddenNow, setHiddenNow] = useState<ReadonlySet<string>>(new Set());
  const [shownNow, setShownNow] = useState<ReadonlySet<string>>(new Set());
  const [kept, setKept] = useState<ReadonlySet<string>>(new Set());
  const [showHidden, setShowHidden] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (line.fixed == null || line.variable == null) return null;
  const codes = new Set<string>((line.codes ?? []).map((code) => code.category));
  const series = new Map((state.data?.series ?? []).map((s) => [seriesId(s), s]));
  const rows = line.fixed.filter((row) => !hiddenNow.has(row.key));
  const hidden = [
    ...(state.data?.hidden ?? []).filter((s) => codes.has(s.lines?.[0]?.category ?? "") && !shownNow.has(seriesId(s))),
    ...(state.data?.series ?? []).filter((s) => hiddenNow.has(seriesId(s))),
  ];

  async function answer(s: RecurringSeries, next: "hidden" | "shown") {
    const id = seriesId(s);
    if (inFlight.current.has(id)) return;
    inFlight.current.add(id);
    const mark = next === "hidden" ? setHiddenNow : setShownNow;
    setBusy((previous) => withId(previous, id, true));
    mark((previous) => withId(previous, id, true));
    setError(null);
    try {
      await postSeriesMark(s, next);
      state.reload();
      onChanged();
    } catch (caught) {
      mark((previous) => withId(previous, id, false));
      setError(caught);
    } finally {
      inFlight.current.delete(id);
      setBusy((previous) => withId(previous, id, false));
    }
  }

  return (
    <div className="stack recurring-panel">
      <section className="stack" aria-label={m.fixedTitle}>
        {/* The fixed payments' sum beside their title, as „Schwankend“ shows its own (§7.13 (b) 4). */}
        <div className="recurring-total">
          <h3>{m.fixedTitle}</h3>
          <strong className="money" data-testid="fixed-total">
            {f.money(line.fixed_monthly ?? "0.00")}
          </strong>
        </div>
        {error != null ? <ErrorNotice error={error} /> : null}
        {rows.length === 0 ? (
          <p className="muted">{m.fixedNone}</p>
        ) : (
          <ul className="recurring-rows">
            {rows.map((row) => (
              <FixedRow
                key={row.key}
                row={row}
                series={series.get(row.key) ?? null}
                asked={!kept.has(row.key)}
                busy={busy.has(row.key)}
                onEnded={(s) => void answer(s, "hidden")}
                onKept={() => setKept((previous) => withId(previous, row.key, true))}
              />
            ))}
          </ul>
        )}
        {hiddenNow.size > 0 ? <p className="muted">{m.endedNote}</p> : null}
      </section>
      <section className="stack" aria-label={m.variableTitle}>
        <div className="recurring-total">
          <strong>{m.variableTitle}</strong>
          <strong>{f.money(line.variable)}</strong>
        </div>
        <p className="muted">{m.variableNote}</p>
      </section>
      {line.fixed_monthly != null ? (
        <p className="recurring-sum" data-testid="fixed-sum">
          {m.sumLine(f.money(line.fixed_monthly), f.money(line.variable), f.money(line.monthly))}
        </p>
      ) : null}
      {hidden.length > 0 ? (
        <div className="stack">
          <button type="button" className="link" aria-expanded={showHidden} onClick={() => setShowHidden((on) => !on)}>
            {showHidden ? m.hideHidden : m.showHidden(hidden.length)}
          </button>
          {showHidden ? (
            <ul className="detail-rows" aria-label={t.hiddenTitle}>
              {hidden.map((s) => (
                <li key={seriesId(s)}>
                  <div className="detail-row recurring-panel-row">
                    <span className="detail-row-text">
                      <span className="detail-row-title">{s.label}</span>
                      <span className="detail-row-sub">
                        {intervalLabel(t, s.interval)} · {f.money(s.last_amount)}
                      </span>
                    </span>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busy.has(seriesId(s))}
                      aria-label={m.showAria(s.label)}
                      onClick={() => void answer(s, "shown")}
                    >
                      {m.show}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function FixedRow({
  row,
  series,
  asked,
  busy,
  onEnded,
  onKept,
}: {
  row: BudgetFixedPayment;
  series: RecurringSeries | null;
  asked: boolean;
  busy: boolean;
  onEnded: (series: RecurringSeries) => void;
  onKept: () => void;
}) {
  const t = useT(catalogue);
  const m = useT(monthBudget);
  const f = useFormat();
  const change = changeShort(t, f, row.change, row.since);
  const ended = row.state === "maybe_ended";
  const sub = ended && series ? m.lastDebited(f.date(series.last_on)) : intervalLabel(t, row.interval);
  return (
    <li className="recurring-fixed" data-key={row.key}>
      <div className="recurring-row">
        <span className="recurring-row-text">
          <span className="recurring-row-title">{row.label}</span>
          <span className="recurring-row-sub">
            {sub}
            {change !== null ? (
              <>
                {" · "}
                <span className={isNegative(row.change ?? "") ? "recurring-down" : "recurring-up"}>{change}</span>
              </>
            ) : null}
          </span>
        </span>
        <strong>{f.money(row.monthly)}</strong>
      </div>
      {ended && series && asked ? (
        <div className="recurring-ask stack">
          <p className="muted">{m.maybeEndedAsk}</p>
          <div className="row">
            <button type="button" className="secondary" disabled={busy} onClick={() => onEnded(series)}>
              {m.yesEnded}
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={onKept}>
              {m.stillRunning}
            </button>
          </div>
        </div>
      ) : null}
    </li>
  );
}
