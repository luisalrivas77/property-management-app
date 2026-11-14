"use client";

// Bankabruf (phase-5.md §11, D115–D120): the account's FinTS connection and its runs.
//
// - No connection yet: the form (BLZ, FinTS-Adresse, Anmeldename). The PIN is never asked for
//   here; it and the product id are server secrets (D119), and the card says so.
// - A connection: its facts, "Zugang ändern", the **Jetzt abrufen** button, the current run
//   in plain words, and the run history. While a run is queued, running, waiting for the
//   pushTAN approval or importing, the card polls GET /syncs/{id} every 2 s; a run waiting for
//   the approval shows the five minutes the job waits for it (counted from when the run
//   entered that state, its updated_at). Once those five minutes are over the button is
//   enabled again as "Erneut abrufen" (a run whose job is gone, after a redeploy mid-wait,
//   would otherwise stay locked), the card polls every 10 s for one more minute, then stops.
//   A queued or running row untouched for the approval wait plus five more minutes (the API's
//   rules.bank_sync.is_stale) has lost its job, for instance one that failed before its first
//   write: the card says so, offers "Erneut abrufen" (the API restarts such a row) and stops
//   polling the same way.
//   Every count shown is the API's; nothing is summed.
// - „Täglich automatisch abrufen“ (D120, step 2): the connection's `auto_sync`, PATCHed at
//   once. The daily run (06:30 UTC) never waits for the phone: when the bank asks for an
//   approval, the run stops at `needs_approval` with trigger `scheduled` and a card appears
//   under „Zu prüfen“. Such a row is not a live wait here (no countdown, no polling, the
//   button enabled): „Jetzt abrufen“ restarts it as a manual run, which then waits as above.
// - „Kontoauszüge als PDF abrufen“ (D154, step 2b): the connection's `fetch_statements`,
//   PATCHed at once like the daily switch. Each run then also asks the bank for its own PDF
//   statements after the Umsätze; the card lists the stored ones (GET
//   /accounts/{id}/bank-statements), each linking to its document, and how many the latest
//   run brought (the rows whose sync_id is that run's, counted here; no amount is summed).
// - Phase 6 step 3 (phase-6.md D163, D173): each connection names its online-banking login's
//   credential slot; its PIN is the API container's secret HAUSBUCH_FINTS_PIN_<SLOT> (slot
//   `primary`: HAUSBUCH_FINTS_PIN), set in the Scaleway console (runbook step 17). The form
//   and the facts name that variable and say whether it is set (GET /bank-credential-slots,
//   names only). The approval texts are the connection's `approval_text` (the DKB app for a
//   DKB connection), and the daily switch stays locked while `auto_sync_allowed` is false
//   (DKB before two manual syncs in a row without an approval).
import Link from "next/link";
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodeText } from "@/api/errors";
import type { Account, BankConnection, BankStatement, BankSync, StatementsRefusal, SyncStatus } from "@/api/schemas";
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
/** A queued or running row untouched this much longer than the approval wait has lost its
 * job (rules.bank_sync.is_stale: the approval timeout plus STALE_GRACE_S); the API then
 * restarts it (start_sync). */
const STALE_GRACE_MS = 5 * 60 * 1000;
/** After the window: a slower poll, long enough for the job to record its timeout. */
const EXPIRED_POLL_MS = 10_000;
const EXPIRED_POLL_FOR_MS = 60_000;
/** The bank's SCA exemption lasts about 180 days (§3); shown as an estimate only. */
const SCA_DAYS = 180;

export function isLiveRun(run: BankSync): boolean {
  return LIVE.includes(run.status);
}

/** A daily run the bank asked to approve: its job already ended the dialog, nobody waits
 * (D120); a person's „Jetzt abrufen“ restarts it. */
function scheduledApproval(run: BankSync): boolean {
  return run.status === "needs_approval" && run.trigger === "scheduled";
}

/** When a manual needs_approval run's wait ends (ms since the epoch), else null. */
function approvalEndsAt(run: BankSync): number | null {
  return run.status === "needs_approval" && !scheduledApproval(run) ? new Date(run.updated_at).getTime() + APPROVAL_WINDOW_MS : null;
}

/** When a queued or running row counts as stuck (its job is gone), else null. Never for
 * importing: the API does not restart that. */
