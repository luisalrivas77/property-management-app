"use client";

// Konten (phase-2.md §7): the rental account(s) and the owners' private accounts, as the API
// lists them (rental first, each by label), and "Konto anlegen". The IBAN is only ever shown
// masked; the API never returns it in full.
import Link from "next/link";

import { api, ok } from "@/api/client";
import { formatDate, formatMoney } from "@/components/format";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const loadAccounts = () => ok(api.GET("/api/accounts"));

export default function AccountsPage() {
  const accounts = useLoad(loadAccounts);
  return (
    <section>
      <div className="page-head">
        <h1>Konten</h1>
        <span className="spacer" />
        <Link href="/accounts/new" className="button-link">
          Konto anlegen
        </Link>
      </div>
      <Loading state={accounts}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch keine Konten. Zuerst das Mietkonto anlegen, dann seine Kontoauszüge importieren.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Bezeichnung</th>
                    <th>Bank</th>
                    <th>IBAN</th>
                    <th className="num">Anfangssaldo</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((account) => (
                    <tr key={account.id}>
                      <td>
                        <Link href={`/accounts/${account.id}`}>{account.label}</Link>
                        {account.purpose === "private" ? (
                          <>
                            {" "}
                            <span className="tag">Privatkonto Eigentümer</span>
                          </>
                        ) : null}
                      </td>
                      <td>{account.bank}</td>
                      <td>{account.iban_masked ?? "–"}</td>
                      <td className="num">
                        {formatMoney(account.opening_balance)}
                        <p className="cell-detail">am {formatDate(account.opening_balance_on)}</p>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
    </section>
  );
}
