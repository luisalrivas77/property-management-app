// The API's error envelope (docs/phases/phase-1-step-3.md §3.5) as a thrown error, and the
// German text the UI shows for it. Rule: show the API's `error`, `message` and field `msg`s,
// never anything the user typed (the API never echoes input either, §3.6).
import type { ErrorKind, ErrorResponse, FieldError } from "./schemas";

export class ApiError extends Error {
  readonly status: number;
  /** The parsed ErrorResponse, or null when the body was not one (e.g. the proxy's own 500). */
  readonly body: ErrorResponse | null;
  readonly requestId: string | null;

  constructor(status: number, body: ErrorResponse | null, requestId: string | null) {
    super(body ? `${status} ${body.error}` : `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.requestId = body?.request_id ?? requestId;
  }

  get kind(): ErrorKind | null {
    return this.body?.error ?? null;
  }
}

/** Parse a non-2xx response body into an ErrorResponse, or null if it is not one. */
export async function readErrorBody(response: Response): Promise<ErrorResponse | null> {
  try {
    const parsed: unknown = await response.json();
    if (parsed && typeof parsed === "object" && typeof (parsed as { error?: unknown }).error === "string") {
      return parsed as ErrorResponse;
    }
  } catch {
    // not JSON
  }
  return null;
}

const KIND_TEXT: Record<ErrorKind, string> = {
  validation: "Die Eingaben sind ungültig.",
  rule_violation: "Die Angaben verletzen eine Regel.",
  invalid: "Der Server hat die Anfrage abgelehnt.",
  unauthenticated: "Nicht angemeldet oder Sitzung abgelaufen.",
  forbidden_origin: "Anfrage abgelehnt: sie kam nicht von dieser Seite.",
  not_found: "Nicht gefunden.",
  conflict: "Konflikt mit dem gespeicherten Stand.",
  retention_hold: "Aufbewahrungsfrist läuft noch.",
  payload_too_large: "Die Datei ist zu groß.",
  unsupported_media_type: "Dieser Dateityp wird nicht angenommen.",
  shutting_down: "Der Server startet gerade neu. Bitte gleich noch einmal versuchen.",
  internal: "Interner Serverfehler.",
};

/** German text for the stable rule codes (phase-1.md, §9.5). */
export const RULE_CODE_TEXT: Record<string, string> = {
  "ownership.shares_must_sum_to_one": "Die Anteile müssen zusammen genau 1 ergeben.",
  "tenancy.exactly_one_primary": "Ein Mietverhältnis braucht genau einen Hauptmieter.",
  "tenancy.no_tenant": "Ein Mietverhältnis braucht mindestens einen Mieter.",
  "tenancy.overlap": "Für diese Einheit gibt es in diesem Zeitraum schon ein Mietverhältnis.",
  "rent_schedule.outside_tenancy": "Die Miete muss innerhalb der Laufzeit des Mietverhältnisses beginnen.",
  "iban.malformed": "Das ist keine gültige IBAN.",
  "iban.bad_length": "Die IBAN hat für dieses Land nicht die richtige Länge.",
  "iban.bad_checksum": "Die Prüfziffern der IBAN stimmen nicht.",
  "loan.payment_does_not_cover_interest": "Die Rate deckt die Zinsen nicht; das Darlehen würde nie getilgt.",
  "loan.term_too_long": "Mit dieser Rate dauert die Tilgung länger als 100 Jahre.",
  "event.correction_mismatch": "Die Korrektur passt nicht zum ursprünglichen Eintrag.",
  // phase 2 (phase-2.md §12)
  "proposal.already_decided": "Über diesen Vorschlag wurde schon entschieden (übernommen oder verworfen).",
  "proposal.not_ready": "Dieser Vorschlag ist noch nicht fertig oder wurde ersetzt.",
  "document.extraction_stale": "Das Dokument wurde inzwischen neu ausgelesen. Bitte die Seite neu laden und die Felder prüfen.",
  "document.extraction_kind_mismatch": "Für diese Dokumentart gibt es eines der Felder von Hand nicht; es kann nicht gespeichert werden.",
  // bank statements (phase-2.md §12 and the step 3 additions, rules/statements.py)
  "statement.unreconciled": "Der berechnete Endsaldo stimmt nicht mit dem Saldo der Bank überein.",
  "statement.opening_mismatch": "Der Anfangssaldo der Datei stimmt nicht mit dem berechneten Kontostand überein.",
  "statement.before_anchor":
    "Der Auszug enthält Umsätze am oder vor dem Datum des Anfangssaldos. Den Anfangssaldo auf einen früheren Tag legen.",
  "statement.unknown_format": "Dateiformat nicht erkannt. Erwartet: camt (XML oder ZIP) oder Sparkasse CSV-CAMT.",
  "statement.unparsable": "Die Datei ist beschädigt oder unvollständig und konnte nicht gelesen werden.",
  "statement.archive_rejected":
    "Das ZIP-Archiv wurde abgelehnt: nur XML-Dateien ohne Ordner, höchstens 400 Dateien und 20 MiB, keine ZIPs im ZIP.",
  "statement.entity_declared": "Die XML-Datei enthält eine DTD oder Entity-Deklaration und wird aus Sicherheitsgründen abgelehnt.",
  "statement.report_unbalanced": "In einem Tagesbericht ergeben Anfangssaldo und Umsätze nicht den Endsaldo.",
  "statement.chain_break": "Ein Tagesbericht beginnt nicht mit dem Endsaldo des vorherigen; vermutlich fehlt ein Tag.",
  "statement.account_mismatch": "Der Auszug gehört zu einem anderen Konto: die IBAN in der Datei passt nicht.",
  "statement.unsupported_currency": "Es werden nur Umsätze in Euro unterstützt.",
  "statement.empty": "Die Datei enthält keine gebuchten Umsätze.",
  "statement.owner_account": "Auf ein Privatkonto der Eigentümer werden keine Kontoauszüge importiert.",
  "statement.balance_from_file": "Dieser Auszug bringt seine Salden selbst mit; ein Saldo wird nicht eingegeben.",
  "statement.duplicate_accounts_differ": "Die beiden Umsätze gehören zu verschiedenen Konten.",
  "statement.duplicate_excludes_both": "Zusammenführen würde beide Umsätze aus dem Saldo nehmen; einer muss zählen.",
  // allocations and learned rules (phase-2.md §8, step 4)
  "allocation.sum_mismatch": "Die Zeilen ergeben zusammen nicht den Betrag der Buchung.",
  "allocation.empty": "Eine Zuordnung braucht mindestens eine Zeile.",
  "allocation.transaction_mismatch": "Die Zuordnung gehört zu einer anderen Buchung. Bitte die Seite neu laden.",
  "allocation.import_not_reconciled":
    "Der Kontoauszug dieser Buchung ist nicht abgestimmt. Erst wenn seine Salden stimmen, kann sie zugeordnet werden.",
  "allocation.line_not_open":
    "Diese Buchung zählt nicht (zusammengeführte Doppelbuchung) oder wartet noch auf die Entscheidung über eine mögliche Doppelbuchung.",
  "allocation.scope_mismatch":
    "Einheit, Mietverhältnis oder Darlehen gehören nicht zur gewählten Immobilie, oder die Immobilie fehlt.",
  "allocation.excluded_transaction": "Diese Buchung wurde als Doppelbuchung zusammengeführt und zählt nicht.",
  "rule.not_learnable":
    "Aus dieser Zuordnung lässt sich keine Regel lernen (zum Beispiel aus einer Aufteilung auf mehrere Kategorien). Ohne Regel bestätigen.",
  "rule.invalid_pattern":
    "Das Muster ist kein gültiger Suchausdruck (regulärer Ausdruck, höchstens 200 Zeichen).",
};

/** German text for a proposal's `error_code`: why reading the document failed or was
 * stopped (phase-2.md §5.5, §12; the job's extra codes in backend rules/errors.py). */
export const AI_ERROR_TEXT: Record<string, string> = {
  "ai.disabled": "Automatisches Auslesen ist auf dem Server nicht eingeschaltet.",
  "ai.blocked_tenant_data": "Dokumente mit Mieterdaten werden nicht an die KI geschickt, solange das nicht freigegeben ist.",
  "ai.unparsable": "Die Antwort der KI ließ sich auch beim zweiten Versuch nicht auswerten.",
  "ai.refused": "Die KI hat das Auslesen abgelehnt oder abgebrochen.",
  "ai.too_many_pages": "Das Dokument hat zu viele Seiten, um es automatisch auszulesen.",
  "ai.unsupported_media_type": "Dieser Dateityp kann nicht automatisch ausgelesen werden (z. B. HEIC); bitte als PDF, JPEG oder PNG hochladen.",
  "ai.api_error": "Der KI-Dienst hat die Anfrage mit einem Fehler beantwortet.",
  "ai.unavailable": "Der KI-Dienst war nicht erreichbar. Später noch einmal auslesen.",
  "ai.unreadable": "Die Datei ließ sich nicht öffnen (beschädigt oder mit Passwort geschützt).",
  "ai.internal_error": "Beim Auslesen ist ein interner Fehler aufgetreten.",
};

/** The German sentence for a proposal's error code; unknown codes get a generic one. */
export function aiErrorText(code: string | null): string {
  return (code && AI_ERROR_TEXT[code]) || "Das Dokument konnte nicht gelesen werden.";
}

/** The German sentence for one rule code, with the payment number of a loan schedule error. */
export function ruleCodeText(code: string, atNumber?: number | null): string | null {
  const text = RULE_CODE_TEXT[code];
  if (!text) return null;
  return atNumber != null && code.startsWith("loan.") ? `${text} (ab Rate Nr. ${atNumber})` : text;
}

/** Every rule code of an error body, without repeats (`code` is also `codes[0]`). */
export function ruleCodes(body: ErrorResponse): string[] {
  return [...new Set([...(body.codes ?? []), ...(body.code ? [body.code] : [])])];
}

export interface ErrorDescription {
  /** One German sentence. */
  text: string;
  /** The API's stable `error` string (or a client-side code), for reference. */
  code: string | null;
  /** The API's `message`, rule-code texts and field errors (`loc: msg`). */
  details: string[];
  /** Quote this when reporting a 500. */
  requestId: string | null;
}

function fieldErrorLine(error: FieldError): string {
  const loc = error.loc[0] === "body" ? error.loc.slice(1) : error.loc;
  return loc.length > 0 ? `${loc.join(".")}: ${error.msg}` : error.msg;
}

export function describeApiError(error: ApiError): ErrorDescription {
  const body = error.body;
  if (!body) {
    const text =
      error.status >= 500
        ? "Der Server ist nicht erreichbar oder hat einen Fehler gemeldet."
        : `Unerwartete Antwort des Servers (HTTP ${error.status}).`;
    return { text, code: null, details: [], requestId: error.requestId };
  }
  const details: string[] = [];
  const codes = ruleCodes(body);
  let allCodesKnown = codes.length > 0;
  for (const code of codes) {
    const known = ruleCodeText(code, body.at_number);
    if (!known) allCodesKnown = false;
    details.push(known ?? code);
  }
  // A known code says it in German; the server's message would only repeat it in English (and
  // a loan schedule message names the submitted payment). Unknown codes show the message.
  if (body.message && !allCodesKnown) details.push(body.message);
  for (const fieldError of body.detail ?? []) details.push(fieldErrorLine(fieldError));
  return { text: KIND_TEXT[body.error] ?? "Fehler.", code: body.error, details, requestId: error.requestId };
}
