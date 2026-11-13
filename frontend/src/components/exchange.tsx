"use client";

// An exchange account (phase 6 step 5a, D166 as amended, D178): the export upload, the to-dos,
// the coins it holds, the imports with „Bestand laut Börse eingeben“, and the movements with
// their answers. Every figure is shown as the API returns it (principle 1): the positions are
// the API's sums of the movements, nothing is added up here. No page shows a withdrawal's
// address or transaction hash, and none is stored for the address (D178 (2)).
import { useCallback, useId, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ruleCodeText } from "@/api/errors";
import type {
  ExchangeImport,
  ExchangeMovementRow,
  ExchangeOverview,
  ExchangeUpload,
  MovementImportBody,
  MovementKind,
} from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { statementMediaType } from "./accounts";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatDate, formatDateTime, formatDecimal, formatMoney } from "./format";
import { toDecimalString } from "./input";
import { IMPORT_STATUS, MOVEMENT_KIND } from "./labels";
import { ActionDialog, Loading } from "./ui";
import { useLoad } from "./useLoad";

/** BISON's export is a CSV; Binance's comes as a ZIP holding the CSV, or the CSV itself. */
export const EXCHANGE_EXPORT_ACCEPT = ".csv,.zip,text/csv,application/zip,application/x-zip-compressed";

const HISTORY_MISSING = "exchange.withdrawal_history_missing";
// A history was checked after every open withdrawal and fits none: say why, not "upload it".
const HISTORY_NO_MATCH = "exchange.withdrawal_history_no_match";

/** A quantity as the API sends it ("0.0000996500") without its trailing zeros, in German:
 * "0,00009965". */
export function formatUnits(value: string | null | undefined): string {
  if (value == null || value === "") return "–";
  const plain = plainDecimal(value);
  const trimmed = plain.includes(".") ? plain.replace(/0+$/, "").replace(/\.$/, "") : plain;
  return formatDecimal(trimmed === "-0" ? "0" : trimmed);
}

/** A decimal string in plain notation: "0E-10" → "0.0000000000", "1E+2" → "100". The API
 * writes plain notation since 13 Nov 2026; this only guards against an exponent string, by
 * moving the decimal point in the text (never through a float). Anything else is returned as
 * it is. */
function plainDecimal(value: string): string {
  const match = /^(-?)(\d*)(?:\.(\d*))?[eE]([+-]?\d+)$/.exec(value.trim());
  if (!match) return value;
  const [, sign, whole, fraction = "", exponent] = match;
  const digits = whole + fraction;
  const point = whole.length + Number(exponent);
  let text: string;
  if (point <= 0) text = `0.${"0".repeat(-point)}${digits}`;
  else if (point >= digits.length) text = digits + "0".repeat(point - digits.length);
  else text = `${digits.slice(0, point)}.${digits.slice(point)}`;
  const [integer, rest] = text.split(".");
  const cleaned = (integer.replace(/^0+(?=\d)/, "") || "0") + (rest !== undefined ? `.${rest}` : "");
  return sign + cleaned;
}

/** The UTC day of a timestamp the API sends ("2026-03-04T10:00:00Z" → "04.03.2026"), as the
 * API's own texts name it. */
function day(value: string | null | undefined): string {
  return value ? formatDate(value.slice(0, 10)) : "–";
}

function importPeriod(imp: ExchangeImport): string {
  if (!imp.first_at) return "–";
  const first = day(imp.first_at);
  const last = day(imp.last_at);
  return first === last ? first : `${first} – ${last}`;
}

interface Uploaded {
  upload: ExchangeUpload;
  /** The same bytes were imported before (200 for a transaction export): nothing was added. */
  existed: boolean;
  fileName: string;
}

/** POST /accounts/{id}/movement-imports as multipart, typed by its extension as a bank
 * statement is (a .csv from Windows would otherwise be sent as Excel). */
async function uploadExport(accountId: string, file: File): Promise<Uploaded> {
  const form = new FormData();
  form.append("file", new Blob([file], { type: statementMediaType(file) }), file.name);
  const { data, response } = await api.POST("/api/accounts/{account_id}/movement-imports", {
    params: { path: { account_id: accountId } },
    body: form as unknown as MovementImportBody,
  });
  const upload = data as ExchangeUpload;
  return { upload, existed: response.status === 200 && upload.movement_import != null, fileName: file.name };
}

