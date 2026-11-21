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
import { ACCOUNT_KIND, ACCOUNT_PURPOSE } from "@/components/labels";
import { Breadcrumbs, Facts, Loading, Notice, TabBar, type Tab } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { WalletSection } from "@/components/wallet";
import { useFormat, useT } from "@/i18n";
import { accountForms } from "@/i18n/accountForms";
import { common } from "@/i18n/common";
import { shared } from "@/i18n/shared";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { AccountOwnership } from "./AccountOwnership";

type TabKey = "kontoauszuege" | "umsaetze" | "doppelbuchungen";

function isTab(value: string | null): value is TabKey {
  return value === "kontoauszuege" || value === "umsaetze" || value === "doppelbuchungen";
}

export default function AccountPage() {
  const c = useT(common);
  return (
    <Suspense fallback={<p className="muted">{c.loading}</p>}>
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
  const t = useT(accountForms);
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
    { key: "kontoauszuege", label: t.tabStatements },
    { key: "umsaetze", label: t.tabLines },
    { key: "doppelbuchungen", label: openCount > 0 ? t.tabDuplicatesCount(openCount) : t.tabDuplicates },
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
            <Breadcrumbs items={[{ href: "/accounts", label: t.breadcrumbAccounts }, { label: data.label }]} />
            <div className="page-head">
              <h1>{data.label}</h1>
              <span className="spacer" />
              <Link href={`/accounts/${data.id}/edit`} className="button-link secondary">
                {t.edit}
              </Link>
            </div>
            <AccountFacts account={data} />
            {data.purpose === "private" && data.kind !== "exchange" && data.kind !== "wallet" ? (
              <p className="muted">{t.privateNote}</p>
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
                {t.rulesOff(rulesDisabled)}
                {data.kind === "depot" ? t.rulesOffDepot : t.rulesOffCard}
              </Notice>
            ) : null}
            <AccountOwnership account={data} onSaved={account.reload} />
            {data.kind === "exchange" ? (
              <>
                <p className="muted">{t.exchangePageNote}</p>
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
                          {t.duplicatesWaiting(openCount)}
                          <Link href={`/accounts/${id}?tab=doppelbuchungen`}>{t.duplicatesCheck}</Link>.
                        </p>
                      </div>
                    ) : null}
                    <h2>{t.importedStatements}</h2>
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
  const t = useT(accountForms);
  const f = useFormat();
  if (account.kind === "exchange") {
    return (
      <Facts
        rows={[
          [t.factExchange, account.provider ?? account.bank],
          [t.factKind, ACCOUNT_KIND[account.kind]],
        ]}
      />
    );
  }
  if (account.kind === "wallet") {
    return (
      <Facts
        rows={[
          [t.factProvider, account.provider ?? account.bank],
          [t.factKind, ACCOUNT_KIND[account.kind]],
        ]}
      />
    );
  }
  if (account.kind === "credit_card") {
    return (
      <Facts
        rows={[
          [t.factBank, account.provider ?? account.bank],
          [t.factCard, account.card_last4 ? t.cardEndsIn(account.card_last4) : t.comesWithFirstStatement],
          [t.factCustomerNumber, account.external_ref_masked ?? t.comesWithFirstStatement],
          [t.factPaymentIban, account.payment_iban_masked ?? "–"],
          [t.factOpening, t.openingOnDay(f.money(account.opening_balance), f.date(account.opening_balance_on))],
          [t.factKind, ACCOUNT_KIND[account.kind]],
        ]}
      />
    );
  }
  return (
    <Facts
      rows={[
        [t.factBank, account.provider ?? account.bank],
        [t.factIban, account.iban_masked ?? "–"],
        [t.factOpening, t.openingOnDay(f.money(account.opening_balance), f.date(account.opening_balance_on))],
        [t.factKind, ACCOUNT_KIND[account.kind]],
        [t.factPurpose, ACCOUNT_PURPOSE[account.purpose]],
      ]}
    />
  );
}

/** Phase 6 step 4 (D156 amended): the IBAN the latest cash statement of a depot prints, masked,
 * until a person confirms it. Confirming reads it again from that statement (never from this
 * page); a transfer from an own bank account to that IBAN is then linked as a transfer (D162). */
function DepotIbanCard({ account, onSaved }: { account: Account; onSaved: (rulesDisabled: number) => void }) {
  const t = useT(accountForms);
  const s = useT(shared);
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
      <h2>{t.depotIbanTitle}</h2>
      <p>
        {t.depotIbanBefore}
        <strong>{proposal.iban_masked}</strong>
        {t.depotIbanAfter}
        {account.iban_masked ? t.confirmedSoFar(account.iban_masked) : t.depotIbanExplain}
      </p>
      <div className="row">
        <button type="button" onClick={() => void confirm()} disabled={busy}>
          {busy ? s.saving : t.confirmIban}
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
  const t = useT(accountForms);
  const s = useT(shared);
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
      <h2>{t.paymentIbanTitle}</h2>
      <p>
        {t.paymentIbanBefore}
        <strong>{proposal.payment_iban_masked}</strong>
        {t.paymentIbanAfter}
        {account.payment_iban_masked ? t.confirmedSoFar(account.payment_iban_masked) : t.paymentIbanExplain}
      </p>
      <div className="row">
        <button type="button" onClick={() => void confirm()} disabled={busy}>
          {busy ? s.saving : t.confirmPaymentIban}
        </button>
      </div>
      <ErrorNotice error={error} />
    </div>
  );
}
