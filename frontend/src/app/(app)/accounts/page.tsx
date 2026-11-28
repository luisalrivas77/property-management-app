"use client";

// Konten (phase 10 D279, mockup 21; before it phase-6.md §4.3, mockup 08): one kind of account
// at a time. Chips at the top, one per kind the household holds, each with its count and a
// short label (§7.6 Konten (1): Bank, Karten, Depots, Börsen, Wallets, Darlehen) in an even
// grid, three columns on the phone, one row on the computer; Bank first and the
// default; the kind is in the URL (`/accounts?art=bank|karte|depot|boerse|wallet|darlehen`).
// The page lists that kind's accounts with all of today's columns (label, provider and masked
// IBAN, „Quelle“, the owners or the to-do „Eigentümer festlegen“ (D157, principle 7), „Stand“
// (the newest reconciled import's last booking day and closing balance, as the API stored them;
// a credit card's what is owed), the last import's status) and the kind's total as the net
// worth computes it today, large on its own line with „<N> Konten · heute“ under it (§7.6
// Konten (2); GET /net-worth/overview: `by_account_tab` per tab, Börsen and Wallets each
// their own, the loans' `loan_balances`), never summed here (principle 1). A tapped account
// opens in the detail panel (D267): its balance, the links into Umsätze, Kontoauszüge and Mögliche
// Doppelbuchungen, the Bankabruf card, the owners with „Anteile ändern“ and the statement
// upload; an exchange or a wallet shows its own section (exchange.tsx, wallet.tsx), as its page
// does. A wallet's Stand is the wallet page's verdicts (GET /wallets/{id}/state) and when it was
// last read. A loan's Restschuld is its balance after the last instalment due by today.
// §7.13 (a), D313: a depot's Stand is its whole value today (funds at the newest price, cash
// and money on its way, the Anlagen portfolio card's total), a credit card's what is owed
// with the payment on its way beside it, both from GET /net-worth/overview's
// `today_accounts` and `today_tabs`; an exchange's the account page's own state
// (GET /accounts/{id}/exchange `reconciled`).
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import type {
  Account,
  AccountParts,
  AccountTab,
  ExchangeOverview,
  Loan,
  LoanSchedule,
  Owner,
  StatementImport,
  WalletAddress,
  WalletState,
} from "@/api/schemas";
import { AccountOwnership } from "@/app/(app)/accounts/[id]/AccountOwnership";
import { ownersText } from "@/components/accountLines";
import { ImportStatusPill, StatementUpload } from "@/components/accounts";
import { BankSyncCard } from "@/components/bankSync";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { ExchangeSection, reconciledPill } from "@/components/exchange";
import { todayIso } from "@/components/format";
import { CHAIN } from "@/components/labels";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { WalletSection, walletChains, walletSummary } from "@/components/wallet";
import { STATE_BADGE } from "@/components/wallet-cards";
import { useFormat, useT, type Formatter } from "@/i18n";
import { accounts as catalogue } from "@/i18n/accounts";
import { Icon } from "@/lib/icons";

import "@/components/accountScreens.css";

type Messages = (typeof catalogue)["de"];

/** `addresses` and `state` are set on a wallet's row only (its masked addresses from GET
 * /wallets/{id}, each address's verdict from GET /wallets/{id}/state); `exchange` on an
 * exchange's (GET /accounts/{id}/exchange); `parts` is the account's share of today's net
 * worth (GET /net-worth/overview `today_accounts`), null when it has no line there. */
type Row = {
  account: Account;
  imports: StatementImport[];
  addresses: WalletAddress[] | null;
  state: WalletState | null;
  exchange: ExchangeOverview | null;
  parts: AccountParts | null;
};
type LoanRow = { loan: Loan; schedule: LoanSchedule };

type KindKey = "bank" | "karte" | "depot" | "boerse" | "wallet" | "darlehen";
const KINDS: { key: KindKey; label: keyof Messages; kinds: Account["kind"][] }[] = [
  { key: "bank", label: "kindBank", kinds: ["bank_current", "bank_savings"] },
  { key: "karte", label: "kindCard", kinds: ["credit_card"] },
  { key: "depot", label: "kindDepot", kinds: ["depot"] },
  { key: "boerse", label: "kindExchange", kinds: ["exchange"] },
  { key: "wallet", label: "kindWallet", kinds: ["wallet"] },
  { key: "darlehen", label: "kindLoan", kinds: [] },
];

