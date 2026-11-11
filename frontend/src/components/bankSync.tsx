"use client";

// Bankabruf (phase-5.md §11, D115–D120): the account's FinTS connection and its runs.
//
// - No connection yet: the form (BLZ, FinTS-Adresse, Anmeldename). The PIN is never asked for
//   here; it and the product id are server secrets (D119), and the card says so.
// - A connection: its facts, "Zugang ändern", the **Jetzt abrufen** button, the current run
//   in plain words, and the run history. While a run is queued, running, waiting for the
//   pushTAN approval or importing, the card polls GET /syncs/{id} every 2 s; a run waiting for
//   the approval shows the five minutes the job waits for it (counted from when the run
//   entered that state, its updated_at). Every count shown is the API's; nothing is summed.
import Link from "next/link";
import { useCallback, useEffect, useId, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodeText } from "@/api/errors";
import type { Account, BankConnection, BankSync, SyncStatus } from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatDate, formatDateTime } from "./format";
import { BALANCE_SOURCE, SYNC_STATUS, SYNC_TRIGGER } from "./labels";
import { ActionDialog, Facts } from "./ui";
import { useLoad } from "./useLoad";

/** The states in which a run is still going and the card polls it. */
const LIVE: readonly SyncStatus[] = ["queued", "running", "needs_approval", "importing"];
const POLL_MS = 2000;
/** The job waits this long for the approval (HAUSBUCH_SYNC_APPROVAL_TIMEOUT_S' default). */
const APPROVAL_WINDOW_MS = 5 * 60 * 1000;
/** The bank's SCA exemption lasts about 180 days (§3); shown as an estimate only. */
const SCA_DAYS = 180;

export function isLiveRun(run: BankSync): boolean {
  return LIVE.includes(run.status);
}

/** The eight digits of a German IBAN's Bankleitzahl (its characters 5–12), or null. */
export function bankCodeFromIban(raw: string): string | null {
  const compact = raw.replace(/\s+/g, "").toUpperCase();
  return /^DE[0-9]{20}$/.test(compact) ? compact.slice(4, 12) : null;
}

async function loadBankSync(accountId: string): Promise<{ connection: BankConnection | null; syncs: BankSync[] }> {
  const path = { params: { path: { account_id: accountId } } };
  let connection: BankConnection | null;
  try {
    connection = await ok(api.GET("/api/accounts/{account_id}/bank-connection", path));
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return { connection: null, syncs: [] };
    throw error;
  }
  const syncs = await ok(api.GET("/api/accounts/{account_id}/syncs", path));
  return { connection, syncs };
}

/** The later of two views of the runs: the one the card polled, or the list's newest. */
function newer(polled: BankSync | null, latest: BankSync | null): BankSync | null {
  if (!polled) return latest;
  if (!latest) return polled;
  if (polled.id === latest.id) return polled.updated_at >= latest.updated_at ? polled : latest;
  return polled.created_at > latest.created_at ? polled : latest;
}

