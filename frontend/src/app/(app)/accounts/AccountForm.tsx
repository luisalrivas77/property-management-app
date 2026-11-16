"use client";

// Konto form (phase-2.md §7, D21): Bezeichnung, Bank, IBAN, Anfangssaldo and its date (the
// balance anchor every import is reconciled against), its Kontoart (Girokonto, Sparkonto or
// Kreditkarte) and whether it is the rental account or a private one (phase 6 D156: both import
// statements; the rental account's lines feed the Anlage V, a private account's never do).
// Editing (AccountPatch) cannot change the IBAN: a different IBAN is another account. Moving the
// anchor makes the API re-check every import of the account; a new purpose is refused while the
// account holds bookings the new purpose does not take (D159). A credit card (phase 6 D176) has
// no IBAN and is always private: its customer number comes with its first PDF statement, and its
// anchor is the first statement's „Alter Saldo“ (an owed amount negative); a bank account stays
// a bank account and a card a card. A depot (phase 6 step 4, D165) is always private too: its
// IBAN (the Verrechnungskonto's) is optional and its account number comes with its first cash
// statement. An exchange (phase 6 step 5a, D166, D178) is always private and has no IBAN and no
// opening balance: it is BISON or Binance, its coins come from its exports, and its balances are
// typed per coin on its page („Bestand laut Börse eingeben“). A wallet (phase 7 step 1, D179)
// is always private too, with no IBAN and no opening balance: it names its provider (Tangem,
// Phantom, …), and a new one is given its owner shares here, „Gemeinsam (je 1/2)“ with the
// reason „Coins vom Gemeinschaftskonto bezahlt“ pre-filled, or one owner at 100 %, recorded right
// after the account (POST /accounts/{id}/ownership); its addresses are added on its page.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError } from "@/api/errors";
import type { Account, AccountKind, AccountPatch, AccountPurpose, Owner, OwnershipShare } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { todayIso } from "@/components/format";
import { decimalInput, toSignedMoneyString } from "@/components/input";
import { ACCOUNT_KIND, ACCOUNT_PURPOSE } from "@/components/labels";
import { Facts } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

const KEYS = ["label", "bank", "iban", "opening_balance", "opening_balance_on", "kind", "purpose", "provider", "owners", "reason"] as const;

const CODE_FIELDS = { "iban.": "iban", "allocation.category_not_for_account": "purpose" };

/** The kinds this form offers: the bank kinds (D156), the credit card (D176), the depot
 * (step 4, D165), the exchange (step 5a, D166) and the wallet (phase 7 step 1, D179). */
const BANK_KINDS: AccountKind[] = ["bank_current", "bank_savings"];
const NEW_KINDS: AccountKind[] = [...BANK_KINDS, "credit_card", "depot", "exchange", "wallet"];
/** Kinds that keep their kind once created. */
const FIXED_KINDS: AccountKind[] = ["credit_card", "depot", "exchange", "wallet"];
/** The exchanges whose exports Hausbuch reads (backend rules/exchange.EXCHANGE_PROVIDERS). */
const EXCHANGES = ["BISON", "Binance"] as const;
/** A joint wallet's reason, as the household design records it (6 Nov 2026). */
const JOINT_REASON = "Coins vom Gemeinschaftskonto bezahlt";
const JOINT = "joint";

const loadOwners = () => ok(api.GET("/api/owners"));

/** The owner shares of a new wallet: both owners 1/2 each, or one owner 1/1. */
function walletShares(choice: string, owners: Owner[], reason: string | null): OwnershipShare[] {
  if (choice === JOINT) {
    return owners.map((owner) => ({ owner_id: owner.id, share_numerator: 1, share_denominator: 2, reason }));
  }
  return [{ owner_id: choice, share_numerator: 1, share_denominator: 1, reason }];
}

/** `initialKind` pre-selects the Kontoart of a new account (a link „Konto anlegen“ for a card or
 * depot statement waiting on its document page, phase 6 D176 (3)); a kind this form does not
 * offer is ignored. */
