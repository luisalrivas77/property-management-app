"use client";

// Rename a passkey in place (PATCH /auth/passkeys/{id}, §6.3).
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Passkey } from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

const MAX_LABEL_LENGTH = 100; // auth_passkey.label CHECK

export function RenamePasskey({ passkey, onDone }: { passkey: Passkey; onDone: (changed: boolean) => void | Promise<void> }) {
  const [label, setLabel] = useState(passkey.label);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const inputId = `rename-${passkey.id}`;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = label.trim();
    if (next === passkey.label) return void onDone(false);
    setBusy(true);
    setError(null);
    try {
      await ok(api.PATCH("/api/auth/passkeys/{passkey_id}", { params: { path: { passkey_id: passkey.id } }, body: { label: next } }));
      await onDone(true);
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <label htmlFor={inputId} className="visually-hidden">
        Neuer Name
      </label>
      <input
        id={inputId}
        type="text"
        value={label}
        onChange={(event) => setLabel(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") void onDone(false);
        }}
        required
        maxLength={MAX_LABEL_LENGTH}
        autoComplete="off"
        autoFocus
      />
      <div className="row">
        <button type="submit" disabled={busy || label.trim() === ""}>
          {busy ? "Speichert …" : "Speichern"}
        </button>
        <button type="button" className="secondary" onClick={() => void onDone(false)}>
          Abbrechen
        </button>
      </div>
      <ErrorNotice error={error} />
    </form>
  );
}
