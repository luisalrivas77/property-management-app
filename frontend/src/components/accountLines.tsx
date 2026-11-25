"use client";

// Konten › Umsätze (phase 10 D269, mockup 16-C2): the account's month card on top, its figures
// from GET /accounts/{id}/month (the closing balance, Eingänge and Ausgänge without the
// transfers between own accounts, the transfers in and out, the lines not booked yet), never
// summed here (principle 1); below it the month's lines from GET /accounts/{id}/transactions,
// grouped by booking day (grouping, not a sum), each with its status in words („Gebucht“,
// „Offen“, „Vorschlag prüfen“, never a dot). A tapped line opens in the detail panel (D267)
// with what Zu prüfen offers for it: the allocation editor (AllocationPanel) for a line that
// can be booked, the link to its possible duplicate or its statement otherwise.
//
// The month is ?monat=YYYY-MM, by default the month of the newest line (an account whose last
// statement is months old opens on it, not on an empty running month). Two older entries keep
// working: ?import=<id> (the lines one statement stored, from an import row and the Bankabruf
// card) and ?von=…&bis=… (a booking-date range, from Planung's months); both list the lines
// without the month card.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type { Account, Owner, OwnershipShare, Transaction } from "@/api/schemas";
import { AllocationPanel } from "@/components/allocations";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { formatSharePercent, todayIso } from "@/components/format";
import { ALLOCATION_CATEGORY } from "@/components/labels";
import { OneOffMark } from "@/components/oneOffMark";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { LOCALE_TAG, useFormat, useLocale, useT, type Formatter } from "@/i18n";
import { accounts as catalogue } from "@/i18n/accounts";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { notifyInboxChanged } from "@/lib/inboxCount";

import "./accountScreens.css";

type Messages = (typeof catalogue)["de"];
type LineFilter = "all" | "open" | "review" | "booked";

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The owners of an account in a few words: a name, „gemeinsam 50/50“, or names with shares. */
export function ownersText(shares: OwnershipShare[] | null | undefined, owners: Owner[], t: Messages): string {
  const list = shares ?? [];
  if (list.length === 0) return t.ownersMissing;
  const name = (ownerId: string) => owners.find((owner) => owner.id === ownerId)?.full_name ?? "?";
  const half = (share: OwnershipShare) => share.share_numerator * 2 === share.share_denominator;
  if (list.length === 2 && list.every(half)) return t.together;
  const [only] = list;
  if (list.length === 1 && only && only.share_numerator === only.share_denominator) return name(only.owner_id);
  return list
    .map((share) => `${name(share.owner_id)} ${formatSharePercent({ numerator: share.share_numerator, denominator: share.share_denominator })}`)
    .join(", ");
}

function isCredit(amount: string): boolean {
  return !amount.startsWith("-") && !/^0+(\.0+)?$/.test(amount);
}

/** „+1.400,00 €“ for a credit, the minus sign the amount carries otherwise. */
export function signed(f: Formatter, amount: string): string {
  return `${isCredit(amount) ? "+" : ""}${f.money(amount)}`;
}

/** A positive figure the API sends as an outflow („614.79“ for Ausgänge) with its minus. */
function outflow(f: Formatter, amount: string): string {
  return /^0+(\.0+)?$/.test(amount) ? f.money(amount) : f.money(`-${amount}`);
}

function matches(line: Transaction, filter: LineFilter): boolean {
  switch (filter) {
    case "open":
      return ["open", "duplicate_open", "not_reconciled"].includes(line.allocation_status);
    case "review":
      return line.allocation_status === "proposed";
    case "booked":
      return line.allocation_status === "confirmed";
    default:
      return true;
  }
}

