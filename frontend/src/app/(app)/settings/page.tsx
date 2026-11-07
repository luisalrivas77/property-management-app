"use client";

// Einstellungen (§9.1, §6.7): the user's passkeys (list, add, rename, remove), the household's
// members (invite → one-time enrollment link, pending invites), the two AI switches
// (docs/phases/phase-2.md D42) and signing out.
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Passkey } from "@/api/schemas";
import { useAuth } from "@/auth/AuthGate";
import { addPasskey, defaultPasskeyLabel } from "@/auth/passkeys";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { AiSettings } from "./AiSettings";
import { Members } from "./Members";
import { RenamePasskey } from "./RenamePasskey";

const MAX_LABEL_LENGTH = 100; // auth_passkey.label CHECK
const dateTime = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short" });

function formatDateTime(value: string | null): string {
  return value ? dateTime.format(new Date(value)) : "–";
}

export default function SettingsPage() {
  const { me, signOut } = useAuth();
  const [passkeys, setPasskeys] = useState<Passkey[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [rowError, setRowError] = useState<unknown>(null);
  const [signOutError, setSignOutError] = useState<unknown>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);

  const reload = useCallback(() => {
    return ok(api.GET("/api/auth/passkeys")).then(
      (list) => {
        setPasskeys(list);
        setListError(null);
      },
      (error: unknown) => setListError(error),
    );
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function remove(passkey: Passkey) {
    const current = passkey.id === me.session.passkey_id;
    const question = current
      ? `Passkey „${passkey.label}“ entfernen? Damit endet auch diese Sitzung.`
      : `Passkey „${passkey.label}“ entfernen? Sitzungen, die damit angemeldet wurden, enden.`;
    if (!window.confirm(question)) return;
    setRemoving(passkey.id);
    setRowError(null);
    try {
      await ok(api.DELETE("/api/auth/passkeys/{passkey_id}", { params: { path: { passkey_id: passkey.id } } }));
      await reload(); // a 401 here (it was this session's passkey) goes to /login
    } catch (error) {
      setRowError(error);
    } finally {
      setRemoving(null);
    }
  }

  return (
    <section>
      <h1>Einstellungen</h1>

      <h2>Passkeys</h2>
      <ErrorNotice error={listError} />
      {passkeys === null && listError == null ? <p className="muted">Lädt …</p> : null}
      {passkeys !== null ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Angelegt</th>
                <th>Zuletzt benutzt</th>
                <th>Synchronisiert</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {passkeys.map((passkey) => (
                <tr key={passkey.id}>
                  <td>
                    {renaming === passkey.id ? (
                      <RenamePasskey
                        passkey={passkey}
                        onDone={async (changed) => {
                          setRenaming(null);
                          if (changed) await reload();
                        }}
                      />
                    ) : (
                      <>
                        {passkey.label}
                        {passkey.id === me.session.passkey_id ? <span className="muted"> (diese Sitzung)</span> : null}
                      </>
                    )}
                  </td>
                  <td>{formatDateTime(passkey.created_at)}</td>
                  <td>{formatDateTime(passkey.last_used_at)}</td>
                  <td>{passkey.backed_up ? "ja" : "nein"}</td>
                  <td className="row">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setRenaming(passkey.id)}
                      disabled={renaming !== null || removing !== null}
                    >
                      Umbenennen
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => remove(passkey)}
                      disabled={removing !== null || passkeys.length === 1}
                      title={passkeys.length === 1 ? "Der letzte Passkey kann nicht entfernt werden." : undefined}
                    >
                      {removing === passkey.id ? "Entfernt …" : "Entfernen"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {passkeys?.length === 1 ? (
        <p className="muted">
          Der letzte Passkey kann nicht entfernt werden. Am besten einen zweiten auf einem anderen Gerät anlegen.
        </p>
      ) : null}
      <ErrorNotice error={rowError} />

      <AddPasskey onAdded={reload} />

      <h2>Mitglieder</h2>
      <Members />

      <h2>KI</h2>
      <AiSettings />

      <h2>Abmelden</h2>
      <div className="row">
        <button type="button" className="secondary" onClick={() => signOut().catch(setSignOutError)}>
          Abmelden
        </button>
        <button type="button" className="secondary" onClick={() => signOut(true).catch(setSignOutError)}>
          Überall abmelden
        </button>
      </div>
      <p className="muted">„Überall abmelden“ beendet alle Sitzungen auf allen Geräten.</p>
      <ErrorNotice error={signOutError} />
    </section>
  );
}

function AddPasskey({ onAdded }: { onAdded: () => Promise<void> }) {
  // Rendered only after the AuthGate resolved the session, i.e. on the client.
  const [label, setLabel] = useState(() => defaultPasskeyLabel().slice(0, MAX_LABEL_LENGTH));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await addPasskey(label.trim());
      await onAdded();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} style={{ marginTop: "1rem" }}>
      <div>
        <label htmlFor="new-passkey-label">Passkey hinzufügen</label>
        <input
          id="new-passkey-label"
          type="text"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          required
          maxLength={MAX_LABEL_LENGTH}
          autoComplete="off"
        />
        <p className="muted">Zum Beispiel für den Laptop, nachdem der erste Passkey auf dem Telefon liegt.</p>
      </div>
      <button type="submit" disabled={busy || label.trim() === ""}>
        {busy ? "Warte auf Passkey …" : "Passkey hinzufügen"}
      </button>
      <ErrorNotice error={error} />
    </form>
  );
}