/** The tab's key in the net worth's `by_account_tab`; the loans read `loan_balances`. */
const TAB_OF_KIND: Record<KindKey, AccountTab | null> = {
  bank: "bank",
  karte: "card",
  depot: "depot",
  boerse: "exchange",
  wallet: "wallet",
  darlehen: null,
};

/** A wallet has no statement imports: its row reads its addresses instead; an exchange its
 * page's overview, whose `reconciled` the row shows. */
async function loadRow(account: Account, parts: AccountParts | null): Promise<Row> {
  const params = { params: { path: { account_id: account.id } } };
  if (account.kind === "wallet") {
    const [page, state] = await Promise.all([
      ok(api.GET("/api/wallets/{account_id}", params)),
      ok(api.GET("/api/wallets/{account_id}/state", params)),
    ]);
    return { account, imports: [], addresses: page.addresses, state, exchange: null, parts };
  }
  if (account.kind === "exchange") {
    const exchange = await ok(api.GET("/api/accounts/{account_id}/exchange", params));
    return { account, imports: [], addresses: null, state: null, exchange, parts };
  }
  const imports = await ok(api.GET("/api/accounts/{account_id}/imports", params));
  return { account, imports, addresses: null, state: null, exchange: null, parts };
}

async function loadAccounts() {
  const [accounts, owners, properties, netWorth] = await Promise.all([
    ok(api.GET("/api/accounts")),
    ok(api.GET("/api/owners")),
    ok(api.GET("/api/properties")),
    ok(api.GET("/api/net-worth/overview")),
  ]);
  // No workspace-wide loan list exists; the loans are read per property, as on Übersicht.
  const loans = (
    await Promise.all(
      properties.map((property) =>
        ok(api.GET("/api/properties/{property_id}/loans", { params: { path: { property_id: property.id } } })),
      ),
    )
  ).flat();
  const [rows, loanRows] = await Promise.all([
    Promise.all(accounts.map((account) => loadRow(account, netWorth.today_accounts?.[account.id] ?? null))),
    Promise.all(
      loans.map(
        async (loan): Promise<LoanRow> => ({
          loan,
          schedule: await ok(api.GET("/api/loans/{loan_id}/schedule", { params: { path: { loan_id: loan.id } } })),
        }),
      ),
    ),
  ]);
  return { rows, owners, loanRows, today: netWorth.today, tabs: netWorth.today_tabs ?? {} };
}

type Loaded = Awaited<ReturnType<typeof loadAccounts>>;

export default function AccountsPage() {
  return (
    <Suspense>
      <Accounts />
    </Suspense>
  );
}

function Accounts() {
  const t = useT(catalogue);
  const loaded = useLoad(loadAccounts);
  return (
    <section>
      <div className="page-head">
        <h1>{t.title}</h1>
        <span className="spacer" />
        <Link href="/accounts/new" className="button-link">
          {t.newAccount}
        </Link>
      </div>
      <Loading state={loaded}>
        {(data) =>
          data.rows.length === 0 && data.loanRows.length === 0 ? (
            <p className="muted">{t.noAccounts}</p>
          ) : (
            <AccountKinds data={data} onChanged={loaded.reload} />
          )
        }
      </Loading>
    </section>
  );
}

