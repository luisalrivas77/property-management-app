"use client";

// An exchange account (phase 6 step 5a, D166 as amended, D178): the export upload, the to-dos,
// the coins it holds, the imports with „Bestand laut Börse eingeben“, and the movements with
// their answers. Every figure is shown as the API returns it (principle 1): the positions are
// the API's sums of the movements, nothing is added up here. No page shows a withdrawal's
// address or transaction hash, and none is stored for the address (D178 (2)).
import Link from "next/link";
import { useCallback, useId, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ruleCodeText } from "@/api/errors";
import type {
  ExchangeImport,
  ExchangeMovementRow,
  ExchangeOverview,
  ExchangeUpload,
  ImportBalance,
  MovementImportBody,
  MovementKind,
} from "@/api/schemas";
import { formatter, translate, useT } from "@/i18n";
import { exchange as messages } from "@/i18n/exchange";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { statementMediaType } from "./accounts";
import { storeAsDocument, StoredAsDocument, UPLOAD_ACCEPT, type Uploaded as StoredUpload } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatDecimal } from "./format";
import { toDecimalString } from "./input";
import { CHAIN, IMPORT_STATUS, MOVEMENT_KIND } from "./labels";
import { ActionDialog, Loading } from "./ui";
import { useLoad } from "./useLoad";

// Shown amounts and dates follow the chosen language (German unchanged); read at call time.
const formatDate = (value: string | null | undefined) => formatter().date(value);
const formatDateTime = (value: string | null | undefined) => formatter().dateTime(value);
const formatMoney = (value: string | null | undefined) => formatter().money(value);

/** BISON's export is a CSV; Binance's comes as a ZIP holding the CSV, or the CSV itself. */
export const EXCHANGE_EXPORT_ACCEPT = ".csv,.zip,text/csv,application/zip,application/x-zip-compressed";

const HISTORY_MISSING = "exchange.withdrawal_history_missing";
// A history was checked after every open withdrawal and fits none: say why, not "upload it".
const HISTORY_NO_MATCH = "exchange.withdrawal_history_no_match";
// D178 amended (4): a Binance export whose file name named no time zone, read as UTC.
const TIME_ZONE_UNKNOWN = "exchange.time_zone_unknown";

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
  const [, sign = "", whole = "", fraction = "", exponent = "0"] = match;
  const digits = whole + fraction;
  const point = whole.length + Number(exponent);
  let text: string;
  if (point <= 0) text = `0.${"0".repeat(-point)}${digits}`;
  else if (point >= digits.length) text = digits + "0".repeat(point - digits.length);
  else text = `${digits.slice(0, point)}.${digits.slice(point)}`;
  const [integer = "", rest] = text.split(".");
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

/** An exchange export is a CSV or a ZIP (Binance); a PDF, image or anything else is a document. */
function isExportFile(file: File): boolean {
  return /\.(csv|zip)$/i.test(file.name) || /csv|zip/.test(file.type);
}

