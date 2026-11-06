"use client";

// Konto (phase-2.md §7): the account's facts, then tabs as ?tab=… links. Kontoauszüge (upload,
// the imports with their reconciliation status, the closing-balance form for a CSV), Umsätze
// (with a booking-date filter, or the lines of one import), and mögliche Doppelbuchungen
// (the same payment imported in two formats, D22). An owner account takes no statements, so
// it shows its facts only.
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Account } from "@/api/schemas";
import { DuplicatesList, ImportsTable, openDuplicateCount, StatementUpload, TransactionsTable } from "@/components/accounts";
import { formatDate, formatMoney } from "@/components/format";
import { Breadcrumbs, Facts, Loading, TabBar, type Tab } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

type TabKey = "kontoauszuege" | "umsaetze" | "doppelbuchungen";

function isTab(value: string | null): value is TabKey {
  return value === "kontoauszuege" || value === "umsaetze" || value === "doppelbuchungen";
}

export default function AccountPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <AccountDetail />
    </Suspense>
  );
}

/** What the Kontoauszüge and Doppelbuchungen tabs show: the imports, every line of the account
 * (to show the two lines of a possible duplicate), the possible duplicates, and the vault's
 * documents (the file name of each import). */
async function loadStatements(accountId: string) {
  const path = { params: { path: { account_id: accountId } } };
  const [imports, transactions, duplicates, documents] = await Promise.all([
    ok(api.GET("/api/accounts/{account_id}/imports", path)),
    ok(api.GET("/api/accounts/{account_id}/transactions", path)),
    ok(api.GET("/api/possible-duplicates")),
    ok(api.GET("/api/documents")),
  ]);
  return {
    imports,
    importById: new Map(imports.map((imp) => [imp.id, imp])),
    transactionById: new Map(transactions.map((line) => [line.id, line])),
    duplicates,
    documentById: new Map(documents.map((document) => [document.id, document])),
  };
}

function AccountDetail() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const requested = search.get("tab");
  const tab: TabKey = isTab(requested) ? requested : "kontoauszuege";
  const loadAccount = useCallback(() => ok(api.GET("/api/accounts/{account_id}", { params: { path: { account_id: id } } })), [id]);
  const account = useLoad(loadAccount);
  const load = useCallback(() => loadStatements(id), [id]);
  const statements = useLoad(load);
  const openCount = statements.data ? openDuplicateCount(statements.data.duplicates, statements.data.transactionById) : 0;

  const tabs: Tab<TabKey>[] = [
    { key: "kontoauszuege", label: "Kontoauszüge" },
    { key: "umsaetze", label: "Umsätze" },
    { key: "doppelbuchungen", label: openCount > 0 ? `Mögliche Doppelbuchungen (${openCount})` : "Mögliche Doppelbuchungen" },
  ];

  return (
    <section>
      <Loading state={account}>
        {(data) => (
          <>
            <Breadcrumbs items={[{ href: "/accounts", label: "Konten" }, { label: data.label }]} />
            <div className="page-head">
              <h1>{data.label}</h1>
              <span className="spacer" />
              <Link href={`/accounts/${data.id}/edit`} className="button-link secondary">
                Bearbeiten
              </Link>
            </div>
            <AccountFacts account={data} />
            {data.is_owner_account ? (
              <p className="muted">
                Privatkonto eines Eigentümers: Hausbuch erkennt Überweisungen von und an dieses Konto als Privateinlagen und
                -entnahmen. Kontoauszüge werden hier nicht importiert.
              </p>
            ) : (
              <>
                <TabBar tabs={tabs} current={tab} href={(key) => `/accounts/${id}?tab=${key}`} />
                {tab === "kontoauszuege" ? (
                  <div className="stack">
                    <StatementUpload accountId={id} onImported={statements.reload} />
                    {openCount > 0 ? (
                      <div className="notice notice-warn">
                        <p>
                          {openCount === 1 ? "1 mögliche Doppelbuchung wartet" : `${openCount} mögliche Doppelbuchungen warten`} auf
                          eine Entscheidung:{" "}
                          <Link href={`/accounts/${id}?tab=doppelbuchungen`}>prüfen</Link>.
                        </p>
                      </div>
                    ) : null}
                    <h2>Importierte Kontoauszüge</h2>
                    <Loading state={statements}>
                      {(loaded) => (
                        <ImportsTable
                          accountId={id}
                          imports={loaded.imports}
                          documents={loaded.documentById}
                          onChanged={statements.reload}
                        />
                      )}
                    </Loading>
                  </div>
                ) : null}
                {tab === "umsaetze" ? <TransactionsTab accountId={id} /> : null}
                {tab === "doppelbuchungen" ? (
                  <Loading state={statements}>
                    {(loaded) => (
                      <DuplicatesList
                        duplicates={loaded.duplicates}
                        transactions={loaded.transactionById}
                        imports={loaded.importById}
                        onDecided={statements.reload}
                      />
                    )}
                  </Loading>
                ) : null}
              </>
            )}
          </>
        )}
      </Loading>
    </section>
  );
}