function AccountKinds({ data, onChanged }: { data: Loaded; onChanged: () => void }) {
  const t = useT(catalogue);
  const f = useFormat();
  const router = useRouter();
  const search = useSearchParams();
  const detail = useDetail("konto");
  const { rows, owners, loanRows, today, tabs } = data;

  const count = (key: KindKey) =>
    key === "darlehen" ? loanRows.length : rows.filter((row) => KINDS.find((kind) => kind.key === key)?.kinds.includes(row.account.kind)).length;
  const held = KINDS.filter((kind) => count(kind.key) > 0);
  const requested = search.get("art");
  const current = held.find((kind) => kind.key === requested) ?? held[0];
  if (!current) return null;
  const members = rows.filter((row) => current.kinds.includes(row.account.kind));

  const selectedRow = rows.find((row) => row.account.id === detail.id);
  const selectedLoan = loanRows.find((row) => row.loan.id === detail.id);
  const panel = selectedRow ? (
    <DetailPanel title={selectedRow.account.label} backLabel={t.title} onClose={detail.close}>
      <AccountPanel row={selectedRow} owners={owners} onChanged={onChanged} />
    </DetailPanel>
  ) : selectedLoan ? (
    <DetailPanel title={selectedLoan.loan.lender} backLabel={t.title} onClose={detail.close}>
      <LoanPanel row={selectedLoan} />
    </DetailPanel>
  ) : null;

  return (
    <DetailLayout panel={panel}>
      <nav className="lines-chips account-kinds" aria-label={t.kindsLabel}>
        {held.map((kind) => (
          <Link
            key={kind.key}
            href={`/accounts?art=${kind.key}`}
            className="chip"
            aria-current={kind.key === current.key ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault();
              router.replace(`/accounts?art=${kind.key}`, { scroll: false });
            }}
          >
            {t[kind.label] as string} <span className="chip-count">{f.count(count(kind.key))}</span>
          </Link>
        ))}
      </nav>
      <KindTotal kind={current.key} rows={members} loans={loanRows} today={today} tabs={tabs} />
      {current.key === "darlehen" ? (
        <LoanTable rows={loanRows} linkProps={detail.linkProps} />
      ) : (
        <AccountTable rows={members} owners={owners} linkProps={detail.linkProps} />
      )}
      {current.key === "depot" ? <p className="muted">{t.depotNote}</p> : null}
      {current.key === "boerse" || current.key === "wallet" ? <p className="muted">{t.cryptoNote}</p> : null}
    </DetailLayout>
  );
}

/** The kind's total as GET /net-worth/overview states it today (`by_account_tab`, the loans'
 * `loan_balances`), large, and its count under it. No total when no account of the tab has a
 * line in the net worth yet. The cards' tab shows what is owed instead (`today_tabs`' balance
 * without the money on its way, §7.13 (a)): their net worth figure is 0 once a payment on its
 * way meets the debt, which read as nothing owed; the payment on its way under it. */
function KindTotal({
  kind,
  rows,
  loans,
  today,
  tabs,
}: {
  kind: KindKey;
  rows: Row[];
  loans: LoanRow[];
  today: Loaded["today"];
  tabs: Loaded["tabs"];
}) {
  const t = useT(catalogue);
  const f = useFormat();
  const tab = TAB_OF_KIND[kind];
  const value = tab === null ? today.loan_balances : today.by_account_tab?.[tab];
  const count = kind === "darlehen" ? t.loanCount(loans.length) : t.accountCount(rows.length);
  const card = tab === "card" ? tabs[tab] : undefined;
  if (card) {
    return (
      <div className="kind-summary">
        <p className="kind-total">{f.money(unsigned(card.balance_eur))}</p>
        <p className="muted">{t.countToday(`${count} · ${isNegative(card.balance_eur) ? t.owedLabel : t.creditLabel}`)}</p>
        {isPositive(card.in_transit_eur) ? <p className="muted">{t.paymentInTransit(f.money(card.in_transit_eur))}</p> : null}
      </div>
    );
  }
  return (
    <div className="kind-summary">
      {value != null ? <p className="kind-total">{f.money(value)}</p> : null}
      <p className="muted">{t.countToday(count)}</p>
    </div>
  );
}

/** Sign tests and the sign dropped on the server's decimal strings; display only, no sum. */
function isNegative(value: string): boolean {
  return value.startsWith("-") && !/^-0*\.?0*$/.test(value);
}
function isPositive(value: string): boolean {
  return !value.startsWith("-") && !/^0*\.?0*$/.test(value);
}
function unsigned(value: string): string {
  return value.startsWith("-") ? value.slice(1) : value;
}

