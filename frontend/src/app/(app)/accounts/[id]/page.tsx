"use client";

// Konto (phase-2.md §7): the account's facts, then tabs as ?tab=… links. Kontoauszüge (upload,
// the imports with their reconciliation status, the closing-balance form for a CSV), Umsätze
// (with a booking-date filter, or the lines of one import), and mögliche Doppelbuchungen
// (the same payment imported in two formats, D22). Since phase 6 (D156) a private account
// imports like the rental one; its owners (D157) are a card under the facts, with the to-do
// „Eigentümer festlegen“ while none is set. Kontoauszüge starts, on the rental account, with
// the Bankabruf card (phase-5.md §11): the FinTS connection and its runs; a run that imports
// reloads the imports below. The private accounts' FinTS connections come with phase 6 step 3.
// A credit card (D176) takes its monthly PDF statements instead, shows its card's last four
// digits and masked customer number, and offers the payment IBAN its latest statement prints
// for confirmation (the card „Zahlungs-IBAN bestätigen“, principle 3).
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Account } from "@/api/schemas";
import { DuplicatesList, ImportsTable, openDuplicateCount, StatementUpload, TransactionsTable } from "@/components/accounts";
import { BankSyncCard } from "@/components/bankSync";
import { formatDate, formatMoney } from "@/components/format";
import { ACCOUNT_KIND, ACCOUNT_PURPOSE } from "@/components/labels";
import { Breadcrumbs, Facts, Loading, TabBar, type Tab } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { AccountOwnership } from "./AccountOwnership";

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
            {data.purpose === "private" ? (
              <p className="muted">
                Privatkonto: seine Umsätze bekommen Haushaltskategorien und zählen nie in die Anlage V. Überweisungen zwischen
                diesem Konto und dem Mietkonto sind Privateinlagen und -entnahmen.
              </p>
            ) : null}
            {data.kind === "credit_card" ? <PaymentIbanCard account={data} onSaved={account.reload} /> : null}
            <AccountOwnership account={data} onSaved={account.reload} />
            <TabBar tabs={tabs} current={tab} href={(key) => `/accounts/${id}?tab=${key}`} />
            {tab === "kontoauszuege" ? (
              <div className="stack">
                {/* Any bank account can fetch over FinTS; a private one only after its history is imported (D163, D173). */}
                {data.kind === "bank_current" || data.kind === "bank_savings" ? (
                  <BankSyncCard account={data} onImported={statements.reload} />
                ) : null}
                <StatementUpload
                  accountId={id}
                  card={data.kind === "credit_card"}
                  onImported={() => {
                    statements.reload();
                    if (data.kind === "credit_card") account.reload();
                  }}
                />
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
      </Loading>
    </section>
  );
}

function AccountFacts({ account }: { account: Account }) {
  if (account.kind === "credit_card") {
    return (
      <Facts
        rows={[
          ["Bank", account.provider ?? account.bank],
          ["Karte", account.card_last4 ? `endet auf ${account.card_last4}` : "kommt mit der ersten Abrechnung"],
          ["Kundennummer", account.external_ref_masked ?? "kommt mit der ersten Abrechnung"],
          ["Zahlungs-IBAN", account.payment_iban_masked ?? "–"],
          ["Anfangssaldo", `${formatMoney(account.opening_balance)} am ${formatDate(account.opening_balance_on)}`],
          ["Kontoart", ACCOUNT_KIND[account.kind]],
        ]}
      />
    );
  }
  return (
    <Facts
      rows={[
        ["Bank", account.provider ?? account.bank],
        ["IBAN", account.iban_masked ?? "–"],
        ["Anfangssaldo", `${formatMoney(account.opening_balance)} am ${formatDate(account.opening_balance_on)}`],
        ["Kontoart", ACCOUNT_KIND[account.kind]],
        ["Wofür", ACCOUNT_PURPOSE[account.purpose]],
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

/** D176 (4): the IBAN the latest card statement prints for payments to the card, masked, until a
 * person confirms it. Confirming reads it again from that statement (never from this page). */
function PaymentIbanCard({ account, onSaved }: { account: Account; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const proposal = account.payment_iban_proposal;
  if (!proposal) return null;

  async function confirm() {
    if (!proposal) return;
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/accounts/{account_id}/payment-iban", {
          params: { path: { account_id: account.id } },
          body: { import_id: proposal.import_id },
        }),
      );
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card stack">
      <h2>Zahlungs-IBAN bestätigen</h2>
      <p>
        Die Abrechnung nennt unter „Angaben bei Einzahlungen“ die IBAN <strong>{proposal.payment_iban_masked}</strong>, auf die
        Sie die Karte bezahlen.
        {account.payment_iban_masked
          ? ` Bisher bestätigt ist ${account.payment_iban_masked}.`
          : " Stimmt sie, bestätigen Sie sie hier: Hausbuch bucht dann die Zahlung vom Girokonto an diese IBAN als Umbuchung zwischen eigenen Konten und verknüpft sie mit der „Bezahlung“ auf der Abrechnung."}
      </p>
      <div className="row">
        <button type="button" onClick={() => void confirm()} disabled={busy}>
          {busy ? "Speichert …" : "Zahlungs-IBAN bestätigen"}
        </button>
      </div>
      <ErrorNotice error={error} />
    </div>
  );
}
