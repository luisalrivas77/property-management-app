"use client";

// Passkey sign-in (§6.3): POST /auth/login/options → browser ceremony → POST /auth/login/verify,
// which sets the session cookie. Discoverable credentials, so there is no username field.
import { browserSupportsWebAuthn } from "@simplewebauthn/browser";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore } from "react";

import { ApiError } from "@/api/errors";
import { signInWithPasskey } from "@/auth/passkeys";
import { translate, useT } from "@/i18n";
import { auth } from "@/i18n/auth";
import { ErrorNotice } from "@/lib/ErrorNotice";

const subscribeNever = () => () => {};

// A 401 from login/verify means the passkey was not accepted, not that a session ran out.
function loginErrorText(error: unknown): string | null {
  return error instanceof ApiError && error.status === 401
    ? translate(auth).loginNotRecognised
    : null;
}

export function LoginForm() {
  const t = useT(auth);
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // Server render: assume support, so the button does not flash a warning.
  const supported = useSyncExternalStore(subscribeNever, browserSupportsWebAuthn, () => true);

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      await signInWithPasskey();
      router.replace("/");
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <section className="card stack">
      <h1>{t.loginTitle}</h1>
      {supported ? (
        <>
          <p className="muted">{t.loginHint}</p>
          <button type="button" onClick={signIn} disabled={busy}>
            {busy ? t.waitingForPasskey : t.signInWithPasskey}
          </button>
        </>
      ) : (
        <p>{t.browserUnsupported}</p>
      )}
      <ErrorNotice error={error} text={loginErrorText} />
      <p className="muted">
        {t.noPasskeyYetBefore}
        <code>/enroll#token=</code>
        {t.noPasskeyYetAfter}
      </p>
    </section>
  );
}
