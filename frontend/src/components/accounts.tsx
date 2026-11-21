"use client";

// Konten (phase-2.md §7, D21): the statement upload, the imports of an account with their
// reconciliation status, the closing-balance form for a CSV export, the transactions table
// and the possible duplicates. Every euro figure is shown as the API returns it (principle 1):
// nothing is summed, subtracted or carried forward here, so there is no running balance and
// an unreconciled import shows the bank's and the computed balance side by side.
import Link from "next/link";
import { useId, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes, ruleCodeText } from "@/api/errors";
import type {
  Document,
  ImportBody,
  Owner,
  OwnershipShare,
  PossibleDuplicate,
  StatementImport,
  Transaction,
  TransactionAllocationStatus,
} from "@/api/schemas";
import { translate, useFormat, useT, type Formatter } from "@/i18n";
import { statements } from "@/i18n/accounts";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { documentLabel, storeAsDocument, StoredAsDocument, UPLOAD_ACCEPT, type Uploaded } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatSharePercent } from "./format";
import { decimalInput, toSignedMoneyString } from "./input";
import { ALLOCATION_CATEGORY, BALANCE_SOURCE, DUPLICATE_STATUS, IMPORT_STATUS, STATEMENT_FORMAT } from "./labels";
import { ActionDialog } from "./ui";

// --- upload ---------------------------------------------------------------------------

/** What POST /accounts/{id}/imports takes: a Sparkasse camt ZIP, one camt XML, a CSV-CAMT, or
 * DKB's CSV export (phase 6 D173 (3)); the API sniffs the format from the bytes. */
export const STATEMENT_ACCEPT =
  ".zip,.xml,.csv,application/zip,application/x-zip-compressed,application/xml,text/xml,text/csv";
/** A credit card's monthly statement, the PDF from the issuer's portal (phase 6 D176). */
export const CARD_STATEMENT_ACCEPT = ".pdf,application/pdf";

const TYPE_BY_EXTENSION: Record<string, string> = {
  csv: "text/csv",
  pdf: "application/pdf",
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
  return translate(statements).lineCounts(statementImport.lines_new, statementImport.lines_skipped);
}

