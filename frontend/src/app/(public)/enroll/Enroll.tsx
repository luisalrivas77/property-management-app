"use client";

// Invite redemption and first-passkey registration (§6.5, §6.7). The enrollment link carries
// the token in the fragment (/enroll#token=…), so it never reaches a server log or the Next
// server; this page reads it, removes it from the address bar and history entry, and posts
// it with POST /auth/enroll/options and /enroll/verify. The token is a bearer secret until
// it is used: it is never rendered.
import { useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore, type FormEvent } from "react";

import { defaultPasskeyLabel, enrollWithInvite } from "@/auth/passkeys";
import { ErrorNotice } from "@/lib/ErrorNotice";

// EnrollmentToken in schemas/auth.py: 32 random bytes, base64url without padding.
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const MAX_LABEL_LENGTH = 100; // auth_passkey.label CHECK

// The token of the last enrollment link opened in this tab. Kept here because the effect in
// Enroll removes the fragment from the URL once it has been read.
let fragmentToken: string | null | undefined;

function readFragmentToken(): string | null {
  const inUrl = new URLSearchParams(window.location.hash.slice(1)).get("token");
  if (inUrl !== null) fragmentToken = inUrl; // a newly opened link always wins
  else if (fragmentToken === undefined) fragmentToken = null;
  return fragmentToken;
}

function forgetFragmentToken(): void {
  fragmentToken = undefined;
}

/** Read the token into fragmentToken, then drop the fragment from the address bar and the
 * history entry. The router's own history state is kept; replaceState fires no hashchange. */
function takeFragment(): void {
  readFragmentToken();
  if (window.location.hash) {
    window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
  }
}

// Opening a second link in the same tab while on /enroll only changes the fragment: the
// page is not reloaded, so listen for it.
function subscribeToFragment(onChange: () => void): () => void {
  const handler = () => {
    takeFragment();
    onChange();
  };
  window.addEventListener("hashchange", handler);
  return () => window.removeEventListener("hashchange", handler);
}

type TokenState = { status: "reading" } | { status: "missing" } | { status: "malformed" } | { status: "ok"; token: string };

function tokenState(token: string | null | undefined): TokenState {
  if (token === undefined) return { status: "reading" }; // server render and hydration
  if (token === null) return { status: "missing" };
  return TOKEN_PATTERN.test(token) ? { status: "ok", token } : { status: "malformed" };
}

export function Enroll() {
  const token = useSyncExternalStore(subscribeToFragment, readFragmentToken, () => undefined);
  const state = tokenState(token);

  useEffect(takeFragment, []);

  return (
    <section className="card stack">
      <h1>Passkey einrichten</h1>
      {state.status === "reading" ? <p className="muted">Lädt …</p> : null}
      {state.status === "missing" ? (
        <p>
          Diese Seite braucht einen Einladungslink. Bitte den vollständigen Link aus der Einladung öffnen
          (er enthält <code>#token=</code>).
        </p>
      ) : null}
      {state.status === "malformed" ? (
        <p>Der Einladungslink ist unvollständig. Bitte den ganzen Link kopieren und erneut öffnen.</p>
      ) : null}
      {state.status === "ok" ? <EnrollForm token={state.token} /> : null}
    </section>
  );
}

function EnrollForm({ token }: { token: string }) {
  const router = useRouter();
  // Mounted only on the client (after the fragment was read), so navigator is available.
  const [label, setLabel] = useState(() => defaultPasskeyLabel().slice(0, MAX_LABEL_LENGTH));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await enrollWithInvite(token, label.trim());
      forgetFragmentToken(); // used up: never offer it again in this tab
      router.replace("/");
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <p className="muted">
        Der Passkey ersetzt das Passwort. Er bleibt auf diesem Gerät (oder in iCloud / Google) und wird mit
        PIN, Fingerabdruck oder Gesicht freigegeben.
      </p>
      <div>
        <label htmlFor="passkey-label">Name des Passkeys</label>
        <input
          id="passkey-label"
          type="text"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          required
          maxLength={MAX_LABEL_LENGTH}
          autoComplete="off"
        />
        <p className="muted">Zum Wiedererkennen in den Einstellungen, z. B. „iPhone von Luis“.</p>
      </div>
      <button type="submit" disabled={busy || label.trim() === ""}>
        {busy ? "Warte auf Passkey …" : "Passkey einrichten"}
      </button>
      <ErrorNotice error={error} />
    </form>
  );
}
