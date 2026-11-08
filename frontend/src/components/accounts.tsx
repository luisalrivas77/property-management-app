"use client";

// Konten (phase-2.md §7, D21): the statement upload, the imports of an account with their
// reconciliation status, the closing-balance form for a CSV export, the transactions table
// and the possible duplicates. Every euro figure is shown as the API returns it (principle 1):
// nothing is summed, subtracted or carried forward here, so there is no running balance and
// an unreconciled import shows the bank's and the computed balance side by side.
import Link from "next/link";
import { useId, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ruleCodeText } from "@/api/errors";
import type { Document, ImportBody, PossibleDuplicate, StatementImport, Transaction } from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { documentLabel } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatDate, formatDateTime, formatMoney } from "./format";
import { decimalInput, toSignedMoneyString } from "./input";
import { BALANCE_SOURCE, DUPLICATE_STATUS, IMPORT_STATUS, STATEMENT_FORMAT } from "./labels";
import { ActionDialog } from "./ui";

// --- upload ---------------------------------------------------------------------------

/** What POST /accounts/{id}/imports takes: a Sparkasse camt ZIP, one camt XML, a CSV-CAMT. */
export const STATEMENT_ACCEPT =
  ".zip,.xml,.csv,application/zip,application/x-zip-compressed,application/xml,text/xml,text/csv";

const TYPE_BY_EXTENSION: Record<string, string> = {
  csv: "text/csv",
  xml: "application/xml",
  zip: "application/zip",
};

/** The media type to declare for a statement file, from its extension. Browsers on Windows
 * label a .csv `application/vnd.ms-excel` (the registry's Excel association), which the API
 * refuses with 415; the API sniffs the format from the bytes anyway. Other extensions keep
 * the browser's type. */
export function statementMediaType(file: File): string {
  const extension = file.name.toLowerCase().split(".").pop() ?? "";
  return TYPE_BY_EXTENSION[extension] ?? file.type;
}

export interface Imported {
  statementImport: StatementImport;
  /** The same bytes were imported before (200 instead of 201): nothing was added. */
  existed: boolean;
  fileName: string;
}

/** POST /accounts/{id}/imports as multipart, with the part's type set from the extension
 * (see statementMediaType). The FormData is passed under the generated body type, as
 * uploadDocument does. */
export async function importStatement(accountId: string, file: File): Promise<Imported> {
  const form = new FormData();
  form.append("file", new Blob([file], { type: statementMediaType(file) }), file.name);
  const { data, response } = await api.POST("/api/accounts/{account_id}/imports", {
    params: { path: { account_id: accountId } },
    body: form as unknown as ImportBody,
  });
  return { statementImport: data as StatementImport, existed: response.status === 200, fileName: file.name };
}

/** "22 neue Umsätze, 3 schon vorhanden" for an import. */
export function lineCounts(statementImport: StatementImport): string {
  const added = statementImport.lines_new === 1 ? "1 neuer Umsatz" : `${statementImport.lines_new} neue Umsätze`;
  return statementImport.lines_skipped > 0 ? `${added}, ${statementImport.lines_skipped} schon vorhanden` : added;
}

