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
import { useT } from "@/i18n";
import { accountForms } from "@/i18n/accountForms";
import { common as commonText } from "@/i18n/common";
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
  const t = useT(accountForms);
  const c = useT(commonText);
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
        ? t.conflictCard
        : depot
          ? t.conflictDepot
          : exchange
            ? t.conflictExchange
          : wallet
            ? t.conflictWallet
          : t.conflictBank
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
    if (!openingDay) values.messages.opening_balance_on = [t.openingDateRequired];
    if (exchange && !account && !provider) values.messages.provider = [t.exchangeRequired];
    if (wallet && provider.trim() === "") values.messages.provider = [t.walletProviderRequired];
    if (wallet && !account && ownerList.length > 0 && ownerChoice === null) values.messages.owners = [t.walletOwnersRequired];
    if (!account && !card && !depot && !noAnchor && iban.trim() === "") values.messages.iban = [t.ibanRequired];
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
          label={t.kind}
          description={t.kindDescription}
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
            label={t.purpose}
            description={t.purposeDescription}
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
          label={t.label}
          hint={
            card ? t.labelHintCard : depot ? t.labelHintDepot : exchange ? t.labelHintExchange : wallet ? t.labelHintWallet : t.labelHintBank
          }
          description={t.labelDescription}
          errors={form.of("label")}
        >
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={100} autoComplete="off" />
        </Field>
        {exchange ? (
          <Field
            id="account-provider"
            label={t.exchange}
            description={t.exchangeDescription}
            errors={form.of("provider")}
          >
            <select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={account != null} required>
              <option value="">{t.pleaseChoose}</option>
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
            label={t.provider}
            hint={t.walletProviderHint}
            description={t.walletProviderDescription}
            errors={form.of("provider")}
          >
            <input type="text" value={provider} onChange={(e) => setProvider(e.target.value)} required maxLength={100} autoComplete="off" />
          </Field>
        ) : (
          <Field
            id="account-bank"
            label={t.bank}
            description={
              card
                ? t.bankDescriptionCard
                : depot
                  ? t.bankDescriptionDepot
                  : t.bankDescriptionBank
            }
            errors={form.of("bank")}
          >
            <input type="text" value={bank} onChange={(e) => setBank(e.target.value)} required autoComplete="off" />
          </Field>
        )}
        {card ? (
          <Field
            id="account-provider"
            label={t.provider}
            hint={t.cardProviderHint}
            description={t.cardProviderDescription}
            errors={form.of("provider")}
          >
            <input type="text" value={provider} onChange={(e) => setProvider(e.target.value)} maxLength={100} autoComplete="off" />
          </Field>
        ) : null}
        {account || card || exchange || wallet ? null : (
          <Field
            id="account-iban"
            label={t.iban}
            hint={depot ? t.ibanHintDepot : t.ibanHint}
            description={
              depot
                ? t.ibanDescriptionDepot
                : t.ibanDescription
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
              label={t.opening}
              hint={
                card
                  ? t.openingHintCard
                  : depot
                    ? t.openingHintDepot
                    : t.openingHintBank
              }
              description={t.openingDescription}
              errors={form.of("opening_balance")}
            >
              <input type="text" inputMode="decimal" value={opening} onChange={(e) => setOpening(e.target.value)} required autoComplete="off" />
            </Field>
            <Field
              id="account-opening-on"
              label={t.openingOn}
              hint={
                card
                  ? t.openingOnHintCard
                  : depot
                    ? t.openingOnHintDepot
                  : t.openingOnHintBank
              }
              description={t.openingOnDescription}
              errors={form.of("opening_balance_on")}
            >
              <input type="date" value={openingOn} onChange={(e) => setOpeningOn(e.target.value)} required />
            </Field>
          </>
        )}
      </div>
      {card ? (
        <p className="muted">{t.cardNote}</p>
      ) : null}
      {depot && !account ? (
        <p className="muted">{t.depotNote}</p>
      ) : null}
      {exchange && !account ? (
        <p className="muted">{t.exchangeNote}</p>
      ) : null}
      {wallet && !account ? (
        <>
          {ownerList.length > 0 ? (
            <fieldset className={form.of("owners").length > 0 ? "field field-invalid" : "field"}>
              <legend>{t.walletOwnersLegend}</legend>
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
                    {t.walletJoint}
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
                    {t.walletAlone(owner.full_name)}
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
              label={t.reasonOptional}
              hint={ownerChoice === JOINT ? t.walletReasonHint : undefined}
              description={t.walletReasonDescription}
              errors={form.of("reason")}
            >
              <input type="text" value={ownerReason} onChange={(e) => setOwnerReason(e.target.value)} maxLength={500} autoComplete="off" />
            </Field>
          ) : null}
          <p className="muted">
            {t.walletNote}
            {ownerList.length === 0 ? t.walletNoteOwners : null}
          </p>
        </>
      ) : null}
      {account && !card && !depot && !exchange && !wallet ? (
        <>
          <Facts rows={[[t.iban, account.iban_masked ?? "–"]]} />
          <p className="muted">{t.ibanFixedNote}</p>
        </>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{account ? c.save : t.createAccount}</Submit>
        <Link href={account ? `/accounts/${account.id}` : "/accounts"}>{c.cancel}</Link>
      </div>
      {form.errors.banner != null ? <ErrorNotice error={form.errors.banner} text={conflictText} /> : <FormBanner errors={form.errors} />}
    </form>
  );
}
