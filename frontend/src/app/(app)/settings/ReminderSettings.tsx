"use client";

// Erinnerungen (docs/phases/phase-5.md D123): the workspace's `reminder_emails` switch. When it
// is on, each member gets one digest email on a day a deadline is due: the kind of deadline,
// its date and the property or unit label, never an amount or a tenant's name. The switch
// rides PATCH /workspace/settings like the KI switches (one workspace.settings_changed event).
// `reminder_emails_available` says whether the server can send mail at all; until it can, a
// plain note says so and the switch only stores the household's choice.
import { useState } from "react";

import { api, ok } from "@/api/client";
import type { WorkspaceSettings } from "@/api/schemas";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { ErrorNotice } from "@/lib/ErrorNotice";

const loadSettings = () => ok(api.GET("/api/workspace/settings"));

export function ReminderSettings() {
  const loaded = useLoad(loadSettings);
  const [saved, setSaved] = useState<WorkspaceSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function change(value: boolean) {
    setBusy(true);
    setError(null);
    try {
      setSaved(await ok(api.PATCH("/api/workspace/settings", { body: { reminder_emails: value } })));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Loading state={loaded}>
      {(fetched) => {
        const settings = saved ?? fetched;
        return (
          <div className="card stack">
            <label className="inline">
              <input
                type="checkbox"
                role="switch"
                aria-checked={settings.reminder_emails}
                checked={settings.reminder_emails}
                disabled={busy}
                onChange={(event) => void change(event.target.checked)}
              />{" "}
              <strong>Erinnerungen per E-Mail</strong>
            </label>
            <p className="muted">
              Steht ein Termin an, zum Beispiel eine Grundsteuer-Rate, eine fehlende Miete oder die Abgabe der Steuererklärung,
              bekommt jedes Mitglied morgens eine E-Mail mit allen fälligen Terminen, höchstens eine am Tag. Sie nennt nur die Art
              des Termins, das Datum und die Bezeichnung der Wohnung oder Einheit, nie einen Betrag oder den Namen eines Mieters;
              die Einzelheiten stehen auf der Übersicht.
            </p>
            <p className="muted">{settings.reminder_emails ? "Eingeschaltet" : "Ausgeschaltet"}</p>
            {settings.reminder_emails_available ? null : (
              <p className="muted" data-testid="reminder-emails-unavailable">
                Der E-Mail-Versand ist noch nicht eingerichtet. Bis dahin wird keine E-Mail verschickt; die Erinnerungen stehen weiter
                auf der Übersicht und unter „Zu prüfen“.
              </p>
            )}
            <ErrorNotice error={error} />
          </div>
        );
      }}
    </Loading>
  );
}
