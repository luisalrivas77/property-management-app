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
import { localized, useFormat, useT } from "@/i18n";
import { common } from "@/i18n/common";
import { settingsForms } from "@/i18n/settingsForms";
import { ErrorNotice } from "@/lib/ErrorNotice";

const loadSecrets = () => ok(api.GET("/api/app-secrets"));

const groupNames = (t: typeof settingsForms.de): Record<AppSecretGroup, string> => ({
  ai: t.groupAi,
  chains: t.groupChains,
  bank: t.groupBank,
  mail: t.groupMail,
  elster: t.groupElster,
});

const GROUPS = localized(groupNames(settingsForms.de), groupNames(settingsForms.en));

const sourceTexts = (t: typeof settingsForms.de): Record<AppSecretSource, string> => ({
  app: t.sourceApp,
  container: t.sourceContainer,
  none: t.sourceNone,
  unreadable: t.sourceUnreadable,
});

const SOURCE_TEXT = localized(sourceTexts(settingsForms.de), sourceTexts(settingsForms.en));

export function AppSecrets() {
  const loaded = useLoad(loadSecrets);
  return <Loading state={loaded}>{(fetched) => <SecretList initial={fetched.secrets} />}</Loading>;
}

function SecretList({ initial }: { initial: AppSecretState[] }) {
  const t = useT(settingsForms);
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
      <p className="muted">{t.secretsIntro}</p>
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
  const t = useT(settingsForms);
  const c = useT(common);
  const f = useFormat();
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
    const question = t.deleteSecret(state.label);
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
        <p>{t.unreadable}</p>
      ) : null}
      {state.source === "app" && state.updated_at ? (
        <p className="muted">{t.typedOn(f.dateTime(state.updated_at))}</p>
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
              {busy ? t.saving : c.save}
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
              {c.cancel}
            </button>
          </div>
        </form>
      ) : (
        <div className="row">
          <button type="button" className="secondary" onClick={() => setEditing(true)} disabled={busy}>
            {typed ? t.change : t.enter}
          </button>
          {typed ? (
            <button type="button" className="danger" onClick={() => void remove()} disabled={busy}>
              {t.delete}
            </button>
          ) : null}
        </div>
      )}
      {state.source === "none" ? (
        <p className="muted">{t.orVariable(state.variable)}</p>
      ) : null}
      <ErrorNotice error={error} />
    </div>
  );
}
