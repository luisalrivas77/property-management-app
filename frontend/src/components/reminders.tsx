"use client";

// Deadline reminders (docs/phases/phase-5.md §6, §11; D122): the German label per label_code,
// the link to the reminder's scope, and the "Erledigt" button that posts the acknowledgement.
// What to do about each kind is REMINDER_GUIDE in components/todos.tsx (phase-3.md D136).
// Used by the Übersicht card "Nächste Termine" and the Zu prüfen cards (components/inbox.tsx).
//
// Every date comes from GET /reminders (due_on, and the dates in label_args); nothing here
// computes a date or a euro. A label never carries an amount or a tenant's name: label_args
// hold a year, a month, a date, or a property, unit, account label or lender (D5), so the
// Grundsteuer card links to the schedule row on Steuern, where the amount is.
import Link from "next/link";
import { useState } from "react";

import { api, ok } from "@/api/client";
import type { components } from "@/api/types";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { formatDate, formatMonth, todayIso } from "./format";
import { reminderTodo } from "./todos";

export type Reminder = components["schemas"]["Reminder"];

function arg(reminder: Reminder, name: string): string {
  const value = reminder.label_args?.[name];
  return value === undefined || value === null ? "" : String(value);
}

/** The year of a due day ("2027-01-15" → "2027"), for links into Steuern. */
function yearOf(isoDate: string): string {
  return isoDate.slice(0, 4);
}

const CHECKLIST_DOCUMENT: Record<string, string> = {
  grundsteuerbescheid: "Grundsteuerbescheid",
  zinsbescheinigung: "Zinsbescheinigung",
  wirtschaftsplan: "Wirtschaftsplan",
  hausgeldabrechnung: "Hausgeldabrechnung",
};

/** The reminder in one German line, from its label_code and label_args (§6.2). */
export function reminderLabel(reminder: Reminder): string {
  const year = arg(reminder, "year");
  const code = reminder.label_code;
  if (code.startsWith("checklist.due.")) {
    const name = CHECKLIST_DOCUMENT[code.slice("checklist.due.".length)] ?? "Unterlage";
    const lender = arg(reminder, "lender");
    return `${name} für ${year} erwartet${lender ? ` (${lender})` : ""}`;
  }
  switch (code) {
    case "grundsteuer.instalment.remind":
      return `Grundsteuer ${arg(reminder, "property")} überweisen`;
    case "grundsteuer.instalment.sepa_mandate":
      return `Grundsteuer ${arg(reminder, "property")}: Lastschrift prüfen`;
    case "tax.return_deadline":
      return `Steuererklärung ${year} abgeben`;
    case "tax.questionnaire_open":
      return `Jahresfragen ${year} beantworten`;
    case "tax.wage_certificate_missing":
      return `Lohnsteuerbescheinigung ${year} fehlt`;
    case "loan.fixed_rate_end":
      return `Zinsbindung ${arg(reminder, "lender")} endet am ${formatDate(arg(reminder, "fixed_until"))}: Anschlussfinanzierung prüfen`;
    case "loan.special_termination":
      return `${arg(reminder, "lender")}: Kündigung nach zehn Jahren möglich, prüfen, ob eine Umschuldung lohnt`;
    case "property.speculation_end":
      return `${arg(reminder, "property")}: Verkauf steuerfrei ab ${formatDate(arg(reminder, "tax_free_from"))}`;
    case "tenancy.settlement_deadline":
      return `Nebenkostenabrechnung ${year} für ${arg(reminder, "unit")} zustellen`;
    case "tenancy.rent_missing":
      return `Miete ${arg(reminder, "unit")} für ${formatMonth(arg(reminder, "month"))} fehlt`;
    case "bank.sync_stale":
      return `Bankabruf ${arg(reminder, "account")} lange nicht erfolgreich`;
    default:
      return "Termin";
  }
}

