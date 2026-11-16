"use client";

// Zugangsschlüssel (API keys) (docs/phases/phase-6.md D260): the provider keys, addresses and
// mail settings a member may type in the app instead of the server configuration (Scaleway).
// GET /app-secrets lists each name with where its value comes from now; PUT stores a value
// encrypted, DELETE falls back to the server's variable. A secret is typed into a password
// field, sent once and never shown again: the API never returns it. A name that is not secret
// (the sender address, the inbox's user, address and label, the TEM project) is shown back.
import { useEffect, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { AppSecretGroup, AppSecretSource, AppSecretState } from "@/api/schemas";
import { APP_SECRETS_ANCHOR } from "@/components/labels";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

const loadSecrets = () => ok(api.GET("/api/app-secrets"));

const GROUPS: Record<AppSecretGroup, string> = {
  ai: "KI",
  chains: "Blockchains & Kurse",
  bank: "Bank",
  mail: "E-Mail",
  elster: "ELSTER",
};

const SOURCE_TEXT: Record<AppSecretSource, string> = {
  app: "In der App gespeichert",
  container: "Aus der Server-Konfiguration",
  none: "Nicht gesetzt",
  unreadable: "Kann nicht gelesen werden",
};

const dateTime = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short" });

export function AppSecrets() {
  const loaded = useLoad(loadSecrets);
  return <Loading state={loaded}>{(fetched) => <SecretList initial={fetched.secrets} />}</Loading>;
}

function SecretList({ initial }: { initial: AppSecretState[] }) {
  const [rows, setRows] = useState(initial);
  const [error, setError] = useState<unknown>(null);

  // Zu prüfen's „Zugangsschlüssel öffnen“ (D260 amended (9)) links to the heading; the cards
  // above it load at their own pace, so the jump is made again once this list is shown.
  useEffect(() => {
    if (window.location.hash === `#${APP_SECRETS_ANCHOR}`) {
      document.getElementById(APP_SECRETS_ANCHOR)?.scrollIntoView({ block: "start" });
    }
  }, []);
  const groups = (Object.keys(GROUPS) as AppSecretGroup[]).filter((group) => rows.some((row) => row.group === group));

  function replace(state: AppSecretState) {
    setRows((current) => current.map((row) => (row.name === state.name ? state : row)));
  }

  async function reload() {
    try {
      setRows((await ok(api.GET("/api/app-secrets"))).secrets);
      setError(null);
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div className="card stack">
      <p className="muted">
        Schlüssel und Zugangsdaten für die Dienste, die Hausbuch nutzt. Ein hier eingetragener Wert gilt vor dem Wert aus der
        Server-Konfiguration und wird verschlüsselt gespeichert. Ein Schlüssel wird nach dem Speichern nie wieder angezeigt;
        „Löschen“ nimmt wieder den Wert aus der Server-Konfiguration, falls dort einer steht.
      </p>
      {groups.map((group) => (
        <div key={group} className="stack">
          <h3>{GROUPS[group]}</h3>
          {rows
            .filter((row) => row.group === group)
            .map((row) => (
              <SecretRow key={row.name} state={row} onChanged={replace} onDeleted={() => void reload()} />
            ))}
        </div>
      ))}
      <ErrorNotice error={error} />
    </div>
  );
}

function SecretRow({
  state,
  onChanged,
  onDeleted,
}: {
  state: AppSecretState;
  onChanged: (state: AppSecretState) => void;
  onDeleted: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const inputId = `app-secret-${state.name}`;
  const typed = state.source === "app" || state.source === "unreadable";

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved = await ok(api.PUT("/api/app-secrets/{name}", { params: { path: { name: state.name } }, body: { value } }));
      setValue("");
      setEditing(false);
      onChanged(saved);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    const question = `„${state.label}“ aus der App löschen? Danach gilt der Wert aus der Server-Konfiguration, falls dort einer steht.`;
    if (!window.confirm(question)) return;
    setBusy(true);
    setError(null);
    try {
      await ok(api.DELETE("/api/app-secrets/{name}", { params: { path: { name: state.name } } }));
      onDeleted();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" data-testid={`app-secret-${state.name}`}>
      <div className="row">
        <strong>{state.label}</strong>
        <span className={state.source === "unreadable" ? "badge badge-danger" : "badge badge-muted"}>{SOURCE_TEXT[state.source]}</span>
      </div>
      <p className="muted">{state.purpose}</p>
      {!state.secret && state.value ? <p>{state.value}</p> : null}
      {state.source === "unreadable" ? (
        <p>Schlüssel kann nicht gelesen werden, bitte neu eintragen.</p>
      ) : null}
      {state.source === "app" && state.updated_at ? (
        <p className="muted">Eingetragen am {dateTime.format(new Date(state.updated_at))}</p>
      ) : null}
      {editing ? (
        <form className="stack" onSubmit={save}>
          <label htmlFor={inputId}>{state.label}</label>
          <input
            id={inputId}
            type={state.secret ? "password" : "text"}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoComplete={state.secret ? "new-password" : "off"}
            spellCheck={false}
            required
          />
          <div className="row">
            <button type="submit" disabled={busy || value.trim() === ""}>
              {busy ? "Speichert …" : "Speichern"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                setEditing(false);
                setValue("");
              }}
              disabled={busy}
            >
              Abbrechen
            </button>
          </div>
        </form>
      ) : (
        <div className="row">
          <button type="button" className="secondary" onClick={() => setEditing(true)} disabled={busy}>
            {typed ? "Ändern" : "Eintragen"}
          </button>
          {typed ? (
            <button type="button" className="danger" onClick={() => void remove()} disabled={busy}>
              Löschen
            </button>
          ) : null}
        </div>
      )}
      {state.source === "none" ? (
        <p className="muted">Alternativ als Variable {state.variable} in der Server-Konfiguration.</p>
      ) : null}
      <ErrorNotice error={error} />
    </div>
  );
}
