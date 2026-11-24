"use client";

// The recurring payments on Haushalt › Monat and the one-off check on Haushalt › Budget (phase 10
// §7.9 (b), (c); D296, D297; mockup 19c panels 4 to 7). Every amount is the server's (GET
// /household/recurring, GET /household/one-off-candidates); the browser formats it and adds
// nothing up. A person's answers are POSTed and the reads are asked again.
//
// - useRecurringPayments(): the card „Regelmäßige Zahlungen“ and its panels for the page's one
//   DetailLayout (D267): `#d-abos-alle` lists every series, `#d-abos-ausgeblendet` the hidden
//   ones, `#d-abo-<id>` one series, its id the opaque id of its oldest payment (never the payee's
//   words in the URL). „Kein Abo, ausblenden“ posts `hidden`, „Wieder anzeigen“ `shown`; neither
//   changes a figure (D296 (4)).
// - useOneOffCheck(): the line „<N> Zahlungen vielleicht einmalig · Prüfen“ and its panel
//   `#d-einmalig-pruefen`. An answer moves the key figures, so `onChanged` asks the plan, the
//   household month, the Budget and the key figures again. While the window has fewer than three
//   closed months the read lists nothing and every POST is a 404: then nothing is shown. A 404
//   after the list was shown (the payment left the window, or it was booked again) reloads the
//   list and says so in a plain sentence.
import { type ReactNode, useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import { ApiError } from "@/api/errors";
import type { components } from "@/api/types";
import { DetailPanel, useDetail } from "@/components/DetailPanel";
import { ALLOCATION_CATEGORY } from "@/components/labels";
import { useLoad, type Loaded } from "@/components/useLoad";
import { useFormat, useT, type Formatter } from "@/i18n";
import { recurring as catalogue } from "@/i18n/recurring";
import { ErrorNotice } from "@/lib/ErrorNotice";

import "./householdRecurring.css";

type AllocationCategory = components["schemas"]["AllocationCategory"];
type RecurringPayments = components["schemas"]["RecurringPayments"];
type RecurringSeries = components["schemas"]["RecurringSeries"];
type OneOffCandidates = components["schemas"]["OneOffCandidates"];
type OneOffCandidate = components["schemas"]["OneOffCandidate"];
type Messages = (typeof catalogue)["de"];

const LIST = "abos";
const SERIES = "abo";
const ONE_OFF = "einmalig";
const ALL = "alle";
const HIDDEN = "ausgeblendet";
const CHECK = "pruefen";

/** At most this many price changes on the card itself (mockup 19c panel 4). */
const CARD_CHANGES = 3;
/** At most this many payments named on the Budget's one-off line. */
const LINE_ITEMS = 2;

function categoryLabel(category: string): string {
  return ALLOCATION_CATEGORY[category as AllocationCategory] ?? category;
}

function isNegative(value: string): boolean {
  return value.trim().startsWith("-");
}

/** A positive amount of a non-zero, non-negative decimal string. */
function isPositive(value: string): boolean {
  return !isNegative(value) && /[1-9]/.test(value);
}

function unsigned(value: string): string {
  return value.trim().replace(/^[-+]/, "");
}

/** The series' id in the URL: the opaque id of its oldest payment. */
function seriesId(series: RecurringSeries): string {
  return series.sources?.[0] ?? series.lines?.[0]?.allocation_id ?? `${series.interval}-${series.label}`;
}

function intervalLabel(t: Messages, interval: RecurringSeries["interval"]): string {
  return interval === "monthly" ? t.monthly : interval === "quarterly" ? t.quarterly : t.yearly;
}

/** „+2,00 € seit März 2026“ / „−6,00 € seit Juli 2026“: the server's change, its sign as words. */
function changeShort(t: Messages, f: Formatter, series: RecurringSeries): string | null {
  if (series.change == null || series.since == null) return null;
  const amount = f.money(unsigned(series.change));
  return isNegative(series.change) ? t.changeDown(amount, f.month(series.since)) : t.changeUp(amount, f.month(series.since));
}

function changeLong(t: Messages, f: Formatter, series: RecurringSeries): string | null {
  if (series.change == null || series.since == null) return null;
  const amount = f.money(unsigned(series.change));
  return isNegative(series.change) ? t.changeDownLong(amount, f.month(series.since)) : t.changeUpLong(amount, f.month(series.since));
}

/** The month a yearly payment can first have come twice: a year after the history starts. */
function yearlyNote(t: Messages, f: Formatter, data: RecurringPayments): string | null {
  if (data.yearly_detectable) return null;
  const start = data.history_start;
  const match = start ? /^(\d{4})-(\d{2})/.exec(start) : null;
  if (!match) return t.yearlyLater;
  return t.yearlyFrom(f.month(`${Number(match[1]) + 1}-${match[2]}`));
}

async function loadRecurring(): Promise<RecurringPayments> {
  return ok(api.GET("/api/household/recurring"));
}

async function postSeriesMark(series: RecurringSeries, answer: "hidden" | "shown"): Promise<void> {
  await ok(api.POST("/api/household/recurring-marks", { body: { key: series.key, interval: series.interval, answer } }));
}

// --- (b) the recurring payments ----------------------------------------------------------------

export interface RecurringView {
  /** The card for the page, or null while nothing is listed (or the read failed). */
  card: ReactNode;
  /** The open panel of this card, or null. */
  panel: ReactNode;
}

/** The card „Regelmäßige Zahlungen“ and its panels; `backLabel` names the page on the phone. */
export function useRecurringPayments(backLabel: string): RecurringView {
  const t = useT(catalogue);
  const state = useLoad(loadRecurring);
  const list = useDetail(LIST);
  const one = useDetail(SERIES);
  const data = state.data;
  if (!data) return { card: null, panel: null };

  const shown = data.series ?? [];
  const hidden = data.hidden ?? [];
  let panel: ReactNode = null;
  if (list.id === HIDDEN) {
    panel = (
      <DetailPanel title={t.hiddenTitle} backLabel={t.recurringTitle} onClose={() => list.open(ALL)}>
        <HiddenPanel hidden={hidden} state={state} />
      </DetailPanel>
    );
  } else if (list.id !== null) {
    panel = (
      <DetailPanel title={t.recurringTitle} backLabel={backLabel} onClose={list.close}>
        <ListPanel data={data} link={one.linkProps} hiddenLink={list.linkProps(HIDDEN)} />
      </DetailPanel>
    );
  } else if (one.id !== null) {
    const found = shown.find((s) => seriesId(s) === one.id);
    const foundHidden = found ? undefined : hidden.find((s) => seriesId(s) === one.id);
    const series = found ?? foundHidden;
    if (series) {
      panel = (
        <DetailPanel title={series.label} backLabel={t.recurringTitle} onClose={() => list.open(foundHidden ? HIDDEN : ALL)}>
          <SeriesPanel
            series={series}
            hidden={foundHidden !== undefined}
            onDone={() => {
              state.reload();
              list.open(ALL);
            }}
          />
        </DetailPanel>
      );
    }
  }

  const card = shown.length > 0 || hidden.length > 0 ? <RecurringCard data={data} listLink={list.linkProps} seriesLink={one.linkProps} /> : null;
  return { card, panel };
}

type LinkProps = ReturnType<ReturnType<typeof useDetail>["linkProps"]>;

function RecurringCard({
  data,
  listLink,
  seriesLink,
}: {
  data: RecurringPayments;
  listLink: (id: string) => LinkProps;
  seriesLink: (id: string) => LinkProps;
}) {
  const t = useT(catalogue);
  const f = useFormat();
  const shown = data.series ?? [];
  const hidden = data.hidden ?? [];
  const changed = shown.filter((s) => s.state === "active" && s.change != null).slice(0, CARD_CHANGES);
  const ended = shown.filter((s) => s.state === "maybe_ended").length;
  const note = yearlyNote(t, f, data);
  const foot = [ended > 0 ? t.maybeEndedCount(ended) : null, note].filter((part): part is string => part !== null);
  return (
    <section className="card stack recurring-card" aria-labelledby="recurring-title">
      <a className="recurring-card-head" {...listLink(ALL)} aria-label={t.recurringOpen}>
        <h2 id="recurring-title">{t.recurringTitle}</h2>
        <span aria-hidden="true">›</span>
      </a>
      {shown.length > 0 ? (
        <p className="recurring-total">
          <strong>{t.payments(data.count)}</strong> <span>{t.perYear(f.money(data.yearly_total))}</span>
        </p>
      ) : (
        <p className="muted">{t.noneShown}</p>
      )}
      {changed.length > 0 ? (
        <ul className="recurring-rows">
          {changed.map((s) => (
            <li key={seriesId(s)}>
              <a className="recurring-row" {...seriesLink(seriesId(s))}>
                <span className="recurring-row-text">
                  <span className="recurring-row-title">{s.label}</span>
                  <span className="recurring-row-sub">{intervalLabel(t, s.interval)}</span>
                </span>
                <strong className={s.change && isNegative(s.change) ? "recurring-down" : "recurring-up"}>{changeShort(t, f, s)}</strong>
              </a>
            </li>
          ))}
        </ul>
      ) : null}
      {foot.length > 0 ? <p className="muted recurring-foot">{foot.join(" · ")}</p> : null}
      {shown.length === 0 && hidden.length > 0 ? (
        <p>
          <a {...listLink(HIDDEN)}>{t.showHidden(hidden.length)} ›</a>
        </p>
      ) : null}
    </section>
  );
}

function SeriesRow({ series, link }: { series: RecurringSeries; link: (id: string) => LinkProps }) {
  const t = useT(catalogue);
  const f = useFormat();
  const change = changeShort(t, f, series);
  let sub: string;
  if (series.state === "maybe_ended") sub = t.lastOn(f.date(series.last_on));
  else if (change !== null) sub = `${intervalLabel(t, series.interval)} · ${t.now(f.money(series.last_amount))}`;
  else {
    const first = series.lines?.[0]?.booked_on;
    sub = first ? t.since(f.month(first)) : t.lastOn(f.date(series.last_on));
  }
  return (
    <li>
      <a className="detail-row recurring-panel-row" {...link(seriesId(series))}>
        <span className="detail-row-text">
          <span className="detail-row-title">{series.label}</span>
          <span className="detail-row-sub">{sub}</span>
        </span>
        <strong className={change !== null && series.state === "active" ? (series.change && isNegative(series.change) ? "recurring-down" : "recurring-up") : undefined}>
          {change !== null && series.state === "active" ? change : f.money(series.last_amount)}
        </strong>
      </a>
    </li>
  );
}

function ListPanel({
  data,
  link,
  hiddenLink,
}: {
  data: RecurringPayments;
  link: (id: string) => LinkProps;
  hiddenLink: LinkProps;
}) {
  const t = useT(catalogue);
  const f = useFormat();
  const shown = data.series ?? [];
  const hidden = data.hidden ?? [];
  const active = shown.filter((s) => s.state === "active");
  const sections: { key: string; title: string; rows: RecurringSeries[] }[] = [
    { key: "changed", title: t.sectionChanged, rows: active.filter((s) => s.change != null) },
    { key: "monthly", title: t.sectionMonthly, rows: active.filter((s) => s.change == null && s.interval === "monthly") },
    { key: "quarterly", title: t.sectionQuarterly, rows: active.filter((s) => s.change == null && s.interval === "quarterly") },
    { key: "yearly", title: t.sectionYearly, rows: active.filter((s) => s.change == null && s.interval === "yearly") },
    { key: "ended", title: t.sectionEnded, rows: shown.filter((s) => s.state === "maybe_ended") },
  ];
  const note = yearlyNote(t, f, data);
  return (
    <div className="stack recurring-panel">
      <p className="muted">
        {t.payments(data.count)} · {t.perYearToday(f.money(data.yearly_total))}
      </p>
      {shown.length === 0 ? <p className="muted">{t.noneShown}</p> : null}
      {sections
        .filter((section) => section.rows.length > 0)
        .map((section) => (
          <section key={section.key} className="stack recurring-section" aria-labelledby={`recurring-${section.key}`}>
            <h3 id={`recurring-${section.key}`}>{section.title}</h3>
            <ul className="detail-rows">
              {section.rows.map((s) => (
                <SeriesRow key={seriesId(s)} series={s} link={link} />
              ))}
            </ul>
          </section>
        ))}
      {note ? <p className="muted">{note}</p> : null}
      {hidden.length > 0 ? (
        <p>
          <a {...hiddenLink}>{t.showHidden(hidden.length)} ›</a>
        </p>
      ) : null}
    </div>
  );
}

function SeriesPanel({ series, hidden, onDone }: { series: RecurringSeries; hidden: boolean; onDone: () => void }) {
  const t = useT(catalogue);
  const f = useFormat();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const lines = [...(series.lines ?? [])].reverse(); // newest first
  const first = series.lines?.[0]?.booked_on;
  const category = lines[0]?.category;
  const change = changeLong(t, f, series);
  const sub = [intervalLabel(t, series.interval), first ? t.since(f.month(first)) : null, t.now(f.money(series.last_amount))].filter(
    (part): part is string => part !== null,
  );

  async function answer(next: "hidden" | "shown") {
    setBusy(true);
    setError(null);
    try {
      await postSeriesMark(series, next);
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack recurring-panel">
      <p className="muted">
        {sub.join(" · ")}
        {hidden ? (
          <>
            {" "}
            <span className="badge">{t.hiddenBadge}</span>
          </>
        ) : null}
      </p>
      {change !== null && series.state === "active" ? (
        <div className="notice notice-warn" role="status">
          <p>{change}</p>
        </div>
      ) : null}
      {series.state === "maybe_ended" ? <p className="muted">{t.endedText(f.date(series.last_on))}</p> : null}
      <p>{t.perYearToday(f.money(series.yearly_total))}</p>
      <h3>{t.paymentsHeading}</h3>
      <ul className="household-rows">
        {lines.map((line) => (
          <li key={line.allocation_id}>
            <span>{f.date(line.booked_on)}</span>
            <span>{f.money(line.amount)}</span>
          </li>
        ))}
      </ul>
      {category ? <p className="muted">{t.categoryOf(categoryLabel(category))}</p> : null}
      {error != null ? <ErrorNotice error={error} /> : null}
      <div className="row">
        {hidden ? (
          <button type="button" disabled={busy} onClick={() => void answer("shown")}>
            {t.show}
          </button>
        ) : (
          <button type="button" className="secondary" disabled={busy} onClick={() => void answer("hidden")}>
            {t.hide}
          </button>
        )}
      </div>
      {!hidden ? <p className="muted">{t.hideNote}</p> : null}
    </div>
  );
}

function HiddenPanel({ hidden, state }: { hidden: RecurringSeries[]; state: Loaded<RecurringPayments> }) {
  const t = useT(catalogue);
  const f = useFormat();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function show(series: RecurringSeries) {
    setBusy(seriesId(series));
    setError(null);
    try {
      await postSeriesMark(series, "shown");
      state.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="stack recurring-panel">
      <p className="muted">{t.hiddenIntro}</p>
      {error != null ? <ErrorNotice error={error} /> : null}
      {hidden.length === 0 ? (
        <p className="muted">{t.hiddenNone}</p>
      ) : (
        <ul className="detail-rows">
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
                  disabled={busy !== null}
                  aria-label={t.showAria(s.label)}
                  onClick={() => void show(s)}
                >
                  {t.show}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- (c) the one-off check ---------------------------------------------------------------------

async function loadOneOff(): Promise<OneOffCandidates | null> {
  // The check is an extra beside the Budget: a read that fails shows no line, never the error.
  return ok(api.GET("/api/household/one-off-candidates")).catch(() => null);
}

async function loadNothing(): Promise<OneOffCandidates | null> {
  return null;
}

export interface OneOffView {
  /** The line for the Budget, or null when nothing is to check or answered. */
  line: ReactNode;
  /** The open panel, or null. */
  panel: ReactNode;
}

/** The one-off check; `enabled` false reads nothing (the plan's other pages); `onChanged` asks
 * every read an answer moves again (the plan, the Budget, the key figures). */
export function useOneOffCheck(enabled: boolean, backLabel: string, onChanged: () => void): OneOffView {
  const t = useT(catalogue);
  const state = useLoad(enabled ? loadOneOff : loadNothing);
  const detail = useDetail(ONE_OFF);
  const [gone, setGone] = useState(false);
  const { reload } = state;
  const refresh = useCallback(() => {
    reload();
    onChanged();
  }, [reload, onChanged]);
  const data = state.data ?? null;
  const candidates = data?.candidates ?? [];
  const answered = data?.answered ?? [];
  const any = candidates.length + answered.length > 0;

  let panel: ReactNode = null;
  if (data && detail.id !== null && (any || gone)) {
    panel = (
      <DetailPanel
        title={t.oneOffTitle}
        backLabel={backLabel}
        onClose={() => {
          setGone(false);
          detail.close();
        }}
      >
        <OneOffPanel
          data={data}
          gone={gone}
          onAnswered={() => {
            setGone(false);
            refresh();
          }}
          onGone={() => {
            setGone(true);
            refresh();
          }}
        />
      </DetailPanel>
    );
  }
  const line = data && any ? <OneOffLine data={data} link={detail.linkProps(CHECK)} /> : null;
  return { line, panel };
}

function OneOffLine({ data, link }: { data: OneOffCandidates; link: LinkProps }) {
  const t = useT(catalogue);
  const f = useFormat();
  const candidates = data.candidates ?? [];
  const answered = data.answered ?? [];
  const named = candidates
    .slice(0, LINE_ITEMS)
    .map((c) => t.oneOffItem(paymentLabel(c), f.money(c.payment.amount), f.month(c.payment.booked_on)));
  return (
    <section className="card recurring-oneoff-line" aria-labelledby="oneoff-line-title">
      <a {...link} className="recurring-oneoff-link">
        <span className="recurring-row-text">
          <strong id="oneoff-line-title">{candidates.length > 0 ? t.oneOffLine(candidates.length) : t.oneOffAnsweredLine}</strong>
          <span className="muted">{candidates.length > 0 ? `${named.join(", ")}${candidates.length > LINE_ITEMS ? ", …" : "."}` : t.oneOffAnsweredSub(answered.length)}</span>
        </span>
        <span className="recurring-oneoff-action">{candidates.length > 0 ? t.check : t.showAnswers} ›</span>
      </a>
    </section>
  );
}

function paymentLabel(candidate: OneOffCandidate): string {
  const p = candidate.payment;
  return p.payee || p.purpose || categoryLabel(p.category);
}

/** Candidates and answered payments in one list, newest first, so a row stays where it is when
 * it is answered and moves from one list of the read to the other. */
function ordered(data: OneOffCandidates): OneOffCandidate[] {
  return [...(data.candidates ?? []), ...(data.answered ?? [])].sort((a, b) => {
    const day = b.payment.booked_on.localeCompare(a.payment.booked_on);
    return day !== 0 ? day : a.payment.allocation_id.localeCompare(b.payment.allocation_id);
  });
}

function OneOffPanel({
  data,
  gone,
  onAnswered,
  onGone,
}: {
  data: OneOffCandidates;
  gone: boolean;
  onAnswered: () => void;
  onGone: () => void;
}) {
  const t = useT(catalogue);
  const f = useFormat();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  // The answer just given, shown at once until the reload brings it back from the server.
  const [given, setGiven] = useState<Record<string, "one_off" | "regular">>({});
  const items = ordered(data);

  async function answer(candidate: OneOffCandidate, next: "one_off" | "regular") {
    const id = candidate.payment.allocation_id;
    setBusy(id);
    setError(null);
    try {
      await ok(api.POST("/api/household/one-off-marks", { body: { allocation_id: id, answer: next } }));
      setGiven((previous) => ({ ...previous, [id]: next }));
      onAnswered();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) onGone();
      else setError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="stack recurring-panel">
      <p className="muted">{t.oneOffIntro}</p>
      {gone ? (
        <div className="notice notice-info" role="status">
          <p>{t.oneOffGone}</p>
        </div>
      ) : null}
      {error != null ? <ErrorNotice error={error} /> : null}
      {items.length === 0 ? (
        <p className="muted">{t.oneOffEmpty}</p>
      ) : (
        <ul className="detail-rows">
          {items.map((c) => {
            const id = c.payment.allocation_id;
            const current = c.answer === "one_off" || c.answer === "regular" ? c.answer : (given[id] ?? null);
            const label = paymentLabel(c);
            const sub = [f.date(c.payment.booked_on), categoryLabel(c.payment.category), isPositive(c.code_median) ? t.otherwise(f.money(c.code_median)) : null].filter(
              (part): part is string => part !== null,
            );
            return (
              <li key={id} className="recurring-oneoff-item" data-allocation={id}>
                <div className="recurring-oneoff-head">
                  <span className="detail-row-text">
                    <span className="detail-row-title">{label}</span>
                    <span className="detail-row-sub">{sub.join(" · ")}</span>
                  </span>
                  <strong>{f.money(c.payment.amount)}</strong>
                </div>
                <div className="recurring-oneoff-answers" role="group" aria-label={t.answerGroup(label)}>
                  <button
                    type="button"
                    className={current === "one_off" ? undefined : "secondary"}
                    aria-pressed={current === "one_off"}
                    disabled={busy !== null}
                    onClick={() => void answer(c, "one_off")}
                  >
                    {t.yesOneOff}
                  </button>
                  <button
                    type="button"
                    className={current === "regular" ? undefined : "secondary"}
                    aria-pressed={current === "regular"}
                    disabled={busy !== null}
                    onClick={() => void answer(c, "regular")}
                  >
                    {t.noRegular}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <div className="notice notice-info">
        <p>
          <strong>{t.oneOffWhatTitle}</strong>
        </p>
        <p>{t.oneOffWhat}</p>
      </div>
      <p className="muted">{t.oneOffFoot}</p>
    </div>
  );
}
