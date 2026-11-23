"use client";

// Was noch fehlt on Übersicht (phase-3.md D136): every open to-do of the household in one list
// with a count, each with what to do and its button or link. It reads only existing endpoints:
// per property the checklist and the Anlage V draft of the checklist's default year (the
// current year within the years the property has), per owner the Anlage N draft, the
// whole-return estimate (its warnings and its open Jahresfragen, one entry for all of them)
// and the reminders the page already holds. What counts as a to-do and what to do about it
// is components/todos.tsx; a source that cannot be read (no rule set for the year, no pay
// yet) adds nothing. The Anlage V tab keeps its own list for one property. Per property also
// the Nebenkostenpauschale rise test (phase-4.md D155 (4)): a Pauschale that can rise or must
// fall is a to-do with its pre-filled „Mietänderung“ and the letter (components/flatRate.tsx).
import Link from "next/link";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type { AnlageVDraft, DocumentChecklistItem, DraftWarning, FlatRateCheck, Owner, Property } from "@/api/schemas";
import type { components } from "@/api/types";
import { translate, useT } from "@/i18n";
import { todosText } from "@/i18n/todos";

import { ReadUpload } from "./documents";
import { flatRateTodos } from "./flatRate";
import { todayIso } from "./format";
import { NoPayButton, noPayHint } from "./noPay";
import { ReminderDone, reminderLabel, reminderLink, type Reminder } from "./reminders";
import { payInputsHref, questionnaireHref, taxFormHref } from "./taxYear";
import {
  REMINDER_GUIDE,
  TodoList,
  checklistEntry,
  reminderTodo,
  coveredByWarning,
  resolveAction,
  warningEntry,
  type GuideContext,
  type TodoEntry,
} from "./todos";
import { useLoad } from "./useLoad";

type AnlageNDraft = components["schemas"]["AnlageNDraft"];
type ReturnEstimate = components["schemas"]["ReturnEstimate"];

interface PropertyTodos {
  property: Property;
  year: number | null;
  items: DocumentChecklistItem[];
  draft: AnlageVDraft | null;
  flatRate: FlatRateCheck | null;
}

interface OverviewTodosData {
  year: number;
  properties: PropertyTodos[];
  /** noPay: the owner has no certificate, payslip or expected pay this year (anlage_n.no_pay). */
  owners: { owner: Owner; draft: AnlageNDraft | null; noPay: boolean }[];
  estimate: ReturnEstimate | null;
}

/** A source that cannot be read adds nothing (its own screen says why). */
function orNull<T>(promise: Promise<T>): Promise<T | null> {
  return promise.then(
    (value) => value,
    () => null,
  );
}

async function loadTodos(): Promise<OverviewTodosData> {
  const year = Number(todayIso().slice(0, 4));
  const [properties, owners, estimate] = await Promise.all([
    ok(api.GET("/api/properties")),
    orNull(ok(api.GET("/api/owners"))),
    orNull(ok(api.GET("/api/tax/return-estimate", { params: { query: { tax_year: year } } }))),
  ]);
  const perProperty = await Promise.all(
    properties.map(async (property): Promise<PropertyTodos> => {
      const path = { property_id: property.id };
      const flatRatePromise = orNull(ok(api.GET("/api/properties/{property_id}/flat-rate-check", { params: { path } })));
      const checklist = await orNull(ok(api.GET("/api/properties/{property_id}/checklist", { params: { path } })));
      const draft = checklist
        ? await orNull(
            ok(api.GET("/api/properties/{property_id}/anlage-v", { params: { path, query: { tax_year: checklist.tax_year } } })),
          )
        : null;
      const flatRate = await flatRatePromise;
      return { property, year: checklist?.tax_year ?? null, items: checklist?.items ?? [], draft, flatRate };
    }),
  );
  const perOwner = await Promise.all(
    (owners ?? []).map(async (owner) => {
      try {
        const draft = await ok(
          api.GET("/api/owners/{owner_id}/anlage-n", { params: { path: { owner_id: owner.id }, query: { tax_year: year } } }),
        );
        return { owner, draft, noPay: false };
      } catch (error) {
        // No pay at all is a to-do of its own (below); any other failure adds nothing.
        const codes = error instanceof ApiError && error.body ? ruleCodes(error.body) : [];
        return { owner, draft: null, noPay: codes.includes("anlage_n.no_pay") };
      }
    }),
  );
  return { year, properties: perProperty, owners: perOwner, estimate };
}

// The open Jahresfragen are one entry of their own; these say the same.
const QUESTION_CODES = new Set(["return.questionnaire_open", "tax_export.questions_open"]);

/** A to-do warning with its action; where the action needs what only the Steuern screen loads
 * (the payments a warning cites), a link to that screen instead. */
function entryWithFallback(
  warning: DraftWarning,
  ctx: GuideContext,
  key: string,
  fallback: string,
  onChanged: () => void,
  context: string,
): TodoEntry | null {
  const entry = warningEntry(warning, ctx, key, onChanged, context);
  if (!entry) return null;
  if (resolveAction(warning, ctx) === null) entry.action = <Link href={fallback}>{translate(todosText).seeOnTax}</Link>;
  return entry;
}