/** The drop zone for an exchange's export; one notice per file afterwards. */
export function ExchangeExportUpload({
  accountId,
  provider,
  label,
  onImported,
}: {
  accountId: string;
  provider: string;
  label?: string;
  onImported: () => void;
}) {
  const t = useT(messages);
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [results, setResults] = useState<Uploaded[]>([]);
  const [stored, setStored] = useState<StoredUpload[]>([]);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    setResults([]);
    setStored([]);
    try {
      // One at a time, in the order chosen: Binance's transaction history must come before its
      // withdrawal history (exchange.transactions_first).
      for (const file of Array.from(files)) {
        if (!isExportFile(file)) {
          // Not a CSV or ZIP export: any other document of the exchange is kept and read by the AI.
          const result = await storeAsDocument(file);
          setStored((previous) => [...previous, result]);
          continue;
        }
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
        <label htmlFor={inputId}>{busy ? t.readingIn : (label ?? t.uploadExport)}</label>
        <input
          ref={input}
          id={inputId}
          type="file"
          accept={`${EXCHANGE_EXPORT_ACCEPT},${UPLOAD_ACCEPT}`}
          multiple
          disabled={busy}
          onChange={(event: ChangeEvent<HTMLInputElement>) => void upload(event.target.files)}
        />
        {provider === "Binance" ? (
          <p className="muted hint">{t.binanceHint}</p>
        ) : (
          <p className="muted hint">{t.bisonHint}</p>
        )}
      </div>
      {results.map((result, index) => (
        <UploadResult key={index} result={result} />
      ))}
      {stored.map((result, index) => (
        <StoredAsDocument key={index} result={result} what={t.notAnExport} />
      ))}
      <ErrorNotice error={error} />
    </div>
  );
}

function UploadResult({ result }: { result: Uploaded }) {
  const t = useT(messages);
  const { upload, existed, fileName } = result;
  const imp = upload.movement_import;
  if (!imp) {
    // Binance's withdrawal history: it completes the withdrawals already read, no import.
    const done = upload.withdrawals_completed;
    return (
      <div className={`notice ${upload.withdrawals_open > 0 ? "notice-warn" : "notice-ok"}`} role="status">
        <p>
          {t.historyDone(fileName, done)}
          {upload.withdrawals_open > 0
            ? t.historyOpen(upload.withdrawals_open, ruleCodeText(upload.withdrawals_reason ?? HISTORY_NO_MATCH))
            : null}
        </p>
      </div>
    );
  }
  if (existed) {
    return (
      <div className="notice notice-info" role="status">
        <p>{t.alreadyRead(fileName)}</p>
      </div>
    );
  }
  return (
    <div className="notice notice-ok" role="status">
      <p>{t.readIn(fileName, imp.rows_new, imp.rows_skipped)}</p>
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
  const t = useT(messages);
  const load = useCallback(() => loadExchange(accountId), [accountId]);
  const state = useLoad(load);
  return (
    <Loading state={state}>
      {({ overview, movements }) => (
        <div className="stack">
          <ExchangeTodos overview={overview} accountId={accountId} onChanged={state.reload} />
          <ExchangeExportUpload accountId={accountId} provider={overview.provider} onImported={state.reload} />
          <h2>{t.holdings}</h2>
          <PositionsTable overview={overview} />
          <h2>{t.imports}</h2>
          <ExchangeImportsTable imports={overview.imports ?? []} movements={movements} onChanged={state.reload} />
          <h2>{t.movements}</h2>
          <MovementsTable movements={movements} onChanged={state.reload} />
          <LearnedAddresses overview={overview} onChanged={state.reload} />
        </div>
      )}
    </Loading>
  );
}

/** Every gap of the account as a to-do with its button (principle 7). */
function ExchangeTodos({ overview, accountId, onChanged }: { overview: ExchangeOverview; accountId: string; onChanged: () => void }) {
  const t = useT(messages);
  const historyMissing = (overview.todos ?? []).includes(HISTORY_MISSING);
  const historyNoMatch = (overview.todos ?? []).includes(HISTORY_NO_MATCH);
  const zoneUnknown = (overview.todos ?? []).includes(TIME_ZONE_UNKNOWN);
  const waiting = (overview.imports ?? []).filter((imp) => imp.status === "imported");
  if (!zoneUnknown && !historyMissing && !historyNoMatch && waiting.length === 0 && overview.movements_to_answer === 0) return null;
  return (
    <ol className="todo-list">
      {zoneUnknown ? (
        <li className="todo-item">
          <p className="todo-title">
            <strong>{t.reuploadTransactions}</strong>
          </p>
          <p className="todo-text">
            {ruleCodeText(TIME_ZONE_UNKNOWN)}
            {t.zoneUnknown}
          </p>
          <ExchangeExportUpload accountId={accountId} provider={overview.provider} label={t.uploadTransactions} onImported={onChanged} />
        </li>
      ) : null}
      {historyMissing ? (
        <li className="todo-item">
          <p className="todo-title">
            <strong>{t.uploadWithdrawals}</strong>
          </p>
          <p className="todo-text">
            {t.withdrawalsOpen(overview.withdrawals_open)} {t.historyWhy}
          </p>
          <ExchangeExportUpload accountId={accountId} provider={overview.provider} label={t.uploadWithdrawals} onImported={onChanged} />
        </li>
      ) : null}
      {historyNoMatch ? (
        <li className="todo-item">
          <p className="todo-title">
            <strong>{t.withdrawalNotFound}</strong>
          </p>
          <p className="todo-text">
            {t.withdrawalsOpen(overview.withdrawals_open)} {ruleCodeText(HISTORY_NO_MATCH)}
            {t.noMatchHint}
          </p>
          <ExchangeExportUpload accountId={accountId} provider={overview.provider} label={t.uploadOtherWithdrawals} onImported={onChanged} />
        </li>
      ) : null}
      {waiting.map((imp) => (
        <li key={imp.id} className="todo-item">
          <p className="todo-title">
            <strong>{t.enterHoldings}</strong>
            {t.exportUntil(day(imp.last_at))}
          </p>
          <p className="todo-text">{t.holdingsWhy(day(imp.last_at))}</p>
          <HoldingsDialog statementImport={imp} onDone={onChanged} buttonClass="" />
        </li>
      ))}
      {overview.movements_to_answer > 0 ? (
        <li className="todo-item">
          <p className="todo-title">
            <strong>{t.answerMovements}</strong>
          </p>
          <p className="todo-text">{t.toAnswer(overview.movements_to_answer)}</p>
        </li>
      ) : null}
    </ol>
  );
}

function reconciledPill(reconciled: boolean | null | undefined) {
  const t = translate(messages);
  if (reconciled === true) return <span className="badge">{t.agrees}</span>;
  if (reconciled === false) return <span className="badge badge-danger">{t.differs}</span>;
  return <span className="badge badge-warn">{t.notEntered}</span>;
}

/** D178 (6) as amended: a EUR balance that agrees only within the rounding of the EUR legs
 * keeps its difference; say so instead of a bare "stimmt". */
function roundingText(reconciled: boolean | null | undefined, difference: string | null | undefined): string | null {
  if (reconciled !== true || difference == null || /^-?0*\.?0*$/.test(difference)) return null;
  // Display only: the check itself is the backend's Decimal rule.
  const cents = Math.abs(Number(difference)) * 100;
  return translate(messages).withinRounding(cents);
}

function PositionsTable({ overview }: { overview: ExchangeOverview }) {
  const t = useT(messages);
  if ((overview.positions ?? []).length === 0) return <p className="muted">{t.noExport}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{t.coin}</th>
            <th className="num">{t.perExport}</th>
            <th className="num">{t.perExchange}</th>
            <th>{t.reconciliation}</th>
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
                  <p className="cell-detail">{t.belowZero}</p>
                ) : null}
              </td>
              <td className="num">
                {formatUnits(position.typed_units)}
                {position.typed_as_of ? <p className="cell-detail">{t.onDay(formatDate(position.typed_as_of))}</p> : null}
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
  const t = useT(messages);
  if (imports.length === 0) return <p className="muted">{t.noExport}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{t.period}</th>
            <th>{t.movements}</th>
            <th>{t.status}</th>
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
                  <p className="cell-detail">{t.importedOn(formatDateTime(imp.created_at))}</p>
                </td>
                <td>
                  {t.newRows(imp.rows_new)}
                  {imp.rows_skipped > 0 ? t.alreadyThere(imp.rows_skipped) : ""}
                </td>
                <td>
                  <span className={imp.status === "reconciled" ? "badge" : imp.status === "imported" ? "badge badge-warn" : "badge badge-danger"}>
                    {imp.status === "imported" ? t.holdingsMissing : IMPORT_STATUS[imp.status]}
                  </span>
                  {rounded.map((balance) => (
                    <p key={balance.asset_id} className="cell-detail">
                      {balance.symbol}: {roundingText(balance.reconciled, balance.difference_units)}
                    </p>
                  ))}
                  {wrong.map((balance) => (
                    <p key={balance.asset_id} className="cell-detail">
                      {balance.symbol}
                      {t.typedVsHeld(formatUnits(balance.typed), formatUnits(balance.held))}
                    </p>
                  ))}
                  {wrong.length > 0 ? (
                    <p className="cell-detail">{t.wrongHint}</p>
                  ) : null}
                </td>
                <td className="actions">
                  <HoldingsDialog
                    statementImport={imp}
                    onDone={onChanged}
                    label={imp.status === "imported" ? t.enterHoldings : t.enterAgain}
                  />
                  {imp.id === latestImportId(imports)
                    ? wrong
                        .filter((balance) => balance.symbol !== "EUR")
                        .map((balance) => (
                          <CorrectionDialog
                            key={balance.asset_id}
                            statementImport={imp}
                            balance={balance}
                            onDone={onChanged}
                            label={wrong.length > 1 ? t.symbolCorrection(balance.symbol) : undefined}
                          />
                        ))
                    : null}
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
  label,
  buttonClass = "secondary",
}: {
  statementImport: ExchangeImport;
  onDone: () => void;
  label?: string;
  buttonClass?: string;
}) {
  const t = useT(messages);
  return (
    <ActionDialog label={label ?? t.enterHoldings} title={t.holdingsTitle} buttonClass={buttonClass}>
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
  const t = useT(messages);
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
        collected.messages[balance.asset_id] = [t.enterHolding(balance.symbol)];
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
      <p className="muted">{t.holdingsIntro(day(imp.last_at))}</p>
      {(imp.balances ?? []).map((balance) => (
        <Field
          key={balance.asset_id}
          id={`holding-${imp.id}-${balance.asset_id}`}
          label={t.symbolPerExchange(balance.symbol)}
          hint={t.heldPerExport(formatUnits(balance.held))}
          description={t.unitsHint}
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
        <Submit busy={busy}>{t.reconcile}</Submit>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- „Differenz als Korrektur buchen“ (D178 amended, migration 0030) --------------------------

/** The account's newest import (by its last row): only there may a correction be booked
 * (exchange.correction_not_latest). The API sends the imports newest first. */
function latestImportId(imports: ExchangeImport[]): string | undefined {
  return imports[0]?.id;
}

/** A signed quantity in German, "+" on an increase. */
function signedUnits(value: string | null | undefined): string {
  const text = formatUnits(value);
  return value != null && !value.startsWith("-") && text !== "0" ? `+${text}` : text;
}

/** „Differenz als Korrektur buchen“ in a dialog, for one coin whose typed balance differs. */
export function CorrectionDialog({
  statementImport,
  balance,
  onDone,
  label,
  buttonClass = "secondary",
}: {
  statementImport: ExchangeImport;
  balance: ImportBalance;
  onDone: () => void;
  label?: string;
  buttonClass?: string;
}) {
  const t = useT(messages);
  return (
    <ActionDialog label={label ?? t.bookCorrection} title={t.symbolCorrection(balance.symbol)} buttonClass={buttonClass}>
      {(close) => (
        <CorrectionForm
          statementImport={statementImport}
          balance={balance}
          onDone={() => {
            close();
            onDone();
          }}
        />
      )}
    </ActionDialog>
  );
}

const CORRECTION_KEYS: readonly string[] = ["reason"];

/** POST /movement-imports/{id}/corrections. The person writes only the reason: the amount is
 * the difference the API computed when the balance was typed (principle 1), shown here as
 * the API sent it. */
function CorrectionForm({
  statementImport: imp,
  balance,
  onDone,
}: {
  statementImport: ExchangeImport;
  balance: ImportBalance;
  onDone: () => void;
}) {
  const t = useT(messages);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(CORRECTION_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const text = reason.trim();
    if (text.length < 3) return form.invalid({ reason: [t.reasonTooShort] });
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.POST("/api/movement-imports/{import_id}/corrections", {
          params: { path: { import_id: imp.id } },
          body: { asset_id: balance.asset_id, reason: text },
        }),
      );
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  const difference = signedUnits(balance.difference_units);
  return (
    <form className="stack" onSubmit={submit} noValidate>
      <p>
        <strong>{balance.symbol}</strong>
        {t.correctionFigures(formatUnits(balance.typed), formatUnits(balance.held), difference, balance.symbol)}
      </p>
      <p className="muted">{t.correctionWhen}</p>
      <p className="muted">{t.correctionHow(difference, balance.symbol, day(imp.last_at))}</p>
      <Field
        id={`correction-${imp.id}-${balance.asset_id}`}
        label={t.reason}
        hint={t.reasonHint}
        errors={form.of("reason")}
      >
        <textarea rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} autoComplete="off" />
      </Field>
      <div className="row">
        <Submit busy={busy}>{t.bookCorrectionButton}</Submit>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** The Zu prüfen card's action for an unreconciled export: the same dialog when exactly one
 * coin (not EUR) differs on the account's newest import, always the link to the account. */
export function ExchangeUnreconciledActions({
  accountId,
  importId,
  onDone,
}: {
  accountId: string;
  importId: string | null | undefined;
  onDone: () => void;
}) {
  const t = useT(messages);
  const load = useCallback(
    () => ok(api.GET("/api/accounts/{account_id}/exchange", { params: { path: { account_id: accountId } } })),
    [accountId],
  );
  const state = useLoad(load);
  const imports = state.data?.imports ?? [];
  const imp = imports.find((candidate) => candidate.id === importId);
  const wrong = (imp?.balances ?? []).filter((balance) => balance.reconciled === false);
  const [first] = wrong;
  const only = imp && imp.id === latestImportId(imports) && wrong.length === 1 && first && first.symbol !== "EUR" ? first : null;
  return (
    <>
      {imp && only ? <CorrectionDialog statementImport={imp} balance={only} onDone={onDone} buttonClass="" /> : null}
      <Link href={`/accounts/${accountId}`}>{t.checkExport}</Link>
    </>
  );
}

/** „Alle Auszahlungen gingen an eigene Wallets“ (D178 (4)): every proposed withdrawal of one
 * import confirmed as a transfer to an own wallet. */
export function ConfirmWithdrawals({ importId, count, onDone }: { importId: string; count?: number; onDone: () => void }) {
  const t = useT(messages);
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
          ? t.saving
          : count != null && count > 1
            ? t.allWithdrawalsN(count)
            : count === 1
              ? t.oneWithdrawal
              : t.allWithdrawals}
      </button>
      <ErrorNotice error={error} />
    </>
  );
}

/** The answers to one proposed movement: „Stimmt“ keeps its kind, every other allowed kind
 * replaces it (POST /movements/{id}/confirm or /answer). An answer never teaches a deposit
 * address (D261, review fixes 16 Nov 2026): the answer does not show which wallet send it would
 * come from. A deposit answered „Übertrag von eigenem Konto“ waits on a transfer card in Zu
 * prüfen with its fitting send, whose pick carries the box „Adresse … merken“. */
export function MovementAnswers({ movement, onDone }: { movement: ExchangeMovementRow; onDone: () => void }) {
  const t = useT(messages);
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
            {busy === kind ? t.saving : MOVEMENT_KIND[kind]}
          </button>
        ))}
      </div>
      <ErrorNotice error={error} />
    </div>
  );
}