function AccountTable({
  rows,
  owners,
  linkProps,
}: {
  rows: Row[];
  owners: Owner[];
  linkProps: ReturnType<typeof useDetail>["linkProps"];
}) {
  const t = useT(catalogue);
  return (
    <ul className="account-list">
      {rows.map((row) => {
        const { account, addresses, state } = row;
        return (
        <li key={account.id}>
          <a {...linkProps(account.id)} className="account-row">
            <span className="account-main">
              <span className="account-name">
                {account.label}
                {account.purpose === "rental" ? <span className="tag">{t.rentalTag}</span> : null}
              </span>
              <span className="account-sub">
                {addresses !== null
                  ? [account.provider ?? account.bank, walletSummary(addresses)].filter(Boolean).join(" · ")
                  : [
                      account.provider ?? account.bank,
                      account.iban_masked,
                      account.card_last4 ? t.cardEnds(account.card_last4) : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
              </span>
              {addresses !== null && walletChains(addresses).length > 0 ? (
                <span className="row">
                  {walletChains(addresses).map((chain) => (
                    <span key={chain} className="tag">
                      {CHAIN[chain]}
                    </span>
                  ))}
                </span>
              ) : null}
            </span>
            <span className="account-cell account-source">
              <span className="account-cell-label">{t.colSource}</span>
              {addresses !== null ? (state && activeStates(state).length > 0 ? t.sourceChain : "–") : account.source === "fints" ? t.sourceSync : t.sourceExport}
            </span>
            <span className="account-cell account-owners">
              <span className="account-cell-label">{t.colOwners}</span>
              {account.ownership_missing ? (
                <span className="badge badge-warn">{t.setOwners}</span>
              ) : (
                <span className="badge">{ownersText(account.ownership, owners, t)}</span>
              )}
            </span>
            <span className="account-cell account-balance">
              <span className="account-cell-label">{t.colBalance}</span>
              <RowStand row={row} />
            </span>
            <span className="account-cell account-import">
              <span className="account-cell-label">{t.colLastImport}</span>
              <LastImport imports={row.imports} />
            </span>
          </a>
        </li>
        );
      })}
    </ul>
  );
}

/** A row's „Stand“ by its kind (§7.13 (a)): a wallet its verdicts, an exchange its value today
 * and the account page's state, a depot its whole value today, a credit card what is owed with
 * the payment on its way, any other account its newest reconciled statement. */
function RowStand({ row }: { row: Row }) {
  const t = useT(catalogue);
  const f = useFormat();
  const { account, parts } = row;
  if (row.addresses !== null && row.state !== null) return <WalletStand state={row.state} f={f} t={t} />;
  if (account.kind === "exchange") {
    return (
      <span className="account-stand">
        {parts ? <strong>{f.money(parts.value_eur)}</strong> : null}
        <span className="account-sub">{reconciledPill(row.exchange?.reconciled)}</span>
      </span>
    );
  }
  if (account.kind === "depot" && parts) {
    return (
      <span className="account-stand">
        <strong>{f.money(parts.value_eur)}</strong>
        <span className="account-sub">{t.depotToday}</span>
      </span>
    );
  }
  if (account.kind === "credit_card" && parts) return <CardStand parts={parts} />;
  return <Stand imports={row.imports} owed={account.kind === "credit_card"} />;
}

/** What a card owes (its balance without the money on its way), „offen“ or „Guthaben“, and the
 * payment on its way when there is one. */
function CardStand({ parts }: { parts: AccountParts }) {
  const t = useT(catalogue);
  const f = useFormat();
  const owed = isNegative(parts.balance_eur);
  return (
    <span className="account-stand">
      <strong>{f.money(unsigned(parts.balance_eur))}</strong>
      <span className="account-sub">
        {isPositive(parts.in_transit_eur) ? t.owedWithTransit(owed, f.money(parts.in_transit_eur)) : owed ? t.owedLabel : t.creditLabel}
      </span>
    </span>
  );
}

function activeStates(state: WalletState) {
  return (state.statuses ?? []).filter((status) => status.state !== "retired");
}

/** One badge per distinct state of the wallet's active addresses and the newest finished run. */
function WalletStand({ state, f, t }: { state: WalletState; f: Formatter; t: Messages }) {
  const active = activeStates(state);
  const states = [...new Set(active.map((status) => status.state))];
  const finished = active.map((status) => status.finished_at).filter((at): at is string => !!at);
  const lastRead = finished.length > 0 ? finished.reduce((a, b) => (new Date(a) > new Date(b) ? a : b)) : null;
  if (states.length === 0) return <>–</>;
  return (
    <span className="account-stand">
      <span>
        {states.map((value, index) => (
          <span key={value}>
            {index > 0 ? " " : null}
            <span className={STATE_BADGE[value].className}>{STATE_BADGE[value].label}</span>
          </span>
        ))}
      </span>
      {lastRead ? <span className="account-sub">{t.lastRead(f.dateTime(lastRead))}</span> : null}
    </span>
  );
}

/** The newest reconciled import's closing balance as stored (declared, which reconciliation
 * found equal to the computed one), on its last booking day; „noch nicht abgestimmt“ without
 * one. On a credit card (`owed`) a negative closing shows as the amount „offen“, a positive one
 * as „Guthaben“ (D176 (2)). */
function newestReconciled(imports: StatementImport[]) {
  return imports
    .filter((imp) => imp.status === "reconciled" && imp.last_booking_on != null)
    .sort((a, b) => (b.last_booking_on ?? "").localeCompare(a.last_booking_on ?? ""))[0];
}

function Stand({ imports, owed = false }: { imports: StatementImport[]; owed?: boolean }) {
  const t = useT(catalogue);
  const f = useFormat();
  const reconciled = newestReconciled(imports);
  if (!reconciled) return <span className="muted">{t.notReconciledYet}</span>;
  const closing = reconciled.declared_closing ?? reconciled.computed_closing;
  if (closing == null) return <span className="muted">{t.notReconciledYet}</span>;
  const day = f.date(reconciled.last_booking_on);
  if (owed) {
    const credit = !closing.startsWith("-");
    return (
      <span className="account-stand">
        <strong>{f.money(credit ? closing : closing.slice(1))}</strong>
        <span className="account-sub">{credit ? t.creditOn(day) : t.owedOn(day)}</span>
      </span>
    );
  }
  return (
    <span className="account-stand">
      <strong>{f.money(closing)}</strong>
      <span className="account-sub">{t.onDay(day)}</span>
    </span>
  );
}

/** The status of the import created last. */
function LastImport({ imports }: { imports: StatementImport[] }) {
  const last = [...imports].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  return last ? <ImportStatusPill statementImport={last} /> : <>–</>;
}

/** A loan's balance after the last instalment due by today, read from the schedule as the API
 * computed it; before the first instalment the amount lent. */
function loanBalance(row: LoanRow, t: Messages, f: Formatter): { value: string; sub: string } {
  const today = todayIso();
  const due = row.schedule.entries.filter((entry) => entry.due_on <= today);
  const last = due[due.length - 1];
  return last
    ? { value: f.money(last.closing_balance), sub: t.afterInstalment(f.date(last.due_on)) }
    : { value: f.money(row.loan.terms.principal), sub: t.noInstalmentYet };
}

function LoanTable({ rows, linkProps }: { rows: LoanRow[]; linkProps: ReturnType<typeof useDetail>["linkProps"] }) {
  const t = useT(catalogue);
  const f = useFormat();
  return (
    <ul className="account-list">
      {rows.map((row) => {
        const balance = loanBalance(row, t, f);
        return (
          <li key={row.loan.id}>
            <a {...linkProps(row.loan.id)} className="account-row account-row-loan">
              <span className="account-main">
                <span className="account-name">{row.loan.lender}</span>
                {row.loan.contract_number ? <span className="account-sub">{row.loan.contract_number}</span> : null}
              </span>
              <span className="account-cell account-balance">
                <span className="account-cell-label">{t.colOwed}</span>
                <span className="account-stand">
                  <strong>{balance.value}</strong>
                  <span className="account-sub">{balance.sub}</span>
                </span>
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}

function kindName(account: Account, t: Messages): string {
  switch (account.kind) {
    case "bank_current":
      return t.kindOneBank;
    case "bank_savings":
      return t.kindOneSavings;
    case "credit_card":
      return t.kindOneCard;
    case "depot":
      return t.kindOneDepot;
    case "exchange":
      return t.kindOneExchange;
    default:
      return t.kindOneWallet;
  }
}

function PanelLink({ href, title, sub }: { href: string; title: string; sub?: ReactNode }) {
  return (
    <Link href={href} className="detail-row">
      <span className="detail-row-text">
        <span className="detail-row-title">{title}</span>
        {sub ? <span className="detail-row-sub">{sub}</span> : null}
      </span>
      <Icon name="chevronRight" size={20} />
    </Link>
  );
}

/** A tapped account (mockup 21): what it is, its balance, the ways into its lines and
 * statements, its bank sync, its owners and its upload; an exchange or a wallet its own
 * section, as on its page. */
function AccountPanel({ row, owners, onChanged }: { row: Row; owners: Owner[]; onChanged: () => void }) {
  const t = useT(catalogue);
  const { account, imports } = row;
  const base = `/accounts/${account.id}`;
  const crypto = account.kind === "exchange" || account.kind === "wallet";
  const owner = account.ownership_missing ? t.setOwners : ownersText(account.ownership, owners, t);
  return (
    <div className="stack">
      <p className="muted">
        {kindName(account, t)} · {owner}
      </p>
      {crypto ? null : (
        <div className="account-panel-balance">
          <span className="muted">{t.panelBalance}</span>
          <RowStand row={row} />
        </div>
      )}
      {account.kind === "depot" && row.parts ? <DepotParts parts={row.parts} /> : null}
      {account.kind === "exchange" ? <ExchangeSection accountId={account.id} /> : null}
      {account.kind === "wallet" ? <WalletSection accountId={account.id} /> : null}
      <div className="detail-rows">
        {crypto ? null : (
          <>
            <PanelLink href={`${base}?tab=umsaetze`} title={t.panelLines} sub={t.panelLinesSub} />
            <PanelLink
              href={`${base}?tab=kontoauszuege`}
             
              title={t.panelStatements}
              sub={imports.length > 0 ? t.panelStatementsSub(imports.length) : t.panelNoStatements}
            />
            <PanelLink href={`${base}?tab=doppelbuchungen`} title={t.panelDuplicates} sub={t.panelDuplicatesSub} />
          </>
        )}
        <PanelLink href={`${base}/edit`} title={t.panelEdit} sub={t.panelEditSub} />
        <PanelLink href={base} title={t.panelOpenPage} />
      </div>
      {account.kind === "bank_current" || account.kind === "bank_savings" ? <BankSyncCard account={account} onImported={onChanged} /> : null}
      <AccountOwnership account={account} onSaved={onChanged} />
      {crypto ? null : (
        <div className="card stack">
          <h2>{t.panelRead}</h2>
          <StatementUpload accountId={account.id} card={account.kind === "credit_card"} depot={account.kind === "depot"} onImported={onChanged} />
        </div>
      )}
    </div>
  );
}

/** A depot's value today in its three parts, each as the net worth counts it. */
function DepotParts({ parts }: { parts: AccountParts }) {
  const t = useT(catalogue);
  const f = useFormat();
  const items: [string, string][] = [
    [t.partFunds, parts.positions_eur],
    [t.partCash, parts.balance_eur],
    [t.partInTransit, parts.in_transit_eur],
  ];
  return (
    <div className="detail-rows">
      {items.map(([label, value]) => (
        <div key={label} className="detail-row">
          <span className="detail-row-text">
            <span className="detail-row-title">{label}</span>
          </span>
          <span className="money">{f.money(value)}</span>
        </div>
      ))}
    </div>
  );
}

function LoanPanel({ row }: { row: LoanRow }) {
  const t = useT(catalogue);
  const f = useFormat();
  const balance = loanBalance(row, t, f);
  return (
    <div className="stack">
      <p className="muted">
        {t.kindOneLoan}
        {row.loan.contract_number ? ` · ${row.loan.contract_number}` : ""}
      </p>
      <div className="account-panel-balance">
        <span className="muted">{t.colOwed}</span>
        <span className="account-stand">
          <strong>{balance.value}</strong>
          <span className="account-sub">{balance.sub}</span>
        </span>
      </div>
      <div className="detail-rows">
        <PanelLink href={`/loans/${row.loan.id}`} title={t.panelLoanPage} sub={t.panelLoanSub} />
      </div>
    </div>
  );
}
