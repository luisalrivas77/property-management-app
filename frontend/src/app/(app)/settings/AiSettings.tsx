"use client";

// KI (docs/phases/phase-2.md D16, D26, D42): the workspace's two AI switches, one card each.
// GET /workspace/settings reads them; a switch sends PATCH with only its own setting, and the
// API records the change as a workspace.settings_changed event. Both start off.
import { useState } from "react";

import { api, ok } from "@/api/client";
import type { WorkspaceSettings } from "@/api/schemas";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

type Setting = "ai_tenant_documents" | "auto_accept_llm"; // reminder_emails: ReminderSettings

const loadSettings = () => ok(api.GET("/api/workspace/settings"));

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
  const loaded = useLoad(loadSettings);
  const [saved, setSaved] = useState<WorkspaceSettings | null>(null);
  const [busy, setBusy] = useState<Setting | null>(null);
  const [error, setError] = useState<unknown>(null);

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
        const settings = saved ?? fetched;
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
            <ErrorNotice error={error} />
          </div>
        );
      }}
    </Loading>
  );
}