/** The drop zone for an exchange's export; one notice per file afterwards. */
export function ExchangeExportUpload({
  accountId,
  provider,
  label = "Export der Börse hochladen",
  onImported,
}: {
  accountId: string;
  provider: string;
  label?: string;
  onImported: () => void;
}) {
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [results, setResults] = useState<Uploaded[]>([]);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    setResults([]);
    try {
      // One at a time, in the order chosen: Binance's transaction history must come before its
      // withdrawal history (exchange.transactions_first).
      for (const file of Array.from(files)) {
        const result = await uploadExport(accountId, file);
        setResults((previous) => [...previous, result]);
      }
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
      onImported();
    }
  }

  return (
    <div className="stack">
      <div
        className={over ? "dropzone over" : "dropzone"}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          void upload(event.dataTransfer.files);
        }}
      >
        <label htmlFor={inputId}>{busy ? "Liest ein …" : label}</label>
        <input
          ref={input}
          id={inputId}
          type="file"
          accept={EXCHANGE_EXPORT_ACCEPT}
          multiple
          disabled={busy}
          onChange={(event: ChangeEvent<HTMLInputElement>) => void upload(event.target.files)}
        />
        {provider === "Binance" ? (
          <p className="muted hint">
            Binance: zuerst den Transaktionsverlauf („Transaction History“), danach den Auszahlungsverlauf („Withdrawal
            History“), beide als CSV oder als die ZIP-Datei, in der Binance sie schickt. Den Spot-Handelsverlauf braucht Hausbuch
            nicht. Hausbuch liest die Dateien selbst, ohne KI; die Nutzer-ID und die Zieladressen der Auszahlungen speichert es
            nicht. Datei hierher ziehen oder auswählen.
          </p>
        ) : (
          <p className="muted hint">
            BISON: der Export der Transaktionen als CSV aus der App (Einstellungen, Steuern und Exporte). Hausbuch liest ihn
            selbst, ohne KI. Datei hierher ziehen oder auswählen.
          </p>
        )}
      </div>
      {results.map((result, index) => (
        <UploadResult key={index} result={result} />
      ))}
      <ErrorNotice error={error} />
    </div>
  );
}

function UploadResult({ result }: { result: Uploaded }) {
  const { upload, existed, fileName } = result;
  const imp = upload.movement_import;
  if (!imp) {
    // Binance's withdrawal history: it completes the withdrawals already read, no import.
    const done = upload.withdrawals_completed;
    return (
      <div className={`notice ${upload.withdrawals_open > 0 ? "notice-warn" : "notice-ok"}`} role="status">
        <p>
          Auszahlungsverlauf {fileName}: {done === 1 ? "1 Auszahlung ergänzt" : `${done} Auszahlungen ergänzt`} (Netzwerk und
          Transaktions-Hash).
          {upload.withdrawals_open > 0
            ? ` ${upload.withdrawals_open === 1 ? "1 Auszahlung passt" : `${upload.withdrawals_open} Auszahlungen passen`} zu keinem Eintrag: ${ruleCodeText(upload.withdrawals_reason ?? HISTORY_NO_MATCH)}`
            : null}
        </p>
      </div>
    );
  }
  if (existed) {
    return (
      <div className="notice notice-info" role="status">
        <p>Bereits eingelesen: {fileName}. Diese Datei wurde schon einmal hochgeladen; es wurde nichts hinzugefügt.</p>
      </div>
    );
  }
  const added = imp.rows_new === 1 ? "1 neue Bewegung" : `${imp.rows_new} neue Bewegungen`;
  return (
    <div className="notice notice-ok" role="status">
      <p>
        Eingelesen: {fileName} · {added}
        {imp.rows_skipped > 0 ? `, ${imp.rows_skipped} schon vorhanden` : ""}. Als Nächstes unten „Bestand laut Börse eingeben“.
      </p>
    </div>
  );
}