export function BankSyncCard({ account, onImported }: { account: Account; onImported: () => void }) {
  const load = useCallback(() => loadBankSync(account.id), [account.id]);
  const state = useLoad(load);
  const [polled, setPolled] = useState<BankSync | null>(null);
  const [tick, setTick] = useState(0);
  const [pollError, setPollError] = useState<unknown>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<unknown>(null);

  const latest = state.data?.syncs[0] ?? null;
  const current = newer(polled, latest);
  const currentId = current?.id ?? null;
  const live = current ? isLiveRun(current) : false;
  const { reload } = state;

  useEffect(() => {
    if (!currentId || !live) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      ok(api.GET("/api/syncs/{sync_id}", { params: { path: { sync_id: currentId } } })).then(
        (fresh) => {
          if (cancelled) return;
          setPolled(fresh);
          setPollError(null);
          if (isLiveRun(fresh)) {
            setTick((n) => n + 1);
          } else {
            reload();
            if (fresh.status === "done") onImported();
          }
        },
        (error: unknown) => {
          if (!cancelled) setPollError(error);
        },
      );
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [currentId, live, tick, reload, onImported]);

  async function start() {
    setStarting(true);
    setStartError(null);
    try {
      const run = await ok(api.POST("/api/accounts/{account_id}/syncs", { params: { path: { account_id: account.id } } }));
      setPolled(run);
      setTick((n) => n + 1);
      reload();
    } catch (error) {
      setStartError(error);
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="card stack" aria-labelledby="bank-sync-title">
      <h2 id="bank-sync-title">Bankabruf</h2>
      <p className="muted">PIN und Produkt-ID liegen in der Serverkonfiguration; Hausbuch fragt hier nie nach der PIN.</p>
      {state.error ? <ErrorNotice error={state.error} /> : null}
      {state.data === undefined ? (
        state.error ? null : <p className="muted">Lädt …</p>
      ) : state.data.connection === null ? (
        <ConnectionCreateForm account={account} onCreated={reload} />
      ) : (
        <>
          <ConnectionFacts connection={state.data.connection} />
          <div className="row">
            <button type="button" onClick={() => void start()} disabled={starting || live}>
              {starting ? "Startet …" : live ? "Abruf läuft …" : "Jetzt abrufen"}
            </button>
            <ActionDialog label="Zugang ändern" title="Bankabruf: Zugang ändern">
              {(close) => (
                <ConnectionEditForm
                  connection={state.data!.connection!}
                  onSaved={() => {
                    close();
                    reload();
                  }}
                />
              )}
            </ActionDialog>
          </div>
          <ErrorNotice error={startError} />
          {current ? <RunNow run={current} accountId={account.id} /> : null}
          <ErrorNotice error={pollError} />
          {state.data.syncs.length > 0 ? <RunHistory runs={state.data.syncs} /> : null}
        </>
      )}
    </section>
  );
}

const localDay = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium" });

/** The local calendar day of a timestamp plus `days` (an evening approval in Berlin is
 * already the next day in UTC). */
function dayAfter(timestamp: string, days: number): string {
  const day = new Date(timestamp);
  day.setDate(day.getDate() + days);
  return localDay.format(day);
}

function ConnectionFacts({ connection }: { connection: BankConnection }) {
  return (
    <Facts
      rows={[
        ["BLZ", connection.bank_code],
        ["FinTS-Adresse", connection.fints_url],
        ["Anmeldename", connection.login_masked],
        [
          "Freigabe",
          connection.sca_confirmed_at
            ? `zuletzt am ${dayAfter(connection.sca_confirmed_at, 0)}, gültig bis etwa ${dayAfter(connection.sca_confirmed_at, SCA_DAYS)} (geschätzt)`
            : "noch keine: beim ersten Abruf fragt die Bank in der S-pushTAN-App nach einer Freigabe",
        ],
        [
          "Letzter Abruf",
          connection.last_sync_at
            ? `${formatDateTime(connection.last_sync_at)}${connection.last_booking_on ? `, Umsätze bis ${formatDate(connection.last_booking_on)}` : ""}`
            : "noch keiner",
        ],
      ]}
    />
  );
}

/** A clock that ticks every second while `on` (for the approval countdown). */
function useNow(on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [on]);
  return now;
}

function ApprovalWait({ run }: { run: BankSync }) {
  const now = useNow(true);
  const left = Math.max(0, new Date(run.updated_at).getTime() + APPROVAL_WINDOW_MS - now);
  const minutes = Math.floor(left / 60_000);
  const seconds = Math.floor((left % 60_000) / 1000);
  return (
    <div className="notice notice-warn" role="status">
      <p>
        <strong>In der S-pushTAN-App freigeben.</strong> Die Bank hat eine Freigabe für den Abruf an Ihr Handy geschickt.
      </p>
      <p>
        {left > 0
          ? `Noch ${minutes}:${String(seconds).padStart(2, "0")} Minuten; danach bricht Hausbuch den Abruf ab.`
          : "Die fünf Minuten sind um; der Abruf wird gleich als abgebrochen gemeldet."}
      </p>
    </div>
  );
}

function linesSentence(run: BankSync): string {
  const added = run.lines_new ?? 0;
  const known = (run.lines_total ?? 0) - added;
  const first = added === 1 ? "1 neuer Umsatz" : `${added} neue Umsätze`;
  return known > 0 ? `${first}, ${known} schon vorhanden` : first;
}

/** The current (or last) run in plain words. */
function RunNow({ run, accountId }: { run: BankSync; accountId: string }) {
  switch (run.status) {
    case "needs_approval":
      return <ApprovalWait run={run} />;
    case "queued":
      return <p role="status">Der Abruf ist angestoßen und startet gleich …</p>;
    case "running":
      return <p role="status">Hausbuch fragt die Bank nach den Umsätzen …</p>;
    case "importing":
      return <p role="status">Die Umsätze der Bank werden importiert …</p>;
    case "done":
      return (
        <div className="notice notice-ok" role="status">
          <p>
            Letzter Abruf {formatDateTime(run.finished_at)}: {linesSentence(run)} importiert.{" "}
            {run.import_id && (run.lines_new ?? 0) > 0 ? (
              <Link href={`/accounts/${accountId}?tab=umsaetze&import=${run.import_id}`}>Die neuen Umsätze anzeigen</Link>
            ) : null}
          </p>
        </div>
      );
    case "nothing_new":
      return (
        <div className={run.error_code === "bank.balance_drift" ? "notice notice-warn" : "notice notice-info"} role="status">
          <p>
            Letzter Abruf {formatDateTime(run.finished_at)}: nichts Neues, alle Umsätze der Bank waren schon da.
            {run.error_code === "bank.balance_drift"
              ? " Der Kontostand der Bank weicht aber vom letzten abgeglichenen Saldo ab: fehlt ein Kontoauszug? Siehe „Zu prüfen“."
              : run.balance_checked
                ? " Der Kontostand der Bank passt zum letzten abgeglichenen Saldo."
                : ""}
          </p>
        </div>
      );
    case "failed":
      return (
        <div className="notice notice-error" role="status">
          <p>
            Letzter Abruf {formatDateTime(run.finished_at)} fehlgeschlagen:{" "}
            {(run.error_code && ruleCodeText(run.error_code)) || "Der Abruf ist fehlgeschlagen. Erneut abrufen."}
            {run.error_code ? <code className="error-code">{run.error_code}</code> : null}
          </p>
        </div>
      );
  }
}

function runResult(run: BankSync): string {
  if (run.status === "done") return linesSentence(run);
  if (run.status === "failed") return run.error_code ? `fehlgeschlagen (${run.error_code})` : "fehlgeschlagen";
  if (run.status === "nothing_new" && run.error_code === "bank.balance_drift") return "nichts Neues, Kontostand weicht ab";
  return SYNC_STATUS[run.status];
}

function runBalance(run: BankSync): string {
  if (run.balance_source) return BALANCE_SOURCE[run.balance_source];
  return run.balance_checked ? "mit dem Kontostand der Bank verglichen" : "–";
}

function RunHistory({ runs }: { runs: BankSync[] }) {
  return (
    <>
      <h3>Letzte Abrufe</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Gestartet</th>
              <th scope="col">Zeitraum</th>
              <th scope="col">Ergebnis</th>
              <th scope="col">Saldo</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>
                  {formatDateTime(run.started_at ?? run.created_at)}
                  <p className="cell-detail">{SYNC_TRIGGER[run.trigger]}</p>
                </td>
                <td>{run.requested_from ? `${formatDate(run.requested_from)} – ${formatDate(run.requested_to)}` : "–"}</td>
                <td>{runResult(run)}</td>
                <td>{runBalance(run)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// --- the connection's forms -------------------------------------------------------------

const CREATE_KEYS = ["bank_code", "fints_url", "login_name"] as const;
const CODE_FIELDS = { "bank.url_not_https": "fints_url" };

const BLZ_HINT = "8 Ziffern: in Ihrer IBAN die Stellen 5 bis 12, nach „DE“ und den zwei Prüfziffern. Sie können auch die ganze IBAN einfügen.";
const URL_HINT =
  "Beginnt mit https://. Ihre Sparkasse nennt sie auf ihrer Website unter „FinTS“ oder „HBCI PIN/TAN“; sonst steht sie in der Bankenliste der Deutschen Kreditwirtschaft.";
const LOGIN_HINT = "Der Anmeldename oder die Legitimations-ID, mit dem Sie sich im Online-Banking anmelden. Wird nur gekürzt angezeigt.";

function checkUrl(url: string): string | null {
  if (!url.trim()) return "Bitte die FinTS-Adresse Ihrer Bank eingeben.";
  return url.trim().startsWith("https://") ? null : "Die FinTS-Adresse muss mit https:// beginnen.";
}

function ConnectionCreateForm({ account, onCreated }: { account: Account; onCreated: () => void }) {
  const ids = useId();
  const [bankCode, setBankCode] = useState("");
  const [url, setUrl] = useState("");
  const [login, setLogin] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(CREATE_KEYS, CODE_FIELDS);

  function changeBankCode(value: string) {
    setBankCode(bankCodeFromIban(value) ?? value);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const messages: Record<string, string[]> = {};
    if (!/^[0-9]{8}$/.test(bankCode.trim())) messages.bank_code = ["Die BLZ hat genau 8 Ziffern."];
    const urlMessage = checkUrl(url);
    if (urlMessage) messages.fints_url = [urlMessage];
    if (!login.trim()) messages.login_name = ["Bitte den Anmeldenamen eingeben."];
    if (Object.keys(messages).length > 0) return form.invalid(messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.POST("/api/accounts/{account_id}/bank-connection", {
          params: { path: { account_id: account.id } },
          body: { account_id: account.id, bank_code: bankCode.trim(), fints_url: url.trim(), login_name: login.trim() },
        }),
      );
      onCreated();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate aria-label="Bankabruf einrichten">
      <p>
        Hausbuch kann die Umsätze dieses Kontos selbst bei der Bank abrufen (FinTS, nur lesen). Beim ersten Abruf geben Sie ihn in
        der S-pushTAN-App frei.
      </p>
      <div className="grid">
        <Field id={`${ids}-blz`} label="BLZ" hint={BLZ_HINT} errors={form.of("bank_code")}>
          <input type="text" inputMode="numeric" value={bankCode} onChange={(e) => changeBankCode(e.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
        <Field id={`${ids}-url`} label="FinTS-Adresse" hint={URL_HINT} errors={form.of("fints_url")}>
          <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off" spellCheck={false} placeholder="https://" />
        </Field>
        <Field id={`${ids}-login`} label="Anmeldename" hint={LOGIN_HINT} errors={form.of("login_name")}>
          <input type="text" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
      </div>
      <FormBanner errors={form.errors} />
      <div className="row">
        <Submit busy={busy}>Bankabruf einrichten</Submit>
      </div>
    </form>
  );
}

const EDIT_KEYS = ["fints_url", "login_name"] as const;

/** PATCH the URL and/or the login; an empty login keeps the stored one (it is shown masked
 * only). The BLZ cannot change: another BLZ is another bank. */
function ConnectionEditForm({ connection, onSaved }: { connection: BankConnection; onSaved: () => void }) {
  const ids = useId();
  const [url, setUrl] = useState(connection.fints_url);
  const [login, setLogin] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(EDIT_KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const urlMessage = checkUrl(url);
    if (urlMessage) return form.invalid({ fints_url: [urlMessage] });
    const body: { fints_url?: string; login_name?: string } = {};
    if (url.trim() !== connection.fints_url) body.fints_url = url.trim();
    if (login.trim()) body.login_name = login.trim();
    if (Object.keys(body).length === 0) return onSaved();
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.PATCH("/api/accounts/{account_id}/bank-connection", {
          params: { path: { account_id: connection.account_id } },
          body,
        }),
      );
      onSaved();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate aria-label="Zugang ändern">
      <p className="muted">
        Eine neue Adresse oder ein neuer Anmeldename verwirft die gespeicherte Verbindung: der nächste Abruf fragt wieder nach
        einer Freigabe in der S-pushTAN-App.
      </p>
      <Field id={`${ids}-url`} label="FinTS-Adresse" hint={URL_HINT} errors={form.of("fints_url")}>
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      <Field
        id={`${ids}-login`}
        label="Neuer Anmeldename"
        hint={`Leer lassen, um ${connection.login_masked} zu behalten.`}
        errors={form.of("login_name")}
      >
        <input type="text" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      <FormBanner errors={form.errors} />
      <div className="row">
        <Submit busy={busy}>Speichern</Submit>
      </div>
    </form>
  );
}
