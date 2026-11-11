"use client";

// Was noch fehlt on Übersicht (phase-3.md D136): every open to-do of the household in one list
// with a count, each with what to do and its button or link. It reads only existing endpoints:
// per property the checklist and the Anlage V draft of the checklist's default year (the
// current year within the years the property has), per owner the Anlage N draft, the
// whole-return estimate (its warnings and its open Jahresfragen, one entry for all of them)
// and the reminders the page already holds. What counts as a to-do and what to do about it
// is components/todos.tsx; a source that cannot be read (no rule set for the year, no pay
// yet) adds nothing. The Anlage V tab keeps its own list for one property.
import Link from "next/link";
import { useCallback, useState } from "react";

import { api, ok } from "@/api/client";
import type { AnlageVDraft, DocumentChecklistItem, DraftWarning, Owner, Property } from "@/api/schemas";
import type { components } from "@/api/types";

import { todayIso } from "./format";
import { ReminderDone, reminderLabel, reminderLink, type Reminder } from "./reminders";
import { questionnaireHref, taxFormHref } from "./taxYear";
import {
  REMINDER_GUIDE,
  TodoList,
  checklistEntry,
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
}

interface OverviewTodosData {
  year: number;
  properties: PropertyTodos[];
  owners: { owner: Owner; draft: AnlageNDraft | null }[];
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
      const checklist = await orNull(ok(api.GET("/api/properties/{property_id}/checklist", { params: { path } })));
      const draft = checklist
        ? await orNull(
            ok(api.GET("/api/properties/{property_id}/anlage-v", { params: { path, query: { tax_year: checklist.tax_year } } })),
          )
        : null;
      return { property, year: checklist?.tax_year ?? null, items: checklist?.items ?? [], draft };
    }),
  );
  const perOwner = await Promise.all(
    (owners ?? []).map(async (owner) => ({
      owner,
      draft: await orNull(
        ok(api.GET("/api/owners/{owner_id}/anlage-n", { params: { path: { owner_id: owner.id }, query: { tax_year: year } } })),
      ),
    })),
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
  if (resolveAction(warning, ctx) === null) entry.action = <Link href={fallback}>Auf Steuern ansehen</Link>;
  return entry;
}

function buildEntries(data: OverviewTodosData, reminders: Reminder[], onChanged: () => void): TodoEntry[] {
  const entries: TodoEntry[] = [];
  const seen = new Set<string>();
  const once = (warning: DraftWarning, scope: string) => {
    const key = `${scope}|${warning.code}|${JSON.stringify(warning.params ?? {})}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  };

  for (const { property, year, items, draft } of data.properties) {
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
  for (const { owner, draft } of data.owners) {
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
      const entry = entryWithFallback(warning, ctx, `household-${index}`, tax, onChanged, "Haushalt");
      if (entry) entries.push(entry);
    });
    const open = estimate.questionnaire.open?.length ?? 0;
    if (open > 0) {
      entries.push({
        key: "questions",
        title: `${open} ${open === 1 ? "Jahresfrage" : "Jahresfragen"} ${data.year} offen`,
        text: "Die Jahresfragen beantworten; sie dauern meist nur ein paar Minuten, und die App rechnet die Antworten ein.",
        action: <Link href={questionnaireHref(data.year)}>Jahresfragen beantworten</Link>,
      });
    }
  }

  for (const reminder of reminders) {
    const guide = REMINDER_GUIDE[reminder.kind];
    if (!guide?.todo || reminder.kind === "tax.questionnaire_open") continue;
    if (reminder.kind === "tax.wage_certificate_missing" && certificateMissing) continue;
    const link = reminderLink(reminder);
    entries.push({
      key: `reminder-${reminder.id}`,
      title: reminderLabel(reminder),
      text: guide.text(reminder),
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

/** `reminders` and `onRemindersChanged` are the page's (the Nächste Termine card shares them). */
export function OverviewTodos({ reminders, onRemindersChanged }: { reminders: Reminder[]; onRemindersChanged: () => void }) {
  const [refresh, setRefresh] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh forces a reload
  const load = useCallback(() => loadTodos(), [refresh]);
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
      loading={data.data === undefined && data.error == null}
      empty={
        data.data === undefined && data.error != null
          ? "Die offenen Punkte konnten gerade nicht geladen werden."
          : "Nichts offen: alle Unterlagen, Angaben und Fragen sind erledigt."
      }
    />
  );
}