/** Drop zone and file input "Kontoauszug hochladen"; one notice per file afterwards. */
export function StatementUpload({
  accountId,
  onImported,
  card = false,
  depot = false,
}: {
  accountId: string;
  onImported: () => void;
  /** A credit card account: its monthly PDF statements (phase 6 D176). */
  card?: boolean;
  /** A depot's cash account: the bank's PDF Kontoauszug, read by code (phase 6 step 4). */
  depot?: boolean;
}) {
  const t = useT(statements);
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [results, setResults] = useState<Imported[]>([]);
  const [stored, setStored] = useState<Uploaded[]>([]);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    setResults([]);
    setStored([]);
    try {
      for (const file of Array.from(files)) {
        try {
          const result = await importStatement(accountId, file);
          setResults((previous) => [...previous, result]);
        } catch (caught) {
          // Not a statement this account reads: any other document is kept and read by the AI.
          if (!isUnknownFormat(caught)) throw caught;
          const result = await storeAsDocument(file);
          setStored((previous) => [...previous, result]);
        }
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
        <label htmlFor={inputId}>
          {busy ? t.importing : card ? t.uploadCard : t.uploadStatement}
        </label>
        <input
          ref={input}
          id={inputId}
          type="file"
          accept={`${card || depot ? CARD_STATEMENT_ACCEPT : STATEMENT_ACCEPT},${UPLOAD_ACCEPT}`}
          multiple
          disabled={busy}
          onChange={(event: ChangeEvent<HTMLInputElement>) => void upload(event.target.files)}
        />
        {card ? (
          <p className="muted hint">{t.hintCard}</p>
        ) : depot ? (
          <p className="muted hint">{t.hintDepot}</p>
        ) : (
          <p className="muted hint">{t.hintBank}</p>
        )}
      </div>
      {results.map((result, index) => (
        <ImportResult key={index} result={result} />
      ))}
      {stored.map((result, index) => (
        <StoredAsDocument key={index} result={result} what={card ? t.storedNotCard : t.storedNotStatement} />
      ))}
      <ErrorNotice error={error} />
    </div>
  );
}

/** A file none of the statement readers knows (statement.unknown_format). */
function isUnknownFormat(error: unknown): boolean {
  return error instanceof ApiError && error.body != null && ruleCodes(error.body).includes("statement.unknown_format");
}

function ImportResult({ result }: { result: Imported }) {
  const { statementImport: imp, existed, fileName } = result;
  const t = useT(statements);
  const f = useFormat();
  if (existed) {
    return (
      <div className="notice notice-info" role="status">
        <p>{t.alreadyImported(fileName)}</p>
      </div>
    );
  }
  const tone = imp.status === "reconciled" ? "notice-ok" : imp.status === "imported" ? "notice-warn" : "notice-error";
  return (
    <div className={`notice ${tone}`} role="status">
      <p>{t.importedLine(fileName, t.lineCounts(imp.lines_new, imp.lines_skipped), IMPORT_STATUS[imp.status])}</p>
      {imp.status === "imported" && imp.stated_closing != null ? (
        <p className="muted">{t.statedClosing(f.date(imp.stated_closing_on))}</p>
      ) : imp.status === "imported" ? (
        <p className="muted">{t.noClosingInFile}</p>
      ) : null}
      {imp.error_code ? <p className="muted">{ruleCodeText(imp.error_code) ?? imp.error_code}</p> : null}
    </div>
  );
}

// --- owners (phase 6 D157) -----------------------------------------------------------

/** The owner chip of an account (mockup 08): „Gemeinsam 50/50“ for two halves, the owner's name
 * for a sole owner, otherwise each owner with their percentage. Empty shares give "" (the caller
 * shows the to-do „Eigentümer festlegen“ instead). */
export function ownerChip(shares: OwnershipShare[], owners: Owner[]): string {
  const name = (ownerId: string) => owners.find((owner) => owner.id === ownerId)?.full_name ?? "?";
  const half = (share: OwnershipShare) => share.share_numerator * 2 === share.share_denominator;
  if (shares.length === 2 && shares.every(half)) return translate(statements).jointHalf;
  const [only] = shares;
  if (shares.length === 1 && only && only.share_numerator === only.share_denominator) return name(only.owner_id);
  return shares
    .map(
      (share) =>
        `${name(share.owner_id)} ${formatSharePercent({ numerator: share.share_numerator, denominator: share.share_denominator })}`,
    )
    .join(", ");
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
function ImportReason({ statementImport: imp, imports }: { statementImport: StatementImport; imports: StatementImport[] }) {
  const t = useT(statements);
  const f = useFormat();
  if (imp.status === "reconciled" || imp.status === "imported") return null;
  const code = imp.error_code;
  const gapAfter = code === "statement.opening_mismatch" && imp.derived_opening_balance == null ? previousReconciled(imp, imports) : null;
  return (
    <>
      {code ? (
        <p className="cell-detail">
          {ruleCodeText(code) ?? t.error} <code className="error-code">{code}</code>
        </p>
      ) : null}
      {code === "statement.opening_mismatch" ? (
        <p className="cell-detail">{t.openingMismatch(f.money(imp.declared_opening), f.money(imp.computed_opening))}</p>
      ) : null}
      {code === "statement.unreconciled" || code === "statement.opening_mismatch" ? (
        <p className="cell-detail">{t.closingMismatch(f.money(imp.declared_closing), f.money(imp.computed_closing))}</p>
      ) : null}
      {gapAfter?.last_booking_on && imp.first_booking_on ? (
        <p className="cell-detail">{t.missingStatement(f.date(gapAfter.last_booking_on), f.date(imp.first_booking_on))}</p>
      ) : null}
    </>
  );
}

/** The latest reconciled import that ends before this one starts: a later statement whose
 * opening disagrees with it is most likely separated from it by a statement not uploaded
 * (principle 7: say which period is missing, not only that the balances differ). */
function previousReconciled(imp: StatementImport, imports: StatementImport[]): StatementImport | null {
  const start = imp.first_booking_on;
  if (!start) return null;
  let best: StatementImport | null = null;
  for (const other of imports) {
    if (other.id === imp.id || other.status !== "reconciled" || !other.last_booking_on) continue;
    if (other.last_booking_on >= start) continue;
    if (!best?.last_booking_on || other.last_booking_on > best.last_booking_on) best = other;
  }
  return best;
}

/** Every import whose balances do not come from the file takes a closing balance typed from
 * online banking (D21): a Sparkasse CSV, a DKB CSV (phase 6 D173 (3), its „Kontostand vom“
 * pre-filled), a bank sync's. Mirrors services/statements._declare, which refuses only a
 * balance_source of file. */
function canDeclareBalance(imp: StatementImport): boolean {
  return imp.balance_source !== "file";
}

function period(imp: StatementImport, f: Formatter): string {
  if (!imp.first_booking_on) return "–";
  return imp.first_booking_on === imp.last_booking_on
    ? f.date(imp.first_booking_on)
    : `${f.date(imp.first_booking_on)} – ${f.date(imp.last_booking_on)}`;
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
  const t = useT(statements);
  const f = useFormat();
  if (imports.length === 0) return <p className="muted">{t.noImports}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{t.colPeriod}</th>
            <th>{t.colFile}</th>
            <th>{t.colLines}</th>
            <th className="num">{t.colClosing}</th>
            <th>{t.colStatus}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {imports.map((imp) => {
            const document = documents.get(imp.document_id);
            return (
              <tr key={imp.id}>
                <td>
                  {period(imp, f)}
                  <p className="cell-detail">{t.importedAt(f.dateTime(imp.created_at))}</p>
                </td>
                <td>
                  <Link href={`/documents/${imp.document_id}`}>{document ? documentLabel(document) : t.file}</Link>
                  <p className="cell-detail">
                    {document?.source === "bank_sync" ? t.viaBankSync : ""}
                    {STATEMENT_FORMAT[imp.format]}
                  </p>
                </td>
                <td>
                  {t.lineCounts(imp.lines_new, imp.lines_skipped)}
                  {imp.lines_new > 0 ? (
                    <p className="cell-detail">
                      {/* ?import= lists the lines this import stored, not the ones it skipped as known. */}
                      <Link href={`/accounts/${accountId}?tab=umsaetze&import=${imp.id}`}>
                        {t.showNewLines(imp.lines_new)}
                      </Link>
                    </p>
                  ) : null}
                </td>
                <td className="num">
                  {f.money(imp.declared_closing)}
                  <p className="cell-detail">{BALANCE_SOURCE[imp.balance_source]}</p>
                </td>
                <td>
                  <ImportStatusPill statementImport={imp} />
                  <ImportReason statementImport={imp} imports={imports} />
                </td>
                <td className="actions">
                  <OpeningFromClosing statementImport={imp} onDone={onChanged} />
                  {canDeclareBalance(imp) ? (
                    <ActionDialog
                      label={imp.declared_closing == null ? t.enterClosing : t.correctClosing}
                      title={t.closingTitle}
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

/** Principle 7: a new account's balance on its anchor day is rarely known, but its first
 * export states today's, and a statement that prints its Alter Saldo states its opening.
 * When the API offers the opening balance (and day) this import implies
 * (derived_opening_balance: the account's earliest import, see statements.derived_opening),
 * one click takes it (POST /imports/{id}/opening-from-closing); the API
 * computes it and re-checks every import of the account. */
export function OpeningFromClosing({ statementImport: imp, onDone }: { statementImport: StatementImport; onDone: () => void }) {
  const t = useT(statements);
  const f = useFormat();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (imp.derived_opening_balance == null) return null;

  async function take() {
    setBusy(true);
    setError(null);
    try {
      await ok(api.POST("/api/imports/{import_id}/opening-from-closing", { params: { path: { import_id: imp.id } } }));
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <p className="cell-detail">{t.derivedOpening(f.date(imp.derived_opening_balance_on), f.money(imp.derived_opening_balance))}</p>
      <button type="button" onClick={() => void take()} disabled={busy}>
        {busy ? t.taking : t.takeOpening}
      </button>
      <ErrorNotice error={error} />
    </div>
  );
}

const BALANCE_KEYS = ["closing_balance"] as const;

/** POST /imports/{id}/balances: the balance Luis reads in online banking (D21). */
export function BalanceForm({ statementImport: imp, onDone }: { statementImport: StatementImport; onDone: () => void }) {
  // Phase 6 D173 (3): a DKB file states its „Kontostand vom“ beside its lines; it pre-fills the
  // closing for the person to confirm, never reconciled against on its own.
  const t = useT(statements);
  const f = useFormat();
  const stated = imp.declared_closing == null && imp.stated_closing != null ? imp.stated_closing : null;
  const [closing, setClosing] = useState(decimalInput(imp.declared_closing ?? stated));
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
        label={t.closingLabel}
        hint={t.closingHint(f.date(imp.last_booking_on))}
        description={t.closingDescription}
        errors={form.of("closing_balance")}
      >
        <input type="text" inputMode="decimal" value={closing} onChange={(e) => setClosing(e.target.value)} autoComplete="off" required />
      </Field>
      {stated != null ? (
        <p className="muted">{t.suggestedFromFile(f.date(imp.stated_closing_on), f.date(imp.last_booking_on))}</p>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{t.reconcile}</Submit>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- transactions -----------------------------------------------------------------------

function isCredit(amount: string): boolean {
  return !amount.startsWith("-") && !/^0+(\.0+)?$/.test(amount);
}

type LineFilter = "all" | "open" | "booked";

const LINE_FILTERS: readonly LineFilter[] = ["all", "open", "booked"];

/** Offen: everything still waiting for a decision (a merged duplicate is in Alle only). */
const OPEN_STATUSES: readonly TransactionAllocationStatus[] = ["open", "proposed", "duplicate_open", "not_reconciled"];

function matchesFilter(line: Transaction, filter: LineFilter): boolean {
  if (filter === "open") return OPEN_STATUSES.includes(line.allocation_status);
  if (filter === "booked") return line.allocation_status === "confirmed";
  return true;
}

/** Where a line stands (Transaction.allocation_status, computed by the API), as a link to
 * where it is decided: the allocation panel on the review page (/inbox?umsatz=…, which also
 * corrects a confirmed allocation), the account's possible duplicates, or its Kontoauszüge. One
 * half of a returned payment (returned_with, D162 amended (7)) names the other half's date. A
 * merged duplicate has no link; its pill sits under the purpose. */
function LineStatus({ line }: { line: Transaction }) {
  const t = useT(statements);
  const f = useFormat();
  const subject = t.lineSubject(line.counterparty_name ?? t.lineFallback, f.date(line.booking_date));
  const panel = `/inbox?umsatz=${line.id}`;
  switch (line.allocation_status) {
    case "excluded":
      return null;
    case "confirmed": {
      // D162 amended (7): one half of a returned payment names the other, since together they
      // count as nothing (not a transfer to another account).
      const partner = line.returned_with;
      const categories = line.allocation_categories.map((category) => ALLOCATION_CATEGORY[category]).join(" + ");
      const text = partner
        ? line.amount.startsWith("-")
          ? t.bookedReturnBack(f.date(partner.booking_date))
          : t.bookedReturnFrom(f.date(partner.booking_date))
        : categories
          ? t.bookedAs(categories)
          : t.booked;
      return (
        <Link href={panel} aria-label={`${text} – ${subject}`}>
          {text}
        </Link>
      );
    }
    case "proposed":
      return (
        <Link href={panel} className="badge badge-warn" aria-label={`${t.reviewSuggestion} – ${subject}`}>
          {t.reviewSuggestion}
        </Link>
      );
    case "duplicate_open":
      return (
        <Link href={`/accounts/${line.account_id}?tab=doppelbuchungen`} className="badge badge-warn" aria-label={`${t.possibleDuplicate} – ${subject}`}>
          {t.possibleDuplicate}
        </Link>
      );
    case "not_reconciled":
      return (
        <Link href={`/accounts/${line.account_id}?tab=kontoauszuege`} className="badge badge-muted" aria-label={`${t.notReconciled} – ${subject}`}>
          {t.notReconciled}
        </Link>
      );
    default:
      return (
        <Link href={panel} className="badge" aria-label={`${t.open} – ${subject}`}>
          {t.open}
        </Link>
      );
  }
}

/** Umsätze, with where each line stands and a filter Alle / Offen / Gebucht. */
export function TransactionsTable({ transactions }: { transactions: Transaction[] }) {
  const t = useT(statements);
  const [filter, setFilter] = useState<LineFilter>("all");
  if (transactions.length === 0) return <p className="muted">{t.noLines}</p>;
  const filterLabel: Record<LineFilter, string> = { all: t.filterAll, open: t.filterOpen, booked: t.filterBooked };
  const shown = transactions.filter((line) => matchesFilter(line, filter));
  return (
    <div className="stack">
      <div className="row" role="group" aria-label={t.filterLines}>
        {LINE_FILTERS.map((key) => (
          <button
            key={key}
            type="button"
            className={key === filter ? undefined : "secondary"}
            aria-pressed={key === filter}
            onClick={() => setFilter(key)}
          >
            {filterLabel[key]} ({transactions.filter((line) => matchesFilter(line, key)).length})
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <p className="muted">{filter === "open" ? t.noOpenLines : t.noBookedLines}</p>
      ) : (
        <TransactionRows transactions={shown} />
      )}
    </div>
  );
}

function TransactionRows({ transactions }: { transactions: Transaction[] }) {
  const t = useT(statements);
  const f = useFormat();
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{t.colBookingDay}</th>
            <th>{t.colCounterparty}</th>
            <th>{t.colPurpose}</th>
            <th className="num">{t.colAmount}</th>
            <th>{t.colAllocation}</th>
          </tr>
        </thead>
        <tbody>
          {transactions.map((line) => (
            <tr key={line.id} className={line.excluded_as_duplicate_of ? "excluded" : undefined}>
              <td>
                {f.date(line.booking_date)}
                {line.value_date && line.value_date !== line.booking_date ? (
                  <p className="cell-detail">{t.valueDate(f.date(line.value_date))}</p>
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
                    <span className="badge badge-muted">{t.duplicateNotCounted}</span>
                  </p>
                ) : null}
              </td>
              <td className={isCredit(line.amount) ? "num amount-in" : "num"}>
                {isCredit(line.amount) ? "+" : ""}
                {f.money(line.amount)}
              </td>
              <td className="actions">
                <LineStatus line={line} />
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
  const t = useT(statements);
  const f = useFormat();
  return (
    <tr className={line.excluded_as_duplicate_of ? "excluded" : undefined}>
      <td>
        {caption}
        <p className="cell-detail">{imp ? t.importedVia(STATEMENT_FORMAT[imp.format], f.dateTime(imp.created_at)) : "–"}</p>
      </td>
      <td>{f.date(line.booking_date)}</td>
      <td>
        {line.counterparty_name ?? "–"}
        {line.counterparty_iban_masked ? <p className="cell-detail">{line.counterparty_iban_masked}</p> : null}
      </td>
      <td>{line.purpose || "–"}</td>
      <td className="num">{f.money(line.amount)}</td>
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
  const t = useT(statements);
  const f = useFormat();
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
              <th>{t.colLine}</th>
              <th>{t.colBookingDay}</th>
              <th>{t.colCounterparty}</th>
              <th>{t.colPurpose}</th>
              <th className="num">{t.colAmount}</th>
            </tr>
          </thead>
          <tbody>
            <DuplicateLine caption={t.firstImported} line={first} imp={imports.get(first.import_id)} />
            <DuplicateLine caption={t.laterImported} line={later} imp={imports.get(later.import_id)} />
          </tbody>
        </table>
      </div>
      {pair.status === "open" ? (
        <div className="row">
          <button type="button" disabled={busy} onClick={() => void decide("merged")}>
            {t.merge}
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={() => void decide("kept_both")}>
            {t.keepBoth}
          </button>
        </div>
      ) : (
        <p>
          <span className="badge badge-muted">{DUPLICATE_STATUS[pair.status]}</span>
          {pair.decided_at ? <span className="muted">{t.decidedOn(f.dateTime(pair.decided_at))}</span> : null}
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
  const t = useT(statements);
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
        <p className="muted">{t.noOpenDuplicates}</p>
      ) : (
        <>
          <p className="muted">{t.duplicatesExplain}</p>
          {open.map(card)}
        </>
      )}
      {decided.length > 0 ? (
        <details>
          <summary>{t.alreadyDecided(decided.length)}</summary>
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
