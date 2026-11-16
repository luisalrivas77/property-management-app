"use client";

// A wallet account (phase 7 step 1, §4, D179): its addresses, read by public address only.
// Every address is shown masked; „kopieren“ asks the API for the full address (GET
// .../plain, never cached, audited as wallet.address_revealed) and writes it straight to the
// clipboard, so no page ever renders it and no state keeps it. „Adresse hinzufügen“ takes the
// chain and the pasted address, and for an Ethereum address offers „Auch auf Arbitrum und BNB
// Smart Chain lesen“ (one row per chain). A label can be changed, an address retired
// („Adresse stilllegen“, its movements stay) and reactivated. Each address shows its last
// read in plain words with what to do (wallet screens, §13); the holdings, „Jetzt abrufen“,
// the transactions to confirm or answer and the hidden spam tokens are in ./wallet-cards.
import { useCallback, useId, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Chain, WalletAddress, WalletAddressStatus } from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { ADDRESS_KIND, CHAIN } from "./labels";
import { Loading, Notice } from "./ui";
import { useLoad } from "./useLoad";
import { AnsweredGroups, GroupCards, HiddenTokens, STATE_BADGE, SyncPanel, useWalletState } from "./wallet-cards";

/** The chains a 0x address is read on with one click besides Ethereum (D179 (2)). */
const EVM_EXTRA: Chain[] = ["arbitrum", "bsc"];

/** What an address of each chain looks like, as rules/chain/address.EXPECTED_SHAPE says it. */
const SHAPE: Record<Chain, string> = {
  bitcoin: "Beginnt mit bc1 (SegWit, Taproot), 1 oder 3.",
  ethereum: "0x und 40 Hex-Zeichen (0–9, a–f), wie die Wallet sie anzeigt.",
  arbitrum: "0x und 40 Hex-Zeichen (0–9, a–f), wie die Wallet sie anzeigt.",
  bsc: "0x und 40 Hex-Zeichen (0–9, a–f), wie die Wallet sie anzeigt.",
  solana: "32 bis 44 Zeichen in Base58 (ohne 0, O, I und l).",
};

/** „4 Chains · 5 Adressen“ over the active addresses (Konten, mockup 08); retired ones are
 * not read and not counted. */
export function walletSummary(addresses: WalletAddress[]): string {
  const active = addresses.filter((address) => address.active);
  if (active.length === 0) return "noch keine Adresse";
  const chains = new Set(active.map((address) => address.chain)).size;
  return `${chains === 1 ? "1 Chain" : `${chains} Chains`} · ${active.length === 1 ? "1 Adresse" : `${active.length} Adressen`}`;
}

/** The active chains in registry order, for the pills on Konten. */
export function walletChains(addresses: WalletAddress[]): Chain[] {
  const order = Object.keys(CHAIN) as Chain[];
  const present = new Set(addresses.filter((address) => address.active).map((address) => address.chain));
  return order.filter((chain) => present.has(chain));
}

export function WalletSection({ accountId }: { accountId: string }) {
  const load = useCallback(() => ok(api.GET("/api/wallets/{account_id}", { params: { path: { account_id: accountId } } })), [accountId]);
  const page = useLoad(load);
  const state = useWalletState(accountId);
  // Bumped after an answer or a sync, so the cards and the hidden tokens load again.
  const [version, setVersion] = useState(0);
  const changed = () => {
    page.reload();
    state.reload();
    setVersion((value) => value + 1);
  };
  const statuses = new Map((state.data?.statuses ?? []).map((status) => [status.address_id, status]));
  return (
    <Loading state={page}>
      {(data) => {
        // D185: a Bitcoin wallet sends change to new addresses; before the first sync, say
        // that Hausbuch will ask about them rather than leave the person wondering.
        const bitcoinUnsynced = data.addresses.some((address) => address.active && address.chain === "bitcoin" && !address.last_sync);
        return (
          <div className="stack">
            <p className="muted">
              Wallet: Hausbuch kennt nur ihre öffentlichen Adressen, nie eine Seed-Phrase oder einen privaten Schlüssel, und
              kann nichts von ihr senden. Die Adressen werden nur gekürzt angezeigt; „kopieren“ legt die volle Adresse in die
              Zwischenablage, damit Sie sie mit der Wallet vergleichen können.
            </p>
            <div className="card stack">
              <h2>Adressen</h2>
              {data.addresses.length === 0 ? (
                <p className="muted">Noch keine Adresse. Fügen Sie unten die erste hinzu.</p>
              ) : (
                <AddressTable accountId={accountId} addresses={data.addresses} statuses={statuses} onChanged={changed} />
              )}
              {bitcoinUnsynced ? (
                <div className="notice notice-info">
                  <p>
                    Eine Bitcoin-Wallet schickt das Wechselgeld einer Zahlung meist an eine neue Adresse. Tragen Sie hier die
                    Adressen ein, die Sie kennen; findet Hausbuch beim Abruf eine Zahlung an eine Adresse, die Ihr Wechselgeld
                    sein könnte, fragt es nach, statt sie als Ausgabe zu zählen.
                  </p>
                </div>
              ) : null}
              <ErrorNotice error={state.error} />
            </div>
            {state.data ? <SyncPanel accountId={accountId} state={state.data} onPoll={state.reload} onSynced={changed} /> : null}
            <GroupCards accountId={accountId} version={version} onChanged={changed} />
            <HiddenTokens accountId={accountId} version={version} onChanged={changed} />
            <AnsweredGroups accountId={accountId} version={version} onChanged={changed} />
            <AddAddress accountId={accountId} onAdded={changed} />
          </div>
        );
      }}
    </Loading>
  );
}

