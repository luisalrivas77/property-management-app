"use client";

// The recurring payments on Haushalt › Monat (phase 10 §7.9 (b); D296; mockup 19c panels 4 and 5).
// Every amount is the server's (GET /household/recurring); the browser formats it and adds
// nothing up. A person's answers are POSTed and the reads are asked again. The one-off check of
// §7.9 (c) (the line „<N> Zahlungen vielleicht einmalig“ and its panel) is gone since §7.12 (D307):
// a category is one-off by default or by its switch, and a single payment is marked from its
// detail panel (mockup 19h, built later).
//
// - useRecurringPayments(): the card „Regelmäßige Zahlungen“ and its panels for the page's one
//   DetailLayout (D267): `#d-abos-alle` lists every series, `#d-abos-ausgeblendet` the hidden
//   ones, `#d-abo-<id>` one series, its id the opaque id of its oldest payment (never the payee's
//   words in the URL). „Kein Abo, ausblenden“ posts `hidden`, „Wieder anzeigen“ `shown`; neither
//   changes a figure (D296 (4)).
// - Every answer shows at once (optimistic, Luis 24 Nov 2026): the row takes its new state before
//   the POST, only that row waits for its POST (a double tap on it is ignored, the other rows stay
//   clickable), and a failure puts that row back as it was and shows the error. The reads are asked
//   again afterwards as before.
import { type ReactNode, useRef, useState } from "react";

import { api, ok } from "@/api/client";
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
type Messages = (typeof catalogue)["de"];

const LIST = "abos";
const SERIES = "abo";
const ALL = "alle";
const HIDDEN = "ausgeblendet";

/** At most this many price changes on the card itself (mockup 19c panel 4). */
const CARD_CHANGES = 3;

function categoryLabel(category: string): string {
  return ALLOCATION_CATEGORY[category as AllocationCategory] ?? category;
}

function isNegative(value: string): boolean {
  return value.trim().startsWith("-");
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

/** A copy of `ids` with `id` in it (`on`) or not. */
function withId(ids: ReadonlySet<string>, id: string, on: boolean): ReadonlySet<string> {
  const next = new Set(ids);
  if (on) next.add(id);
  else next.delete(id);
  return next;
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
            key={seriesId(series)}
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

function SeriesPanel({ series, hidden: hiddenRead, onDone }: { series: RecurringSeries; hidden: boolean; onDone: () => void }) {
  const t = useT(catalogue);
  const f = useFormat();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // The answer just given, shown at once; null while none is in flight or after it failed.
  const [given, setGiven] = useState<"hidden" | "shown" | null>(null);
  const inFlight = useRef(false); // a double tap is ignored before the button is disabled
  const hidden = given === null ? hiddenRead : given === "hidden";
  const lines = [...(series.lines ?? [])].reverse(); // newest first
  const first = series.lines?.[0]?.booked_on;
  const category = lines[0]?.category;
  const change = changeLong(t, f, series);
  const sub = [intervalLabel(t, series.interval), first ? t.since(f.month(first)) : null, t.now(f.money(series.last_amount))].filter(
    (part): part is string => part !== null,
  );

  async function answer(next: "hidden" | "shown") {
    if (inFlight.current) return;
    inFlight.current = true;
    const before = given;
    setGiven(next);
    setBusy(true);
    setError(null);
    try {
      await postSeriesMark(series, next);
      onDone();
    } catch (caught) {
      setGiven(before);
      setError(caught);
    } finally {
      inFlight.current = false;
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
  // Per row: the POST in flight, and the series shown again before the reload brings it back.
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const inFlight = useRef(new Set<string>()); // a double tap is ignored before the row is disabled
  const [shownNow, setShownNow] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<unknown>(null);
  const rows = hidden.filter((s) => !shownNow.has(seriesId(s)));

  async function show(series: RecurringSeries) {
    const id = seriesId(series);
    if (inFlight.current.has(id)) return;
    inFlight.current.add(id);
    setBusy((previous) => withId(previous, id, true));
    setShownNow((previous) => withId(previous, id, true));
    setError(null);
    try {
      await postSeriesMark(series, "shown");
      state.reload();
    } catch (caught) {
      setShownNow((previous) => withId(previous, id, false));
      setError(caught);
    } finally {
      inFlight.current.delete(id);
      setBusy((previous) => withId(previous, id, false));
    }
  }

  return (
    <div className="stack recurring-panel">
      <p className="muted">{t.hiddenIntro}</p>
      {error != null ? <ErrorNotice error={error} /> : null}
      {rows.length === 0 ? (
        <p className="muted">{t.hiddenNone}</p>
      ) : (
        <ul className="detail-rows">
          {rows.map((s) => (
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