/** What GET /accounts/{id}/exchange and /movements return, loaded together. */
async function loadExchange(accountId: string): Promise<{ overview: ExchangeOverview; movements: ExchangeMovementRow[] }> {
  const path = { params: { path: { account_id: accountId } } };
  const [overview, movements] = await Promise.all([
    ok(api.GET("/api/accounts/{account_id}/exchange", path)),
    ok(api.GET("/api/accounts/{account_id}/movements", path)),
  ]);
  return { overview, movements };
}

/** The exchange account's page below its facts. */
export function ExchangeSection({ accountId }: { accountId: string }) {
  const load = useCallback(() => loadExchange(accountId), [accountId]);
  const state = useLoad(load);
  return (
    <Loading state={state}>
      {({ overview, movements }) => (
        <div className="stack">
          <ExchangeTodos overview={overview} accountId={accountId} onChanged={state.reload} />
          <ExchangeExportUpload accountId={accountId} provider={overview.provider} onImported={state.reload} />
          <h2>Bestand</h2>
          <PositionsTable overview={overview} />
          <h2>Eingelesene Exporte</h2>
          <ExchangeImportsTable imports={overview.imports ?? []} movements={movements} onChanged={state.reload} />
          <h2>Bewegungen</h2>
          <MovementsTable movements={movements} onChanged={state.reload} />
        </div>
      )}
    </Loading>
  );
}

/** Every gap of the account as a to-do with its button (principle 7). */
function ExchangeTodos({ overview, accountId, onChanged }: { overview: ExchangeOverview; accountId: string; onChanged: () => void }) {
  const historyMissing = (overview.todos ?? []).includes(HISTORY_MISSING);
  const historyNoMatch = (overview.todos ?? []).includes(HISTORY_NO_MATCH);
  const waiting = (overview.imports ?? []).filter((imp) => imp.status === "imported");
  if (!historyMissing && !historyNoMatch && waiting.length === 0 && overview.movements_to_answer === 0) return null;
  return (
    <ol className="todo-list">
      {historyMissing ? (
        <li className="todo-item">
          <p className="todo-title">
            <strong>Auszahlungsverlauf hochladen</strong>
          </p>
          <p className="todo-text">
            {overview.withdrawals_open === 1
              ? "1 Auszahlung von Coins hat noch kein Netzwerk und keinen Transaktions-Hash."
              : `${overview.withdrawals_open} Auszahlungen von Coins haben noch kein Netzwerk und keinen Transaktions-Hash.`}{" "}
            Binance nennt beides nur im Auszahlungsverlauf („Withdrawal History“). Ohne ihn lässt sich später nicht zeigen, dass die
            Coins auf eine eigene Wallet gingen und wann sie gekauft wurden.
          </p>
          <ExchangeExportUpload accountId={accountId} provider={overview.provider} label="Auszahlungsverlauf hochladen" onImported={onChanged} />
        </li>
      ) : null}
      {historyNoMatch ? (
        <li className="todo-item">
          <p className="todo-title">
            <strong>Auszahlung nicht im Auszahlungsverlauf gefunden</strong>
          </p>
          <p className="todo-text">
            {overview.withdrawals_open === 1
              ? "1 Auszahlung von Coins hat noch kein Netzwerk und keinen Transaktions-Hash."
              : `${overview.withdrawals_open} Auszahlungen von Coins haben noch kein Netzwerk und keinen Transaktions-Hash.`}{" "}
            {ruleCodeText(HISTORY_NO_MATCH)} Fehlt die Auszahlung im Verlauf (anderer Zeitraum, noch nicht abgeschlossen), bitte
            einen Verlauf hochladen, der sie enthält.
          </p>
          <ExchangeExportUpload accountId={accountId} provider={overview.provider} label="Anderen Auszahlungsverlauf hochladen" onImported={onChanged} />
        </li>
      ) : null}
      {waiting.map((imp) => (
        <li key={imp.id} className="todo-item">
          <p className="todo-title">
            <strong>Bestand laut Börse eingeben</strong> · Export bis {day(imp.last_at)}
          </p>
          <p className="todo-text">
            Damit Hausbuch prüfen kann, dass der Export vollständig ist: je Coin den Bestand, den die Börse zum{" "}
            {day(imp.last_at)} zeigt.
          </p>
          <HoldingsDialog statementImport={imp} onDone={onChanged} buttonClass="" />
        </li>
      ))}
      {overview.movements_to_answer > 0 ? (
        <li className="todo-item">
          <p className="todo-title">
            <strong>Bewegungen beantworten</strong>
          </p>
          <p className="todo-text">
            {overview.movements_to_answer === 1
              ? "1 Bewegung wartet auf eine Antwort"
              : `${overview.movements_to_answer} Bewegungen warten auf eine Antwort`}
            : unten unter „Bewegungen“, oder für alle Auszahlungen eines Exports auf einmal unter „Eingelesene Exporte“.
          </p>
        </li>
      ) : null}
    </ol>
  );
}