function AccountFacts({ account }: { account: Account }) {
  return (
    <Facts
      rows={[
        ["Bank", account.bank],
        ["IBAN", account.iban_masked],
        ["Anfangssaldo", `${formatMoney(account.opening_balance)} am ${formatDate(account.opening_balance_on)}`],
        ["Art", account.is_owner_account ? "Privatkonto Eigentümer" : "Mietkonto"],
      ]}
    />
  );
}

/** Umsätze, newest booking day first, as GET /accounts/{id}/transactions returns them. The
 * filter lives in the URL (?von=…&bis=…, or ?import=… from an import row). */
function TransactionsTab({ accountId }: { accountId: string }) {
  const router = useRouter();
  const search = useSearchParams();
  const from = search.get("von") ?? "";
  const to = search.get("bis") ?? "";
  const importId = search.get("import");
  const [fromInput, setFromInput] = useState(from);
  const [toInput, setToInput] = useState(to);

  const load = useCallback(
    () =>
      ok(
        api.GET("/api/accounts/{account_id}/transactions", {
          params: {
            path: { account_id: accountId },
            query: { booked_from: from || null, booked_to: to || null, import_id: importId },
          },
        }),
      ),
    [accountId, from, to, importId],
  );
  const transactions = useLoad(load);

  function href(params: Record<string, string>): string {
    const query = new URLSearchParams({ tab: "umsaetze", ...params });
    return `/accounts/${accountId}?${query.toString()}`;
  }

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const params: Record<string, string> = {};
    if (fromInput) params.von = fromInput;
    if (toInput) params.bis = toInput;
    router.replace(href(params), { scroll: false });
  }

  function reset() {
    setFromInput("");
    setToInput("");
    router.replace(href({}), { scroll: false });
  }

  return (
    <div className="stack">
      {importId ? (
        <div className="notice notice-info">
          <p>
            Nur die Umsätze, die ein Kontoauszug neu gespeichert hat. <Link href={href({})}>Alle Umsätze anzeigen</Link>
          </p>
        </div>
      ) : (
        <form className="filter-row" onSubmit={apply} noValidate>
          <div className="field">
            <label htmlFor="transactions-from">Buchungstag von</label>
            <input id="transactions-from" type="date" value={fromInput} onChange={(e) => setFromInput(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="transactions-to">bis</label>
            <input id="transactions-to" type="date" value={toInput} onChange={(e) => setToInput(e.target.value)} />
          </div>
          <div className="row">
            <button type="submit">Anzeigen</button>
            {from || to ? (
              <button type="button" className="secondary" onClick={reset}>
                Zurücksetzen
              </button>
            ) : null}
          </div>
        </form>
      )}
      <Loading state={transactions}>
        {(lines) => (
          <>
            <p className="muted">{lines.length === 1 ? "1 Umsatz" : `${lines.length} Umsätze`}</p>
            <TransactionsTable transactions={lines} />
          </>
        )}
      </Loading>
    </div>
  );
}
