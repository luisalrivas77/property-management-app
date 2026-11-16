"use client";

// A wallet's sync state, holdings and cards (phase 7 §13, §14, D183, D185, D186, D187).
// Every address is shown masked; no full address, transaction hash, URL or key reaches this
// page. A missing key is named by its Scaleway container variable only (principle 7).
//
// - SyncPanel: „Jetzt abrufen“ and, per asset, the chain's balance at the last read beside what
//   Hausbuch books for it (D186 (2)).
// - GroupCards: each transaction Hausbuch proposes or asks about, with „Bestätigen“ and the
//   answers that fit its legs (D183 (5)); a Bitcoin change card asks per output (D185 (2)).
// - HiddenTokens: tokens hidden as spam, with „Kein Spam“ (D187 (3)).
import { useCallback, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { WalletAddressState, WalletAnswer, WalletGroup, WalletHolding, WalletState } from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { notifyInboxChanged } from "@/lib/inboxCount";

import { formatUnits } from "./exchange";
import { formatDateTime } from "./format";
import { CHAIN, MOVEMENT_KIND } from "./labels";
import { Loading, Notice } from "./ui";
import { useLoad } from "./useLoad";

/** The badge of an address's or a holding's state. */
export const STATE_BADGE: Record<WalletAddressState, { label: string; className: string }> = {
  retired: { label: "stillgelegt", className: "badge" },
  key_missing: { label: "Schlüssel fehlt", className: "badge badge-danger" },
  source_unavailable: {
    label: "noch nicht lesbar",
    className: "badge badge-warn",
  },
  not_synced: { label: "noch nicht abgerufen", className: "badge badge-info" },
  failed: { label: "Abruf fehlgeschlagen", className: "badge badge-danger" },
  throttled: { label: "später erneut", className: "badge badge-warn" },
  refused: { label: "Zugang abgelehnt", className: "badge badge-danger" },
  deferred: { label: "wird noch gelesen", className: "badge badge-info" },
  reconciled: { label: "stimmt", className: "badge" },
  waiting: { label: "wartet auf Antwort", className: "badge badge-warn" },
  pending: { label: "mit anderer Adresse", className: "badge badge-info" },
  unreconciled: { label: "weicht ab", className: "badge badge-danger" },
  coins_left: { label: "Coins abgegangen", className: "badge badge-danger" },
};

const ANSWER_LABEL: Record<WalletAnswer, string> = {
  payment: "Bezahlung",
  gift: "Geschenk oder Zahlung erhalten",
  reward: "Belohnung",
  spam: "Spam",
};

export function useWalletState(accountId: string) {
  const load = useCallback(
    () =>
      ok(
        api.GET("/api/wallets/{account_id}/state", {
          params: { path: { account_id: accountId } },
        }),
      ),
    [accountId],
  );
  return useLoad(load);
}

/** „Jetzt abrufen“ and the holdings table. */
export function SyncPanel({ accountId, state, onSynced }: { accountId: string; state: WalletState; onSynced: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [queued, setQueued] = useState<number | null>(null);

  async function sync() {
    setBusy(true);
    setError(null);
    setQueued(null);
    try {
      const answer = await ok(
        api.POST("/api/wallets/{account_id}/sync", {
          params: { path: { account_id: accountId } },
        }),
      );
      setQueued(answer.addresses);
      onSynced();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card stack">
      <h2>Bestand laut Blockchain</h2>
      <p className="muted">
        Hausbuch liest jede Adresse täglich. Je Coin oder Token steht hier der Bestand, den die Blockchain beim letzten Abruf meldete, neben
        dem, was Hausbuch aus den gebuchten Bewegungen zählt. Stimmen beide überein, ist die Adresse abgeglichen.
      </p>
      {(state.holdings ?? []).length === 0 ? <p className="muted">Noch kein Bestand gelesen.</p> : <HoldingsTable holdings={state.holdings ?? []} />}
      <div className="row">
        <button type="button" onClick={() => void sync()} disabled={busy}>
          {busy ? "Startet …" : "Jetzt abrufen"}
        </button>
      </div>
      {queued === 0 ? <Notice>Diese Wallet hat keine aktive Adresse; es gibt nichts abzurufen.</Notice> : null}
      {queued !== null && queued > 0 ? (
        <Notice>
          Abruf gestartet für {queued === 1 ? "1 Adresse" : `${queued} Adressen`}. Das Ergebnis erscheint hier, sobald er fertig ist; bitte
          die Seite dann neu laden.
        </Notice>
      ) : null}
      <ErrorNotice error={error} />
    </div>
  );
}

function HoldingsTable({ holdings }: { holdings: WalletHolding[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Chain</th>
            <th>Adresse</th>
            <th>Coin / Token</th>
            <th className="num">Laut Blockchain</th>
            <th className="num">In Hausbuch gebucht</th>
            <th>Abgleich</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((holding, index) => (
            <tr key={`${holding.chain}-${holding.address_id ?? "all"}-${holding.asset_id ?? holding.symbol}-${index}`}>
              <td>{CHAIN[holding.chain]}</td>
              <td>{holding.address_masked ? <code>{holding.address_masked}</code> : <span className="muted">alle Adressen</span>}</td>
              <td>{holding.symbol}</td>
              <td className="num">{formatUnits(holding.chain_units)}</td>
              <td className="num">{holding.booked_units == null ? "–" : formatUnits(holding.booked_units)}</td>
              <td>
                <span className={STATE_BADGE[holding.state].className}>{STATE_BADGE[holding.state].label}</span>
                {holding.read_at ? <p className="cell-detail">gelesen {formatDateTime(holding.read_at)}</p> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The wallet's transactions that wait for a person: questions first, then the newest. */
export function GroupCards({ accountId, version, onChanged }: { accountId: string; version: number; onChanged: () => void }) {
  const load = useCallback(
    () =>
      ok(
        api.GET("/api/wallets/{account_id}/groups", {
          params: { path: { account_id: accountId } },
        }),
      ),
    // version re-runs the load after an answer elsewhere on the page
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [accountId, version],
  );
  const groups = useLoad(load);
  return (
    <Loading state={groups}>
      {(list) =>
        list.length === 0 ? null : (
          <div className="card stack">
            <h2>Transaktionen prüfen</h2>
            <p className="muted">
              Diese Transaktionen hat Hausbuch auf der Blockchain gefunden. „Bestätigen“ übernimmt den Vorschlag; stimmt er nicht, wählen
              Sie, was die Transaktion war.
            </p>
            <ul className="checklist-items">
              {list.map((group) => (
                <GroupCard
                  key={group.id}
                  group={group}
                  onDone={() => {
                    groups.reload();
                    onChanged();
                    notifyInboxChanged();
                  }}
                />
              ))}
            </ul>
          </div>
        )
      }
    </Loading>
  );
}

function GroupCard({ group, onDone }: { group: WalletGroup; onDone: () => void }) {
  const id = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [cost, setCost] = useState("");
  const path = { params: { path: { group_id: group.id } } };
  const legs = group.legs ?? [];
  const answers = group.answers ?? [];
  const moving = legs.filter((leg) => leg.kind !== "fee");

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const confirm = () => run(() => ok(api.POST("/api/groups/{group_id}/confirm", path)));
  const answer = (value: WalletAnswer) =>
    run(() =>
      ok(
        api.POST("/api/groups/{group_id}/answer", {
          ...path,
          body: {
            answer: value,
            cost_eur: value === "gift" && cost.trim() !== "" ? cost.trim().replace(",", ".") : null,
          },
        }),
      ),
    );

  return (
    <li className="checklist-item">
      <div className="stack">
        <div>
          <strong>
            {CHAIN[group.chain]} · {formatDateTime(group.occurred_at)}
          </strong>
          <p>{group.text}</p>
          {group.question && group.card !== "proposed" ? <p className="inbox-question">{group.question}</p> : null}
        </div>
        {legs.length > 0 ? (
          <ul>
            {legs.map((leg) => (
              <li key={leg.movement_id}>
                {formatUnits(leg.quantity)} {leg.symbol} · {MOVEMENT_KIND[leg.kind]}
                {leg.address_masked ? (
                  <>
                    {" "}
                    · <code>{leg.address_masked}</code>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {group.card === "change" ? <ChangeAnswers groupId={group.id} onDone={onDone} /> : null}
        {group.card === "unreadable" ? (
          <p className="muted">
            Hausbuch kann die Beträge dieser Transaktion nicht lesen. Sie bleibt hier stehen, bis ein späterer Ausbauschritt sie lesen kann;
            der Abgleich der Adresse zeigt solange, ob etwas fehlt.
          </p>
        ) : null}
        {group.card === "unstorable" ? (
          <p className="muted">Diese Transaktion lässt sich nicht als Bewegung speichern; sie ist hier nur zur Kenntnis.</p>
        ) : null}
        {group.confirmable || answers.length > 0 ? (
          <div className="row">
            {group.confirmable ? (
              <button type="button" onClick={() => void confirm()} disabled={busy}>
                Bestätigen
              </button>
            ) : null}
            {answers.map((value) => (
              <button key={value} type="button" className="secondary" onClick={() => void answer(value)} disabled={busy}>
                {ANSWER_LABEL[value]}
              </button>
            ))}
          </div>
        ) : null}
        {answers.includes("gift") && moving.length === 1 ? (
          <div className="field">
            <label htmlFor={`${id}-cost`}>Kaufpreis in Euro, falls bezahlt (optional)</label>
            <input
              id={`${id}-cost`}
              type="text"
              inputMode="decimal"
              value={cost}
              onChange={(e) => setCost(e.target.value)}
              autoComplete="off"
              placeholder="0,00"
            />
            <p className="muted hint">
              Für „Geschenk oder Zahlung erhalten“: leer lassen bei einem Geschenk; bei einem Kauf außerhalb einer Börse den gezahlten
              Betrag eintragen. Er wird der Kaufpreis dieser Coins.
            </p>
          </div>
        ) : null}
        <ErrorNotice error={error} />
      </div>
    </li>
  );
}

/** A Bitcoin change card (D185 (2)): per output „Ja, eigene Adresse“ (registers it as this
 * wallet's change address) or „Nein, Empfänger“ (a payment to someone else). */
export function ChangeAnswers({ groupId, onDone }: { groupId: string; onDone: () => void }) {
  const load = useCallback(
    () =>
      ok(
        api.GET("/api/groups/{group_id}/change", {
          params: { path: { group_id: groupId } },
        }),
      ),
    [groupId],
  );
  const card = useLoad(load);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function answer(vout: number, own: boolean) {
    setBusy(true);
    setError(null);
    try {
      const options = {
        params: { path: { group_id: groupId } },
        body: { vout },
      };
      if (own) await ok(api.POST("/api/groups/{group_id}/change/own", options));
      else await ok(api.POST("/api/groups/{group_id}/change/payee", options));
      notifyInboxChanged();
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Loading state={card}>
      {(data) =>
        data.original_missing ? (
          <p className="muted">
            Die Daten zu dieser Zahlung wurden beim Abruf nicht gespeichert, deshalb lässt sich die Frage hier nicht beantworten.
          </p>
        ) : (
          <div className="stack">
            <p className="muted">Ging einer dieser Beträge an eine eigene Adresse dieser Wallet (Wechselgeld) oder an einen Empfänger?</p>
            {(data.candidates ?? []).map((candidate) => (
              <div key={candidate.vout} className="row">
                <span>
                  {formatUnits(candidate.amount_btc)} BTC an <code>{candidate.address_masked}</code>
                </span>
                <button type="button" onClick={() => void answer(candidate.vout, true)} disabled={busy}>
                  Ja, eigene Adresse
                </button>
                <button type="button" className="secondary" onClick={() => void answer(candidate.vout, false)} disabled={busy}>
                  Nein, Empfänger
                </button>
              </div>
            ))}
            <ErrorNotice error={error} />
          </div>
        )
      }
    </Loading>
  );
}

/** Tokens hidden as spam (D187): never priced, never in the net worth. „Kein Spam“ shows one
 * again and proposes its transactions for confirmation. */
export function HiddenTokens({ accountId, version, onChanged }: { accountId: string; version: number; onChanged: () => void }) {
  const load = useCallback(
    () =>
      ok(
        api.GET("/api/wallets/{account_id}/hidden-tokens", {
          params: { path: { account_id: accountId } },
        }),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [accountId, version],
  );
  const tokens = useLoad(load);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function promote(assetId: string) {
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/assets/{asset_id}/promote", {
          params: { path: { asset_id: assetId } },
        }),
      );
      tokens.reload();
      onChanged();
      notifyInboxChanged();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Loading state={tokens}>
      {(list) =>
        list.length === 0 ? null : (
          <details className="card stack">
            <summary>
              <strong>Als Spam verborgene Tokens ({list.length})</strong>
            </summary>
            <p className="muted">
              Diese Tokens kamen ungefragt an diese Wallet. Hausbuch zählt sie nicht und bewertet sie nicht. Ist einer doch echt, „Kein
              Spam“ wählen: seine Transaktionen erscheinen dann oben zum Bestätigen.
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Chain</th>
                    <th>Token</th>
                    <th className="num">Menge</th>
                    <th aria-label="Aktionen" />
                  </tr>
                </thead>
                <tbody>
                  {list.map((token) => (
                    <tr key={token.asset_id}>
                      <td>{CHAIN[token.chain]}</td>
                      <td>{token.name}</td>
                      <td className="num">{formatUnits(token.units)}</td>
                      <td>
                        <button type="button" className="secondary" onClick={() => void promote(token.asset_id)} disabled={busy}>
                          Kein Spam
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ErrorNotice error={error} />
          </details>
        )
      }
    </Loading>
  );
}