/** „Verknüpfung lösen“ (D261, review fixes 16 Nov 2026) on a movement the app linked by
 * itself from a learned deposit address or withdrawal pair: the link ends, both sides wait in
 * Zu prüfen again, and what the app had learned is forgotten, so it does not link them again.
 * A correct link by hand (with its box) teaches it anew. */
function ReleaseLearnedLink({ linkId, deposit, onDone }: { linkId: string; deposit: boolean; onDone: () => void }) {
  const t = useT(messages);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const learned = deposit ? t.learnedDeposit : t.learnedWithdrawal;

  async function release() {
    if (!window.confirm(t.releaseQuestion(learned))) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await ok(api.POST("/api/transfers/{link_id}/release", { params: { path: { link_id: linkId } } }));
      onDone();
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <p className="cell-detail">{t.linkedAutomatically}</p>
      <button type="button" className="secondary" onClick={() => void release()} disabled={busy}>
        {busy ? t.releasing : t.release}
      </button>
      <ErrorNotice error={error} />
    </div>
  );
}

function MovementsTable({ movements, onChanged }: { movements: ExchangeMovementRow[]; onChanged: () => void }) {
  const t = useT(messages);
  if (movements.length === 0) return <p className="muted">{t.noMovements}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{t.date}</th>
            <th>{t.kind}</th>
            <th className="num">{t.quantity}</th>
            <th className="num">{t.euro}</th>
            <th>{t.status}</th>
          </tr>
        </thead>
        <tbody>
          {movements.map((movement) => (
            <tr key={movement.id}>
              <td>{formatDateTime(movement.occurred_at)}</td>
              <td>
                {MOVEMENT_KIND[movement.kind]}
                {movement.network ? <p className="cell-detail">{t.network(movement.network)}</p> : null}
                {movement.note ? <p className="cell-detail">{t.noteReason(movement.note)}</p> : null}
              </td>
              <td className="num">
                {formatUnits(movement.quantity)} {movement.symbol}
                {movement.fee_quantity && !/^0*\.?0*$/.test(movement.fee_quantity) ? (
                  <p className="cell-detail">{t.fee(formatUnits(movement.fee_quantity), movement.symbol)}</p>
                ) : null}
              </td>
              <td className="num">{movement.eur_amount != null ? formatMoney(movement.eur_amount) : "–"}</td>
              <td>
                {movement.status === "confirmed" ? (
                  <span className="badge">{t.confirmed}</span>
                ) : (
                  <MovementAnswers movement={movement} onDone={onChanged} />
                )}
                {movement.learned_link_id ? (
                  <ReleaseLearnedLink linkId={movement.learned_link_id} deposit={!movement.quantity.startsWith("-")} onDone={onChanged} />
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type LearnedRow = NonNullable<ExchangeOverview["learned"]>[number];

/** D261: what the app learned from the person's links, each with „Vergessen“. A deposit
 * address is shown masked with its chain (never in clear, D179); a withdrawal pair names the
 * wallet and its masked address. Forgetting stops the automatic links; the links already made
 * stay. Nothing shows while nothing is learned. */
function LearnedAddresses({ overview, onChanged }: { overview: ExchangeOverview; onChanged: () => void }) {
  const t = useT(messages);
  const rows = overview.learned ?? [];
  if (rows.length === 0) return null;
  return (
    <>
      <h2>{t.learnedAddresses}</h2>
      <p className="muted">{t.learnedHint}</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t.kind}</th>
              <th>{t.networkHeading}</th>
              <th>{t.address}</th>
              <th>{t.since}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <LearnedLine key={row.id} accountId={overview.account_id} row={row} onChanged={onChanged} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function LearnedLine({ accountId, row, onChanged }: { accountId: string; row: LearnedRow; onChanged: () => void }) {
  const t = useT(messages);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function forget() {
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/accounts/{account_id}/learned/{learned_id}/forget", {
          params: { path: { account_id: accountId, learned_id: row.id } },
        }),
      );
      onChanged();
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <tr>
      <td>{row.kind === "deposit_address" ? t.depositAddress : t.withdrawalsTo(row.wallet_label)}</td>
      <td>{CHAIN[row.chain as keyof typeof CHAIN] ?? row.chain}</td>
      <td>{row.address_masked}</td>
      <td>{formatDate(row.created_at)}</td>
      <td>
        <button type="button" className="secondary" onClick={() => void forget()} disabled={busy}>
          {busy ? t.forgetting : t.forget}
        </button>
        <ErrorNotice error={error} />
      </td>
    </tr>
  );
}
