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

/** The error line under a form or button. Renders nothing for `null`. `text`, when given,
 * replaces the sentence for a context where the generic one would mislead. */
export function ErrorNotice({ error, text: override }: { error: unknown; text?: (error: unknown) => string | null }) {
  if (error == null) return null;
  const described = describeError(error);
  const { code, details, requestId } = described;
  const text = override?.(error) ?? described.text;
  return (
    <div className="notice notice-error" role="alert">
      <p>
        {text}
        {/* The stable code for reference, after a space and in brackets: never glued to the sentence. */}
        {code ? (
          <>
            {" "}
            <span className="muted">
              (<code>{code}</code>)
            </span>
          </>
        ) : null}
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