/** A day as the list's heading: „Donnerstag, 19. November“ / „Thursday, 19 November“. */
function useDayHeading(): (iso: string) => string {
  const locale = useLocale();
  return useMemo(() => {
    const format = new Intl.DateTimeFormat(LOCALE_TAG[locale], { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
    return (iso: string) => format.format(new Date(`${iso}T00:00:00Z`));
  }, [locale]);
}

function currentMonth(): string {
  return todayIso().slice(0, 7);
}

function categoriesOf(line: Transaction): string {
  return line.allocation_categories.map((category) => ALLOCATION_CATEGORY[category]).join(" + ");
}

/** What a line's category column says. */
function categoryText(line: Transaction, t: Messages): string {
  if (line.returned_with) return t.returned;
  const categories = categoriesOf(line);
  if (line.allocation_status === "confirmed") return categories || t.statusBooked;
  if (line.allocation_status === "proposed") return categories ? t.proposal(categories) : t.statusReview;
  if (line.allocation_status === "excluded") return "";
  return t.noCategory;
}

function StatusPill({ line, t }: { line: Transaction; t: Messages }) {
  switch (line.allocation_status) {
    case "confirmed":
      return <span className="line-status line-status-booked">✓ {t.statusBooked}</span>;
    case "proposed":
      return <span className="badge badge-warn">{t.statusReview}</span>;
    case "duplicate_open":
      return <span className="badge badge-warn">{t.statusDuplicate}</span>;
    case "not_reconciled":
      return <span className="badge badge-muted">{t.statusNotReconciled}</span>;
    case "excluded":
      return <span className="badge badge-muted">{t.statusExcluded}</span>;
    default:
      return <span className="badge">{t.statusOpen}</span>;
  }
}

interface LinesData {
  lines: Transaction[];
  /** The waiting inbox items' reasoning and question per line, for the panel. */
  asked: Map<string, { reasoning: string | null; question: string | null }>;
  owners: Owner[];
  allAccounts: Account[];
}

async function loadLines(accountId: string, importId: string | null, from: string | null, to: string | null): Promise<LinesData> {
  const [lines, items, owners, allAccounts] = await Promise.all([
    ok(
      api.GET("/api/accounts/{account_id}/transactions", {
        params: { path: { account_id: accountId }, query: { import_id: importId, booked_from: from, booked_to: to } },
      }),
    ),
    ok(api.GET("/api/inbox")),
    ok(api.GET("/api/owners")),
    ok(api.GET("/api/accounts")),
  ]);
  const asked = new Map<string, { reasoning: string | null; question: string | null }>();
  for (const item of items) {
    if (item.transaction_id) asked.set(item.transaction_id, { reasoning: item.reasoning ?? null, question: item.question ?? null });
  }
  return { lines, asked, owners, allAccounts };
}

export function AccountLinesView({
  account,
  tabs,
}: {
  account: Account;
  /** The account page's tab bar, shown under the heading. */
  tabs: ReactNode;
}) {
  const t = useT(catalogue);
  const f = useFormat();
  const router = useRouter();
  const search = useSearchParams();
  const importId = search.get("import");
  const fromParam = search.get("von");
  const toParam = search.get("bis");
  const from = fromParam && DAY.test(fromParam) ? fromParam : null;
  const to = toParam && DAY.test(toParam) ? toParam : null;
  const ranged = !importId && (from !== null || to !== null);
  const requested = search.get("monat");

  const load = useCallback(() => loadLines(account.id, importId, ranged ? from : null, ranged ? to : null), [account.id, importId, ranged, from, to]);
  const loaded = useLoad(load);
  const switcher = useDetail("konto");
  const lineDetail = useDetail("umsatz");
  const dayHeading = useDayHeading();

  const href = (params: Record<string, string>) => `/accounts/${account.id}?${new URLSearchParams({ tab: "umsaetze", ...params }).toString()}`;

  const switchPanelFor = (allAccounts: Account[]) =>
    switcher.id === "wechseln" ? (
      <DetailPanel title={t.switchTitle} backLabel={t.lines} onClose={switcher.close}>
        <div className="detail-rows">
          {allAccounts
            .filter((other) => other.kind !== "exchange" && other.kind !== "wallet")
            .map((other) => (
              <Link
                key={other.id}
                href={`/accounts/${other.id}?tab=umsaetze`}
                className="detail-row"
                aria-current={other.id === account.id ? "true" : undefined}
              >
                <span className="detail-row-text">
                  <span className="detail-row-title">{other.label}</span>
                  <span className="detail-row-sub">
                    {[other.provider ?? other.bank, other.iban_masked].filter(Boolean).join(" · ")}
                    {other.id === account.id ? ` · ${t.currentAccount}` : ""}
                  </span>
                </span>
              </Link>
            ))}
        </div>
      </DetailPanel>
    ) : null;

  return (
    <Loading state={loaded}>
      {({ lines, asked, owners, allAccounts }) => {
        const owner = ownersText(account.ownership, owners, t);
        const switchPanel = switchPanelFor(allAccounts);
        // The months on offer: each month with a line, and the running one; never a later one.
        const running = currentMonth();
        const months = [...new Set([running, ...lines.map((line) => line.booking_date.slice(0, 7))])]
          .filter((month) => month <= running)
          .sort()
          .reverse();
        const newest = lines.length > 0 ? lines.map((line) => line.booking_date.slice(0, 7)).sort().reverse()[0] : running;
        const fallback = newest && newest <= running ? newest : running;
        const month = requested && MONTH.test(requested) && requested <= running ? requested : fallback;
        const monthMode = !importId && !ranged;
        const shown = monthMode ? lines.filter((line) => line.booking_date.startsWith(month)) : lines;
        const selected = lines.find((line) => line.id === lineDetail.id);
        const linePanel = selected ? (
          <DetailPanel title={selected.counterparty_name ?? selected.booking_text ?? t.noPayee} backLabel={t.lines} onClose={lineDetail.close}>
            <LinePanel
              line={selected}
              account={account}
              asked={asked.get(selected.id)}
              dayHeading={dayHeading}
              onSaved={() => {
                notifyInboxChanged();
                loaded.reload();
              }}
            />
          </DetailPanel>
        ) : null;
        return (
          <DetailLayout panel={switchPanel ?? linePanel}>
            <div className="lines-head">
              <Link href="/accounts" className="lines-back">
                ‹ {t.backToAccounts}
              </Link>
              <div className="page-head">
                <h1>{t.linesOf(account.label)}</h1>
                <span className="spacer" />
                <a {...switcher.linkProps("wechseln")} className="button-link secondary">
                  {t.switchAccount}
                </a>
              </div>
              <p className="muted">{[account.provider ?? account.bank, account.iban_masked, owner].filter(Boolean).join(" · ")}</p>
            </div>
            {tabs}
            {monthMode ? <MonthCard account={account} month={month} owner={owner} /> : null}
            {importId ? (
              <div className="notice notice-info">
                <p>
                  {t.onlyImport} <Link href={href({})}>{t.showAll}</Link>
                </p>
              </div>
            ) : null}
            {ranged ? (
              <div className="notice notice-info">
                <p>
                  {t.rangeOnly(f.date(from), f.date(to))} <Link href={href({})}>{t.showAll}</Link>
                </p>
              </div>
            ) : null}
            <LineList
              key={`${account.id}-${monthMode ? month : "all"}`}
              lines={shown}
              linkProps={lineDetail.linkProps}
              dayHeading={dayHeading}
              emptyText={monthMode ? t.noLines : t.noLinesRange}
              monthSelect={
                monthMode ? (
                  <label className="lines-month">
                    <span className="visually-hidden">{t.month}</span>
                    <select
                      aria-label={t.month}
                      value={month}
                      onChange={(event) => router.replace(href({ monat: event.target.value }), { scroll: false })}
                    >
                      {months.map((value) => (
                        <option key={value} value={value}>
                          {f.month(value)}
                          {value === running ? ` (${t.runningMonth})` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null
              }
            />
          </DetailLayout>
        );
      }}
    </Loading>
  );
}

/** The month card: the four figures of GET /accounts/{id}/month as the API sends them. */
function MonthCard({ account, month, owner }: { account: Account; month: string; owner: string }) {
  const t = useT(catalogue);
  const f = useFormat();
  const load = useCallback(
    () => ok(api.GET("/api/accounts/{account_id}/month", { params: { path: { account_id: account.id }, query: { month } } })),
    [account.id, month],
  );
  const state = useLoad(load);
  const inFuture = (error: unknown) =>
    error instanceof ApiError && error.body !== null && ruleCodes(error.body).includes("account.month_in_future") ? t.monthInFuture : null;
  if (state.error && !state.data) return <ErrorNotice error={state.error} text={inFuture} />;
  const data = state.data;
  if (!data) return <p className="muted">…</p>;
  const name = f.month(month);
  const balanceLabel = data.closing_as_of ? (data.running ? t.balanceShort(f.date(data.closing_as_of)) : t.balanceOn(f.date(data.closing_as_of))) : t.balanceToday;
  const hasTransfers = !/^0+(\.0+)?$/.test(data.transfers_in) || !/^0+(\.0+)?$/.test(data.transfers_out);
  return (
    <section className="month-card card" aria-label={name}>
      <div className="month-figure month-balance">
        <span className="month-label">{balanceLabel}</span>
        <strong className="month-value">{data.closing_balance != null ? f.money(data.closing_balance) : "–"}</strong>
        <span className="month-sub">{data.closing_balance != null ? `${account.label} · ${owner}` : t.noBalance}</span>
      </div>
      <div className="month-figure">
        <span className="month-label">{t.incomingIn(name)}</span>
        <strong className="month-value amount-in">{signed(f, data.incoming)}</strong>
      </div>
      <div className="month-figure">
        <span className="month-label">{t.outgoingIn(name)}</span>
        <strong className="month-value">{outflow(f, data.outgoing)}</strong>
      </div>
      <div className="month-figure month-transfers">
        <span className="month-label">{t.transfers}</span>
        <strong className="month-value month-value-small">
          {t.transfersIn(signed(f, data.transfers_in))} · {t.transfersOut(outflow(f, data.transfers_out))}
        </strong>
        <span className="month-sub">{t.transfersNote}</span>
      </div>
      <p className="month-foot">
        {t.lineCount(data.lines)}
        {hasTransfers ? null : ` · ${t.noTransfers}`}
        {data.open_lines > 0 ? ` · ${t.openInMonth(data.open_lines)}` : ""}
      </p>
    </section>
  );
}

function LineList({
  lines,
  linkProps,
  dayHeading,
  emptyText,
  monthSelect,
}: {
  lines: Transaction[];
  linkProps: ReturnType<typeof useDetail>["linkProps"];
  dayHeading: (iso: string) => string;
  emptyText: string;
  monthSelect: ReactNode;
}) {
  const t = useT(catalogue);
  const f = useFormat();
  const [filter, setFilter] = useState<LineFilter>("all");
  const shown = lines.filter((line) => matches(line, filter));
  const days = new Map<string, Transaction[]>();
  for (const line of shown) days.set(line.booking_date, [...(days.get(line.booking_date) ?? []), line]);
  const filters: { key: LineFilter; label: string }[] = [
    { key: "all", label: t.filterAll },
    { key: "open", label: t.filterOpen },
    { key: "review", label: t.filterReview },
    { key: "booked", label: t.filterBooked },
  ];

  return (
    <>
      <div className="lines-toolbar">
        <div className="lines-chips" role="group" aria-label={t.filterLabel}>
          {filters.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              className={key === filter ? "chip chip-on" : "chip"}
              aria-pressed={key === filter}
              onClick={() => setFilter(key)}
            >
              {label} <span className="chip-count">{f.count(lines.filter((line) => matches(line, key)).length)}</span>
            </button>
          ))}
        </div>
        {monthSelect}
      </div>
      {lines.length === 0 ? (
        <p className="muted">{emptyText}</p>
      ) : shown.length === 0 ? (
        <p className="muted">{t.noLinesFilter}</p>
      ) : (
        <div className="line-days">
          {[...days.entries()].map(([day, dayLines]) => (
            <section key={day} className="line-day" aria-label={dayHeading(day)}>
              <h3 className="line-day-title">{dayHeading(day)}</h3>
              <ul className="line-list">
                {dayLines.map((line) => {
                  const payee = line.counterparty_name ?? line.booking_text ?? t.noPayee;
                  return (
                    <li key={line.id}>
                      <a {...linkProps(line.id)} className={line.allocation_status === "excluded" ? "line-row excluded" : "line-row"}>
                        <span className="line-initial" aria-hidden="true">
                          {(payee.trim()[0] ?? t.initialFallback).toLocaleUpperCase()}
                        </span>
                        <span className="line-text">
                          <span className="line-payee">{payee}</span>
                          <span className="line-purpose">{line.purpose || line.booking_text || ""}</span>
                          <span className="line-category-sub">{categoryText(line, t)}</span>
                        </span>
                        <span className="line-category">{categoryText(line, t)}</span>
                        <span className={isCredit(line.amount) ? "line-amount amount-in" : "line-amount"}>{signed(f, line.amount)}</span>
                        <span className="line-state">
                          <StatusPill line={line} t={t} />
                        </span>
                      </a>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  );
}

function LinePanel({
  line,
  account,
  asked,
  onSaved,
  dayHeading,
}: {
  line: Transaction;
  account: Account;
  asked: { reasoning: string | null; question: string | null } | undefined;
  onSaved: () => void;
  dayHeading: (iso: string) => string;
}) {
  const t = useT(catalogue);
  const f = useFormat();
  // Each save of the booking reads the payment's one-off parts again: a new category can make a
  // part markable or take its button away (phase 10 §7.12 (b)).
  const [saves, setSaves] = useState(0);
  const meta = <p className="muted">{t.linePanelMeta(dayHeading(line.booking_date), account.label)}</p>;
  const facts = (
    <dl className="line-facts">
      <div>
        <dt>{t.payee}</dt>
        <dd>
          {line.counterparty_name ?? t.noPayee}
          {line.counterparty_iban_masked ? <span className="cell-detail"> {line.counterparty_iban_masked}</span> : null}
        </dd>
      </div>
      <div>
        <dt>{t.purpose}</dt>
        <dd>{line.purpose || t.noPurpose}</dd>
      </div>
      {line.booking_text ? (
        <div>
          <dt>{t.bookingText}</dt>
          <dd>{line.booking_text}</dd>
        </div>
      ) : null}
      {line.value_date && line.value_date !== line.booking_date ? (
        <div>
          <dt>{t.valueDate}</dt>
          <dd>{f.date(line.value_date)}</dd>
        </div>
      ) : null}
    </dl>
  );
  switch (line.allocation_status) {
    case "duplicate_open":
      return (
        <div className="stack">
          {meta}
          <p className={isCredit(line.amount) ? "line-panel-amount amount-in" : "line-panel-amount"}>{signed(f, line.amount)}</p>
          {facts}
          <p>{t.duplicateText}</p>
          <Link href={`/accounts/${line.account_id}?tab=doppelbuchungen`} className="button-link">
            {t.duplicateLink}
          </Link>
        </div>
      );
    case "not_reconciled":
      return (
        <div className="stack">
          {meta}
          <p className={isCredit(line.amount) ? "line-panel-amount amount-in" : "line-panel-amount"}>{signed(f, line.amount)}</p>
          {facts}
          <p>{t.notReconciledText}</p>
          <Link href={`/accounts/${line.account_id}?tab=kontoauszuege`} className="button-link">
            {t.notReconciledLink}
          </Link>
        </div>
      );
    case "excluded":
      return (
        <div className="stack">
          {meta}
          <p className="line-panel-amount">{signed(f, line.amount)}</p>
          {facts}
          <p className="muted">{t.excludedText}</p>
        </div>
      );
    default:
      return (
        <div className="stack">
          {meta}
          {line.returned_with ? <p className="muted">{t.returnedText(f.date(line.returned_with.booking_date))}</p> : null}
          <AllocationPanel
            key={line.id}
            transactionId={line.id}
            reasoning={asked?.reasoning}
            question={asked?.question}
            onSaved={() => {
              setSaves((n) => n + 1);
              onSaved();
            }}
          />
          {/* Only a private payment can be one-off; the server names its parts (D307). */}
          {account.purpose === "rental" ? null : <OneOffMark key={`${line.id}-${saves}`} transactionId={line.id} />}
        </div>
      );
  }
}