function reconciledPill(reconciled: boolean | null | undefined) {
  if (reconciled === true) return <span className="badge">stimmt</span>;
  if (reconciled === false) return <span className="badge badge-danger">weicht ab</span>;
  return <span className="badge badge-warn">noch nicht eingegeben</span>;
}

/** D178 (6) as amended: a EUR balance that agrees only within the rounding of the EUR legs
 * keeps its difference; say so instead of a bare "stimmt". */
function roundingText(reconciled: boolean | null | undefined, difference: string | null | undefined): string | null {
  if (reconciled !== true || difference == null || /^-?0*\.?0*$/.test(difference)) return null;
  // Display only: the check itself is the backend's Decimal rule.
  const cents = Math.abs(Number(difference)) * 100;
  return `innerhalb der Rundung (${cents < 1 ? "unter 1 Cent" : `${Math.round(cents)} Cent`})`;
}

function PositionsTable({ overview }: { overview: ExchangeOverview }) {
  if ((overview.positions ?? []).length === 0) return <p className="muted">Noch kein Export eingelesen.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Coin</th>
            <th className="num">Laut Export</th>
            <th className="num">Laut Börse</th>
            <th>Abgleich</th>
          </tr>
        </thead>
        <tbody>
          {(overview.positions ?? []).map((position) => (
            <tr key={position.asset_id}>
              <td>
                {position.symbol}
                <p className="cell-detail">{position.name}</p>
              </td>
              <td className="num">
                {formatUnits(position.units)}
                {position.units.startsWith("-") ? (
                  <p className="cell-detail">Weniger als null: ein Teil der Käufe fehlt im Export.</p>
                ) : null}
              </td>
              <td className="num">
                {formatUnits(position.typed_units)}
                {position.typed_as_of ? <p className="cell-detail">am {formatDate(position.typed_as_of)}</p> : null}
              </td>
              <td>
                {reconciledPill(position.reconciled)}
                {roundingText(position.reconciled, position.difference_units) ? (
                  <p className="cell-detail">{roundingText(position.reconciled, position.difference_units)}</p>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The proposed coin withdrawals an import read (they carry its document). */
function openWithdrawals(imp: ExchangeImport, movements: ExchangeMovementRow[]): number {
  return movements.filter(
    (movement) =>
      movement.document_id === imp.document_id && movement.kind === "internal_transfer_out" && movement.status === "proposed",
  ).length;
}

function ExchangeImportsTable({
  imports,
  movements,
  onChanged,
}: {
  imports: ExchangeImport[];
  movements: ExchangeMovementRow[];
  onChanged: () => void;
}) {
  if (imports.length === 0) return <p className="muted">Noch kein Export eingelesen.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Zeitraum</th>
            <th>Bewegungen</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {imports.map((imp) => {
            const withdrawals = openWithdrawals(imp, movements);
            const wrong = (imp.balances ?? []).filter((balance) => balance.reconciled === false);
            const rounded = (imp.balances ?? []).filter((balance) => roundingText(balance.reconciled, balance.difference_units));
            return (
              <tr key={imp.id}>
                <td>
                  {importPeriod(imp)}
                  <p className="cell-detail">eingelesen {formatDateTime(imp.created_at)}</p>
                </td>
                <td>
                  {imp.rows_new === 1 ? "1 neue" : `${imp.rows_new} neue`}
                  {imp.rows_skipped > 0 ? `, ${imp.rows_skipped} schon vorhanden` : ""}
                </td>
                <td>
                  <span className={imp.status === "reconciled" ? "badge" : imp.status === "imported" ? "badge badge-warn" : "badge badge-danger"}>
                    {imp.status === "imported" ? "Bestand fehlt" : IMPORT_STATUS[imp.status]}
                  </span>
                  {rounded.map((balance) => (
                    <p key={balance.asset_id} className="cell-detail">
                      {balance.symbol}: {roundingText(balance.reconciled, balance.difference_units)}
                    </p>
                  ))}
                  {wrong.map((balance) => (
                    <p key={balance.asset_id} className="cell-detail">
                      {balance.symbol}: laut Börse {formatUnits(balance.typed)}, laut Export {formatUnits(balance.held)}
                    </p>
                  ))}
                  {wrong.length > 0 ? (
                    <p className="cell-detail">
                      Fehlt ein Teil des Exports, oder wurde ein Bestand falsch abgelesen? Einen vollständigen Export hochladen oder
                      den Bestand neu eingeben; Hausbuch gleicht nie selbst aus.
                    </p>
                  ) : null}
                </td>
                <td className="actions">
                  <HoldingsDialog
                    statementImport={imp}
                    onDone={onChanged}
                    label={imp.status === "imported" ? "Bestand laut Börse eingeben" : "Bestand neu eingeben"}
                  />
                  {withdrawals > 0 ? <ConfirmWithdrawals importId={imp.id} count={withdrawals} onDone={onChanged} /> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** „Bestand laut Börse eingeben“ in a dialog. */
export function HoldingsDialog({
  statementImport,
  onDone,
  label = "Bestand laut Börse eingeben",
  buttonClass = "secondary",
}: {
  statementImport: ExchangeImport;
  onDone: () => void;
  label?: string;
  buttonClass?: string;
}) {
  return (
    <ActionDialog label={label} title="Bestand laut Börse" buttonClass={buttonClass}>
      {(close) => (
        <HoldingsForm
          statementImport={statementImport}
          onDone={() => {
            close();
            onDone();
          }}
        />
      )}
    </ActionDialog>
  );
}

/** No body path maps to one coin's field (the API names a coin by its index), so every API
 * error is shown in the banner below the form. */
const HOLDINGS_KEYS: readonly string[] = [];

/** One field per coin the export holds or touched; POST /movement-imports/{id}/holdings. A
 * coin the export leaves at zero is pre-filled with 0; one typed before shows its value. */
function HoldingsForm({ statementImport: imp, onDone }: { statementImport: ExchangeImport; onDone: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      (imp.balances ?? []).map((balance) => [
        balance.asset_id,
        balance.typed != null ? formatUnits(balance.typed).replace(/\./g, "") : /^-?0*\.?0*$/.test(plainDecimal(balance.held)) ? "0" : "",
      ]),
    ),
  );
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(HOLDINGS_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const collected = collector();
    const balances: { asset_id: string; units: string }[] = [];
    for (const balance of imp.balances ?? []) {
      const raw = (values[balance.asset_id] ?? "").trim();
      if (raw === "") {
        collected.messages[balance.asset_id] = [`Bitte den Bestand an ${balance.symbol} eingeben (0, wenn keiner).`];
        continue;
      }
      const units = collected.take(balance.asset_id, toDecimalString(raw, 10));
      if (units !== undefined) balances.push({ asset_id: balance.asset_id, units });
    }
    if (!collected.ok) return form.invalid(collected.messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.POST("/api/movement-imports/{import_id}/holdings", {
          params: { path: { import_id: imp.id } },
          body: { balances },
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
      <p className="muted">
        Je Coin der Bestand, den die Börse nach der letzten Bewegung dieses Exports zeigt ({day(imp.last_at)}), wie in der App
        oder auf der Website der Börse. Hausbuch vergleicht ihn mit dem, was die Bewegungen des Exports ergeben.
      </p>
      {(imp.balances ?? []).map((balance) => (
        <Field
          key={balance.asset_id}
          id={`holding-${imp.id}-${balance.asset_id}`}
          label={`${balance.symbol} laut Börse`}
          hint={`Laut Export: ${formatUnits(balance.held)}`}
          description="Die Stückzahl, nicht der Wert in Euro. Komma oder Punkt als Dezimalzeichen, z. B. 0,00009965."
          errors={form.of(balance.asset_id)}
        >
          <input
            type="text"
            inputMode="decimal"
            value={values[balance.asset_id] ?? ""}
            onChange={(e) => setValues((current) => ({ ...current, [balance.asset_id]: e.target.value }))}
            autoComplete="off"
          />
        </Field>
      ))}
      <div className="row">
        <Submit busy={busy}>Abgleichen</Submit>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** „Alle Auszahlungen gingen an eigene Wallets“ (D178 (4)): every proposed withdrawal of one
 * import confirmed as a transfer to an own wallet. */
export function ConfirmWithdrawals({ importId, count, onDone }: { importId: string; count?: number; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await ok(api.POST("/api/movement-imports/{import_id}/withdrawals/confirm", { params: { path: { import_id: importId } } }));
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" onClick={() => void confirm()} disabled={busy}>
        {busy
          ? "Speichert …"
          : count != null && count > 1
            ? `Alle ${count} Auszahlungen gingen an eigene Wallets`
            : count === 1
              ? "Die Auszahlung ging an eine eigene Wallet"
              : "Alle Auszahlungen gingen an eigene Wallets"}
      </button>
      <ErrorNotice error={error} />
    </>
  );
}

/** The answers to one proposed movement: „Stimmt“ keeps its kind, every other allowed kind
 * replaces it (POST /movements/{id}/confirm or /answer). */
export function MovementAnswers({ movement, onDone }: { movement: ExchangeMovementRow; onDone: () => void }) {
  const [busy, setBusy] = useState<MovementKind | null>(null);
  const [error, setError] = useState<unknown>(null);
  if (movement.status !== "proposed") return null;

  async function answer(kind: MovementKind) {
    setBusy(kind);
    setError(null);
    try {
      if (kind === movement.kind) {
        await ok(api.POST("/api/movements/{movement_id}/confirm", { params: { path: { movement_id: movement.id } } }));
      } else {
        await ok(
          api.POST("/api/movements/{movement_id}/answer", {
            params: { path: { movement_id: movement.id } },
            body: { kind },
          }),
        );
      }
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  const answers = movement.answers ?? [];
  const kinds = answers.includes(movement.kind) ? answers : [movement.kind, ...answers];
  return (
    <div className="stack">
      {movement.question ? <p className="cell-detail">{ruleCodeText(movement.question) ?? movement.question}</p> : null}
      <div className="row">
        {kinds.map((kind) => (
          <button
            key={kind}
            type="button"
            className={kind === movement.kind ? "" : "secondary"}
            onClick={() => void answer(kind)}
            disabled={busy !== null}
          >
            {busy === kind ? "Speichert …" : MOVEMENT_KIND[kind]}
          </button>
        ))}
      </div>
      <ErrorNotice error={error} />
    </div>
  );
}

function MovementsTable({ movements, onChanged }: { movements: ExchangeMovementRow[]; onChanged: () => void }) {
  if (movements.length === 0) return <p className="muted">Noch keine Bewegungen.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Datum</th>
            <th>Art</th>
            <th className="num">Menge</th>
            <th className="num">Euro</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {movements.map((movement) => (
            <tr key={movement.id}>
              <td>{formatDateTime(movement.occurred_at)}</td>
              <td>
                {MOVEMENT_KIND[movement.kind]}
                {movement.network ? <p className="cell-detail">Netzwerk {movement.network}</p> : null}
              </td>
              <td className="num">
                {formatUnits(movement.quantity)} {movement.symbol}
                {movement.fee_quantity && !/^0*\.?0*$/.test(movement.fee_quantity) ? (
                  <p className="cell-detail">
                    Gebühr {formatUnits(movement.fee_quantity)} {movement.symbol}
                  </p>
                ) : null}
              </td>
              <td className="num">{movement.eur_amount != null ? formatMoney(movement.eur_amount) : "–"}</td>
              <td>
                {movement.status === "confirmed" ? (
                  <span className="badge">bestätigt</span>
                ) : (
                  <MovementAnswers movement={movement} onDone={onChanged} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
