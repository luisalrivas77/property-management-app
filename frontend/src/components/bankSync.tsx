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
import { getLocale, LOCALE_TAG, translate, useFormat, useT } from "@/i18n";
import { statements } from "@/i18n/accounts";
import { bankSync } from "@/i18n/bankSync";
import { common } from "@/i18n/common";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { Field, FormBanner, Submit, useFormErrors } from "./form";
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
  const t = useT(bankSync);
  const c = useT(common);
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
      <h2 id="bank-sync-title">{t.title}</h2>
      <p className="muted">{t.intro}</p>
      {state.error ? <ErrorNotice error={state.error} /> : null}
      {state.data === undefined ? (
        state.error ? null : <p className="muted">{c.loading}</p>
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
            label={t.autoSync}
            hint={t.autoSyncHint}
            locked={state.data.connection.auto_sync_allowed ? null : t.autoSyncWaits}
            onSaved={reload}
          />
          <ConnectionSwitch
            connection={state.data.connection}
            field="fetch_statements"
            label={t.fetchStatements}
            hint={t.fetchStatementsHint}
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
              {starting ? t.starting : busy ? t.running : expired ? t.syncAgain : t.syncNow}
            </button>
            <ActionDialog label={t.changeLogin} title={t.dialogTitle(t.changeLogin)}>
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

/** The local calendar day of a timestamp plus `days` (an evening approval in Berlin is
 * already the next day in UTC). */
function dayAfter(timestamp: string, days: number): string {
  const day = new Date(timestamp);
  day.setDate(day.getDate() + days);
  return new Intl.DateTimeFormat(LOCALE_TAG[getLocale()], { dateStyle: "medium" }).format(day);
}

/** Where a slot's PIN comes from, in one sentence (names and dates only, never a PIN). */
function pinSentence(slot: string, pin: PinInfo): ReactNode {
  const t = translate(bankSync);
  if (pin.source === "app" && pin.readable && !pin.serverMatches) {
    return (
      <>
        <strong>{t.pinRetypeLead}</strong>
        {t.pinRetypeText}
      </>
    );
  }
  if (pin.source === "app") {
    return pin.readable ? (
      <>{t.pinSavedInApp(pin.setAt ? dayAfter(pin.setAt, 0) : "–")}</>
    ) : (
      <>
        <strong>{t.pinUnreadableLead}</strong>
        {t.pinUnreadableText}
      </>
    );
  }
  if (pin.source === "server") {
    return (
      <>
        {t.pinOnServerBefore}
        <code>{pinVariable(slot)}</code>
        {t.pinOnServerAfter}
      </>
    );
  }
  return <strong>{t.noPinYet}</strong>;
}

const PIN_KEYS = ["pin"] as const;

/** A PIN's length in characters, as the API counts them (code points, not UTF-16 units,
 * after trimming the surrounding blanks, D196). */
function checkPin(pin: string): string | null {
  const length = [...pin.trim()].length;
  return length >= 5 && length <= 64 ? null : translate(bankSync).pinLength;
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
  const t = useT(bankSync);
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
    <form className="stack" onSubmit={submit} noValidate aria-label={t.savePin}>
      <Field id={`${id}-pin`} label={t.pinLabel} hint={t.pinHint} errors={form.of("pin")}>
        <PinInput value={pin} onChange={setPin} />
      </Field>
      <FormBanner errors={form.errors} />
      <div className="row">
        <Submit busy={busy}>{t.savePin}</Submit>
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
  const t = useT(bankSync);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function remove() {
    const after = serverSet ? t.afterDeleteServer : t.afterDeleteNone;
    if (!window.confirm(t.confirmDeletePin(slot, after))) return;
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
    <ActionDialog label={label} title={t.dialogTitle(label)}>
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
      <h3>{t.pin}</h3>
      {pin.source === "none" ? (
        <>
          <p>
            {pinSentence(slot, pin)}
            {t.pinNoneAfter}
          </p>
          <PinForm slot={slot} fintsUrl={fintsUrl} onSaved={onChanged} />
        </>
      ) : (
        <>
          <p className={pin.readable && pin.serverMatches ? undefined : "notice notice-warn"}>
            {pinSentence(slot, pin)}
            {pin.source === "app" && pin.readable && pin.serverMatches && serverSet
              ? t.pinBeforeServer
              : ""}
          </p>
          <div className="row">
            {pin.source === "app" ? (
              <>
                {change(t.changePin)}
                <button type="button" className="danger" onClick={() => void remove()} disabled={busy}>
                  {t.deletePin}
                </button>
              </>
            ) : (
              change(t.savePinInApp)
            )}
          </div>
          <ErrorNotice error={error} />
        </>
      )}
    </div>
  );
}

/** The PINs typed into the app under a name no connection of the household uses (a setup the
 * bank form refused after the PIN was saved, a renamed connection; D196 review). They are
 * listed so they can be deleted; Hausbuch never deletes one by itself. */
function UnusedPins({ slots, onChanged }: { slots: Slots; onChanged: () => void }) {
  const t = useT(bankSync);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const unused = slots.app.filter((c) => !c.used);
  if (unused.length === 0) return null;

  async function remove(slot: string) {
    if (!window.confirm(t.confirmDeletePin(slot, t.noConnectionUses))) {
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
      <h3>{t.unusedPins}</h3>
      <p className="muted">{t.unusedPinsExplain}</p>
      <ul aria-label={t.unusedPins} className="stack">
        {unused.map((c) => (
          <li key={c.slot} className="row">
            <span>
              {t.unusedPin(c.slot, dayAfter(c.set_at, 0))}
              {c.readable ? "" : t.unreadable}
            </span>
            <button type="button" className="danger" onClick={() => void remove(c.slot)} disabled={busy !== null}>
              {t.deletePin}
            </button>
          </li>
        ))}
      </ul>
      <ErrorNotice error={error} />
    </div>
  );
}

function ConnectionFacts({ connection }: { connection: BankConnection }) {
  const t = useT(bankSync);
  const f = useFormat();
  return (
    <Facts
      rows={[
        [t.factBankCode, connection.bank_code],
        ...(connection.bank_name ? ([[t.factBankName, connection.bank_name]] as [string, ReactNode][]) : []),
        [t.factUrl, connection.fints_url],
        [
          t.factLogin,
          connection.name_readable ? (
            connection.login_masked
          ) : (
            <>
              <strong>{t.loginUnreadableLead}</strong>
              {t.loginUnreadableText}
            </>
          ),
        ],
        [t.factSlot, t.quotedSlot(connection.credential_slot)],
        [
          t.factApproval,
          connection.sca_confirmed_at
            ? t.approvalLast(dayAfter(connection.sca_confirmed_at, 0), dayAfter(connection.sca_confirmed_at, SCA_DAYS))
            : t.approvalNone(connection.approval_text),
        ],
        [
          t.factLastSync,
          connection.last_sync_at
            ? `${f.dateTime(connection.last_sync_at)}${connection.last_booking_on ? t.linesUntil(f.date(connection.last_booking_on)) : ""}`
            : t.noSyncYet,
        ],
      ]}
    />
  );
}

/** What a permission refusal code of the PDF statement fetch means (14 Nov 2026). */
function refusalMeaning(code: string): string | undefined {
  const t = translate(bankSync);
  if (code === "9370") return t.refusal9370;
  if (code === "9380") return t.refusal9380;
  return undefined;
}

/** The bank refuses this access the PDF statements (14 Nov 2026: 9370 on HKEKP): what it
 * means and how to get it unlocked. Shown while the switch is on and no later run's request
 * was answered without that refusal. */
function StatementsRefused({ refusal, bankName }: { refusal: StatementsRefusal; bankName: string | null }) {
  const t = useT(bankSync);
  const meaning = refusalMeaning(refusal.code);
  const code = meaning ? t.refusalCodeMeaning(refusal.code, meaning) : t.refusalCode(refusal.code);
  return (
    <p className="notice notice-warn" role="status">
      {t.statementsRefused(bankName, code)}
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
  const t = useT(bankSync);
  const f = useFormat();
  const fromLatest = latest ? statements.filter((s) => s.sync_id === latest.id).length : 0;
  return (
    <>
      <h3>{t.bankStatements}</h3>
      {statements.length === 0 ? (
        <p className="muted">{t.noStatementsYet}</p>
      ) : (
        <>
          {fromLatest > 0 ? (
            <p className="muted">{t.fetchedLast(fromLatest)}</p>
          ) : null}
          <ul>
            {statements.map((s) => (
              <li key={s.id}>
                <Link href={`/documents/${s.document_id}`}>{t.statementNumber(s.number, s.year)}</Link>{" "}
                <span className="muted">{t.fetchedAt(f.dateTime(s.fetched_at))}</span>
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
  const t = useT(bankSync);
  const left = Math.max(0, new Date(run.updated_at).getTime() + APPROVAL_WINDOW_MS - now);
  const minutes = Math.floor(left / 60_000);
  const seconds = Math.floor((left % 60_000) / 1000);
  return (
    <div className="notice notice-warn" role="status">
      <p>
        <strong>{approvalText}.</strong>
        {t.approvalSent}
      </p>
      <p>
        {left > 0
          ? t.timeLeft(`${minutes}:${String(seconds).padStart(2, "0")}`)
          : t.approvalExpired}
      </p>
    </div>
  );
}

function linesSentence(run: BankSync): string {
  const added = run.lines_new ?? 0;
  const known = (run.lines_total ?? 0) - added;
  return translate(statements).lineCounts(added, known);
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
  const t = useT(bankSync);
  const f = useFormat();
  const last = run.trigger === "scheduled" ? t.lastScheduled : t.lastManual;
  if (stuck) {
    return (
      <div className="notice notice-warn" role="status">
        <p>{t.stuck(f.dateTime(run.created_at))}</p>
      </div>
    );
  }
  switch (run.status) {
    case "needs_approval":
      return scheduledApproval(run) ? (
        <div className="notice notice-warn" role="status">
          <p>
            <strong>{t.scheduledNeedsApproval(approvalText)}</strong> {t.scheduledAsked(f.dateTime(run.updated_at))}
          </p>
        </div>
      ) : (
        <ApprovalWait run={run} approvalText={approvalText} now={now} />
      );
    case "queued":
      return <p role="status">{t.queued}</p>;
    case "running":
      return <p role="status">{t.fetching}</p>;
    case "importing":
      return <p role="status">{t.importing}</p>;
    case "done":
      return (
        <div className="notice notice-ok" role="status">
          <p>
            {t.doneLine(last, f.dateTime(run.finished_at), linesSentence(run))}{" "}
            {run.import_id && (run.lines_new ?? 0) > 0 ? (
              <Link href={`/accounts/${accountId}?tab=umsaetze&import=${run.import_id}`}>{t.showNewLines}</Link>
            ) : null}
          </p>
        </div>
      );
    case "nothing_new":
      return (
        <div className={run.error_code === "bank.balance_drift" ? "notice notice-warn" : "notice notice-info"} role="status">
          <p>
            {t.nothingNew(last, f.dateTime(run.finished_at))}
            {run.error_code === "bank.balance_drift" ? t.balanceDrift : run.balance_checked ? t.balanceMatches : ""}
          </p>
        </div>
      );
    case "failed":
      return (
        <div className="notice notice-error" role="status">
          <p>
            {t.failedLine(last, f.dateTime(run.finished_at))}{" "}
            {(run.error_code && ruleCodeText(run.error_code)) || t.failedFallback}
            {run.error_code ? <code className="error-code">{run.error_code}</code> : null}
          </p>
        </div>
      );
  }
}

function runResult(run: BankSync): string {
  const t = translate(bankSync);
  if (run.status === "done") return linesSentence(run);
  if (run.status === "failed") return run.error_code ? t.resultFailedCode(run.error_code) : t.resultFailed;
  if (run.status === "nothing_new" && run.error_code === "bank.balance_drift") return t.resultDrift;
  return SYNC_STATUS[run.status];
}

function runBalance(run: BankSync): string {
  if (run.balance_source) return BALANCE_SOURCE[run.balance_source];
  return run.balance_checked ? translate(bankSync).balanceCompared : "–";
}

function RunHistory({ runs }: { runs: BankSync[] }) {
  const t = useT(bankSync);
  const f = useFormat();
  return (
    <>
      <h3>{t.recentRuns}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">{t.colStarted}</th>
              <th scope="col">{t.colPeriod}</th>
              <th scope="col">{t.colResult}</th>
              <th scope="col">{t.colBalance}</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>
                  {f.dateTime(run.started_at ?? run.created_at)}
                  <p className="cell-detail">{SYNC_TRIGGER[run.trigger]}</p>
                </td>
                <td>{run.requested_from ? `${f.date(run.requested_from)} – ${f.date(run.requested_to)}` : "–"}</td>
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

function checkSlot(slot: string): string | null {
  return SLOT_PATTERN.test(slot.trim()) ? null : translate(bankSync).slotInvalid;
}

function checkUrl(url: string): string | null {
  const t = translate(bankSync);
  if (!url.trim()) return t.urlRequired;
  return url.trim().startsWith("https://") ? null : t.urlHttps;
}

/** A slot with no usable PIN yet: none from either place, a stored one that no longer
 * decrypts, or one typed for another FinTS address (D196 amended). */
function pinNeeded(pin: PinInfo): boolean {
  return pin.source === "none" || !pin.readable || !pin.serverMatches;
}

function ConnectionCreateForm({ account, slots, onCreated }: { account: Account; slots: Slots; onCreated: () => void }) {
  const t = useT(bankSync);
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
    if (!/^[0-9]{8}$/.test(bankCode.trim())) messages.bank_code = [t.bankCodeInvalid];
    const urlMessage = checkUrl(url);
    if (urlMessage) messages.fints_url = [urlMessage];
    if (!login.trim()) messages.login_name = [t.loginRequired];
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
    <form className="stack" onSubmit={submit} noValidate aria-label={t.setUp}>
      <p>{t.setUpIntro}</p>
      <div className="grid">
        <Field id={`${ids}-blz`} label={t.factBankCode} hint={t.bankCodeHint} errors={form.of("bank_code")}>
          <input type="text" inputMode="numeric" value={bankCode} onChange={(e) => changeBankCode(e.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
        <Field id={`${ids}-url`} label={t.factUrl} hint={t.urlHint} errors={form.of("fints_url")}>
          <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off" spellCheck={false} placeholder="https://" />
        </Field>
        <Field id={`${ids}-login`} label={t.factLogin} hint={t.loginHint} errors={form.of("login_name")}>
          <input type="text" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
        <Field id={`${ids}-slot`} label={t.slotLabel} hint={t.slotHint} errors={form.of("credential_slot")}>
          <input type="text" value={slot} onChange={(e) => setSlot(e.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
        {askPin ? (
          <Field id={`${ids}-pin`} label={t.pinLabel} hint={t.pinHint} errors={form.of("pin")}>
            <PinInput value={pin} onChange={setPin} />
          </Field>
        ) : null}
      </div>
      {slotValid ? (
        <p className={current.readable ? "muted" : "notice notice-warn"}>
          {askPin && current.source === "none" ? (
            t.pinNoneNote
          ) : (
            pinSentence(slot.trim(), current)
          )}
        </p>
      ) : null}
      <FormBanner errors={form.errors} />
      <div className="row">
        <Submit busy={busy}>{t.setUp}</Submit>
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
  const t = useT(bankSync);
  const c = useT(common);
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
      return form.invalid({ login_name: [t.loginRetype] });
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
    <form className="stack" onSubmit={submit} noValidate aria-label={t.changeLogin}>
      <p className="muted">{t.editNote(connection.approval_text)}</p>
      <Field id={`${ids}-url`} label={t.factUrl} hint={t.urlHint} errors={form.of("fints_url")}>
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      <Field
        id={`${ids}-login`}
        label={t.newLogin}
        hint={connection.name_readable ? t.keepLogin(connection.login_masked) : t.loginUnreadableHint}
        errors={form.of("login_name")}
      >
        <input type="text" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      <Field id={`${ids}-slot`} label={t.slotLabel} hint={t.slotHint} errors={form.of("credential_slot")}>
        <input type="text" value={slot} onChange={(e) => setSlot(e.target.value)} autoComplete="off" spellCheck={false} />
      </Field>
      {checkSlot(slot) === null ? <p className="muted">{pinSentence(slot.trim(), slotPin(slot.trim(), slots))}</p> : null}
      <FormBanner errors={form.errors} />
      <div className="row">
        <Submit busy={busy}>{c.save}</Submit>
      </div>
    </form>
  );
}
