"use client";

// Einstellungen (§9.1, §6.7; phase 10 mockup 24): the groups as rows, each opening its forms in
// the detail panel (D267): Darstellung and Sprache (D283, D284), the user's passkeys and the
// household's members (invite → one-time enrollment link), the AI switches (docs/phases/phase-2.md
// D42) and the Hinweis-Modus of the household plan (docs/phases/phase-9.md D251), the reminder
// email switch (docs/phases/phase-5.md D123), the email-in address (docs/phases/phase-5.md §5.2),
// the provider keys typed in the app (docs/phases/phase-6.md D260), and signing out below the
// rows. The old anchors `#ki` and `#zugangsschluessel` (links from Zu prüfen and Planung) open
// their group's panel and keep their URL; closing that panel drops the anchor.
import { useCallback, useEffect, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import type { Passkey } from "@/api/schemas";
import { useAuth } from "@/auth/AuthGate";
import { addPasskey, defaultPasskeyLabel } from "@/auth/passkeys";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { AI_SETTINGS_ANCHOR, APP_SECRETS_ANCHOR } from "@/components/labels";
import { useLocale, useT } from "@/i18n";
import { settings as messages } from "@/i18n/settings";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { Icon, type IconName } from "@/lib/icons";
import { useTheme } from "@/lib/theme";

import { AdviceModeSettings } from "./AdviceModeSettings";
import { AppearanceSettings } from "./AppearanceSettings";
import { AiSettings } from "./AiSettings";
import { AppSecrets } from "./AppSecrets";
import { InboxAddress } from "./InboxAddress";
import { Members } from "./Members";
import { ReminderSettings } from "./ReminderSettings";
import { RenamePasskey } from "./RenamePasskey";
import "./settings.css";

const MAX_LABEL_LENGTH = 100; // auth_passkey.label CHECK
const dateTime = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short" });

function formatDateTime(value: string | null): string {
  return value ? dateTime.format(new Date(value)) : "–";
}

type Group = "theme" | "language" | "passkeys" | "ai" | "advice" | "reminders" | "inbox" | "secrets";
const GROUPS: readonly Group[] = ["theme", "language", "passkeys", "ai", "advice", "reminders", "inbox", "secrets"];

/** The old in-page anchors, which other screens still link to. */
const LEGACY: Record<string, Group> = { [AI_SETTINGS_ANCHOR]: "ai", [APP_SECRETS_ANCHOR]: "secrets" };

function subscribeHash(listener: () => void): () => void {
  window.addEventListener("hashchange", listener);
  window.addEventListener("popstate", listener);
  return () => {
    window.removeEventListener("hashchange", listener);
    window.removeEventListener("popstate", listener);
  };
}

function legacyGroup(): Group | null {
  return LEGACY[window.location.hash.replace(/^#/, "")] ?? null;
}

function useLegacyGroup(): Group | null {
  return useSyncExternalStore(subscribeHash, legacyGroup, () => null);
}

function dropLegacyAnchor(): void {
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

export default function SettingsPage() {
  const t = useT(messages);
  const { signOut } = useAuth();
  const theme = useTheme();
  const locale = useLocale();
  const detail = useDetail("setting");
  const legacy = useLegacyGroup();
  const [signOutError, setSignOutError] = useState<unknown>(null);
  const selected = (GROUPS as readonly string[]).includes(detail.id ?? "") ? (detail.id as Group) : legacy;
  const close = legacy && !detail.id ? dropLegacyAnchor : detail.close;

  const themeLabel = theme === "light" ? t.themeLight : theme === "dark" ? t.themeDark : t.themeSystem;
  const groups: { title: string; rows: { id: Group; icon: IconName; title: string; hint: string }[] }[] = [
    {
      title: t.appearanceGroup,
      rows: [
        { id: "theme", icon: "settings", title: t.appearance, hint: themeLabel },
        { id: "language", icon: "overview", title: t.language, hint: locale === "de" ? t.languageGerman : t.languageEnglish },
      ],
    },
    { title: t.signInGroup, rows: [{ id: "passkeys", icon: "household", title: t.passkeys, hint: t.passkeysHint }] },
    {
      title: t.aiGroup,
      rows: [
        { id: "ai", icon: "settings", title: t.aiSwitches, hint: t.aiSwitchesHint },
        { id: "advice", icon: "planning", title: t.adviceMode, hint: t.adviceModeHint },
      ],
    },
    {
      title: t.mailGroup,
      rows: [
        { id: "reminders", icon: "inbox", title: t.reminders, hint: t.remindersHint },
        { id: "inbox", icon: "upload", title: t.inbox, hint: t.inboxHint },
      ],
    },
    { title: t.secretsGroup, rows: [{ id: "secrets", icon: "accounts", title: t.secrets, hint: t.secretsHint }] },
  ];
  const titles: Record<Group, string> = {
    theme: t.appearanceGroup,
    language: t.appearanceGroup,
    passkeys: t.passkeys,
    ai: t.aiSwitches,
    advice: t.adviceMode,
    reminders: t.reminders,
    inbox: t.inbox,
    secrets: t.secretsTitle,
  };

  let body: ReactNode = null;
  switch (selected) {
    case "theme":
    case "language":
      body = <AppearanceSettings />;
      break;
    case "passkeys":
      body = <PasskeysAndMembers />;
      break;
    case "ai":
      body = (
        <div id={AI_SETTINGS_ANCHOR}>
          <AiSettings />
        </div>
      );
      break;
    case "advice":
      body = <AdviceModeSettings />;
      break;
    case "reminders":
      body = <ReminderSettings />;
      break;
    case "inbox":
      body = <InboxAddress />;
      break;
    case "secrets":
      body = <AppSecrets />;
      break;
  }
  const panel = selected ? (
    <DetailPanel title={titles[selected]} backLabel={t.title} onClose={close}>
      {body}
    </DetailPanel>
  ) : null;

  return (
    <section>
      <h1>{t.title}</h1>
      <DetailLayout panel={panel}>
        <div className="stack settings-groups">
          {groups.map((group) => (
            <section key={group.title} aria-label={group.title}>
              <h2 className="settings-group-title">{group.title}</h2>
              <ul className="detail-rows card">
                {group.rows.map((row) => (
                  <li key={row.id}>
                    <a
                      {...detail.linkProps(row.id)}
                      aria-current={selected === row.id ? "true" : undefined}
                      className="detail-row"
                    >
                      <Icon name={row.icon} />
                      <span className="detail-row-text">
                        <span className="detail-row-title">{row.title}</span>
                        <span className="detail-row-sub">{row.hint}</span>
                      </span>
                      <Icon name="chevronRight" size={18} />
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          <div className="row">
            <button type="button" className="secondary" onClick={() => signOut().catch(setSignOutError)}>
              {t.signOut}
            </button>
            <button type="button" className="secondary" onClick={() => signOut(true).catch(setSignOutError)}>
              {t.signOutEverywhere}
            </button>
          </div>
          <p className="muted">{t.signOutNote}</p>
          <ErrorNotice error={signOutError} />
        </div>
      </DetailLayout>
    </section>
  );
}

function PasskeysAndMembers() {
  const { me } = useAuth();
  const [passkeys, setPasskeys] = useState<Passkey[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [rowError, setRowError] = useState<unknown>(null);
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
    <div className="stack">
      <h3>Passkeys</h3>
      <ErrorNotice error={listError} />
      {passkeys === null && listError == null ? <p className="muted">Lädt …</p> : null}
      {passkeys !== null ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Zuletzt benutzt</th>
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
                        <span className="muted settings-passkey-meta">
                          Angelegt {formatDateTime(passkey.created_at)} ·{" "}
                          {passkey.backed_up ? "synchronisiert" : "nicht synchronisiert"}
                        </span>
                      </>
                    )}
                  </td>
                  <td>{formatDateTime(passkey.last_used_at)}</td>
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

      <h3>Mitglieder</h3>
      <Members />
    </div>
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
    <form className="stack" onSubmit={submit}>
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
