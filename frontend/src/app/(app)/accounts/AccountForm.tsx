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
// statement. Exchanges and wallets come with their own steps.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError } from "@/api/errors";
import type { Account, AccountKind, AccountPatch, AccountPurpose } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { decimalInput, toSignedMoneyString } from "@/components/input";
import { ACCOUNT_KIND, ACCOUNT_PURPOSE } from "@/components/labels";
import { Facts } from "@/components/ui";
import { ErrorNotice } from "@/lib/ErrorNotice";

const KEYS = ["label", "bank", "iban", "opening_balance", "opening_balance_on", "kind", "purpose", "provider"] as const;

const CODE_FIELDS = { "iban.": "iban", "allocation.category_not_for_account": "purpose" };

/** The kinds this form offers: the bank kinds (D156), the credit card (D176) and the depot
 * (step 4, D165); the exchange comes with step 5, the wallet with phase 7. */
const BANK_KINDS: AccountKind[] = ["bank_current", "bank_savings"];
const NEW_KINDS: AccountKind[] = [...BANK_KINDS, "credit_card", "depot"];

export function AccountForm({ account }: { account?: Account }) {
  const router = useRouter();
  const [label, setLabel] = useState(account?.label ?? "");
  const [bank, setBank] = useState(account?.bank ?? "");
  const [iban, setIban] = useState("");
  const [opening, setOpening] = useState(account ? decimalInput(account.opening_balance) : "0,00");
  const [openingOn, setOpeningOn] = useState(account?.opening_balance_on ?? "");
  // Phase 6 (D156, migration 0025): is_owner_account is true on every account, the rental one
  // included; whether an account is a private one is its purpose. A new account is private
  // unless the person picks the rental purpose.
  const [kind, setKind] = useState<AccountKind>(account?.kind ?? "bank_current");
  const [purpose, setPurpose] = useState<AccountPurpose>(account?.purpose ?? "private");
  const [provider, setProvider] = useState(account?.provider ?? "");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);
  const card = kind === "credit_card";
  const depot = kind === "depot";
  // An existing card stays a card, a depot a depot, an existing bank account a bank account.
  const kinds: AccountKind[] = account
    ? account.kind === "credit_card" || account.kind === "depot"
      ? [account.kind]
      : BANK_KINDS
    : NEW_KINDS;

  // A 409 on create means the IBAN is taken; the generic conflict sentence would not say so.
  const conflictText = (error: unknown) =>
    error instanceof ApiError && error.status === 409 && !account
      ? card
        ? "Eine Karte mit dieser Kundennummer gibt es in diesem Haushalt schon."
        : depot
          ? "Ein Depot mit dieser IBAN gibt es in diesem Haushalt schon."
          : "Ein Konto mit dieser IBAN gibt es in diesem Haushalt schon."
      : null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const openingBalance = values.take("opening_balance", toSignedMoneyString(opening));
    if (!openingOn) values.messages.opening_balance_on = ["Bitte das Datum des Anfangssaldos eingeben."];
    if (!account && !card && !depot && iban.trim() === "") values.messages.iban = ["Bitte die IBAN des Kontos eingeben."];
    if (!values.ok || openingBalance === undefined) return form.invalid(values.messages);
    const common = {
      label: label.trim(),
      bank: bank.trim(),
      opening_balance: openingBalance,
      opening_balance_on: openingOn,
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
        if (card && provider.trim() !== (account.provider ?? "")) body.provider = provider.trim() || null;
        await ok(api.PATCH("/api/accounts/{account_id}", { params: { path: { account_id: account.id } }, body }));
        router.push(`/accounts/${account.id}`);
      } else {
        // Every own account is recognised by its IBAN (is_owner_account, D156); its purpose is
        // sent. A credit card has no IBAN and is always private (D176): its statements name it.
        const body = card
          ? { ...common, is_owner_account: true, kind, purpose: "private" as const, provider: provider.trim() || null }
          : depot
            ? // A depot is always private; its IBAN is optional (its cash statement offers it, D156).
              { ...common, iban: iban.trim() || null, is_owner_account: true, kind, purpose: "private" as const }
            : { ...common, iban: iban.trim(), is_owner_account: true, kind, purpose };
        const created = await ok(api.POST("/api/accounts", { body }));
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
          description="Girokonto, Sparkonto (auch Tagesgeld), Kreditkarte oder Depot. Eine Kreditkarte wird aus ihren monatlichen Abrechnungen (PDF) gefüllt, ein Depot aus den Dokumenten seiner Bank (Kontoauszüge, Abrechnungen, Berichte). Börsenkonten kommen später dazu."
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
        {card || depot ? null : (
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
          hint={card ? "z. B. Kreditkarte" : depot ? "z. B. Depot" : "z. B. Mietkonto Musterstraße"}
          description="Ein frei wählbarer Name, unter dem Sie das Konto in Hausbuch wiederfinden."
          errors={form.of("label")}
        >
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={100} autoComplete="off" />
        </Field>
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
        {account || card ? null : (
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
      {account && !card && !depot ? (
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