function AddressTable({
  accountId,
  addresses,
  statuses,
  onChanged,
}: {
  accountId: string;
  addresses: WalletAddress[];
  statuses: Map<string, WalletAddressStatus>;
  onChanged: () => void;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Chain</th>
            <th>Adresse</th>
            <th>Art</th>
            <th>Stand</th>
            <th aria-label="Aktionen" />
          </tr>
        </thead>
        <tbody>
          {addresses.map((address) => (
            <AddressRow key={address.id} accountId={accountId} address={address} status={statuses.get(address.id)} onChanged={onChanged} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AddressRow({
  accountId,
  address,
  status,
  onChanged,
}: {
  accountId: string;
  address: WalletAddress;
  status: WalletAddressStatus | undefined;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState<"ok" | "blocked" | null>(null);
  const path = { params: { path: { account_id: accountId, address_id: address.id } } };

  async function setActive(active: boolean) {
    setBusy(true);
    setError(null);
    try {
      await ok(api.PATCH("/api/wallets/{account_id}/addresses/{address_id}", { ...path, body: { active } }));
      onChanged();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  // The full address goes from the answer straight to the clipboard: it is never put into
  // state or the page. If the browser refuses the clipboard, it is not shown instead. Safari
  // allows a clipboard write only within the click itself, so where the browser has
  // ClipboardItem the write starts at once with the address still being fetched; elsewhere
  // the address is written once it has arrived.
  async function copy() {
    setBusy(true);
    setError(null);
    setCopied(null);
    const address = ok(api.GET("/api/wallets/{account_id}/addresses/{address_id}/plain", path)).then((plain) => plain.address);
    try {
      if (typeof ClipboardItem !== "undefined" && typeof navigator.clipboard?.write === "function") {
        const blob = address.then((text) => new Blob([text], { type: "text/plain" }));
        await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
      } else {
        await navigator.clipboard.writeText(await address);
      }
      setCopied("ok");
    } catch {
      // Either the request failed (its error is shown) or the clipboard refused.
      await address.then(
        () => setCopied("blocked"),
        (caught: unknown) => setError(caught),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr>
      <td>{CHAIN[address.chain]}</td>
      <td>
        <code>{address.address_masked}</code>
        {address.active ? null : (
          <>
            {" "}
            <span className="tag">stillgelegt</span>
          </>
        )}
        {editing ? (
          <LabelForm
            accountId={accountId}
            address={address}
            onDone={() => {
              setEditing(false);
              onChanged();
            }}
            onCancel={() => setEditing(false)}
          />
        ) : address.label ? (
          <p className="cell-detail">{address.label}</p>
        ) : null}
        {copied === "ok" ? <p className="cell-detail">Volle Adresse in die Zwischenablage kopiert.</p> : null}
        {copied === "blocked" ? (
          <p className="cell-detail">Der Browser erlaubt hier keinen Zugriff auf die Zwischenablage; bitte erneut versuchen.</p>
        ) : null}
        <ErrorNotice error={error} />
      </td>
      <td>{ADDRESS_KIND[address.kind]}</td>
      <td>
        {status ? <AddressStatusCell status={status} /> : <span className="muted">{address.active ? "…" : "wird nicht abgerufen"}</span>}
      </td>
      <td>
        <div className="row">
          <button type="button" className="secondary" onClick={() => void copy()} disabled={busy}>
            kopieren
          </button>
          {editing ? null : (
            <button type="button" className="secondary" onClick={() => setEditing(true)} disabled={busy}>
              Bezeichnung ändern
            </button>
          )}
          {address.active ? (
            <button type="button" className="secondary" onClick={() => void setActive(false)} disabled={busy}>
              Adresse stilllegen
            </button>
          ) : (
            <button type="button" className="secondary" onClick={() => void setActive(true)} disabled={busy}>
              Wieder aktivieren
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

/** The last read of one address in plain words, with what to do and, when the cure is a
 * key (typed under Einstellungen › Zugangsschlüssel, phase 6 D260, or a Scaleway container
 * variable), the variable's name (never its value). */
function AddressStatusCell({ status }: { status: WalletAddressStatus }) {
  const badge = STATE_BADGE[status.state];
  return (
    <>
      <span className={badge.className}>{badge.label}</span>
      <p className="cell-detail">{status.text}</p>
      {status.todo ? <p className="cell-detail todo-text">Was tun: {status.todo}</p> : null}
      {status.setting ? (
        <p className="cell-detail">
          Unter Einstellungen › Zugangsschlüssel eintragen, oder als Variable im Scaleway-Container:{" "}
          <code>{status.setting}</code>
        </p>
      ) : null}
    </>
  );
}

function LabelForm({
  accountId,
  address,
  onDone,
  onCancel,
}: {
  accountId: string;
  address: WalletAddress;
  onDone: () => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [label, setLabel] = useState(address.label ?? "");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(["label"]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.PATCH("/api/wallets/{account_id}/addresses/{address_id}", {
          params: { path: { account_id: accountId, address_id: address.id } },
          body: { label: label.trim() || null },
        }),
      );
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <Field id={`${id}-label`} label="Bezeichnung" hint="Leer lassen: keine Bezeichnung." errors={form.of("label")}>
        <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={100} autoComplete="off" />
      </Field>
      <div className="row">
        <Submit busy={busy}>Speichern</Submit>
        <button type="button" className="secondary" onClick={onCancel}>
          Abbrechen
        </button>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

function AddAddress({ accountId, onAdded }: { accountId: string; onAdded: () => void }) {
  const [chain, setChain] = useState<Chain>("ethereum");
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [alsoEvm, setAlsoEvm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState<Chain[] | null>(null);
  const form = useFormErrors(["chain", "address", "label", "also_on"], { "wallet.": "address" });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    if (address.trim() === "") values.messages.address = ["Bitte die öffentliche Adresse aus der Wallet einfügen."];
    if (!values.ok) return form.invalid(values.messages);
    setBusy(true);
    setAdded(null);
    form.clear();
    try {
      const rows = await ok(
        api.POST("/api/wallets/{account_id}/addresses", {
          params: { path: { account_id: accountId } },
          body: { chain, address: address.trim(), label: label.trim() || null, also_on: chain === "ethereum" && alsoEvm ? EVM_EXTRA : [] },
        }),
      );
      setAddress("");
      setLabel("");
      setAdded(rows.map((row) => row.chain));
      onAdded();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate>
      <h2>Adresse hinzufügen</h2>
      <div className="grid">
        <Field
          id="wallet-chain"
          label="Chain"
          description="Die Blockchain, auf der die Adresse liegt. Eine 0x-Adresse einer Hardware-Wallet ist auf Ethereum, Arbitrum und BNB Smart Chain dieselbe; Hausbuch liest sie auf jeder Chain, die Sie eintragen."
          errors={form.of("chain")}
        >
          <select value={chain} onChange={(e) => setChain(e.target.value as Chain)}>
            {(Object.keys(CHAIN) as Chain[]).map((value) => (
              <option key={value} value={value}>
                {CHAIN[value]}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="wallet-address"
          label="Öffentliche Adresse"
          hint={SHAPE[chain]}
          description="Die Empfangsadresse, wie die Wallet sie anzeigt. Nie die Seed-Phrase (12 oder 24 Wörter) oder einen privaten Schlüssel eingeben: Hausbuch braucht sie nicht und nimmt sie nicht an."
          errors={form.of("address")}
        >
          <input
            type="text"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            required
          />
        </Field>
        <Field
          id="wallet-label"
          label="Bezeichnung (optional)"
          hint="z. B. „Tangem Karte 1“"
          description="Ein Name, unter dem Sie die Adresse wiedererkennen."
          errors={form.of("label")}
        >
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={100} autoComplete="off" />
        </Field>
      </div>
      {chain === "ethereum" ? (
        <div className="field">
          <label className="inline" htmlFor="wallet-also-evm">
            <input id="wallet-also-evm" type="checkbox" checked={alsoEvm} onChange={(e) => setAlsoEvm(e.target.checked)} /> Auch auf
            Arbitrum und BNB Smart Chain lesen
          </label>
          <p className="muted hint">
            Legt dieselbe Adresse zusätzlich für Arbitrum und BNB Smart Chain an; jede wird für sich abgerufen. Eine Chain, auf
            der diese Wallet die Adresse schon hat, wird übersprungen.
          </p>
        </div>
      ) : null}
      <div className="row">
        <Submit busy={busy}>Adresse hinzufügen</Submit>
      </div>
      {added !== null ? <Notice>Hinzugefügt für {added.map((value) => CHAIN[value]).join(", ")}.</Notice> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}
