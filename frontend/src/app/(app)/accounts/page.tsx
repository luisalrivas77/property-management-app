"use client";

// Konten (phase-6.md §4.3, mockup 08): every account of the household grouped by kind (Bank,
// then Depot and Krypto once steps 4 and 5 add them), and the Verbindlichkeiten (the property
// loans with their balance from the schedule, read-only). Per account: label, provider and
// masked IBAN, „Quelle“ (Bankabruf for a FinTS account, else Export), the owner chip or the to-do
// „Eigentümer festlegen“ (D157, principle 7), the chip „Mietobjekt“ on the rental account,
// „Stand“ (the newest reconciled import's last booking day and closing balance, as the API
// stored them) and the status of the last import. No total is summed here (principle 1; the
// net worth is step 6's, computed by the API). The box „Überweisungen zwischen eigenen
// Konten“ is step 2's, wallets phase 7's.
import Link from "next/link";

import { api, ok } from "@/api/client";
import type { Account, Loan, LoanSchedule, Owner, StatementImport } from "@/api/schemas";
import { ImportStatusPill, ownerChip } from "@/components/accounts";
import { formatDate, formatMoney, todayIso } from "@/components/format";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

type Row = { account: Account; imports: StatementImport[] };
type LoanRow = { loan: Loan; schedule: LoanSchedule };

const GROUPS: { label: string; kinds: Account["kind"][] }[] = [
  { label: "Bank", kinds: ["bank_current", "bank_savings"] },
  { label: "Depot", kinds: ["depot"] },
  { label: "Krypto", kinds: ["exchange"] },
];

async function loadAccounts() {
  const [accounts, owners, loans] = await Promise.all([
    ok(api.GET("/api/accounts")),
    ok(api.GET("/api/owners")),
    ok(api.GET("/api/loans")),
  ]);
  const [rows, loanRows] = await Promise.all([
    Promise.all(
      accounts.map(
        async (account): Promise<Row> => ({
          account,
          imports: await ok(api.GET("/api/accounts/{account_id}/imports", { params: { path: { account_id: account.id } } })),
        }),
      ),
    ),
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
                const members = rows.filter((row) => group.kinds.includes(row.account.kind));
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
            {rows.map(({ account, imports }) => (
              <tr key={account.id}>
                <td>
                  <Link href={`/accounts/${account.id}`}>{account.label}</Link>
                  {account.purpose === "rental" ? (
                    <>
                      {" "}
                      <span className="tag">Mietobjekt</span>
                    </>
                  ) : null}
                  <p className="cell-detail">
                    {account.provider ?? account.bank}
                    {account.iban_masked ? ` · ${account.iban_masked}` : ""}
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
                <Stand imports={imports} />
                <LastImport imports={imports} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** The newest reconciled import's closing balance as stored (declared, which reconciliation found
 * equal to the computed one), on its last booking day; „noch nicht abgestimmt“ without one. */
function Stand({ imports }: { imports: StatementImport[] }) {
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
  return (
    <td className="num">
      {formatMoney(reconciled.declared_closing ?? reconciled.computed_closing)}
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
