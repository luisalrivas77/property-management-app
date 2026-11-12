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
import { ErrorNotice } from "@/lib/ErrorNotice";

type Setting = "ai_tenant_documents" | "auto_accept_llm" | "ai_private_accounts"; // reminder_emails: ReminderSettings

const loadSettings = () =>
  Promise.all([ok(api.GET("/api/workspace/settings")), ok(api.GET("/api/auth/members"))]).then(([settings, members]) => ({
    settings,
    members,
  }));

const PRIVATE_TEXT =
  "Ist dies eingeschaltet, schlägt die KI für Umsätze Ihrer privaten Konten eine Haushaltskategorie vor, wenn keine Regel und " +
  "kein Abgleich sie erklärt. Gesendet werden nur Name der Gegenseite, Verwendungszweck, Betrag, Datum, die letzten vier Stellen " +
  "der IBAN und die Kontoart, nie der Name des Kontos oder seiner Eigentümer; verarbeitet bei Anthropic (Claude API) in den USA wie " +
  "die übrigen Dokumente. Ein gemeinsames Konto zeigt auch Gehalt, Einkäufe und Zahlungen an Kirche, Gewerkschaft, Apotheke oder " +
  "Arzt der anderen Person; deshalb gilt es erst, wenn beide Mitglieder des Haushalts zugestimmt haben. Wer es einschaltet, stimmt " +
  "damit zu; das andere Mitglied stimmt hier mit eigenem Klick zu. Ausschalten setzt beide Zustimmungen zurück. Solange es nicht " +
  "gilt, kommen solche Umsätze mit allen Haushaltskategorien zur Auswahl unter „Zu prüfen“.";

const SWITCHES: { setting: Setting; label: string; text: string }[] = [
  {
    setting: "ai_tenant_documents",
    label: "Mieterdokumente von der KI lesen lassen",
    text:
      "Mietverträge, Übergabeprotokolle, Kautionsnachweise und Briefe von Mietern enthalten personenbezogene Daten Ihrer Mieter. " +
      "Ist dies eingeschaltet, werden diese Dokumente zum Auslesen an Anthropic (Claude API) gesendet und in den USA verarbeitet, " +
      "auf Grundlage des Auftragsverarbeitungsvertrags mit EU-Standardvertragsklauseln. Anthropic verwendet sie nicht zum Training " +
      "und löscht sie innerhalb von 30 Tagen. Schalten Sie es nur ein, wenn Ihre Datenschutzhinweise an die Mieter Anthropic als " +
      "Auftragsverarbeiter nennen. Sie können es jederzeit wieder ausschalten; bereits gelesene Dokumente bleiben gelesen.",
  },
  {
    setting: "auto_accept_llm",
    label: "KI-Zuordnungen automatisch bestätigen",
    text:
      "Ist die KI sich sicher, wird ein Umsatz ohne Rückfrage gebucht. Miete, Nebenkosten-Vorauszahlung, Darlehensraten, Hausgeld, " +
      "Überweisungen zwischen Ihren eigenen Konten und aufgeteilte Beträge legt die KI trotzdem immer zur Prüfung vor. " +
      "Empfohlen erst ab dem zweiten importierten Monat.",
  },
];

export function AiSettings() {
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
            {SWITCHES.map(({ setting, label, text }) => (
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
                <p className="muted">{settings[setting] ? "Eingeschaltet" : "Ausgeschaltet"}</p>
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
  const on = settings.ai_private_accounts;
  const consented = new Set(settings.ai_private_accounts_consented_by ?? []);
  const status = !on
    ? "Ausgeschaltet"
    : settings.ai_private_accounts_active
      ? "Eingeschaltet: beide haben zugestimmt, die KI ordnet private Umsätze zu."
      : "Eingeschaltet, wartet auf die Zustimmung des anderen Mitglieds. Bis dahin sendet Hausbuch keine privaten Umsätze an die KI.";
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
        <strong>Private Konten mit KI zuordnen</strong>
      </label>
      <p className="muted">{PRIVATE_TEXT}</p>
      {on ? (
        <ul>
          {members.map((member) => (
            <li key={member.user.id}>
              {member.user.display_name}
              {member.user.id === meId ? " (Sie)" : ""}:{" "}
              {consented.has(member.user.id) ? "hat zugestimmt" : "Zustimmung fehlt"}
            </li>
          ))}
        </ul>
      ) : null}
      {on && !consented.has(meId) ? (
        <div>
          <button type="button" disabled={busy} onClick={onConsent}>
            Ich stimme zu
          </button>
        </div>
      ) : null}
      <p className="muted">{status}</p>
    </div>
  );
}