export function AccountForm({ account, initialKind }: { account?: Account; initialKind?: string | null }) {
  const router = useRouter();
  const [label, setLabel] = useState(account?.label ?? "");
  const [bank, setBank] = useState(account?.bank ?? "");
  const [iban, setIban] = useState("");
  const [opening, setOpening] = useState(account ? decimalInput(account.opening_balance) : "0,00");
  const [openingOn, setOpeningOn] = useState(account?.opening_balance_on ?? "");
  // Phase 6 (D156, migration 0025): is_owner_account is true on every account, the rental one
  // included; whether an account is a private one is its purpose. A new account is private
  // unless the person picks the rental purpose.
  const offered = NEW_KINDS.find((k) => k === initialKind);
  const [kind, setKind] = useState<AccountKind>(account?.kind ?? offered ?? "bank_current");
  const [purpose, setPurpose] = useState<AccountPurpose>(account?.purpose ?? "private");
  const [provider, setProvider] = useState(account?.provider ?? "");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);
  const card = kind === "credit_card";
  const depot = kind === "depot";
  const exchange = kind === "exchange";
  const wallet = kind === "wallet";
  // A new wallet's owners: null until the person picks, then JOINT or an owner's id.
  const owners = useLoad(loadOwners);
  const [ownerPick, setOwnerPick] = useState<string | null>(null);
  const [ownerReason, setOwnerReason] = useState(JOINT_REASON);
  const ownerList = owners.data ?? [];
  const ownerChoice = ownerPick ?? (ownerList.length === 2 ? JOINT : ownerList.length === 1 ? (ownerList[0]?.id ?? null) : null);
  // An existing card stays a card, a depot a depot, an exchange an exchange, a wallet a wallet,
  // an existing bank account a bank account.
  const kinds: AccountKind[] = account ? (FIXED_KINDS.includes(account.kind) ? [account.kind] : BANK_KINDS) : NEW_KINDS;

  // A 409 on create means the IBAN is taken; the generic conflict sentence would not say so.
  const conflictText = (error: unknown) =>
    error instanceof ApiError && error.status === 409 && !account
      ? card
        ? "Eine Karte mit dieser Kundennummer gibt es in diesem Haushalt schon."
        : depot
          ? "Ein Depot mit dieser IBAN gibt es in diesem Haushalt schon."
          : exchange
            ? "Dieses Börsenkonto gibt es in diesem Haushalt schon."
          : wallet
            ? "Diese Wallet gibt es in diesem Haushalt schon."
          : "Ein Konto mit dieser IBAN gibt es in diesem Haushalt schon."
      : null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    // An exchange or a wallet has no cash balance to anchor (an exchange's coins are checked per
    // import, D166; a wallet's against the chain, D186): its anchor is 0,00 on the day it is
    // created, and its bank is the exchange or the wallet's provider.
    const noAnchor = exchange || wallet;
    const openingBalance = noAnchor ? (account?.opening_balance ?? "0.00") : values.take("opening_balance", toSignedMoneyString(opening));
    const openingDay = noAnchor ? (account?.opening_balance_on ?? todayIso()) : openingOn;
    if (!openingDay) values.messages.opening_balance_on = ["Bitte das Datum des Anfangssaldos eingeben."];
    if (exchange && !account && !provider) values.messages.provider = ["Bitte die Börse wählen."];
    if (wallet && provider.trim() === "") values.messages.provider = ["Bitte den Anbieter der Wallet eingeben, z. B. Tangem."];
    if (wallet && !account && ownerList.length > 0 && ownerChoice === null) values.messages.owners = ["Bitte wählen, wem die Wallet gehört."];
    if (!account && !card && !depot && !noAnchor && iban.trim() === "") values.messages.iban = ["Bitte die IBAN des Kontos eingeben."];
    if (!values.ok || openingBalance === undefined) return form.invalid(values.messages);
    const common = {
      label: label.trim(),
      bank: exchange ? (account?.bank ?? provider) : wallet ? (account?.bank ?? provider.trim()) : bank.trim(),
      opening_balance: openingBalance,
      opening_balance_on: openingDay,
    };
    setBusy(true);
    form.clear();
    try {
      if (account) {
        // Kind, purpose and provider only when the person changed them, so saving the other
        // fields never records an account.kind_set event.
        const body: AccountPatch = { ...common };
        if (kind !== account.kind) body.kind = kind;
        if (purpose !== account.purpose) body.purpose = purpose;
        if ((card || wallet) && provider.trim() !== (account.provider ?? "")) body.provider = provider.trim() || null;
        await ok(api.PATCH("/api/accounts/{account_id}", { params: { path: { account_id: account.id } }, body }));
        router.push(`/accounts/${account.id}`);
      } else {
        // Every own account is recognised by its IBAN (is_owner_account, D156); its purpose is
        // sent. A credit card has no IBAN and is always private (D176): its statements name it.
        const body = card
          ? { ...common, is_owner_account: true, kind, purpose: "private" as const, provider: provider.trim() || null }
          : exchange
            ? // An exchange is always private and has no IBAN (D166).
              { ...common, is_owner_account: true, kind, purpose: "private" as const, provider }
          : wallet
            ? // A wallet is always private and has no IBAN or number (D179).
              { ...common, is_owner_account: true, kind, purpose: "private" as const, provider: provider.trim() }
          : depot
            ? // A depot is always private; its IBAN is optional (its cash statement offers it, D156).
              { ...common, iban: iban.trim() || null, is_owner_account: true, kind, purpose: "private" as const }
            : { ...common, iban: iban.trim(), is_owner_account: true, kind, purpose };
        const created = await ok(api.POST("/api/accounts", { body }));
        if (wallet && ownerChoice !== null) {
          // The shares are a second write; if it fails, the wallet's page shows the to-do
          // „Eigentümer festlegen“ with its own form, so nothing is lost.
          try {
            await ok(
              api.POST("/api/accounts/{account_id}/ownership", {
                params: { path: { account_id: created.id } },
                body: { shares: walletShares(ownerChoice, ownerList, ownerReason.trim() || null), effective_from: null },
              }),
            );
          } catch {
            // the to-do on the wallet's page takes over
          }
        }
        router.push(`/accounts/${created.id}`);
      }
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div className="grid">
        <Field
          id="account-kind"
          label="Kontoart"
          description="Girokonto, Sparkonto (auch Tagesgeld), Kreditkarte, Depot, Börse oder Wallet. Eine Kreditkarte wird aus ihren monatlichen Abrechnungen (PDF) gefüllt, ein Depot aus den Dokumenten seiner Bank (Kontoauszüge, Abrechnungen, Berichte), eine Krypto-Börse (BISON, Binance) aus ihren Exporten, eine Wallet (z. B. Tangem, Phantom) aus ihren öffentlichen Adressen."
          errors={form.of("kind")}
        >
          <select value={kind} onChange={(e) => setKind(e.target.value as AccountKind)} disabled={kinds.length === 1}>
            {kinds.map((value) => (
              <option key={value} value={value}>
                {ACCOUNT_KIND[value]}
              </option>
            ))}
          </select>
        </Field>
        {card || depot || exchange || wallet ? null : (
          <Field
            id="account-purpose"
            label="Wofür"
            description="Das Mietkonto ist das Konto, über das die Wohnung läuft: seine Umsätze gehen in die Anlage V. Ein Privatkonto bekommt Haushaltskategorien und zählt nie in die Anlage V; Überweisungen zwischen dem Mietkonto und einem Privatkonto sind Privateinlagen und -entnahmen."
            errors={form.of("purpose")}
          >
            <select value={purpose} onChange={(e) => setPurpose(e.target.value as AccountPurpose)}>
              {(Object.keys(ACCOUNT_PURPOSE) as AccountPurpose[]).map((value) => (
                <option key={value} value={value}>
                  {ACCOUNT_PURPOSE[value]}
                </option>
              ))}
            </select>
          </Field>
        )}
      </div>
      <div className="grid">
        <Field
          id="account-label"
          label="Bezeichnung"
          hint={
            card ? "z. B. Kreditkarte" : depot ? "z. B. Depot" : exchange ? "z. B. BISON" : wallet ? "z. B. Tangem" : "z. B. Mietkonto Musterstraße"
          }
          description="Ein frei wählbarer Name, unter dem Sie das Konto in Hausbuch wiederfinden."
          errors={form.of("label")}
        >
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={100} autoComplete="off" />
        </Field>
        {exchange ? (
          <Field
            id="account-provider"
            label="Börse"
            description="Die Börse, deren Exporte Sie hochladen. Hausbuch liest die Exporte von BISON und Binance; eine andere Börse kommt erst, wenn ihr Export bekannt ist."
            errors={form.of("provider")}
          >
            <select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={account != null} required>
              <option value="">Bitte wählen</option>
              {EXCHANGES.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </Field>
        ) : wallet ? (
          <Field
            id="account-provider"
            label="Anbieter"
            hint="z. B. Tangem, Phantom, Ledger"
            description="Die Wallet-App oder das Gerät, in dem die Coins liegen. Hausbuch liest nur die öffentlichen Adressen, die Sie auf der Seite der Wallet eintragen."
            errors={form.of("provider")}
          >
            <input type="text" value={provider} onChange={(e) => setProvider(e.target.value)} required maxLength={100} autoComplete="off" />
          </Field>
        ) : (
          <Field
            id="account-bank"
            label="Bank"
            description={
              card
                ? "Die Bank, die die Karte herausgibt, wie auf der Abrechnung."
                : depot
                  ? "Die Bank, bei der das Depot geführt wird, wie auf seinem Kontoauszug."
                  : "Die Bank, bei der das Konto geführt wird, wie auf dem Kontoauszug."
            }
            errors={form.of("bank")}
          >
            <input type="text" value={bank} onChange={(e) => setBank(e.target.value)} required autoComplete="off" />
          </Field>
        )}
        {card ? (
          <Field
            id="account-provider"
            label="Anbieter"
            hint="Optional, z. B. der Markenname der Karte."
            description="Der Name, unter dem die Karte auftritt, wenn er von der Bank abweicht. Die Karte hat keine eigene IBAN: Hausbuch erkennt ihre Abrechnungen an der Kundennummer, die die erste Abrechnung mitbringt."
            errors={form.of("provider")}
          >
            <input type="text" value={provider} onChange={(e) => setProvider(e.target.value)} maxLength={100} autoComplete="off" />
          </Field>
        ) : null}
        {account || card || exchange || wallet ? null : (
          <Field
            id="account-iban"
            label="IBAN"
            hint={depot ? "Optional; wird nur maskiert angezeigt." : "Wird nur maskiert angezeigt."}
            description={
              depot
                ? "Die IBAN des Verrechnungskontos, falls Sie sie zur Hand haben. Sonst leer lassen: der erste Kontoauszug bringt sie mit, und Sie bestätigen sie auf der Seite des Depots."
                : "Die IBAN des Kontos laut Kontoauszug. Hausbuch zeigt sie nur maskiert und prüft damit, dass ein hochgeladener Kontoauszug zu diesem Konto gehört."
            }
            errors={form.of("iban")}
          >
            <input type="text" value={iban} onChange={(e) => setIban(e.target.value)} required={!depot} autoComplete="off" spellCheck={false} />
          </Field>
        )}
        {exchange || wallet ? null : (
          <>
            <Field
              id="account-opening"
              label="Anfangssaldo (€)"
              hint={
                card
                  ? "Der „Alte Saldo“ der ersten Abrechnung, die Sie hochladen; ein offener Betrag mit Minus."
                  : depot
                    ? "Der „alte Saldo“ des ersten Kontoauszugs, den Sie hochladen. Unbekannt? 0,00 lassen und nach dem Hochladen „Anfangssaldo daraus übernehmen“ wählen."
                    : "Der Kontostand am Ende des Tages unten, laut Bank. Bei einem neuen Konto 0,00; negativ mit Minus."
              }
              description="Der Kontostand laut Bank am Ende des Tages, den Sie darunter angeben. Von ihm aus prüft Hausbuch, ob jeder importierte Kontoauszug aufgeht: Anfangssaldo plus Umsätze gleich Endsaldo."
              errors={form.of("opening_balance")}
            >
              <input type="text" inputMode="decimal" value={opening} onChange={(e) => setOpening(e.target.value)} required autoComplete="off" />
            </Field>
            <Field
              id="account-opening-on"
              label="Anfangssaldo am"
              hint={
                card
                  ? "Ein Tag vor der ersten Buchung dieser Abrechnung, z. B. der letzte Tag des Vormonats."
                  : depot
                    ? "Ein Tag vor der ersten Buchung des ersten Kontoauszugs, z. B. der letzte Tag des Vormonats."
                  : "Ein Tag vor dem ersten Umsatz, der importiert wird: Umsätze an oder vor diesem Tag passen nicht zum Anfangssaldo."
              }
              description="Der Tag, an dem dieser Kontostand galt, laut Kontoauszug oder Online-Banking."
              errors={form.of("opening_balance_on")}
            >
              <input type="date" value={openingOn} onChange={(e) => setOpeningOn(e.target.value)} required />
            </Field>
          </>
        )}
      </div>
      {card ? (
        <p className="muted">
          Eine Kreditkarte ist immer ein Privatkonto: ihre Umsätze bekommen Haushaltskategorien, Zinsen und Gebühren der Karte
          ordnet Hausbuch selbst zu. Wem die Karte gehört, legen Sie nach dem Anlegen auf ihrer Seite fest.
        </p>
      ) : null}
      {depot && !account ? (
        <p className="muted">
          Ein Depot ist immer ein Privatkonto. Legen Sie eins je Konto Ihrer Depotbank an, das auf deren Kontoauszügen erscheint
          (bei quirion das Verrechnungskonto und jedes Vermögensverwaltungskonto). Die Kontonummer bringt der erste Kontoauszug
          mit; wem das Depot gehört, legen Sie nach dem Anlegen auf seiner Seite fest.
        </p>
      ) : null}
      {exchange && !account ? (
        <p className="muted">
          Ein Börsenkonto ist immer ein Privatkonto und hat keine IBAN und keinen Anfangssaldo: nach dem Anlegen laden Sie auf
          seiner Seite den Export der Börse hoch, und Hausbuch zeigt je Coin, was er ergibt. Wem das Konto gehört (bei BISON die
          Person, auf deren Namen es läuft), legen Sie nach dem Anlegen auf seiner Seite fest.
        </p>
      ) : null}
      {wallet && !account ? (
        <>
          {ownerList.length > 0 ? (
            <fieldset className={form.of("owners").length > 0 ? "field field-invalid" : "field"}>
              <legend>Wem gehört die Wallet?</legend>
              <div className="stack" role="radiogroup">
                {ownerList.length === 2 ? (
                  <label className="inline" htmlFor="wallet-owner-joint">
                    <input
                      id="wallet-owner-joint"
                      type="radio"
                      name="wallet-owner"
                      checked={ownerChoice === JOINT}
                      onChange={() => {
                        setOwnerPick(JOINT);
                        setOwnerReason(JOINT_REASON);
                      }}
                    />{" "}
                    Gemeinsam, je 1/2 (50/50)
                  </label>
                ) : null}
                {ownerList.map((owner) => (
                  <label key={owner.id} className="inline" htmlFor={`wallet-owner-${owner.id}`}>
                    <input
                      id={`wallet-owner-${owner.id}`}
                      type="radio"
                      name="wallet-owner"
                      checked={ownerChoice === owner.id}
                      onChange={() => {
                        setOwnerPick(owner.id);
                        if (ownerReason === JOINT_REASON) setOwnerReason("");
                      }}
                    />{" "}
                    {owner.full_name} allein (100 %)
                  </label>
                ))}
              </div>
              {form.of("owners").length > 0 ? (
                <ul className="field-errors">
                  {form.of("owners").map((message, index) => (
                    <li key={index}>{message}</li>
                  ))}
                </ul>
              ) : null}
            </fieldset>
          ) : null}
          {ownerList.length > 0 ? (
            <Field
              id="wallet-owner-reason"
              label="Begründung (optional)"
              hint={ownerChoice === JOINT ? "Vorgeschlagen, weil die Coins meist vom gemeinsamen Konto bezahlt wurden." : undefined}
              description="Warum die Wallet diesen Personen zu diesen Anteilen gehört, z. B. von welchem Konto die Coins bezahlt wurden. Hausbuch speichert es mit den Anteilen, damit es später nachvollziehbar bleibt."
              errors={form.of("reason")}
            >
              <input type="text" value={ownerReason} onChange={(e) => setOwnerReason(e.target.value)} maxLength={500} autoComplete="off" />
            </Field>
          ) : null}
          <p className="muted">
            Eine Wallet ist immer ein Privatkonto und hat keine IBAN und keinen Anfangssaldo. Nach dem Anlegen tragen Sie auf
            ihrer Seite die öffentlichen Adressen ein (nie die Seed-Phrase oder einen privaten Schlüssel).
            {ownerList.length === 0 ? " Wem sie gehört, legen Sie nach dem Anlegen auf ihrer Seite fest." : null}
          </p>
        </>
      ) : null}
      {account && !card && !depot && !exchange && !wallet ? (
        <>
          <Facts rows={[["IBAN", account.iban_masked ?? "–"]]} />
          <p className="muted">
            Die IBAN lässt sich nicht ändern: eine andere IBAN ist ein anderes Konto. Wird der Anfangssaldo geändert, prüft
            Hausbuch alle Kontoauszüge dieses Kontos neu.
          </p>
        </>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{account ? "Speichern" : "Konto anlegen"}</Submit>
        <Link href={account ? `/accounts/${account.id}` : "/accounts"}>Abbrechen</Link>
      </div>
      {form.errors.banner != null ? <ErrorNotice error={form.errors.banner} text={conflictText} /> : <FormBanner errors={form.errors} />}
    </form>
  );
}
