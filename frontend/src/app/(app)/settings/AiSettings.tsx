"use client";

// KI (docs/phases/phase-2.md D16, D26, D42): the workspace's two AI switches, one card each.
// GET /workspace/settings reads them; a switch sends PATCH with only its own setting, and the
// API records the change as a workspace.settings_changed event. Both start off.
// Phase 6 D168 adds a third card, „Private Konten mit KI zuordnen“: switching it on counts as the
// switching member's consent, the other member consents by their own click (POST
// /workspace/settings/ai-consent), and the switch takes effect only once both have consented.
import { useState } from "react";

import { api, ok } from "@/api/client";
import type { Member, WorkspaceSettings } from "@/api/schemas";
import { useAuth } from "@/auth/AuthGate";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useT } from "@/i18n";
import { settingsForms } from "@/i18n/settingsForms";
import { ErrorNotice } from "@/lib/ErrorNotice";

type Setting = "ai_tenant_documents" | "auto_accept_llm" | "ai_private_accounts"; // reminder_emails: ReminderSettings

const loadSettings = () =>
  Promise.all([ok(api.GET("/api/workspace/settings")), ok(api.GET("/api/auth/members"))]).then(([settings, members]) => ({
    settings,
    members,
  }));

export function AiSettings() {
  const t = useT(settingsForms);
  const switches: { setting: Setting; label: string; text: string }[] = [
    { setting: "ai_tenant_documents", label: t.tenantDocuments, text: t.tenantDocumentsText },
    { setting: "auto_accept_llm", label: t.autoAccept, text: t.autoAcceptText },
  ];
  const { me } = useAuth();
  const loaded = useLoad(loadSettings);
  const [saved, setSaved] = useState<WorkspaceSettings | null>(null);
  const [busy, setBusy] = useState<Setting | "consent" | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function consent() {
    setBusy("consent");
    setError(null);
    try {
      setSaved(await ok(api.POST("/api/workspace/settings/ai-consent")));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  async function change(setting: Setting, value: boolean) {
    setBusy(setting);
    setError(null);
    try {
      setSaved(await ok(api.PATCH("/api/workspace/settings", { body: { [setting]: value } })));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Loading state={loaded}>
      {(fetched) => {
        const settings = saved ?? fetched.settings;
        return (
          <div className="stack">
            {switches.map(({ setting, label, text }) => (
              <div key={setting} className="card stack">
                <label className="inline">
                  <input
                    type="checkbox"
                    role="switch"
                    aria-checked={settings[setting]}
                    checked={settings[setting]}
                    disabled={busy !== null}
                    onChange={(event) => void change(setting, event.target.checked)}
                  />{" "}
                  <strong>{label}</strong>
                </label>
                <p className="muted">{text}</p>
                <p className="muted">{settings[setting] ? t.on : t.off}</p>
              </div>
            ))}
            <PrivateAccountsCard
              settings={settings}
              members={fetched.members}
              meId={me.user.id}
              busy={busy !== null}
              onSwitch={(value) => void change("ai_private_accounts", value)}
              onConsent={() => void consent()}
            />
            <ErrorNotice error={error} />
          </div>
        );
      }}
    </Loading>
  );
}

/** „Private Konten mit KI zuordnen“ (phase 6 D168): the switch, who has consented, and the
 * signed-in member's own „Ich stimme zu“ while the switch is on and their consent is missing. */
function PrivateAccountsCard({
  settings,
  members,
  meId,
  busy,
  onSwitch,
  onConsent,
}: {
  settings: WorkspaceSettings;
  members: Member[];
  meId: string;
  busy: boolean;
  onSwitch: (value: boolean) => void;
  onConsent: () => void;
}) {
  const t = useT(settingsForms);
  const on = settings.ai_private_accounts;
  const consented = new Set(settings.ai_private_accounts_consented_by ?? []);
  const status = !on ? t.off : settings.ai_private_accounts_active ? t.privateActive : t.privateNoConsent;
  return (
    <div className="card stack">
      <label className="inline">
        <input
          type="checkbox"
          role="switch"
          aria-checked={on}
          checked={on}
          disabled={busy}
          onChange={(event) => onSwitch(event.target.checked)}
        />{" "}
        <strong>{t.privateAccounts}</strong>
      </label>
      <p className="muted">{t.privateAccountsText}</p>
      {on ? (
        <ul>
          {members.map((member) => (
            <li key={member.user.id}>
              {member.user.display_name}
              {member.user.id === meId ? t.youFormal : ""}:{" "}
              {consented.has(member.user.id) ? t.consented : t.consentMissing}
            </li>
          ))}
        </ul>
      ) : null}
      {on && !consented.has(meId) ? (
        <div>
          <button type="button" disabled={busy} onClick={onConsent}>
            {t.iConsent}
          </button>
        </div>
      ) : null}
      <p className="muted">{status}</p>
    </div>
  );
}
