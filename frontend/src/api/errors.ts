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

/** German text for stable rule codes (phase-1.md). Chunk 3b adds the domain codes. */
export const RULE_CODE_TEXT: Record<string, string> = {
  "ownership.shares_must_sum_to_one": "Die Anteile müssen zusammen genau 1 ergeben.",
};

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
  for (const code of [...(body.codes ?? []), ...(body.code ? [body.code] : [])]) {
    details.push(RULE_CODE_TEXT[code] ?? code);
  }
  if (body.message) details.push(body.message);
  for (const fieldError of body.detail ?? []) details.push(fieldErrorLine(fieldError));
  return { text: KIND_TEXT[body.error] ?? "Fehler.", code: body.error, details, requestId: error.requestId };
}
