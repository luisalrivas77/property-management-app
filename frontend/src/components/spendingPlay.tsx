"use client";

// Planung › Haushalt › „Ausgaben durchspielen“ (phase 9 D263, §7.6, §14; docs/mockups/
// 12d-planning-spending-play.png). One row per spending group of the Haushalt screen, opening
// into its subcategories, each with its measured „Ø pro Monat“ and an empty field for the
// planned amount (€ or % of the mean) from a month and optionally to a month; „Neue Ausgabe“
// for a code with no history. „Was eure Änderungen bewirken“ compares today (no spending
// change) with the typed changes, both from POST /household-plan/preview, called a moment after
// a member stops typing; nothing is stored until „Szenario speichern“ (D263 (c)). Every figure
// is the server's (means, planned amounts, the rate and „Anlegbar“ per stretch of months, the
// reserve path, the net worth); this file only formats them. No colour, no mark: the app
// proposes no amount and no target (D263 (e)), the mean is the starting value, never advice.
import { useEffect, useId, useRef, useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { formatMoney, formatMonth } from "./format";
import { formatRatio, type HouseholdLevers, type HouseholdPlan, longDate, monthRange, toSignedRate } from "./healthShared";
import { decimalInput, percentInput, toMoneyString } from "./input";
import { ALLOCATION_CATEGORY, HOUSEHOLD_GROUP, HOUSEHOLD_SPENDING_GROUPS } from "./labels";

type AllocationCategory = components["schemas"]["AllocationCategory"];
type HouseholdGroup = components["schemas"]["HouseholdGroup"];
type SpendingChange = components["schemas"]["SpendingChange"];
type SpendingPlay = components["schemas"]["SpendingPlay"];
type SpendingPlayGroup = components["schemas"]["SpendingPlayGroup"];
type PlayedChange = components["schemas"]["PlayedChange"];
type RateSegment = components["schemas"]["RateSegment"];

type SetDraft = (update: (previous: HouseholdLevers) => HouseholdLevers) => void;

const DEBOUNCE_MS = 400;
const GROUPS_SHOWN = 8;

// --- the preview -----------------------------------------------------------------------------

export interface PlanPreview {
  /** The plan with the typed levers (the saved plan while nothing is changed). */
  withChanges: HouseholdPlan;
  /** The same levers without any spending change: the column „heute“; null until it arrives. */
  base: HouseholdPlan | null;
  /** The last refusal of the preview; the figures stay at the last good answer. */
  error: unknown;
  pending: boolean;
}

/** POST /household-plan/preview for the draft, a moment after the last keystroke; an answer
 * that arrives after a newer request was sent is dropped. Nothing is stored (D263 (c)). */
export function usePlanPreview(saved: HouseholdPlan, draft: HouseholdLevers, dirty: boolean): PlanPreview {
  const key = JSON.stringify(draft);
  const needsBase = (draft.spending_changes ?? []).length > 0;
  const idle = !dirty && !needsBase;
  const [result, setResult] = useState<{ key: string; withChanges: HouseholdPlan; base: HouseholdPlan } | null>(null);
  const [error, setError] = useState<{ key: string; error: unknown } | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    if (idle) return;
    const levers = JSON.parse(key) as HouseholdLevers;
    const id = latest.current + 1;
    latest.current = id;
    const timer = window.setTimeout(() => {
      const preview = (body: HouseholdLevers) => ok(api.POST("/api/household-plan/preview", { body: { levers: body } }));
      const withChanges = dirty ? preview(levers) : Promise.resolve(saved);
      const base = (levers.spending_changes ?? []).length > 0 ? preview({ ...levers, spending_changes: [] }) : withChanges;
      Promise.all([withChanges, base]).then(
        ([w, b]) => {
          if (id !== latest.current) return;
          setResult({ key, withChanges: w, base: b });
          setError(null);
        },
        (caught: unknown) => {
          if (id === latest.current) setError({ key, error: caught });
        },
      );
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [key, idle, dirty, saved]);

  if (idle) return { withChanges: saved, base: saved, error: null, pending: false };
  const failed = error?.key === key ? error.error : null;
  return {
    withChanges: result?.withChanges ?? saved,
    base: result?.base ?? null,
    error: failed,
    pending: result?.key !== key && failed == null,
  };
}

// --- helpers ---------------------------------------------------------------------------------

function categoryLabel(code: string): string {
  return ALLOCATION_CATEGORY[code as AllocationCategory] ?? code;
}

function groupLabel(group: string): string {
  return HOUSEHOLD_GROUP[group as HouseholdGroup] ?? group;
}

/** The group the Haushalt screen shows a code in (labels' HOUSEHOLD_SPENDING_GROUPS). */
function groupOfCode(code: string): HouseholdGroup | null {
  return HOUSEHOLD_SPENDING_GROUPS.find((entry) => entry.categories.includes(code as AllocationCategory))?.group ?? null;
}

/** A group's fallback code („Sonstige …“, the last of its codes), which stands for the group. */
function fallbackOf(group: string): AllocationCategory | null {
  const categories = HOUSEHOLD_SPENDING_GROUPS.find((entry) => entry.group === group)?.categories;
  return categories && categories.length > 1 ? (categories[categories.length - 1] ?? null) : null;
}

function unsigned(value: string): string {
  return value.startsWith("-") ? value.slice(1) : value;
}

function isZero(value: string | null | undefined): boolean {
  return value == null || /^-?0+(\.0+)?$/.test(value);
}

/** The months of a stretch in words: „ab Juni 2027“ when it runs to the horizon, else „Juli bis
 * September 2027“ or one month. */
function stretch(segment: RateSegment, last: string | undefined): string {
  if (segment.to_month === last) return `ab ${formatMonth(segment.from_month)}`;
  return monthRange([segment.from_month, segment.to_month]);
}

interface Entry {
  text: string;
  unit: "eur" | "pct";
  from: string;
  to: string;
}

function entryOf(change: SpendingChange | undefined, first: string): Entry {
  if (!change) return { text: "", unit: "eur", from: first, to: "" };
  return {
    text: change.percent != null ? percentInput(change.percent) : decimalInput(change.monthly),
    unit: change.percent != null ? "pct" : "eur",
    from: change.from_month,
    to: change.to_month ?? "",
  };
}

/** A typed entry → the change it plans (null: the field is empty, „wie bisher“), or why not. */
function parseEntry(code: AllocationCategory, entry: Entry): { ok: true; change: SpendingChange | null } | { ok: false; message: string } {
  if (entry.text.trim() === "") return { ok: true, change: null };
  if (entry.to !== "" && entry.to < entry.from) return { ok: false, message: "Das Ende liegt im oder nach dem Startmonat." };
  if (entry.unit === "eur") {
    const parsed = toMoneyString(entry.text);
    if (!parsed.ok) return parsed;
    return {
      ok: true,
      change: { code, monthly: parsed.value, percent: null, from_month: entry.from, to_month: entry.to || null, mean_at_save: null },
    };
  }
  const parsed = toSignedRate(entry.text, 2);
  if (!parsed.ok) return parsed;
  const value = Number(parsed.value);
  if (value < -1 || value > 2) return { ok: false, message: "Zwischen −100 % und +200 %." };
  return {
    ok: true,
    change: { code, monthly: null, percent: parsed.value, from_month: entry.from, to_month: entry.to || null, mean_at_save: null },
  };
}

/** The change the play shows for a code (a group's own code on the group row). */
function playedOf(play: SpendingPlay | null | undefined, code: string): PlayedChange | undefined {
  for (const group of play?.groups ?? []) {
    if (group.code === code && group.changes?.length) return group.changes[0];
    for (const row of group.rows) if (row.code === code && row.changes?.length) return row.changes[0];
  }
  return undefined;
}

// --- the section -----------------------------------------------------------------------------

export function SpendingPlaySection({
  saved,
  windowMonths,
  preview,
  draft,
  setDraft,
  onValidity,
}: {
  saved: HouseholdPlan;
  /** The window's closed months the means come from (GET /health). */
  windowMonths: readonly string[];
  preview: PlanPreview;
  draft: HouseholdLevers;
  setDraft: SetDraft;
  /** A field holding an entry it cannot take keeps the draft's old change; no save until fixed. */
  onValidity: (invalid: boolean) => void;
}) {
  const ids = useId();
  const savedPlay = saved.spending_play;
  const play = preview.withChanges.spending_play ?? savedPlay;
  const months = (saved.months ?? []).map((m) => m.month);
  const first = months[0] ?? "";
  const changes = draft.spending_changes ?? [];
  const planned = new Set(changes.map((c) => c.code));

  // Codes a member added under „Neue Ausgabe“ stay in that list while typed, so the field
  // never moves; the saved plan's rows with a mean are the table.
  const tableGroups = (savedPlay?.groups ?? [])
    .map((group) => ({ ...group, rows: group.rows.filter((row) => !isZero(row.mean)) }))
    .filter((group) => !isZero(group.mean) || group.rows.length > 0);
  const inTable = new Set<string>(tableGroups.flatMap((g) => [...g.rows.map((r) => r.code), ...(g.code ? [g.code] : [])]));
  const [added, setAdded] = useState<AllocationCategory[]>(() => changes.map((c) => c.code).filter((code) => !inTable.has(code)));
  const [entries, setEntries] = useState<Record<string, Entry>>(() =>
    Object.fromEntries(changes.map((c) => [c.code, entryOf(c, first)])),
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<ReadonlySet<string>>(
    () => new Set(tableGroups.filter((g) => g.rows.some((r) => r.code !== g.code && planned.has(r.code))).map((g) => g.group)),
  );
  const [allGroups, setAllGroups] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const [newCode, setNewCode] = useState("");

  function update(code: AllocationCategory, patch: Partial<Entry>) {
    const entry = { ...(entries[code] ?? entryOf(undefined, first)), ...patch };
    setEntries((previous) => ({ ...previous, [code]: entry }));
    const parsed = parseEntry(code, entry);
    const nextErrors = { ...errors };
    if (parsed.ok) delete nextErrors[code];
    else nextErrors[code] = parsed.message;
    setErrors(nextErrors);
    onValidity(Object.keys(nextErrors).length > 0);
    if (!parsed.ok) return;
    setDraft((previous) => {
      const list = [...(previous.spending_changes ?? [])];
      const index = list.findIndex((c) => c.code === code);
      if (parsed.change === null) {
        if (index >= 0) list.splice(index, 1);
      } else {
        const change = { ...parsed.change, mean_at_save: index >= 0 ? (list[index]?.mean_at_save ?? null) : null };
        if (index >= 0) list[index] = change;
        else list.push(change);
      }
      return { ...previous, spending_changes: list };
    });
  }

  const toggle = (group: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });

  // „Neue Ausgabe“: the server's list, less a code that would sit beside its group's own
  // change (a fallback stands for the whole group, §7.6 (1)).
  const offered = (play?.addable ?? []).filter((code) => {
    if (added.includes(code)) return false;
    const group = groupOfCode(code);
    const fallback = group ? fallbackOf(group) : null;
    if (fallback && fallback !== code && planned.has(fallback)) return false;
    if (fallback === code && group) {
      const members = HOUSEHOLD_SPENDING_GROUPS.find((entry) => entry.group === group)?.categories ?? [];
      if (members.some((member) => member !== code && planned.has(member))) return false;
    }
    return true;
  });

  const field = (code: AllocationCategory, label: string, mean: string, locked: string | null, hasMean: boolean) => {
    const entry = entries[code] ?? entryOf(undefined, first);
    const id = `${ids}-${code}`;
    const error = errors[code];
    const played = playedOf(play, code);
    const active = planned.has(code);
    if (locked) {
      return (
        <div className="spending-play-field">
          <span className="spending-play-input">
            <input id={id} type="text" inputMode="decimal" aria-label={`Geplant pro Monat: ${label}`} value="" disabled />
            <span aria-hidden="true">€</span>
          </span>
          <span className="muted">{locked}</span>
        </div>
      );
    }
    return (
      <div className="spending-play-field">
        <div className="spending-play-controls">
          <span className="spending-play-input">
            <input
              id={id}
              type="text"
              inputMode="decimal"
              aria-label={`Geplant pro Monat: ${label}`}
              aria-invalid={error ? true : undefined}
              aria-describedby={error || (active && played) ? `${id}-hint` : undefined}
              value={entry.text}
              onChange={(event) => update(code, { text: event.target.value })}
            />
            <span aria-hidden="true">{entry.unit === "eur" ? "€" : "%"}</span>
          </span>
          {active || entry.text !== "" ? (
            <>
              <span role="group" aria-label={`Betrag oder Prozent: ${label}`} className="spending-play-unit">
                <button type="button" aria-pressed={entry.unit === "eur"} onClick={() => update(code, { unit: "eur" })}>
                  €
                </button>
                <button
                  type="button"
                  aria-pressed={entry.unit === "pct"}
                  disabled={!hasMean}
                  title={hasMean ? undefined : "Ohne gemessenen Durchschnitt nur als Betrag"}
                  onClick={() => update(code, { unit: "pct" })}
                >
                  %
                </button>
              </span>
              <label className="inline">
                von
                <select aria-label={`von: ${label}`} value={entry.from} onChange={(event) => update(code, { from: event.target.value })}>
                  {months.map((m) => (
                    <option key={m} value={m}>
                      {formatMonth(m)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="inline">
                bis
                <select aria-label={`bis: ${label}`} value={entry.to} onChange={(event) => update(code, { to: event.target.value })}>
                  <option value="">ohne Ende</option>
                  {months.map((m) => (
                    <option key={m} value={m}>
                      {formatMonth(m)}
                    </option>
                  ))}
                </select>
              </label>
            </>
          ) : null}
        </div>
        {error ? (
          <ul id={`${id}-hint`} className="field-errors">
            <li>{error}</li>
          </ul>
        ) : active && played ? (
          <p id={`${id}-hint`} className="muted">
            {hint(played, mean)}
          </p>
        ) : null}
      </div>
    );
  };

  const shownGroups = allGroups ? tableGroups : tableGroups.slice(0, GROUPS_SHOWN);
  const hidden = tableGroups.slice(GROUPS_SHOWN);
  const segments = play?.rates ?? [];
  const lastMonth = months[months.length - 1];

  return (
    <section className="card stack" id="ausgaben-durchspielen" aria-labelledby={`${ids}-title`}>
      <div className="planning-card-head">
        <h2 id={`${ids}-title`}>Ausgaben durchspielen</h2>
        <span className="spacer" />
        <button type="button" className="link" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
          {expanded ? "zuklappen ▴" : "aufklappen ▾"}
        </button>
      </div>
      {expanded && savedPlay ? (
        <>
          <p className="muted">
            Tragt für eine Kategorie ein, wie viel ihr pro Monat ausgeben wollt, als Betrag oder als Prozent ihres Durchschnitts, und ab
            wann. Ein leeres Feld heißt: wie bisher, also der Durchschnitt. „Ø pro Monat“ ist euer gemessener Durchschnitt aus{" "}
            {monthRange(windowMonths)}, kein Vorschlag. Einmalausgaben wie ein Autokauf sind hier nicht dabei.
          </p>
          <div className="table-wrap">
            <table className="spending-play-table">
              <thead>
                <tr>
                  <th scope="col">Kategorie</th>
                  <th scope="col" className="num">
                    Ø pro Monat
                  </th>
                  <th scope="col">Geplant pro Monat</th>
                </tr>
              </thead>
              <tbody>
                {shownGroups.flatMap((group) => groupRows(group))}
                {!allGroups && hidden.length > 0 ? (
                  <tr>
                    <th scope="row">
                      <button type="button" className="household-toggle" onClick={() => setAllGroups(true)}>
                        <span aria-hidden="true">▸</span> {hidden.length} weitere {hidden.length === 1 ? "Gruppe" : "Gruppen"} anzeigen
                      </button>
                    </th>
                    <td />
                    <td />
                  </tr>
                ) : null}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">Regelmäßige Ausgaben</th>
                  <td className="num">
                    <strong>{formatMoney(play?.mean_regular_spending)}</strong>
                  </td>
                  <td>
                    {changes.length > 0 && segments.length > 0 ? (
                      <span className="spending-play-segments">
                        <span>mit euren Änderungen:</span>
                        {segments.map((segment) => (
                          <span key={segment.from_month}>
                            {stretch(segment, lastMonth)}: {formatMoney(segment.regular_spending)}
                          </span>
                        ))}
                      </span>
                    ) : null}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>

          <div className="stack">
            <h3>Neue Ausgabe</h3>
            {added.length > 0 ? (
              <table className="spending-play-table">
                <tbody>
                  {added.map((code) => (
                    <tr key={code}>
                      <th scope="row">
                        {categoryLabel(code)}
                        <span className="muted"> · {groupLabel(groupOfCode(code) ?? "other")}</span>
                      </th>
                      <td className="num">{formatMoney("0.00")}</td>
                      <td>
                        {field(code, categoryLabel(code), "0.00", null, false)}
                        <button
                          type="button"
                          className="link"
                          onClick={() => {
                            update(code, { text: "" });
                            setAdded((previous) => previous.filter((c) => c !== code));
                          }}
                        >
                          Entfernen
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            {offered.length > 0 ? (
              <div className="row">
                <label htmlFor={`${ids}-new`}>Kategorie</label>
                <select id={`${ids}-new`} value={newCode} onChange={(event) => setNewCode(event.target.value)}>
                  <option value="">Kategorie wählen …</option>
                  {offered.map((code) => (
                    <option key={code} value={code}>
                      {categoryLabel(code)} ({groupLabel(groupOfCode(code) ?? "other")})
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="secondary"
                  disabled={newCode === ""}
                  onClick={() => {
                    setAdded((previous) => [...previous, newCode as AllocationCategory]);
                    setNewCode("");
                  }}
                >
                  + Neue Ausgabe hinzufügen
                </button>
                <span className="muted">für eine Ausgabe, die es bei euch bisher nicht gibt (Ø 0 €)</span>
              </div>
            ) : null}
          </div>

          <Effects saved={saved} preview={preview} />
        </>
      ) : null}
    </section>
  );

  function groupRows(group: SpendingPlayGroup) {
    const own = group.code ?? null;
    const subRows = group.rows.filter((row) => row.code !== own || group.rows.length > 1);
    const opens = subRows.length > 0;
    const isOpen = open.has(group.group);
    const label = groupLabel(group.group);
    const rowId = `${ids}-group-${group.group}`;
    const subPlanned = group.rows.some((row) => row.code !== own && planned.has(row.code));
    const out = [
      <tr key={group.group} className="spending-play-group">
        <th scope="row">
          {opens ? (
            <button
              type="button"
              className="household-toggle"
              aria-expanded={isOpen}
              aria-controls={subRows.map((row) => `${rowId}-${row.code}`).join(" ")}
              onClick={() => toggle(group.group)}
            >
              <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span> {label}
            </button>
          ) : (
            label
          )}
        </th>
        <td className="num">{formatMoney(group.mean)}</td>
        <td>
          {own
            ? field(own, label, group.mean, subPlanned ? "ihr plant hier je Unterkategorie" : null, !isZero(group.mean))
            : <span className="muted">wird hier nicht geplant</span>}
        </td>
      </tr>,
    ];
    if (!opens || !isOpen) return out;
    for (const row of subRows) {
      const rowLabel = categoryLabel(row.code);
      let cell;
      if (row.code === own) {
        cell = <span className="muted">zählt mit der ganzen Gruppe: planbar oben in der Gruppenzeile, wenn keine Unterkategorie geplant ist</span>;
      } else if (!row.playable) {
        cell = <span className="muted">zählt mit, wird aber nicht geplant</span>;
      } else {
        const locked = own && planned.has(own) ? "ihr plant hier für die ganze Gruppe" : null;
        cell = field(row.code, rowLabel, row.mean, locked, !isZero(row.mean));
      }
      out.push(
        <tr key={`${group.group}-${row.code}`} id={`${rowId}-${row.code}`} className="household-subrow">
          <th scope="row">{rowLabel}</th>
          <td className="num">{formatMoney(row.mean)}</td>
          <td>{cell}</td>
        </tr>,
      );
    }
    return out;
  }
}

/** „= 682,50 € im Monat, 32,50 € mehr als der Durchschnitt“ from the server's planned and Δ. */
function hint(played: PlayedChange, mean: string): string {
  const delta = played.delta;
  const words = isZero(delta)
    ? "so viel wie der Durchschnitt"
    : `${formatMoney(unsigned(delta))} ${delta.startsWith("-") ? "weniger" : "mehr"} als der Durchschnitt`;
  const amount = played.change.percent != null ? `= ${formatMoney(played.planned)} im Monat, ${words}` : words;
  const change = played.change;
  if (change.to_month) {
    return `${amount}, ${monthRange([change.from_month, change.to_month])}; danach wieder ${formatMoney(mean)}`;
  }
  return `${amount}, ab ${formatMonth(change.from_month)}`;
}

// --- Was eure Änderungen bewirken ---------------------------------------------------------------

function Effects({ saved, preview }: { saved: HouseholdPlan; preview: PlanPreview }) {
  const now = preview.base;
  const next = preview.withChanges;
  const nowPlay = now?.spending_play;
  const nextPlay = next.spending_play;
  const months = (saved.months ?? []).map((m) => m.month);
  const last = months[months.length - 1];
  const segments = nextPlay?.rates ?? [];
  const reached = (plan: HouseholdPlan | null) => {
    if (!plan) return "…";
    const path = (plan.paths ?? []).find((p) => p.key === "as_is");
    if (!path) return "–";
    return path.reached ? formatMonth(path.reached) : `nicht in ${plan.levers.horizon_months} Monaten`;
  };
  const lastPoint = (plan: HouseholdPlan | null) => {
    const points = plan?.projection?.points ?? [];
    return points[points.length - 1] ?? null;
  };
  const nowPoint = lastPoint(now);
  const nextPoint = lastPoint(next);
  const year = (nextPoint ?? nowPoint)?.on.slice(0, 4);

  const bySegment = (value: (segment: RateSegment) => string) =>
    segments.length === 0 ? (
      "–"
    ) : (
      <span className="spending-play-segments">
        {segments.map((segment, index) =>
          index === 0 ? (
            <span key={segment.from_month}>
              <strong>{value(segment)}</strong> <span className="muted">{stretch(segment, last)}</span>
            </span>
          ) : (
            <span key={segment.from_month} className="muted">
              {stretch(segment, last)}: {value(segment)}
            </span>
          ),
        )}
      </span>
    );

  return (
    <div className="spending-play-effects stack" aria-live="polite">
      <h3>Was eure Änderungen bewirken</h3>
      <p className="muted">
        Die App rechnet bei jeder Eingabe neu. Beide Spalten aus denselben gemessenen Monaten; welche Änderung passt, entscheidet ihr.
        {preview.pending ? " Rechnet …" : ""}
      </p>
      {preview.error != null ? <ErrorNotice error={preview.error} /> : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">
                <span className="visually-hidden">Kennzahl</span>
              </th>
              <th scope="col">heute (ohne Einmalausgaben)</th>
              <th scope="col">mit euren Änderungen</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Sparquote</th>
              <td>{formatRatio(nowPlay?.rate_regular_now)}</td>
              <td data-testid="effect-rate">{bySegment((segment) => formatRatio(segment.rate))}</td>
            </tr>
            <tr>
              <th scope="row">Anlegbar pro Monat</th>
              <td>{now ? formatMoney(nowPlay?.rates?.[0]?.investable) : "…"}</td>
              <td data-testid="effect-investable">{bySegment((segment) => formatMoney(segment.investable))}</td>
            </tr>
            <tr>
              <th scope="row">
                Reserveziel erreicht
                <span className="muted spending-play-sub">auf dem Weg „Alles weiter wie bisher“</span>
              </th>
              <td>{reached(now)}</td>
              <td>{reached(next)}</td>
            </tr>
            <tr>
              <th scope="row">
                {year ? `Vermögen am ${longDate(`${year}-12-31`)}` : "Vermögen am Ende"}
                <span className="muted spending-play-sub">nach eurer Annahme für Fonds und Krypto, siehe „Vermögen in einigen Jahren“</span>
              </th>
              <td>{now ? (nowPoint ? formatMoney(nowPoint.total) : "–") : "…"}</td>
              <td>{nextPoint ? formatMoney(nextPoint.total) : "–"}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="muted">
        Sparquote heute: ohne Einmalausgaben. Die Sparquote mit euren Änderungen zeigt jeden Monat, in dem sie sich ändert, nie einen
        Durchschnitt der Monate.
      </p>
    </div>
  );
}
