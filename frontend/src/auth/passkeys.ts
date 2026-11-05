// The three passkey ceremonies the screens drive (docs/phases/phase-1-step-3.md §6.3, §6.7):
// fetch options from the API, run the browser ceremony, post the credential back. The
// options and the credential are opaque JSON to the API contract (§5); only
// @simplewebauthn/browser looks inside them. The challenge lives in the API's signed ceremony
// cookie, which the browser sends back on the verify call. Browser-only: call from event
// handlers of client components.
import {
  startAuthentication,
  startRegistration,
  WebAuthnError,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";

import { api, ok } from "@/api/client";
import type { ErrorDescription } from "@/api/errors";
import type { Me, Passkey } from "@/api/schemas";

type Json = Record<string, unknown>;

/** A credential as the JSON object the API's `credential` field takes. */
function asJson(value: object): Json {
  return { ...value };
}

/** Sign in with any discoverable passkey of this site (no username, §6.1). */
export async function signInWithPasskey(): Promise<Me> {
  const options = await ok(api.POST("/api/auth/login/options", { body: {} }));
  const credential = await startAuthentication({
    optionsJSON: options as unknown as PublicKeyCredentialRequestOptionsJSON,
  });
  return ok(api.POST("/api/auth/login/verify", { body: { credential: asJson(credential) } }));
}

/** Redeem an enrollment token (first user, spouse, recovery) with a new passkey. */
export async function enrollWithInvite(token: string, label: string): Promise<Me> {
  const options = await ok(api.POST("/api/auth/enroll/options", { body: { token } }));
  const credential = await startRegistration({
    optionsJSON: options as unknown as PublicKeyCredentialCreationOptionsJSON,
  });
  return ok(api.POST("/api/auth/enroll/verify", { body: { token, credential: asJson(credential), label } }));
}

/** Add a further passkey for the signed-in user (e.g. the laptop after the phone). */
export async function addPasskey(label: string): Promise<Passkey> {
  const options = await ok(api.POST("/api/auth/passkeys/options", { body: {} }));
  const credential = await startRegistration({
    optionsJSON: options as unknown as PublicKeyCredentialCreationOptionsJSON,
  });
  return ok(api.POST("/api/auth/passkeys/verify", { body: { credential: asJson(credential), label } }));
}

/** German text for a failed browser ceremony, or null if `error` is not one. */
export function describeCeremonyError(error: unknown): ErrorDescription | null {
  if (!(error instanceof WebAuthnError)) return null;
  const causeName = error.cause instanceof Error ? error.cause.name : null;
  let text: string;
  switch (error.code) {
    case "ERROR_CEREMONY_ABORTED":
      text = "Der Vorgang wurde abgebrochen.";
      break;
    case "ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED":
      text = "Auf diesem Gerät gibt es schon einen Passkey für Hausbuch.";
      break;
    case "ERROR_INVALID_DOMAIN":
    case "ERROR_INVALID_RP_ID":
      text = "Passkeys funktionieren nur über HTTPS oder auf localhost unter der eingestellten Adresse.";
      break;
    case "ERROR_AUTHENTICATOR_MISSING_DISCOVERABLE_CREDENTIAL_SUPPORT":
    case "ERROR_AUTHENTICATOR_MISSING_USER_VERIFICATION_SUPPORT":
      text = "Dieses Gerät unterstützt die nötige Passkey-Art nicht (mit PIN oder Biometrie).";
      break;
    default:
      text =
        causeName === "NotAllowedError"
          ? "Der Vorgang wurde abgebrochen oder ist abgelaufen."
          : "Der Passkey-Vorgang im Browser ist fehlgeschlagen.";
  }
  return { text, code: error.code, details: [], requestId: null };
}

/** A default passkey label from the browser and OS, e.g. "Safari auf iPhone". */
export function defaultPasskeyLabel(): string {
  const ua = navigator.userAgent;
  const os = /iPhone/.test(ua)
    ? "iPhone"
    : /iPad/.test(ua)
      ? "iPad"
      : /Android/.test(ua)
        ? "Android"
        : /Mac OS X|Macintosh/.test(ua)
          ? "Mac"
          : /Windows/.test(ua)
            ? "Windows"
            : /Linux/.test(ua)
              ? "Linux"
              : null;
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Firefox\//.test(ua)
      ? "Firefox"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : null;
  if (browser && os) return `${browser} auf ${os}`;
  return browser ?? os ?? "Passkey";
}
