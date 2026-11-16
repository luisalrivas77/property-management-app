"use client";

// Bankabruf (phase-5.md §11, D115–D120): the account's FinTS connection and its runs.
//
// - No connection yet: the form (BLZ, FinTS-Adresse, Anmeldename, Name des Zugangs and, while
//   the slot has no PIN from either place, „PIN (Online-Banking)“). The product id stays a
//   server secret (D119).
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
//   credential slot; its PIN can be the API container's secret HAUSBUCH_FINTS_PIN_<SLOT> (slot
//   `primary`: HAUSBUCH_FINTS_PIN), set in the Scaleway console (runbook step 17), and the
//   card says whether it is set (GET /bank-credential-slots, names only). The approval texts
//   are the connection's `approval_text` (the DKB app for a DKB connection), and the daily
//   switch stays locked while `auto_sync_allowed` is false (DKB before two manual syncs in a
//   row without an approval).
// - Phase 6 D196: the PIN can also be typed here, the way the runbook now leads with (PUT
//   /bank-credentials/{slot}); the API stores it encrypted and it wins over the variable. The input is a password field that is
//   never prefilled and is emptied once saved; no response carries the PIN back, so the card
//   shows only where it comes from (`credential_source`) and since when. „PIN löschen“
//   (DELETE) falls back to the variable. After the server's key changed, a stored PIN or
//   Anmeldename no longer decrypts (`credential_readable`, `name_readable` false) and the card
//   asks for it again.
// - D196 amended (16 Nov 2026): a PIN is saved for the FinTS address the form shows (the PUT
//   carries it) and is sent only there. After the connection's address changed, the stored
//   PIN is refused (`credential_server_matches` false) and the card asks to type it again.
import Link from "next/link";
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodeText } from "@/api/errors";
import type {
  Account,
  AppCredential,
  BankConnection,
  BankStatement,
  BankSync,
  CredentialSource,
  StatementsRefusal,
  SyncStatus,
} from "@/api/schemas";
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

/** The slots with a PIN, by place: the server's variables and the PINs typed into the app
 * (names and dates only, never a PIN). */
type Slots = { server: string[]; app: AppCredential[] };

async function loadSlots(): Promise<Slots> {
  const answer = await ok(api.GET("/api/bank-credential-slots"));
  return { server: answer.slots ?? [], app: answer.app_slots ?? [] };
}