function staleAt(run: BankSync): number | null {
  return run.status === "queued" || run.status === "running"
    ? new Date(run.updated_at).getTime() + APPROVAL_WINDOW_MS + STALE_GRACE_MS
    : null;
}

/** From when the button may start a still-live run again: a manual approval's five minutes
 * are over, or a queued or running row is stuck. */
function restartableAt(run: BankSync): number | null {
  return approvalEndsAt(run) ?? staleAt(run);
}

/** How often the card polls the run: every 2 s while it is live, every 10 s for a minute
 * after an approval's five minutes are over or a row turned stuck, then never (a row whose
 * job is gone, or a daily run that waits for a person). */
function pollInterval(run: BankSync | null, now: number): number | null {
  if (!run || !isLiveRun(run) || scheduledApproval(run)) return null;
  const endsAt = restartableAt(run);
  if (endsAt === null || now < endsAt) return POLL_MS;
  return now < endsAt + EXPIRED_POLL_FOR_MS ? EXPIRED_POLL_MS : null;
}

/** The eight digits of a German IBAN's Bankleitzahl (its characters 5–12), or null. */
export function bankCodeFromIban(raw: string): string | null {
  const compact = raw.replace(/\s+/g, "").toUpperCase();
  return /^DE[0-9]{20}$/.test(compact) ? compact.slice(4, 12) : null;
}

/** A credential slot's name (rules.bank_sync.SLOT_PATTERN, phase 6 D163). */
const SLOT_PATTERN = /^[a-z][a-z0-9_]{0,31}$/;
const PRIMARY_SLOT = "primary";

/** The API container's secret that holds a slot's PIN (rules.bank_sync.pin_variable). */
export function pinVariable(slot: string): string {
  return slot === PRIMARY_SLOT ? "HAUSBUCH_FINTS_PIN" : `HAUSBUCH_FINTS_PIN_${slot.toUpperCase()}`;
}

async function loadSlots(): Promise<string[]> {
  return (await ok(api.GET("/api/bank-credential-slots"))).slots ?? [];
}

