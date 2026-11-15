"use client";

// Konten (phase-6.md §4.3, mockup 08): every account of the household grouped by kind (Bank,
// then Depot and Krypto once steps 4 and 5 add them), and the Verbindlichkeiten (the property
// loans with their balance from the schedule, read-only). Per account: label, provider and
// masked IBAN, „Quelle“ (Bankabruf for a FinTS account, else Export), the owner chip or the to-do
// „Eigentümer festlegen“ (D157, principle 7), the chip „Mietobjekt“ on the rental account,
// „Stand“ (the newest reconciled import's last booking day and closing balance, as the API
// stored them) and the status of the last import. No total is summed here (principle 1; the
// net worth is step 6's, computed by the API). The box „Überweisungen zwischen eigenen
// Konten“ is step 2's, wallets phase 7's. A credit card (D176) is listed under Bank with the
// chip „Kreditkarte“ and its card's last four digits; its Stand is what is owed on the card
// (the statement's Neuer Saldo, negative when owed, shown as a positive amount „offen“).
// A wallet (phase 7 step 1, §14) is listed under Krypto after the exchanges, with its
// provider, its chains as pills and „N Chains · M Adressen“ (active addresses only); its Quelle
// and Stand read „noch nicht abgerufen“ until step 2 reads the chains.
import Link from "next/link";

import { api, ok } from "@/api/client";
import type { Account, Loan, LoanSchedule, Owner, StatementImport, WalletAddress } from "@/api/schemas";
import { ImportStatusPill, ownerChip } from "@/components/accounts";
import { formatDate, formatMoney, todayIso } from "@/components/format";
import { CHAIN } from "@/components/labels";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { walletChains, walletSummary } from "@/components/wallet";

/** `addresses` is set on a wallet's row only (its masked addresses, from GET /wallets/{id}). */
type Row = { account: Account; imports: StatementImport[]; addresses: WalletAddress[] | null };
type LoanRow = { loan: Loan; schedule: LoanSchedule };

const GROUPS: { label: string; kinds: Account["kind"][] }[] = [
  { label: "Bank", kinds: ["bank_current", "bank_savings", "credit_card"] },
  { label: "Depot", kinds: ["depot"] },
  { label: "Krypto", kinds: ["exchange", "wallet"] },
];

/** A wallet has no statement imports: its row reads its addresses instead. */
async function loadRow(account: Account): Promise<Row> {
  if (account.kind === "wallet") {
    const page = await ok(api.GET("/api/wallets/{account_id}", { params: { path: { account_id: account.id } } }));
    return { account, imports: [], addresses: page.addresses };
  }
  const imports = await ok(api.GET("/api/accounts/{account_id}/imports", { params: { path: { account_id: account.id } } }));
  return { account, imports, addresses: null };
}

async function loadAccounts() {
  const [accounts, owners, properties] = await Promise.all([
    ok(api.GET("/api/accounts")),
    ok(api.GET("/api/owners")),
    ok(api.GET("/api/properties")),
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
    Promise.all(accounts.map(loadRow)),
    Promise.all(
      loans.map(
        async (loan): Promise<LoanRow> => ({
          loan,
          schedule: await ok(api.GET("/api/loans/{loan_id}/schedule", { params: { path: { loan_id: loan.id } } })),
        }),
      ),
    ),
  ]);
  return { rows, owners, loanRows };
}

export default function AccountsPage() {
  const loaded = useLoad(loadAccounts);
  return (
    <section>
      <div className="page-head">
        <h1>Konten</h1>
        <span className="spacer" />
        <Link href="/accounts/new" className="button-link">
          Konto anlegen
        </Link>
      </div>
      <Loading state={loaded}>
        {({ rows, owners, loanRows }) =>
          rows.length === 0 ? (
            <p className="muted">Noch keine Konten. Zuerst das Mietkonto anlegen, dann seine Kontoauszüge importieren.</p>
          ) : (
            <div className="stack">
              {GROUPS.map((group) => {
                // Within Krypto the exchanges come first, then the wallets (§14); otherwise the API's order.
                const rank = (row: Row) => (row.account.kind === "wallet" ? 1 : 0);
                const members = rows.filter((row) => group.kinds.includes(row.account.kind)).sort((a, b) => rank(a) - rank(b));
                return members.length === 0 ? null : (
                  <AccountGroup key={group.label} label={group.label} rows={members} owners={owners} />
                );
              })}
              {loanRows.length > 0 ? <LoanGroup rows={loanRows} /> : null}
            </div>
          )
        }
      </Loading>
    </section>
  );
}