async function loadBankSync(
  accountId: string,
): Promise<{ connection: BankConnection | null; syncs: BankSync[]; statements: BankStatement[]; slots: Slots }> {
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

/** Where a slot's PIN comes from at the next sync (D196's precedence: the app's, then the
 * server's variable). */
type PinInfo = { source: CredentialSource; setAt: string | null; readable: boolean; serverMatches: boolean };

/** A slot's PIN without a connection: whether it was typed for this address is unknown
 * here, so it counts as matching; the connection's card says otherwise once it exists. */
function slotPin(slot: string, slots: Slots): PinInfo {
  const app = slots.app.find((c) => c.slot === slot);
  if (app) return { source: "app", setAt: app.set_at, readable: app.readable, serverMatches: true };
  return { source: slots.server.includes(slot) ? "server" : "none", setAt: null, readable: true, serverMatches: true };
}

function connectionPin(connection: BankConnection): PinInfo {
  return {
    source: connection.credential_source,
    setAt: connection.credential_set_at ?? null,
    readable: connection.credential_readable,
    serverMatches: connection.credential_server_matches,
  };
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
      <p className="muted">
        Die PIN Ihres Online-Bankings speichert Hausbuch nur verschlüsselt und zeigt sie nie wieder an; die Produkt-ID liegt in
        der Serverkonfiguration.
      </p>
      {state.error ? <ErrorNotice error={state.error} /> : null}
      {state.data === undefined ? (
        state.error ? null : <p className="muted">Lädt …</p>
      ) : state.data.connection === null ? (
        <>
          <ConnectionCreateForm account={account} slots={state.data.slots} onCreated={reload} />
          <UnusedPins slots={state.data.slots} onChanged={reload} />
        </>
      ) : (
        <>
          <ConnectionFacts connection={state.data.connection} />
          <PinPanel
            slot={state.data.connection.credential_slot}
            fintsUrl={state.data.connection.fints_url}
            pin={connectionPin(state.data.connection)}
            serverSet={state.data.slots.server.includes(state.data.connection.credential_slot)}
            onChanged={reload}
          />
          <UnusedPins slots={state.data.slots} onChanged={reload} />
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

/** Where a slot's PIN comes from, in one sentence (names and dates only, never a PIN). */
function pinSentence(slot: string, pin: PinInfo): ReactNode {
  if (pin.source === "app" && pin.readable && !pin.serverMatches) {
    return (
      <>
        <strong>Bitte die PIN einmal neu eingeben</strong>: Hausbuch sendet eine gespeicherte PIN nur an die FinTS-Adresse,
        für die sie eingegeben wurde. Eine PIN, die vor dem 16.11.2026 eingegeben wurde, nennt noch keine Adresse; sonst wurde
        die FinTS-Adresse dieses Zugangs seither geändert. „PIN ändern“ wählen und die PIN für diese Adresse neu eingeben.
      </>
    );
  }
  if (pin.source === "app") {
    return pin.readable ? (
      <>PIN in der App gespeichert am {pin.setAt ? dayAfter(pin.setAt, 0) : "–"} (verschlüsselt).</>
    ) : (
      <>
        <strong>Die gespeicherte PIN lässt sich nicht mehr lesen</strong>: der Schlüssel des Servers wurde gewechselt. Bitte
        „PIN ändern“ wählen und die PIN neu eingeben.
      </>
    );
  }
  if (pin.source === "server") {
    return (
      <>
        PIN auf dem Server gesetzt (<code>{pinVariable(slot)}</code>).
      </>
    );
  }
  return <strong>Noch keine PIN hinterlegt.</strong>;
}

const PIN_LABEL = "PIN (Online-Banking)";
const PIN_HINT =
  "Die PIN, mit der Sie sich im Online-Banking anmelden. Hausbuch speichert sie verschlüsselt und zeigt sie nie wieder an, auch Ihnen nicht.";
const PIN_LENGTH = "Die PIN hat 5 bis 64 Zeichen.";
const PIN_KEYS = ["pin"] as const;

/** A PIN's length in characters, as the API counts them (code points, not UTF-16 units,
 * after trimming the surrounding blanks, D196). */
function checkPin(pin: string): string | null {
  const length = [...pin.trim()].length;
  return length >= 5 && length <= 64 ? null : PIN_LENGTH;
}

/** The password input of a PIN: never prefilled, no browser autofill of a saved password.
 * `Field` clones its child with the id and the aria links of its label, hint and errors, so
 * they are passed on to the input. */
function PinInput({
  value,
  onChange,
  ...linked
}: {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}) {
  return (
    <input
      {...linked}
      type="password"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      autoComplete="new-password"
      autoCapitalize="off"
      spellCheck={false}
    />
  );
}

/** PUT the slot's PIN (D196). The PIN goes out in this one request body and is never read back;
 * the input is emptied once it is saved. It is saved for `fintsUrl`, the address the form
 * belongs to, and sent only there (D196 amended). */
async function savePin(slot: string, pin: string, fintsUrl: string): Promise<void> {
  await ok(
    api.PUT("/api/bank-credentials/{slot}", { params: { path: { slot } }, body: { pin, fints_url: fintsUrl } }),
  );
}

function PinForm({ slot, fintsUrl, onSaved }: { slot: string; fintsUrl: string; onSaved: () => void }) {
  const id = useId();
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(PIN_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const message = checkPin(pin);
    if (message) return form.invalid({ pin: [message] });
    setBusy(true);
    form.clear();
    try {
      await savePin(slot, pin, fintsUrl);
      setPin("");
      onSaved();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate aria-label="PIN speichern">
      <Field id={`${id}-pin`} label={PIN_LABEL} hint={PIN_HINT} errors={form.of("pin")}>
        <PinInput value={pin} onChange={setPin} />
      </Field>
      <FormBanner errors={form.errors} />
      <div className="row">
        <Submit busy={busy}>PIN speichern</Submit>
      </div>
    </form>
  );
}

/** The connection's PIN (D196): where it comes from and the buttons to set, change or delete
 * the one typed into the app. */
function PinPanel({
  slot,
  fintsUrl,
  pin,
  serverSet,
  onChanged,
}: {
  slot: string;
  fintsUrl: string;
  pin: PinInfo;
  serverSet: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function remove() {
    const after = serverSet
      ? "Danach nutzt der Abruf wieder die PIN auf dem Server."
      : "Danach endet jeder Abruf mit „PIN fehlt“, bis wieder eine PIN gespeichert ist.";
    if (!window.confirm(`Die in Hausbuch gespeicherte PIN für den Zugang „${slot}“ löschen? ${after}`)) return;
    setBusy(true);
    setError(null);
    try {
      await ok(api.DELETE("/api/bank-credentials/{slot}", { params: { path: { slot } } }));
      onChanged();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const change = (label: string) => (
    <ActionDialog label={label} title={`Bankabruf: ${label}`}>
      {(close) => (
        <PinForm
          slot={slot}
          fintsUrl={fintsUrl}
          onSaved={() => {
            close();
            onChanged();
          }}
        />
      )}
    </ActionDialog>
  );

  return (
    <div className="stack">
      <h3>PIN</h3>
      {pin.source === "none" ? (
        <>
          <p>
            {pinSentence(slot, pin)} Hausbuch speichert die PIN verschlüsselt und zeigt sie danach nie wieder an. Ohne PIN endet
            jeder Abruf mit „PIN fehlt“.
          </p>
          <PinForm slot={slot} fintsUrl={fintsUrl} onSaved={onChanged} />
        </>
      ) : (
        <>
          <p className={pin.readable && pin.serverMatches ? undefined : "notice notice-warn"}>
            {pinSentence(slot, pin)}
            {pin.source === "app" && pin.readable && pin.serverMatches && serverSet
              ? " Sie gilt vor der PIN auf dem Server."
              : ""}
          </p>
          <div className="row">
            {pin.source === "app" ? (
              <>
                {change("PIN ändern")}
                <button type="button" className="danger" onClick={() => void remove()} disabled={busy}>
                  PIN löschen
                </button>
              </>
            ) : (
              change("PIN in der App speichern")
            )}
          </div>
          <ErrorNotice error={error} />
        </>
      )}
    </div>
  );
}

const UNUSED_PINS = "Gespeicherte PINs ohne Bankzugang";

/** The PINs typed into the app under a name no connection of the household uses (a setup the
 * bank form refused after the PIN was saved, a renamed connection; D196 review). They are
 * listed so they can be deleted; Hausbuch never deletes one by itself. */
function UnusedPins({ slots, onChanged }: { slots: Slots; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const unused = slots.app.filter((c) => !c.used);
  if (unused.length === 0) return null;

  async function remove(slot: string) {
    if (!window.confirm(`Die in Hausbuch gespeicherte PIN für den Zugang „${slot}“ löschen? Kein Bankzugang nutzt sie.`)) {
      return;
    }
    setBusy(slot);
    setError(null);
    try {
      await ok(api.DELETE("/api/bank-credentials/{slot}", { params: { path: { slot } } }));
      onChanged();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="stack">
      <h3>{UNUSED_PINS}</h3>
      <p className="muted">
        Diese PINs sind in Hausbuch gespeichert, aber kein Bankzugang trägt ihren Namen, etwa nach einer abgelehnten Einrichtung
        oder einem umbenannten Zugang. Hausbuch löscht sie nie von selbst; ein Zugang mit diesem Namen würde sie wieder nutzen.
      </p>
      <ul aria-label={UNUSED_PINS} className="stack">
        {unused.map((c) => (
          <li key={c.slot} className="row">
            <span>
              Zugang „{c.slot}“, gespeichert am {dayAfter(c.set_at, 0)}
              {c.readable ? "" : " (nicht mehr lesbar)"}
            </span>
            <button type="button" className="danger" onClick={() => void remove(c.slot)} disabled={busy !== null}>
              PIN löschen
            </button>
          </li>
        ))}
      </ul>
      <ErrorNotice error={error} />
    </div>
  );
}

function ConnectionFacts({ connection }: { connection: BankConnection }) {
  return (
    <Facts
      rows={[
        ["BLZ", connection.bank_code],
        ...(connection.bank_name ? ([["Bank (laut FinTS)", connection.bank_name]] as [string, ReactNode][]) : []),
        ["FinTS-Adresse", connection.fints_url],
        [
          "Anmeldename",
          connection.name_readable ? (
            connection.login_masked
          ) : (
            <>
              <strong>lässt sich nicht mehr lesen</strong>: der Schlüssel des Servers wurde gewechselt. „Zugang ändern“ wählen,
              den Anmeldenamen neu eingeben.
            </>
          ),
        ],
        ["Zugang", `„${connection.credential_slot}“`],
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
const STATEMENTS_REFUSAL_MEANING: Record<string, string> = {
  "9370": "Signaturberechtigung reicht nicht aus",
  "9380": "keine Auftragsberechtigung",
};

/** The bank refuses this access the PDF statements (14 Nov 2026: 9370 on HKEKP): what it
 * means and how to get it unlocked. Shown while the switch is on and no later run's request
 * was answered without that refusal. */
function StatementsRefused({ refusal, bankName }: { refusal: StatementsRefusal; bankName: string | null }) {
  const meaning = STATEMENTS_REFUSAL_MEANING[refusal.code];
  const code = meaning ? `Code ${refusal.code}: ${meaning}` : `Code ${refusal.code}`;
  const bank = bankName ?? "Bank";
  return (
    <p className="notice notice-warn" role="status">
      Die {bank} erlaubt diesem Online-Banking-Zugang den Abruf der Kontoauszüge als PDF nicht ({code}). Freischalten kann
      das nur die {bank}: sie bitten, für diesen Zugang den Abruf elektronischer Kontoauszüge als PDF über FinTS/HBCI (HKEKP)
      für dieses Konto freizuschalten. Das elektronische Postfach allein genügt nicht. Bis dahin diesen Schalter ausschalten;
      die Zahlungsnachweise im Jahresarchiv erstellt Hausbuch ohnehin. Sobald der Abruf klappt, verschwindet dieser Hinweis.
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

const CREATE_KEYS = ["bank_code", "fints_url", "login_name", "credential_slot", "pin"] as const;
const CODE_FIELDS = { "bank.url_not_https": "fints_url", "bank.pin_invalid": "pin" };

const BLZ_HINT = "8 Ziffern: in Ihrer IBAN die Stellen 5 bis 12, nach „DE“ und den zwei Prüfziffern. Sie können auch die ganze IBAN einfügen.";
const URL_HINT =
  "Beginnt mit https://. Ihre Sparkasse nennt sie auf ihrer Website unter „FinTS“ oder „HBCI PIN/TAN“; sonst steht sie in der Bankenliste der Deutschen Kreditwirtschaft.";
const LOGIN_HINT =
  "Der Anmeldename oder die Legitimations-ID, mit dem Sie sich im Online-Banking anmelden. Hausbuch speichert ihn verschlüsselt und zeigt ihn nur gekürzt an.";
const SLOT_HINT =
  "Ein kurzer Name für diesen Online-Banking-Zugang, klein geschrieben, z. B. „dkb“ oder „sparkasse_b“; „primary“ ist der Zugang des Mietkontos. Konten unter demselben Zugang tragen denselben Namen und nutzen dieselbe PIN.";

function checkSlot(slot: string): string | null {
  return SLOT_PATTERN.test(slot.trim())
    ? null
    : "Kleinbuchstaben, Ziffern und _ , beginnend mit einem Buchstaben, höchstens 32 Zeichen (z. B. dkb).";
}

function checkUrl(url: string): string | null {
  if (!url.trim()) return "Bitte die FinTS-Adresse Ihrer Bank eingeben.";
  return url.trim().startsWith("https://") ? null : "Die FinTS-Adresse muss mit https:// beginnen.";
}

/** A slot with no usable PIN yet: none from either place, a stored one that no longer
 * decrypts, or one typed for another FinTS address (D196 amended). */
function pinNeeded(pin: PinInfo): boolean {
  return pin.source === "none" || !pin.readable || !pin.serverMatches;
}

function ConnectionCreateForm({ account, slots, onCreated }: { account: Account; slots: Slots; onCreated: () => void }) {
  const ids = useId();
  const [bankCode, setBankCode] = useState("");
  const [url, setUrl] = useState("");
  const [login, setLogin] = useState("");
  const [slot, setSlot] = useState(PRIMARY_SLOT);
  const [pin, setPin] = useState("");
  // The slot whose PIN this form already saved, when the connection's POST then failed.
  const [savedFor, setSavedFor] = useState<{ slot: string; url: string; at: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(CREATE_KEYS, CODE_FIELDS);
  const slotValid = checkSlot(slot) === null;
  const current: PinInfo =
    savedFor && savedFor.slot === slot.trim() && savedFor.url === url.trim()
      ? { source: "app", setAt: savedFor.at, readable: true, serverMatches: true }
      : slotPin(slot.trim(), slots);
  const askPin = slotValid && pinNeeded(current);

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
    // The PIN may also stay empty: it can be saved later on the card, or set on the server.
    const pinMessage = askPin && pin ? checkPin(pin) : null;
    if (pinMessage) messages.pin = [pinMessage];
    if (Object.keys(messages).length > 0) return form.invalid(messages);
    setBusy(true);
    form.clear();
    try {
      if (askPin && pin) {
        // The PIN first (D196): it is stored by slot, so a refused connection keeps it and
        // the form says so; the input is emptied either way once it is saved.
        // Saved for the address typed above (D196 amended): the one the connection gets.
        await savePin(slot.trim(), pin, url.trim());
        setPin("");
        setSavedFor({ slot: slot.trim(), url: url.trim(), at: new Date().toISOString() });
      }
      await ok(
        api.POST("/api/accounts/{account_id}/bank-connection", {
          params: { path: { account_id: account.id } },
          // The slot names the login's PIN (phase 6 D163, D196); the PIN itself never comes here.
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
        {askPin ? (
          <Field id={`${ids}-pin`} label={PIN_LABEL} hint={PIN_HINT} errors={form.of("pin")}>
            <PinInput value={pin} onChange={setPin} />
          </Field>
        ) : null}
      </div>
      {slotValid ? (
        <p className={current.readable ? "muted" : "notice notice-warn"}>
          {askPin && current.source === "none" ? (
            <>
              Die PIN wird verschlüsselt gespeichert und danach nie wieder angezeigt. Sie können das Feld auch leer lassen und die
              PIN später speichern; ohne PIN endet jeder Abruf mit „PIN fehlt“.
            </>
          ) : (
            pinSentence(slot.trim(), current)
          )}
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
 * masked only), unless the stored one no longer decrypts (D196). The BLZ cannot change:
 * another BLZ is another bank. A new slot keeps the stored connection: only the PIN it uses
 * changes. */
function ConnectionEditForm({
  connection,
  slots,
  onSaved,
}: {
  connection: BankConnection;
  slots: Slots;
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
    if (!connection.name_readable && !login.trim()) {
      return form.invalid({ login_name: ["Bitte den Anmeldenamen neu eingeben: der gespeicherte lässt sich nicht mehr lesen."] });
    }
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
        einer Freigabe („{connection.approval_text}“). Ein neuer Name des Zugangs ändert nur, welche PIN der Abruf nutzt. Die PIN
        selbst ändern Sie auf der Karte mit „PIN ändern“.
      </p>
      <Field id={`${ids}-url`} label="FinTS-Adresse" hint={URL_HINT} errors={form.of("fints_url")}>
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      <Field
        id={`${ids}-login`}
        label="Neuer Anmeldename"
        hint={
          connection.name_readable
            ? `Leer lassen, um ${connection.login_masked} zu behalten.`
            : "Der gespeicherte Anmeldename lässt sich nicht mehr lesen (der Schlüssel des Servers wurde gewechselt); bitte neu eingeben."
        }
        errors={form.of("login_name")}
      >
        <input type="text" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      <Field id={`${ids}-slot`} label="Name des Zugangs" hint={SLOT_HINT} errors={form.of("credential_slot")}>
        <input type="text" value={slot} onChange={(e) => setSlot(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      {checkSlot(slot) === null ? <p className="muted">{pinSentence(slot.trim(), slotPin(slot.trim(), slots))}</p> : null}
      <FormBanner errors={form.errors} />
      <div className="row">
        <Submit busy={busy}>Speichern</Submit>
      </div>
    </form>
  );
}
