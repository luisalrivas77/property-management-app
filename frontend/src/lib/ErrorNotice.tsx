"use client";

import { ApiError, describeApiError, type ErrorDescription } from "@/api/errors";
import { describeCeremonyError } from "@/auth/passkeys";

/** German text for anything a call can throw: an ApiError, a failed passkey ceremony, or
 * a network failure. Never includes what the user typed. */
export function describeError(error: unknown): ErrorDescription {
  if (error instanceof ApiError) return describeApiError(error);
  const ceremony = describeCeremonyError(error);
  if (ceremony) return ceremony;
  if (error instanceof TypeError) {
    return { text: "Keine Verbindung zum Server.", code: null, details: [], requestId: null };
  }
  return { text: "Unerwarteter Fehler.", code: null, details: [], requestId: null };
}

/** The error line under a form or button. Renders nothing for `null`. */
export function ErrorNotice({ error }: { error: unknown }) {
  if (error == null) return null;
  const { text, code, details, requestId } = describeError(error);
  return (
    <div className="notice notice-error" role="alert">
      <p>
        {text}
        {code ? <code className="error-code">{code}</code> : null}
      </p>
      {details.length > 0 ? (
        <ul>
          {details.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      ) : null}
      {requestId ? <p className="muted">Anfrage-ID: {requestId}</p> : null}
    </div>
  );
}