/** Where the reminder's scope lives: the page that settles it. */
export function reminderLink(reminder: Reminder): { href: string; label: string } | null {
  const id = reminder.scope_id ?? "";
  const dueYear = yearOf(reminder.due_on);
  switch (reminder.kind) {
    case "grundsteuer.instalment":
      return { href: `/tax/${dueYear}?immobilie=${encodeURIComponent(id)}#tax-grundsteuer`, label: "Grundsteuer ansehen" };
    case "checklist.due":
      return reminder.scope === "loan"
        ? { href: `/loans/${id}`, label: "Darlehen öffnen" }
        : { href: `/properties/${id}?tab=dokumente`, label: "Dokumente öffnen" };
    case "tax.return_deadline":
      return { href: `/tax/${arg(reminder, "year")}`, label: "Steuern öffnen" };
    case "tax.questionnaire_open":
      return { href: `/tax/${arg(reminder, "year")}/fragen`, label: "Jahresfragen öffnen" };
    case "tax.wage_certificate_missing":
      return { href: `/tax/${arg(reminder, "year")}?tab=anlage-n&eigentuemer=${encodeURIComponent(id)}`, label: "Anlage N öffnen" };
    case "loan.fixed_rate_end":
    case "loan.special_termination":
      return { href: `/loans/${id}`, label: "Darlehen öffnen" };
    case "property.speculation_end":
      return { href: `/properties/${id}`, label: "Immobilie öffnen" };
    case "tenancy.settlement_deadline":
    case "tenancy.rent_missing":
      return { href: `/tenancies/${id}`, label: "Mietverhältnis öffnen" };
    case "bank.sync_stale":
      return { href: `/accounts/${id}`, label: "Konto öffnen" };
    default:
      return null;
  }
}

/** "Erledigt": POST /reminders/{id}/ack with action done, which hides this instance only. */
export function ReminderDone({ reminder, onDone }: { reminder: Reminder; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function done() {
    setBusy(true);
    setError(null);
    try {
      await ok(
        api.POST("/api/reminders/{reminder_id}/ack", {
          params: { path: { reminder_id: reminder.id } },
          body: { action: "done" },
        }),
      );
      onDone();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className="secondary" disabled={busy} onClick={() => void done()}>
        Erledigt
      </button>
      <ErrorNotice error={error} />
    </>
  );
}

/** How many rows the Übersicht card shows (§11). */
const NEXT_DATES = 10;

/**
 * Nächste Termine (mockup 01, "Coming up"): the first ten open reminders by due day, each
 * with its label, its date and the link to its scope; an `action` reminder also has
 * "Erledigt". The list is GET /reminders as it comes (sorted by the API).
 */
export function NextDates({ reminders, onChanged }: { reminders: Reminder[]; onChanged: () => void }) {
  // bank.sync_stale is a watchdog, not a date: its day moves on with every good fetch, so it
  // is listed only once it is due (then it is also a to-do and a Zu prüfen card).
  const today = todayIso();
  const rows = reminders
    .filter((reminder) => reminder.kind !== "bank.sync_stale" || reminder.visible_from <= today)
    .slice(0, NEXT_DATES);
  return (
    <section className="card stack" aria-labelledby="next-dates">
      <h2 id="next-dates">Nächste Termine</h2>
      {rows.length === 0 ? (
        <p className="muted">Keine Termine in den nächsten 90 Tagen.</p>
      ) : (
        <ul className="stack next-dates">
          {rows.map((reminder) => {
            const link = reminderLink(reminder);
            const todo = reminderTodo(reminder);
            return (
              <li key={reminder.id} className="row">
                <strong className="next-dates-when">{formatDate(reminder.due_on)}</strong>
                <span>
                  {reminderLabel(reminder)}
                  {link ? (
                    <>
                      {" · "}
                      <Link href={link.href}>{link.label}</Link>
                    </>
                  ) : null}
                  {todo ? <span className="todo-text next-dates-todo">{todo}</span> : null}
                </span>
                <span className="spacer" />
                {reminder.severity === "action" ? <ReminderDone reminder={reminder} onDone={onChanged} /> : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