async function loadBankSync(
  accountId: string,
): Promise<{ connection: BankConnection | null; syncs: BankSync[]; statements: BankStatement[]; slots: string[] }> {
  const path = { params: { path: { account_id: accountId } } };
  let connection: BankConnection | null;
  try {
    connection = await ok(api.GET("/api/accounts/{account_id}/bank-connection", path));
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return { connection: null, syncs: [], statements: [], slots: await loadSlots() };
    throw error;
  }
  const [syncs, statements, slots] = await Promise.all([
    ok(api.GET("/api/accounts/{account_id}/syncs", path)),
    ok(api.GET("/api/accounts/{account_id}/bank-statements", path)),
    loadSlots(),
  ]);
  return { connection, syncs, statements, slots };
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
  const endsAt = current ? restartableAt(current) : null;
  const now = useNow(endsAt !== null);
  const expired = endsAt !== null && now >= endsAt;
  // A run waiting for an approval past its five minutes can be started again, so can a stuck
  // queued or running row, and so can a daily run the bank asked to approve (nobody waits).
  const busy = current ? isLiveRun(current) && !expired && !scheduledApproval(current) : false;
  const pollMs = pollInterval(current, now);
  const { reload } = state;

  useEffect(() => {
    if (!currentId || pollMs === null) return;
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
    }, pollMs);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [currentId, pollMs, tick, reload, onImported]);

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
        <ConnectionCreateForm account={account} slots={state.data.slots} onCreated={reload} />
      ) : (
        <>
          <ConnectionFacts connection={state.data.connection} slots={state.data.slots} />
          <ConnectionSwitch
            connection={state.data.connection}
            field="auto_sync"
            label="Täglich automatisch abrufen"
            hint={AUTO_SYNC_HINT}
            locked={state.data.connection.auto_sync_allowed ? null : AUTO_SYNC_WAITS}
            onSaved={reload}
          />
          <ConnectionSwitch
            connection={state.data.connection}
            field="fetch_statements"
            label="Kontoauszüge als PDF abrufen"
            hint={FETCH_STATEMENTS_HINT}
            onSaved={reload}
          />
          {state.data.connection.statements_refusal ? (
            <StatementsRefused
              refusal={state.data.connection.statements_refusal}
              bankName={state.data.connection.bank_name ?? null}
            />
          ) : null}
          <div className="row">
            <button type="button" onClick={() => void start()} disabled={starting || busy}>
              {starting ? "Startet …" : busy ? "Abruf läuft …" : expired ? "Erneut abrufen" : "Jetzt abrufen"}
            </button>
            <ActionDialog label="Zugang ändern" title="Bankabruf: Zugang ändern">
              {(close) => (
                <ConnectionEditForm
                  connection={state.data!.connection!}
                  slots={state.data!.slots}
                  onSaved={() => {
                    close();
                    reload();
                  }}
                />
              )}
            </ActionDialog>
          </div>
          <ErrorNotice error={startError} />
          {current ? (
            <RunNow
              run={current}
              accountId={account.id}
              approvalText={state.data.connection.approval_text}
              now={now}
              stuck={expired && staleAt(current) !== null}
            />
          ) : null}
          <ErrorNotice error={pollError} />
          {state.data.syncs.length > 0 ? <RunHistory runs={state.data.syncs} /> : null}
          {state.data.connection.fetch_statements || state.data.statements.length > 0 ? (
            <StatementList statements={state.data.statements} latest={latest} />
          ) : null}
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

/** Where a slot's PIN goes and whether the server has it (names only, never a PIN). */
function PinPlace({ slot, slots }: { slot: string; slots: string[] }) {
  const variable = pinVariable(slot);
  return slots.includes(slot) ? (
    <>
      <code>{variable}</code>: auf dem Server gesetzt.
    </>
  ) : (
    <>
      <code>{variable}</code>: <strong>noch nicht gesetzt.</strong> In der Scaleway-Konsole beim API-Container als geheime
      Umgebungsvariable (Secret) mit genau diesem Namen anlegen, als Wert die PIN dieses Online-Banking-Zugangs, und den
      Container neu bereitstellen (Runbook Schritt 17). Bis dahin endet jeder Abruf mit „PIN fehlt“.
    </>
  );
}

function ConnectionFacts({ connection, slots }: { connection: BankConnection; slots: string[] }) {
  return (
    <Facts
      rows={[
        ["BLZ", connection.bank_code],
        ...(connection.bank_name ? ([["Bank (laut FinTS)", connection.bank_name]] as [string, ReactNode][]) : []),
        ["FinTS-Adresse", connection.fints_url],
        ["Anmeldename", connection.login_masked],
        [
          "PIN-Variable",
          <>
            Zugang „{connection.credential_slot}“, <PinPlace slot={connection.credential_slot} slots={slots} />
          </>,
        ],
        [
          "Freigabe",
          connection.sca_confirmed_at
            ? `zuletzt am ${dayAfter(connection.sca_confirmed_at, 0)}, gültig bis etwa ${dayAfter(connection.sca_confirmed_at, SCA_DAYS)} (geschätzt)`
            : `noch keine: beim ersten Abruf fragt die Bank nach einer Freigabe („${connection.approval_text}“)`,
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

const AUTO_SYNC_HINT = (
  <>
    Hausbuch fragt die Bank jeden Morgen gegen halb acht (im Sommer gegen halb neun) und holt nur die Umsätze seit dem
    letzten Abruf, zur Sicherheit ab zwei Wochen davor; schon vorhandene erkennt Hausbuch und bucht sie nicht doppelt.
    Verlangt die Bank dafür einmal eine Freigabe, erscheint unter „Zu prüfen“ eine Karte; bis dahin ruft Hausbuch dieses
    Konto nicht automatisch ab, damit keine weiteren Anfragen aufs Handy kommen.
  </>
);

const AUTO_SYNC_WAITS = (
  <>
    Noch nicht möglich: diese Bank (DKB) fragt womöglich bei jedem Abruf nach einer Freigabe in der DKB-App, und dann käme
    jeden Morgen eine Anfrage aufs Handy. Der tägliche Abruf lässt sich einschalten, sobald zwei Abrufe von Hand
    nacheinander ohne Freigabe durchgelaufen sind. Bis dahin „Jetzt abrufen“ von Hand nutzen oder den CSV-Export hochladen.
  </>
);

const FETCH_STATEMENTS_HINT = (
  <>
    Bei jedem Abruf holt Hausbuch nach den Umsätzen auch die Kontoauszüge der Bank als PDF, die noch fehlen, nach ihrer
    Nummer. Sie liegen dann unten in der Liste und im Jahresarchiv unter „kontoauszuege/“, als Nachweis, den das
    Finanzamt kennt. Gebucht wird weiter nur aus den Umsätzen; ein Auszug ändert keine Buchung. Hausbuch fragt nur nach
    Auszügen, die die Bank schon erstellt hat, und fordert nie einen neuen an.
  </>
);

/** What a permission refusal code of the PDF statement fetch means (14 Nov 2026). */
const STATEMENTS_REFUSAL_MEANING: Record<string, string> = { "9370": "keine Auftragsberechtigung" };

/** The bank refuses this access the PDF statements (14 Nov 2026: 9370 on HKEKP): what it
 * means and how to get it unlocked. Shown while the switch is on and no later run's request
 * was answered without that refusal. */
function StatementsRefused({ refusal, bankName }: { refusal: StatementsRefusal; bankName: string | null }) {
  const meaning = STATEMENTS_REFUSAL_MEANING[refusal.code];
  const code = meaning ? `Code ${refusal.code}: ${meaning}` : `Code ${refusal.code}`;
  return (
    <p className="notice notice-warn" role="status">
      {bankName ? `Die ${bankName}` : "Die Bank"} erlaubt diesem Online-Banking-Zugang den Abruf der Kontoauszüge als PDF
      nicht ({code}). So lässt es sich freischalten: im Online-Banking {bankName ? `der ${bankName}` : "der Bank"} die
      Kontoauszüge dieses Kontos auf das elektronische Postfach umstellen, oder die Bank bitten, den Abruf elektronischer
      Kontoauszüge über FinTS/HBCI für diesen Zugang freizuschalten. Hausbuch fragt bei jedem Abruf erneut; sobald es klappt,
      verschwindet dieser Hinweis.
    </p>
  );
}

/** A switch of the connection (the daily fetch, D120; the PDF statements, D154), saved as
 * soon as it is ticked. */
function ConnectionSwitch({
  connection,
  field,
  label,
  hint,
  locked = null,
  onSaved,
}: {
  connection: BankConnection;
  field: "auto_sync" | "fetch_statements";
  label: string;
  hint: ReactNode;
  /** Why the switch cannot be turned on yet (D173 (5)); turning it off stays possible. */
  locked?: ReactNode;
  onSaved: () => void;
}) {
  const id = useId();
  const [saved, setSaved] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const on = saved ?? connection[field];

  async function change(value: boolean) {
    setBusy(true);
    setError(null);
    try {
      const updated = await ok(
        api.PATCH("/api/accounts/{account_id}/bank-connection", {
          params: { path: { account_id: connection.account_id } },
          body: field === "auto_sync" ? { auto_sync: value } : { fetch_statements: value },
        }),
      );
      setSaved(updated[field]);
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="field">
      <label className="inline" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={on}
          disabled={busy || (locked !== null && !on)}
          onChange={(e) => void change(e.target.checked)}
        />{" "}
        {label}
      </label>
      {locked !== null ? <p className="hint">{locked}</p> : null}
      <p className="muted hint">{hint}</p>
      <ErrorNotice error={error} />
    </div>
  );
}

/** The bank's PDF statements stored for the account (D154), newest first as the API lists
 * them, each opening its document; and how many the latest run brought. */
function StatementList({ statements, latest }: { statements: BankStatement[]; latest: BankSync | null }) {
  const fromLatest = latest ? statements.filter((s) => s.sync_id === latest.id).length : 0;
  return (
    <>
      <h3>Kontoauszüge der Bank (PDF)</h3>
      {statements.length === 0 ? (
        <p className="muted">
          Noch keiner abgerufen. Beim nächsten Abruf fragt Hausbuch die Bank nach ihren Kontoauszügen als PDF.
        </p>
      ) : (
        <>
          {fromLatest > 0 ? (
            <p className="muted">
              Beim letzten Abruf {fromLatest === 1 ? "1 Kontoauszug" : `${fromLatest} Kontoauszüge`} neu abgerufen.
            </p>
          ) : null}
          <ul>
            {statements.map((s) => (
              <li key={s.id}>
                <Link href={`/documents/${s.document_id}`}>
                  Kontoauszug {s.number}/{s.year}
                </Link>{" "}
                <span className="muted">abgerufen {formatDateTime(s.fetched_at)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

/** A clock that ticks every second while `on` (for the approval countdown and a stuck row). */
function useNow(on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [on]);
  return now;
}

function ApprovalWait({ run, approvalText, now }: { run: BankSync; approvalText: string; now: number }) {
  const left = Math.max(0, new Date(run.updated_at).getTime() + APPROVAL_WINDOW_MS - now);
  const minutes = Math.floor(left / 60_000);
  const seconds = Math.floor((left % 60_000) / 1000);
  return (
    <div className="notice notice-warn" role="status">
      <p>
        <strong>{approvalText}.</strong> Die Bank hat eine Freigabe für den Abruf an Ihr Handy geschickt.
      </p>
      <p>
        {left > 0
          ? `Noch ${minutes}:${String(seconds).padStart(2, "0")} Minuten; danach bricht Hausbuch den Abruf ab.`
          : "Die Freigabe wurde nicht innerhalb von fünf Minuten bestätigt. „Erneut abrufen“ drücken und die neue Anfrage in der App freigeben."}
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

/** The current (or last) run in plain words; a daily run says so. */
function RunNow({
  run,
  accountId,
  approvalText,
  now,
  stuck,
}: {
  run: BankSync;
  accountId: string;
  approvalText: string;
  now: number;
  stuck: boolean;
}) {
  const last = run.trigger === "scheduled" ? "Letzter automatischer Abruf" : "Letzter Abruf";
  if (stuck) {
    return (
      <div className="notice notice-warn" role="status">
        <p>
          Der Abruf vom {formatDateTime(run.created_at)} kommt nicht voran, zum Beispiel nach einem Neustart des Servers.
          „Erneut abrufen“ drücken, um ihn neu zu starten.
        </p>
      </div>
    );
  }
  switch (run.status) {
    case "needs_approval":
      return scheduledApproval(run) ? (
        <div className="notice notice-warn" role="status">
          <p>
            <strong>
              Der automatische Abruf braucht eine Freigabe: „Jetzt abrufen“ drücken. {approvalText}.
            </strong>{" "}
            Die Bank hat beim Abruf am {formatDateTime(run.updated_at)} nach einer Freigabe gefragt; Hausbuch hat ihn beendet,
            weil niemand aufs Handy schaut. „Jetzt abrufen“ drücken und die neue Anfrage gleich in der App freigeben. Bis dahin
            ruft Hausbuch dieses Konto nicht automatisch ab.
          </p>
        </div>
      ) : (
        <ApprovalWait run={run} approvalText={approvalText} now={now} />
      );
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
            {last} {formatDateTime(run.finished_at)}: {linesSentence(run)} importiert.{" "}
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
            {last} {formatDateTime(run.finished_at)}: nichts Neues, alle Umsätze der Bank waren schon da.
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
            {last} {formatDateTime(run.finished_at)} fehlgeschlagen:{" "}
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

const CREATE_KEYS = ["bank_code", "fints_url", "login_name", "credential_slot"] as const;
const CODE_FIELDS = { "bank.url_not_https": "fints_url" };

const BLZ_HINT = "8 Ziffern: in Ihrer IBAN die Stellen 5 bis 12, nach „DE“ und den zwei Prüfziffern. Sie können auch die ganze IBAN einfügen.";
const URL_HINT =
  "Beginnt mit https://. Ihre Sparkasse nennt sie auf ihrer Website unter „FinTS“ oder „HBCI PIN/TAN“; sonst steht sie in der Bankenliste der Deutschen Kreditwirtschaft.";
const LOGIN_HINT = "Der Anmeldename oder die Legitimations-ID, mit dem Sie sich im Online-Banking anmelden. Wird nur gekürzt angezeigt.";
const SLOT_HINT =
  "Ein kurzer Name für diesen Online-Banking-Zugang, klein geschrieben, z. B. „dkb“ oder „sparkasse_b“; „primary“ ist der Zugang des Mietkontos. Konten unter demselben Zugang tragen denselben Namen. Die PIN geben Sie hier nie ein.";

function checkSlot(slot: string): string | null {
  return SLOT_PATTERN.test(slot.trim())
    ? null
    : "Kleinbuchstaben, Ziffern und _ , beginnend mit einem Buchstaben, höchstens 32 Zeichen (z. B. dkb).";
}

function checkUrl(url: string): string | null {
  if (!url.trim()) return "Bitte die FinTS-Adresse Ihrer Bank eingeben.";
  return url.trim().startsWith("https://") ? null : "Die FinTS-Adresse muss mit https:// beginnen.";
}

function ConnectionCreateForm({ account, slots, onCreated }: { account: Account; slots: string[]; onCreated: () => void }) {
  const ids = useId();
  const [bankCode, setBankCode] = useState("");
  const [url, setUrl] = useState("");
  const [login, setLogin] = useState("");
  const [slot, setSlot] = useState(PRIMARY_SLOT);
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
    const slotMessage = checkSlot(slot);
    if (slotMessage) messages.credential_slot = [slotMessage];
    if (Object.keys(messages).length > 0) return form.invalid(messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.POST("/api/accounts/{account_id}/bank-connection", {
          params: { path: { account_id: account.id } },
          // The slot names the PIN's container secret (phase 6 D163); the PIN itself never comes here.
          body: {
            account_id: account.id,
            bank_code: bankCode.trim(),
            fints_url: url.trim(),
            login_name: login.trim(),
            credential_slot: slot.trim(),
          },
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
        der App Ihrer Bank frei (Sparkasse: S-pushTAN-App, DKB: DKB-App).
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
        <Field id={`${ids}-slot`} label="Name des Zugangs" hint={SLOT_HINT} errors={form.of("credential_slot")}>
          <input type="text" value={slot} onChange={(e) => setSlot(e.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
      </div>
      {checkSlot(slot) === null ? (
        <p>
          Die PIN dieses Zugangs liegt nur auf dem Server, in der Variable <PinPlace slot={slot.trim()} slots={slots} />
        </p>
      ) : null}
      <FormBanner errors={form.errors} />
      <div className="row">
        <Submit busy={busy}>Bankabruf einrichten</Submit>
      </div>
    </form>
  );
}

const EDIT_KEYS = ["fints_url", "login_name", "credential_slot"] as const;

/** PATCH the URL, the login and/or the slot; an empty login keeps the stored one (it is shown
 * masked only). The BLZ cannot change: another BLZ is another bank. A new slot keeps the
 * stored connection: only the PIN's variable changes. */
function ConnectionEditForm({
  connection,
  slots,
  onSaved,
}: {
  connection: BankConnection;
  slots: string[];
  onSaved: () => void;
}) {
  const ids = useId();
  const [url, setUrl] = useState(connection.fints_url);
  const [login, setLogin] = useState("");
  const [slot, setSlot] = useState(connection.credential_slot);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(EDIT_KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const urlMessage = checkUrl(url);
    if (urlMessage) return form.invalid({ fints_url: [urlMessage] });
    const slotMessage = checkSlot(slot);
    if (slotMessage) return form.invalid({ credential_slot: [slotMessage] });
    const body: { fints_url?: string; login_name?: string; credential_slot?: string } = {};
    if (url.trim() !== connection.fints_url) body.fints_url = url.trim();
    if (login.trim()) body.login_name = login.trim();
    if (slot.trim() !== connection.credential_slot) body.credential_slot = slot.trim();
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
        einer Freigabe („{connection.approval_text}“). Ein neuer Name des Zugangs ändert nur, aus welcher Variable der Server
        die PIN liest.
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
      <Field id={`${ids}-slot`} label="Name des Zugangs" hint={SLOT_HINT} errors={form.of("credential_slot")}>
        <input type="text" value={slot} onChange={(e) => setSlot(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      {checkSlot(slot) === null ? (
        <p className="muted">
          PIN-Variable: <PinPlace slot={slot.trim()} slots={slots} />
        </p>
      ) : null}
      <FormBanner errors={form.errors} />
      <div className="row">
        <Submit busy={busy}>Speichern</Submit>
      </div>
    </form>
  );
}