/** Drop zone and file input "Kontoauszug hochladen"; one notice per file afterwards. */
export function StatementUpload({ accountId, onImported }: { accountId: string; onImported: () => void }) {
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [results, setResults] = useState<Imported[]>([]);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    setResults([]);
    try {
      for (const file of Array.from(files)) {
        const result = await importStatement(accountId, file);
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
        <label htmlFor={inputId}>{busy ? "Importiert …" : "Kontoauszug hochladen"}</label>
        <input
          ref={input}
          id={inputId}
          type="file"
          accept={STATEMENT_ACCEPT}
          multiple
          disabled={busy}
          onChange={(event: ChangeEvent<HTMLInputElement>) => void upload(event.target.files)}
        />
        <p className="muted hint">
          Sparkasse „Umsätze exportieren“: camt als ZIP (bevorzugt, mit Salden) oder CSV-CAMT; auch eine einzelne camt-XML.
          Datei hierher ziehen oder auswählen.
        </p>
      </div>
      {results.map((result, index) => (
        <ImportResult key={index} result={result} />
      ))}
      <ErrorNotice error={error} />
    </div>
  );
}

function ImportResult({ result }: { result: Imported }) {
  const { statementImport: imp, existed, fileName } = result;
  if (existed) {
    return (
      <div className="notice notice-info" role="status">
        <p>
          Bereits importiert: {fileName}. Diese Datei wurde schon einmal hochgeladen; es wurde nichts hinzugefügt.
        </p>
      </div>
    );
  }
  const tone = imp.status === "reconciled" ? "notice-ok" : imp.status === "imported" ? "notice-warn" : "notice-error";
  return (
    <div className={`notice ${tone}`} role="status">
      <p>
        Importiert: {fileName} · {lineCounts(imp)} · {IMPORT_STATUS[imp.status]}
      </p>
      {imp.status === "imported" ? (
        <p className="muted">Die Datei enthält keinen Kontostand: unten „Endsaldo eingeben“ mit dem Saldo aus dem Online-Banking.</p>
      ) : null}
      {imp.error_code ? <p className="muted">{ruleCodeText(imp.error_code) ?? imp.error_code}</p> : null}
    </div>
  );
}

// --- imports --------------------------------------------------------------------------

const PILL_CLASS: Record<StatementImport["status"], string> = {
  reconciled: "badge",
  imported: "badge badge-warn",
  unreconciled: "badge badge-danger",
  failed: "badge badge-danger",
};

export function ImportStatusPill({ statementImport }: { statementImport: StatementImport }) {
  return <span className={PILL_CLASS[statementImport.status]}>{IMPORT_STATUS[statementImport.status]}</span>;
}

/** Why an import is not reconciled, with the two balances the API compared. */
function ImportReason({ statementImport: imp }: { statementImport: StatementImport }) {
  if (imp.status === "reconciled" || imp.status === "imported") return null;
  const code = imp.error_code;
  return (
    <>
      {code ? (
        <p className="cell-detail">
          {ruleCodeText(code) ?? "Fehler."} <code className="error-code">{code}</code>
        </p>
      ) : null}
      {code === "statement.opening_mismatch" ? (
        <p className="cell-detail">
          Anfangssaldo laut Datei {formatMoney(imp.declared_opening)} · berechnet {formatMoney(imp.computed_opening)}
        </p>
      ) : null}
      {code === "statement.unreconciled" || code === "statement.opening_mismatch" ? (
        <p className="cell-detail">
          Endsaldo laut Bank {formatMoney(imp.declared_closing)} · berechnet {formatMoney(imp.computed_closing)}
        </p>
      ) : null}
    </>
  );
}

/** A CSV export states no balance (D21): the closing balance typed from online banking. */
function canDeclareBalance(imp: StatementImport): boolean {
  return imp.format === "csv_camt" && imp.balance_source !== "file";
}

function period(imp: StatementImport): string {
  if (!imp.first_booking_on) return "–";
  return imp.first_booking_on === imp.last_booking_on
    ? formatDate(imp.first_booking_on)
    : `${formatDate(imp.first_booking_on)} – ${formatDate(imp.last_booking_on)}`;
}

export function ImportsTable({
  accountId,
  imports,
  documents,
  onChanged,
}: {
  accountId: string;
  imports: StatementImport[];
  documents: Map<string, Document>;
  onChanged: () => void;
}) {
  if (imports.length === 0) return <p className="muted">Noch keine Kontoauszüge importiert.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Zeitraum</th>
            <th>Datei</th>
            <th>Umsätze</th>
            <th className="num">Endsaldo</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {imports.map((imp) => {
            const document = documents.get(imp.document_id);
            return (
              <tr key={imp.id}>
                <td>
                  {period(imp)}
                  <p className="cell-detail">importiert {formatDateTime(imp.created_at)}</p>
                </td>
                <td>
                  <Link href={`/documents/${imp.document_id}`}>{document ? documentLabel(document) : "Datei"}</Link>
                  <p className="cell-detail">{STATEMENT_FORMAT[imp.format]}</p>
                </td>
                <td>
                  {lineCounts(imp)}
                  {imp.lines_new > 0 ? (
                    <p className="cell-detail">
                      {/* ?import= lists the lines this import stored, not the ones it skipped as known. */}
                      <Link href={`/accounts/${accountId}?tab=umsaetze&import=${imp.id}`}>
                        {imp.lines_new === 1 ? "Den neuen Umsatz anzeigen" : `Die ${imp.lines_new} neuen Umsätze anzeigen`}
                      </Link>
                    </p>
                  ) : null}
                </td>
                <td className="num">
                  {formatMoney(imp.declared_closing)}
                  <p className="cell-detail">{BALANCE_SOURCE[imp.balance_source]}</p>
                </td>
                <td>
                  <ImportStatusPill statementImport={imp} />
                  <ImportReason statementImport={imp} />
                </td>
                <td className="actions">
                  {canDeclareBalance(imp) ? (
                    <ActionDialog
                      label={imp.declared_closing == null ? "Endsaldo eingeben" : "Endsaldo korrigieren"}
                      title="Endsaldo laut Online-Banking"
                      buttonClass={imp.declared_closing == null ? "" : "secondary"}
                    >
                      {(close) => (
                        <BalanceForm
                          statementImport={imp}
                          onDone={() => {
                            close();
                            onChanged();
                          }}
                        />
                      )}
                    </ActionDialog>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const BALANCE_KEYS = ["closing_balance"] as const;

/** POST /imports/{id}/balances: the balance Luis reads in online banking (D21). */
export function BalanceForm({ statementImport: imp, onDone }: { statementImport: StatementImport; onDone: () => void }) {
  const [closing, setClosing] = useState(decimalInput(imp.declared_closing));
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(BALANCE_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const closingBalance = values.take("closing_balance", toSignedMoneyString(closing));
    if (!values.ok || closingBalance === undefined) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.POST("/api/imports/{import_id}/balances", {
          params: { path: { import_id: imp.id } },
          body: { closing_balance: closingBalance },
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
      <Field
        id={`balance-${imp.id}`}
        label="Endsaldo laut Online-Banking (€)"
        hint={`Der Kontostand am Ende des ${formatDate(imp.last_booking_on)}, nach dem letzten Umsatz der Datei. Negativ mit Minus, z. B. -3.028,51.`}
        description="Der Kontostand laut Online-Banking nach dem letzten Umsatz dieser Datei. Die CSV-Datei enthält keinen Saldo; mit diesem Wert prüft Hausbuch, ob der Import aufgeht."
        errors={form.of("closing_balance")}
      >
        <input type="text" inputMode="decimal" value={closing} onChange={(e) => setClosing(e.target.value)} autoComplete="off" required />
      </Field>
      <div className="row">
        <Submit busy={busy}>Abstimmen</Submit>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- transactions -----------------------------------------------------------------------

function isCredit(amount: string): boolean {
  return !amount.startsWith("-") && !/^0+(\.0+)?$/.test(amount);
}

/** Umsätze. Each counting line links to its allocation panel on the review page
 * (/inbox?umsatz=…), which also corrects an allocation already confirmed. */
export function TransactionsTable({ transactions }: { transactions: Transaction[] }) {
  if (transactions.length === 0) return <p className="muted">Keine Umsätze in diesem Zeitraum.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Buchungstag</th>
            <th>Gegenseite</th>
            <th>Verwendungszweck</th>
            <th className="num">Betrag</th>
            <th>Zuordnung</th>
          </tr>
        </thead>
        <tbody>
          {transactions.map((line) => (
            <tr key={line.id} className={line.excluded_as_duplicate_of ? "excluded" : undefined}>
              <td>
                {formatDate(line.booking_date)}
                {line.value_date && line.value_date !== line.booking_date ? (
                  <p className="cell-detail">Wert {formatDate(line.value_date)}</p>
                ) : null}
              </td>
              <td>
                {line.counterparty_name ?? "–"}
                {line.counterparty_iban_masked ? <p className="cell-detail">{line.counterparty_iban_masked}</p> : null}
              </td>
              <td>
                {line.purpose || "–"}
                {line.booking_text ? <p className="cell-detail">{line.booking_text}</p> : null}
                {line.excluded_as_duplicate_of ? (
                  <p className="cell-detail">
                    <span className="badge badge-muted">Doppelbuchung, zählt nicht</span>
                  </p>
                ) : null}
              </td>
              <td className={isCredit(line.amount) ? "num amount-in" : "num"}>
                {isCredit(line.amount) ? "+" : ""}
                {formatMoney(line.amount)}
              </td>
              <td className="actions">
                {line.excluded_as_duplicate_of ? null : (
                  <Link href={`/inbox?umsatz=${line.id}`} aria-label={`Zuordnung: ${line.counterparty_name ?? "Umsatz"} am ${formatDate(line.booking_date)}`}>
                    Zuordnung
                  </Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- possible duplicates ------------------------------------------------------------------

function DuplicateLine({ caption, line, imp }: { caption: string; line: Transaction; imp: StatementImport | undefined }) {
  return (
    <tr className={line.excluded_as_duplicate_of ? "excluded" : undefined}>
      <td>
        {caption}
        <p className="cell-detail">{imp ? `${STATEMENT_FORMAT[imp.format]}, importiert ${formatDateTime(imp.created_at)}` : "–"}</p>
      </td>
      <td>{formatDate(line.booking_date)}</td>
      <td>
        {line.counterparty_name ?? "–"}
        {line.counterparty_iban_masked ? <p className="cell-detail">{line.counterparty_iban_masked}</p> : null}
      </td>
      <td>{line.purpose || "–"}</td>
      <td className="num">{formatMoney(line.amount)}</td>
    </tr>
  );
}

export function DuplicateCard({
  pair,
  first,
  later,
  imports,
  onDecided,
}: {
  pair: PossibleDuplicate;
  first: Transaction;
  later: Transaction;
  imports: Map<string, StatementImport>;
  onDecided: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function decide(status: "merged" | "kept_both") {
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/possible-duplicates/{possible_duplicate_id}", {
          params: { path: { possible_duplicate_id: pair.id } },
          body: { status },
        }),
      );
      onDecided();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card stack">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Umsatz</th>
              <th>Buchungstag</th>
              <th>Gegenseite</th>
              <th>Verwendungszweck</th>
              <th className="num">Betrag</th>
            </tr>
          </thead>
          <tbody>
            <DuplicateLine caption="Zuerst importiert" line={first} imp={imports.get(first.import_id)} />
            <DuplicateLine caption="Später importiert" line={later} imp={imports.get(later.import_id)} />
          </tbody>
        </table>
      </div>
      {pair.status === "open" ? (
        <div className="row">
          <button type="button" disabled={busy} onClick={() => void decide("merged")}>
            Zusammenführen
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={() => void decide("kept_both")}>
            Beide behalten
          </button>
        </div>
      ) : (
        <p>
          <span className="badge badge-muted">{DUPLICATE_STATUS[pair.status]}</span>
          {pair.decided_at ? <span className="muted"> am {formatDateTime(pair.decided_at)}</span> : null}
        </p>
      )}
      <ErrorNotice error={error} />
    </div>
  );
}

/** The possible duplicates whose lines are on this account (GET /possible-duplicates is per
 * workspace and names transaction ids only, so they are matched against the account's
 * transactions): the open ones to decide, the decided ones folded away. */
export function DuplicatesList({
  duplicates,
  transactions,
  imports,
  onDecided,
}: {
  duplicates: PossibleDuplicate[];
  transactions: Map<string, Transaction>;
  imports: Map<string, StatementImport>;
  onDecided: () => void;
}) {
  const shown = duplicates.flatMap((pair) => {
    const first = transactions.get(pair.transaction_id);
    const later = transactions.get(pair.other_transaction_id);
    return first && later ? [{ pair, first, later }] : [];
  });
  const open = shown.filter((entry) => entry.pair.status === "open");
  const decided = shown.filter((entry) => entry.pair.status !== "open");
  const card = ({ pair, first, later }: (typeof shown)[number]) => (
    <DuplicateCard key={pair.id} pair={pair} first={first} later={later} imports={imports} onDecided={onDecided} />
  );
  return (
    <div className="stack">
      {open.length === 0 ? (
        <p className="muted">Keine offenen möglichen Doppelbuchungen.</p>
      ) : (
        <>
          <p className="muted">
            Dieselbe Zahlung zweimal importiert, etwa einmal als CSV und einmal als camt? „Zusammenführen“ nimmt den später
            importierten Umsatz aus allen Salden, und die Kontoauszüge werden neu abgestimmt. „Beide behalten“, wenn es zwei
            echte Zahlungen waren.
          </p>
          {open.map(card)}
        </>
      )}
      {decided.length > 0 ? (
        <details>
          <summary>{decided.length} bereits entschieden</summary>
          <div className="stack">{decided.map(card)}</div>
        </details>
      ) : null}
    </div>
  );
}

/** Open possible duplicates whose first line is on this account. */
export function openDuplicateCount(duplicates: PossibleDuplicate[], transactions: Map<string, Transaction>): number {
  return duplicates.filter((pair) => pair.status === "open" && transactions.has(pair.transaction_id)).length;
}
