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
import { formatter, localized, translate, useFormat, useT } from "@/i18n";
import { remindersText } from "@/i18n/reminders";
import { ErrorNotice } from "@/lib/ErrorNotice";
import { notifyInboxChanged } from "@/lib/inboxCount";

import { todayIso } from "./format";
import { reminderTodo } from "./todos";
import { taxStepHref } from "./taxYear";

export type Reminder = components["schemas"]["Reminder"];

function arg(reminder: Reminder, name: string): string {
  const value = reminder.label_args?.[name];
  return value === undefined || value === null ? "" : String(value);
}

/** The year of a due day ("2027-01-15" → "2027"), for links into Steuern. */
function yearOf(isoDate: string): string {
  return isoDate.slice(0, 4);
}

const CHECKLIST_DOCUMENT: Record<string, string> = localized<string>(
  {
    grundsteuerbescheid: "Grundsteuerbescheid",
    zinsbescheinigung: "Zinsbescheinigung",
    wirtschaftsplan: "Wirtschaftsplan",
    hausgeldabrechnung: "Hausgeldabrechnung",
  },
  {
    grundsteuerbescheid: "Grundsteuerbescheid (property tax assessment)",
    zinsbescheinigung: "Zinsbescheinigung (loan interest certificate)",
    wirtschaftsplan: "Wirtschaftsplan (owners' association budget)",
    hausgeldabrechnung: "Hausgeldabrechnung (owners' association annual statement)",
  },
);

/** The reminder in one German line, from its label_code and label_args (§6.2). */
export function reminderLabel(reminder: Reminder): string {
  const t = translate(remindersText);
  const f = formatter();
  const year = arg(reminder, "year");
  const code = reminder.label_code;
  if (code.startsWith("checklist.due.")) {
    const name = CHECKLIST_DOCUMENT[code.slice("checklist.due.".length)] ?? t.document;
    return t.checklistDue(name, year, arg(reminder, "lender"));
  }
  switch (code) {
    case "grundsteuer.instalment.remind":
      return t.grundsteuerRemind(arg(reminder, "property"));
    case "grundsteuer.instalment.sepa_mandate":
      return t.grundsteuerMandate(arg(reminder, "property"));
    case "tax.return_deadline":
      return t.returnDeadline(year);
    case "tax.questionnaire_open":
      return t.questionnaireOpen(year);
    case "tax.wage_certificate_missing":
      return t.wageCertificateMissing(year);
    case "loan.fixed_rate_end":
      return t.fixedRateEnd(arg(reminder, "lender"), f.date(arg(reminder, "fixed_until")));
    case "loan.special_termination":
      return t.specialTermination(arg(reminder, "lender"));
    case "property.speculation_end":
      return t.speculationEnd(arg(reminder, "property"), f.date(arg(reminder, "tax_free_from")));
    case "tenancy.settlement_deadline":
      return t.settlementDeadline(year, arg(reminder, "unit"));
    case "tenancy.rent_missing":
      return t.rentMissing(arg(reminder, "unit"), f.month(arg(reminder, "month")));
    case "bank.sync_stale":
      return t.syncStale(arg(reminder, "account"));
    case "bank.statement_due":
      return t.statementDue(arg(reminder, "account"));
    case "crypto.holdings_statement":
      return t.holdingsStatement(year);
    default:
      return t.fallback;
  }
}

/** Where the reminder's scope lives: the page that settles it. */
export function reminderLink(reminder: Reminder): { href: string; label: string } | null {
  const t = translate(remindersText);
  const id = reminder.scope_id ?? "";
  const dueYear = yearOf(reminder.due_on);
  switch (reminder.kind) {
    case "grundsteuer.instalment":
      return { href: `${taxStepHref(Number(dueYear), "immobilie", id)}#tax-grundsteuer`, label: t.seeGrundsteuer };
    case "checklist.due":
      return reminder.scope === "loan"
        ? { href: `/loans/${id}`, label: t.openLoan }
        : { href: `/properties/${id}?tab=dokumente`, label: t.openDocuments };
    case "tax.return_deadline":
      return { href: `/tax/${arg(reminder, "year")}`, label: t.openTax };
    case "tax.questionnaire_open":
      return { href: `/tax/${arg(reminder, "year")}/fragen`, label: t.openQuestionnaire };
    case "tax.wage_certificate_missing":
      return { href: `/tax/${arg(reminder, "year")}?tab=anlage-n&eigentuemer=${encodeURIComponent(id)}`, label: t.openAnlageN };
    case "loan.fixed_rate_end":
    case "loan.special_termination":
      return { href: `/loans/${id}`, label: t.openLoan };
    case "property.speculation_end":
      return { href: `/properties/${id}`, label: t.openProperty };
    case "tenancy.settlement_deadline":
    case "tenancy.rent_missing":
      return { href: `/tenancies/${id}`, label: t.openTenancy };
    case "bank.sync_stale":
      return { href: `/accounts/${id}`, label: t.openAccount };
    case "bank.statement_due":
      // D122 amended: the Kontoauszüge tab holds the account's statement upload.
      return { href: `/accounts/${id}?tab=kontoauszuege`, label: t.uploadStatement };
    case "crypto.holdings_statement":
      // Phase 7 D190: the button sits on Anlagen › Krypto.
      return { href: "/assets?tab=krypto", label: t.createHoldingsStatement };
    default:
      return null;
  }
}

/** Kinds whose day moves with every fetch or import: listed under Nächste Termine once due. */
const WATCHDOGS = new Set<Reminder["kind"]>(["bank.sync_stale", "bank.statement_due"]);

/** "Erledigt": POST /reminders/{id}/ack with action done, which hides this instance only. */
export function ReminderDone({ reminder, onDone }: { reminder: Reminder; onDone: () => void }) {
  const t = useT(remindersText);
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
      notifyInboxChanged(); // a reminder is a Zu prüfen item too: the menu's count asks again
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
        {t.done}
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
  const t = useT(remindersText);
  const f = useFormat();
  // bank.sync_stale is a watchdog, not a date: its day moves on with every good fetch, so it
  // is listed only once it is due (then it is also a to-do and a Zu prüfen card). So is
  // bank.statement_due (D122 amended): its day moves on with every import.
  const today = todayIso();
  const rows = reminders
    .filter((reminder) => !WATCHDOGS.has(reminder.kind) || reminder.visible_from <= today)
    .slice(0, NEXT_DATES);
  return (
    <section className="card stack" aria-labelledby="next-dates">
      <h2 id="next-dates">{t.nextDates}</h2>
      {rows.length === 0 ? (
        <p className="muted">{t.noDates}</p>
      ) : (
        <ul className="stack next-dates">
          {rows.map((reminder) => {
            const link = reminderLink(reminder);
            const todo = reminderTodo(reminder);
            return (
              <li key={reminder.id} className="row">
                <strong className="next-dates-when">{f.date(reminder.due_on)}</strong>
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
