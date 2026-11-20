"use client";

// Konto (phase-2.md §7): the account's facts, then tabs as ?tab=… links. Kontoauszüge (upload,
// the imports with their reconciliation status, the closing-balance form for a CSV), Umsätze
// (phase 10 D269: the month card and the lines by day, components/accountLines.tsx), and mögliche Doppelbuchungen
// (the same payment imported in two formats, D22). Since phase 6 (D156) a private account
// imports like the rental one; its owners (D157) are a card under the facts, with the to-do
// „Eigentümer festlegen“ while none is set. Kontoauszüge starts, on the rental account, with
// the Bankabruf card (phase-5.md §11): the FinTS connection and its runs; a run that imports
// reloads the imports below. The private accounts' FinTS connections come with phase 6 step 3.
// A credit card (D176) takes its monthly PDF statements instead, shows its card's last four
// digits and masked customer number, and offers the payment IBAN its latest statement prints
// for confirmation (the card „Zahlungs-IBAN bestätigen“, principle 3). An exchange account
// (phase 6 step 5a, D166, D178) has no statements or bank lines: its page is the export upload,
// its to-dos, the coins it holds, the imports with „Bestand laut Börse eingeben“ and the
// movements with their answers (components/exchange.tsx). A wallet (phase 7 step 1, §4) has
// none either: its page is its addresses, masked, with „Adresse hinzufügen“, „kopieren“ and
// „Adresse stilllegen“ (components/wallet.tsx).
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { Account } from "@/api/schemas";
import { DuplicatesList, ImportsTable, openDuplicateCount, StatementUpload } from "@/components/accounts";
import { AccountLinesView } from "@/components/accountLines";
import { BankSyncCard } from "@/components/bankSync";
import { ExchangeSection } from "@/components/exchange";
import { formatDate, formatMoney } from "@/components/format";
import { ACCOUNT_KIND, ACCOUNT_PURPOSE } from "@/components/labels";
import { Breadcrumbs, Facts, Loading, Notice, TabBar, type Tab } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { WalletSection } from "@/components/wallet";
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
  // D176 (4): how many learned rules on the payment IBAN its confirmation switched off.
  const [rulesDisabled, setRulesDisabled] = useState(0);
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
        {(data) =>
          tab === "umsaetze" && data.kind !== "exchange" && data.kind !== "wallet" ? (
            // Konten › Umsätze (phase 10 D269, 16-C2): its own heading and month card.
            <AccountLinesView account={data} tabs={<TabBar tabs={tabs} current={tab} href={(key) => `/accounts/${id}?tab=${key}`} />} />
          ) : (
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
            {data.purpose === "private" && data.kind !== "exchange" && data.kind !== "wallet" ? (
              <p className="muted">
                Privatkonto: seine Umsätze bekommen Haushaltskategorien und zählen nie in die Anlage V. Überweisungen zwischen
                diesem Konto und dem Mietkonto sind Privateinlagen und -entnahmen.
              </p>
            ) : null}
            {data.kind === "credit_card" ? (
              <PaymentIbanCard
                account={data}
                onSaved={(disabled) => {
                  setRulesDisabled(disabled);
                  account.reload();
                }}
              />
            ) : null}
            {data.kind === "depot" ? (
              <DepotIbanCard
                account={data}
                onSaved={(disabled) => {
                  setRulesDisabled(disabled);
                  account.reload();
                }}
              />
            ) : null}
            {rulesDisabled > 0 ? (
              <Notice>
                {rulesDisabled === 1 ? "1 gelernte Regel" : `${rulesDisabled} gelernte Regeln`} für diese Kontonummer abgeschaltet:
                {data.kind === "depot"
                  ? " Überweisungen auf das Depot sind jetzt Überträge."
                  : " Zahlungen an die Karte sind jetzt Überträge."}
              </Notice>
            ) : null}
            <AccountOwnership account={data} onSaved={account.reload} />
            {data.kind === "exchange" ? (
              <>
                <p className="muted">
                  Börsenkonto: Hausbuch liest die Exporte der Börse selbst, ohne KI, und zeigt je Coin, was sie ergeben. Ein Kauf,
                  ein Verkauf und eine Gebühr sind damit gebucht; nur Auszahlungen und Eingänge, deren Ziel oder Herkunft der Export
                  nicht nennt, fragt Hausbuch nach.
                </p>
                <ExchangeSection accountId={data.id} />
              </>
            ) : null}
            {data.kind === "wallet" ? <WalletSection accountId={data.id} /> : null}
            {data.kind === "exchange" || data.kind === "wallet" ? null : (
              <>
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
                      depot={data.kind === "depot"}
                      onImported={() => {
                        statements.reload();
                        if (data.kind === "credit_card" || data.kind === "depot") account.reload();
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
          )
        }
      </Loading>
    </section>
  );
}

function AccountFacts({ account }: { account: Account }) {
  if (account.kind === "exchange") {
    return (
      <Facts
        rows={[
          ["Börse", account.provider ?? account.bank],
          ["Kontoart", ACCOUNT_KIND[account.kind]],
        ]}
      />
    );
  }
  if (account.kind === "wallet") {
    return (
      <Facts
        rows={[
          ["Anbieter", account.provider ?? account.bank],
          ["Kontoart", ACCOUNT_KIND[account.kind]],
        ]}
      />
    );
  }
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

/** Phase 6 step 4 (D156 amended): the IBAN the latest cash statement of a depot prints, masked,
 * until a person confirms it. Confirming reads it again from that statement (never from this
 * page); a transfer from an own bank account to that IBAN is then linked as a transfer (D162). */
function DepotIbanCard({ account, onSaved }: { account: Account; onSaved: (rulesDisabled: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const proposal = account.iban_proposal;
  if (!proposal) return null;

  async function confirm() {
    if (!proposal) return;
    setBusy(true);
    setError(null);
    try {
      const result = await ok(
        api.POST("/api/accounts/{account_id}/iban", {
          params: { path: { account_id: account.id } },
          body: { import_id: proposal.import_id },
        }),
      );
      onSaved(result.rules_disabled);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card stack">
      <h2>IBAN des Depots bestätigen</h2>
      <p>
        Der Kontoauszug nennt die IBAN <strong>{proposal.iban_masked}</strong> für dieses Depot.
        {account.iban_masked
          ? ` Bisher bestätigt ist ${account.iban_masked}.`
          : " Stimmt sie, bestätigen Sie sie hier: Hausbuch bucht dann eine Überweisung von Ihrem Girokonto auf diese IBAN als Umbuchung zwischen eigenen Konten, nicht als Ausgabe, und verknüpft sie mit der Einlage auf dem Kontoauszug des Depots."}
      </p>
      <div className="row">
        <button type="button" onClick={() => void confirm()} disabled={busy}>
          {busy ? "Speichert …" : "IBAN bestätigen"}
        </button>
      </div>
      <ErrorNotice error={error} />
    </div>
  );
}

/** D176 (4): the IBAN the latest card statement prints for payments to the card, masked, until a
 * person confirms it. Confirming reads it again from that statement (never from this page) and
 * switches off the learned rules on that IBAN; onSaved gets how many. */
function PaymentIbanCard({ account, onSaved }: { account: Account; onSaved: (rulesDisabled: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const proposal = account.payment_iban_proposal;
  if (!proposal) return null;

  async function confirm() {
    if (!proposal) return;
    setBusy(true);
    setError(null);
    try {
      const result = await ok(
        api.POST("/api/accounts/{account_id}/payment-iban", {
          params: { path: { account_id: account.id } },
          body: { import_id: proposal.import_id },
        }),
      );
      onSaved(result.rules_disabled);
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