function AccountGroup({ label, rows, owners }: { label: string; rows: Row[]; owners: Owner[] }) {
  return (
    <div className="stack">
      <h2>{label}</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Konto</th>
              <th>Quelle</th>
              <th>Eigentümer</th>
              <th className="num">Stand</th>
              <th>Letzter Import</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ account, imports, addresses }) =>
              addresses !== null ? (
                <WalletRow key={account.id} account={account} addresses={addresses} owners={owners} />
              ) : (
              <tr key={account.id}>
                <td>
                  <Link href={`/accounts/${account.id}`}>{account.label}</Link>
                  {account.purpose === "rental" ? (
                    <>
                      {" "}
                      <span className="tag">Mietobjekt</span>
                    </>
                  ) : null}
                  {account.kind === "credit_card" ? (
                    <>
                      {" "}
                      <span className="tag">Kreditkarte</span>
                    </>
                  ) : null}
                  <p className="cell-detail">
                    {account.provider ?? account.bank}
                    {account.iban_masked ? ` · ${account.iban_masked}` : ""}
                    {account.card_last4 ? ` · Karte endet auf ${account.card_last4}` : ""}
                  </p>
                </td>
                <td>{account.source === "fints" ? "Bankabruf" : "Export"}</td>
                <td>
                  {account.ownership_missing ? (
                    <Link href={`/accounts/${account.id}`}>Eigentümer festlegen</Link>
                  ) : (
                    <span className="badge">{ownerChip(account.ownership ?? [], owners)}</span>
                  )}
                </td>
                <Stand imports={imports} owed={account.kind === "credit_card"} />
                <LastImport imports={imports} />
              </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** One wallet: its provider, its chains as pills and how many active addresses it reads. No
 * address is shown here, not even masked. Quelle and Stand stay „noch nicht abgerufen“ until
 * step 2 syncs the chains. */
function WalletRow({ account, addresses, owners }: { account: Account; addresses: WalletAddress[]; owners: Owner[] }) {
  return (
    <tr>
      <td>
        <Link href={`/accounts/${account.id}`}>{account.label}</Link> <span className="tag">Wallet</span>
        <p className="cell-detail">
          {account.provider ?? account.bank} · {walletSummary(addresses)}
        </p>
        {walletChains(addresses).length > 0 ? (
          <p className="row">
            {walletChains(addresses).map((chain) => (
              <span key={chain} className="tag">
                {CHAIN[chain]}
              </span>
            ))}
          </p>
        ) : null}
      </td>
      <td>
        <span className="muted">noch nicht abgerufen</span>
      </td>
      <td>
        {account.ownership_missing ? (
          <Link href={`/accounts/${account.id}`}>Eigentümer festlegen</Link>
        ) : (
          <span className="badge">{ownerChip(account.ownership ?? [], owners)}</span>
        )}
      </td>
      <td className="num">
        <span className="muted">noch nicht abgerufen</span>
      </td>
      <td>–</td>
    </tr>
  );
}

/** The newest reconciled import's closing balance as stored (declared, which reconciliation found
 * equal to the computed one), on its last booking day; „noch nicht abgestimmt“ without one. On a
 * credit card (`owed`) the balance is a debt: a negative closing shows as the amount „offen“, a
 * positive one as „Guthaben“ (D176 (2)). */
function Stand({ imports, owed = false }: { imports: StatementImport[]; owed?: boolean }) {
  const reconciled = imports
    .filter((imp) => imp.status === "reconciled" && imp.last_booking_on != null)
    .sort((a, b) => (b.last_booking_on ?? "").localeCompare(a.last_booking_on ?? ""))[0];
  if (!reconciled) {
    return (
      <td className="num">
        <span className="muted">noch nicht abgestimmt</span>
      </td>
    );
  }
  const closing = reconciled.declared_closing ?? reconciled.computed_closing;
  if (owed && closing != null) {
    const credit = !closing.startsWith("-");
    return (
      <td className="num">
        {formatMoney(credit ? closing : closing.slice(1))}
        <p className="cell-detail">
          {credit ? "Guthaben" : "offen"} am {formatDate(reconciled.last_booking_on)}
        </p>
      </td>
    );
  }
  return (
    <td className="num">
      {formatMoney(closing)}
      <p className="cell-detail">am {formatDate(reconciled.last_booking_on)}</p>
    </td>
  );
}

/** The status of the import created last. */
function LastImport({ imports }: { imports: StatementImport[] }) {
  const last = [...imports].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  return <td>{last ? <ImportStatusPill statementImport={last} /> : "–"}</td>;
}

/** The property loans with their balance after the last instalment due by today, read from the
 * schedule as the API computed it; before the first instalment the amount lent. */
function LoanGroup({ rows }: { rows: LoanRow[] }) {
  const today = todayIso();
  return (
    <div className="stack">
      <h2>Verbindlichkeiten</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Darlehen</th>
              <th className="num">Restschuld</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ loan, schedule }) => {
              const due = schedule.entries.filter((entry) => entry.due_on <= today);
              const last = due[due.length - 1];
              return (
                <tr key={loan.id}>
                  <td>
                    <Link href={`/loans/${loan.id}`}>{loan.lender}</Link>
                    {loan.contract_number ? <p className="cell-detail">{loan.contract_number}</p> : null}
                  </td>
                  <td className="num">
                    {formatMoney(last ? last.closing_balance : loan.terms.principal)}
                    <p className="cell-detail">
                      {last ? `nach der Rate vom ${formatDate(last.due_on)}` : "Darlehensbetrag, noch keine Rate fällig"}
                    </p>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