function buildEntries(data: OverviewTodosData, reminders: Reminder[], onChanged: () => void): TodoEntry[] {
  const t = translate(todosText);
  const entries: TodoEntry[] = [];
  const seen = new Set<string>();
  const once = (warning: DraftWarning, scope: string) => {
    const key = `${scope}|${warning.code}|${JSON.stringify(warning.params ?? {})}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  };

  for (const { property, year, items, draft, flatRate } of data.properties) {
    // The rise test needs no tax year, so it is listed even when the checklist is unreadable.
    entries.push(...flatRateTodos(flatRate, { propertyId: property.id, context: property.label }));
    if (year === null) continue;
    const ctx: GuideContext = { year, propertyId: property.id, draft: draft ?? undefined };
    const tax = taxFormHref("v", { year, propertyId: property.id, ownerId: null });
    const warnings = draft?.warnings ?? [];
    warnings.forEach((warning, index) => {
      if (QUESTION_CODES.has(warning.code) || !once(warning, property.id)) return;
      const entry = entryWithFallback(warning, ctx, `${property.id}-w-${index}`, tax, onChanged, property.label);
      if (entry) entries.push(entry);
    });
    for (const item of items) {
      if (coveredByWarning(item, warnings)) continue;
      const entry = checklistEntry(item, year, { propertyId: property.id, items, onChanged }, property.label);
      if (entry) entries.push(entry);
    }
  }

  let certificateMissing = false;
  for (const { owner, draft, noPay } of data.owners) {
    // Before, the Anlage N draft's anlage_n.no_pay was swallowed here, so an owner without any
    // pay for the year had no to-do at all (Luis, 12 Nov 2026). A confirmed payslip or
    // certificate, or an expected gross pay typed on Eigentümer, clears it, and so does
    // „Kein Arbeitslohn in <Jahr>“ for a year without salary (phase 4b D195, Luis 15 Nov 2026).
    if (noPay) {
      entries.push({
        key: `${owner.id}-no-pay`,
        title: t.noPayTitle(data.year),
        text: t.noPayText + noPayHint(),
        action: (
          <>
            <ReadUpload label={t.uploadPayslip} onUploaded={onChanged} />
            <Link href={payInputsHref(data.year, owner.id)}>{t.typeGrossPay}</Link>
            <NoPayButton ownerId={owner.id} year={data.year} onDone={onChanged} />
          </>
        ),
        context: owner.full_name,
      });
    }
    const ctx: GuideContext = { year: data.year, ownerId: owner.id };
    const tax = taxFormHref("n", { year: data.year, propertyId: null, ownerId: owner.id });
    (draft?.warnings ?? []).forEach((warning, index) => {
      if (QUESTION_CODES.has(warning.code) || !once(warning, owner.id)) return;
      if (warning.code === "anlage_n.certificate_missing") certificateMissing = true;
      const entry = entryWithFallback(warning, ctx, `${owner.id}-n-${index}`, tax, onChanged, owner.full_name);
      if (entry) entries.push(entry);
    });
  }

  const estimate = data.estimate;
  if (estimate) {
    const ctx: GuideContext = { year: data.year };
    const tax = taxFormHref("v", { year: data.year, propertyId: null, ownerId: null });
    (estimate.warnings ?? []).forEach((warning, index) => {
      if (QUESTION_CODES.has(warning.code) || !once(warning, "household")) return;
      const entry = entryWithFallback(warning, ctx, `household-${index}`, tax, onChanged, t.household);
      if (entry) entries.push(entry);
    });
    const open = estimate.questionnaire.open?.length ?? 0;
    if (open > 0) {
      entries.push({
        key: "questions",
        title: t.questionsTitle(open, data.year),
        text: t.questionsText,
        action: <Link href={questionnaireHref(data.year)}>{t.answerQuestions}</Link>,
      });
    }
  }

  // A reminder becomes a to-do on its visible_from day, the day its Zu prüfen card appears;
  // until then it is only a date under Nächste Termine. Before, every listed reminder was a
  // to-do, so the bank.sync_stale watchdog, due 35 days after the last good fetch, read as
  // "Bankabruf … lange nicht erfolgreich" the morning after a successful one (12 Nov 2026).
  const today = todayIso();
  for (const reminder of reminders) {
    const guide = REMINDER_GUIDE[reminder.kind];
    if (!guide?.todo || reminder.kind === "tax.questionnaire_open") continue;
    if (reminder.visible_from > today) continue;
    if (reminder.kind === "tax.wage_certificate_missing" && certificateMissing) continue;
    const link = reminderLink(reminder);
    entries.push({
      key: `reminder-${reminder.id}`,
      title: reminderLabel(reminder),
      text: reminderTodo(reminder),
      action: (
        <>
          {link ? <Link href={link.href}>{link.label}</Link> : null}
          {reminder.severity === "action" ? <ReminderDone reminder={reminder} onDone={onChanged} /> : null}
        </>
      ),
    });
  }
  return entries;
}

/** Übersicht shows the first five to-dos and „Alle N anzeigen“ (phase 10 §7.6). */
const OVERVIEW_LIMIT = 5;

/** `reminders` and `onRemindersChanged` are the page's (the Nächste Termine card shares them). */
const NOW: Promise<unknown> = Promise.resolve();

/** `after`: the list's reads start once it settles; Übersicht lets its tiles' reads go first on
 * the small container (21 Nov 2026). Without it they start at once. */
export function OverviewTodos({
  reminders,
  onRemindersChanged,
  after = NOW,
}: {
  reminders: Reminder[];
  onRemindersChanged: () => void;
  after?: Promise<unknown>;
}) {
  const t = useT(todosText);
  const [refresh, setRefresh] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh forces a reload
  const load = useCallback(() => after.then(loadTodos), [refresh, after]);
  const data = useLoad(load);
  const changed = useCallback(() => {
    setRefresh((n) => n + 1);
    onRemindersChanged();
  }, [onRemindersChanged]);
  const entries = data.data ? buildEntries(data.data, reminders, changed) : [];
  return (
    <TodoList
      titleId="overview-todos"
      entries={entries}
      limit={OVERVIEW_LIMIT}
      loading={data.data === undefined && data.error == null}
      empty={
        data.data === undefined && data.error != null
          ? t.loadFailed
          : t.nothingOpen
      }
    />
  );
}
