"use client";

// Unterlagen (spec amendment 3): the documents a tax year of this property needs, as
// GET /properties/{id}/checklist computes them (rules.document_checklist). Status, labels and
// hints only; nothing is decided here. The title, hint, Was tun and fix of each requirement
// code live in components/todos.tsx (REQUIREMENT_GUIDE, phase-3.md D136), shared with the
// "Was noch fehlt" lists. Styled like the "Paperwork for this return" card of
// docs/mockups/04-tax-year.png.
import Link from "next/link";
import { useCallback, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type {
  AllocationFact,
  ChecklistBankLine,
  ChecklistDocumentRef,
  ChecklistStatus,
  DocumentChecklistItem,
} from "@/api/schemas";
import { formatDate, formatMoney } from "@/components/format";
import { DOCUMENT_SUBTYPE, DOCUMENT_TYPE } from "@/components/labels";
import { REQUIREMENT_GUIDE, checklistAction, requirementTodo } from "@/components/todos";
import { Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const STATUS_LABEL: Record<ChecklistStatus, string> = {
  present: "vorhanden",
  missing: "fehlt",
  expected: "noch nicht fällig",
  incomplete: "Steuerjahr fehlt",
};

const STATUS_GLYPH: Record<ChecklistStatus, string> = {
  present: "✓",
  missing: "!",
  expected: "",
  incomplete: "!",
};

/** "Kaufvertrag · 15.03.2024": by type and date, never by title (titles can name a tenant). */
function refLabel(ref: ChecklistDocumentRef): string {
  const kind = ref.subtype ? DOCUMENT_SUBTYPE[ref.subtype] : ref.type ? DOCUMENT_TYPE[ref.type] : "Dokument";
  const date = ref.document_date ? formatDate(ref.document_date) : `eingegangen ${formatDate(ref.received_at)}`;
  return `${kind} · ${date}`;
}

/** "Bankumsatz vom 04.05.2026 · 1.000,00 €": a deposit paid into the account (D142). */
function bankLineLabel(line: ChecklistBankLine): string {
  return `Bankumsatz vom ${formatDate(line.booking_date)} · ${formatMoney(line.amount)}`;
}

function hintFor(item: DocumentChecklistItem): string {
  const base = REQUIREMENT_GUIDE[item.code].hint;
  if (item.status === "incomplete") return "Steuerjahr am Dokument eintragen";
  if (item.status === "expected" && item.due_on) return `${base} · erwartet bis ${formatDate(item.due_on)}`;
  if (item.code === "price_split" && item.status === "present" && item.documents.length === 0) {
    return "Ergebnis der BMF-Arbeitshilfe gespeichert";
  }
  if (item.code === "deposit_record" && item.status === "present" && item.documents.length === 0) {
    return "Nachgewiesen durch die Zahlung auf dem Konto";
  }
  return base;
}

/** `year`, when given, fixes the tax year and hides the selector (the Steuern screen, whose
 * own selector sets it). `refresh` reloads the list after an action elsewhere on the screen;
 * `payments` (the draft's cited payments) lets a missing receipt open its bank line;
 * `onChanged` is called after a row's action changed the ledger (a deposit linked to its bank
 * line, D142), else the list reloads itself. */
export function ChecklistTab({
  propertyId,
  year: fixedYear,
  refresh = 0,
  payments,
  onChanged,
}: {
  propertyId: string;
  year?: number;
  refresh?: number;
  payments?: Map<string, AllocationFact>;
  onChanged?: () => void;
}) {
  const [chosenYear, setYear] = useState<number | null>(null);
  const year = fixedYear ?? chosenYear;
  const [years, setYears] = useState<number[]>([]);
  const [defaultYear, setDefaultYear] = useState<number | null>(null);
  const selectId = useId();
  // The years are kept apart from the data, so the selector stays on screen (and keeps
  // focus) while another year loads.
  const load = useCallback(
    () =>
      ok(
        api.GET("/api/properties/{property_id}/checklist", {
          params: { path: { property_id: propertyId }, query: year === null ? {} : { tax_year: year } },
        }),
      ).then((data) => {
        setYears(data.available_years);
        if (year === null) setDefaultYear(data.tax_year);
        return data;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh forces a reload
    [propertyId, year, refresh],
  );
  const checklist = useLoad(load);
  const shownYear = year ?? defaultYear;

  return (
    <section className="card checklist" aria-labelledby={`${selectId}-title`}>
      <div className="row checklist-head">
        <h2 id={`${selectId}-title`}>Unterlagen für die Steuererklärung</h2>
        <span className="spacer" />
        {fixedYear === undefined && years.length > 0 && shownYear !== null ? (
          <label htmlFor={selectId} className="inline">
            Steuerjahr
            <select id={selectId} value={shownYear} onChange={(event) => setYear(Number(event.target.value))}>
              {years.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <Loading state={checklist}>
        {(data) =>
          data.items.length === 0 ? (
            <p className="muted">Für {data.tax_year} sind keine Unterlagen nötig.</p>
          ) : (
            <ul className="checklist-items">
              {data.items.map((item) => (
                <ChecklistRow
                  key={`${item.code}-${item.scope.id}`}
                  item={item}
                  year={data.tax_year}
                  propertyId={propertyId}
                  payments={payments}
                  items={data.items}
                  onChanged={onChanged ?? checklist.reload}
                />
              ))}
            </ul>
          )
        }
      </Loading>
      <p className="muted checklist-foot">Planungshilfe, keine Steuerberatung.</p>
    </section>
  );
}

function ChecklistRow({
  item,
  year,
  propertyId,
  payments,
  items,
  onChanged,
}: {
  item: DocumentChecklistItem;
  year: number;
  propertyId: string;
  payments?: Map<string, AllocationFact>;
  items: readonly DocumentChecklistItem[];
  onChanged: () => void;
}) {
  const guide = REQUIREMENT_GUIDE[item.code];
  const action = checklistAction(item, { propertyId, payments, items, onChanged });
  const bankLines = item.bank_lines ?? [];
  const todo = requirementTodo(item);
  return (
    <li className={`checklist-item checklist-${item.status}`}>
      <span className="checklist-icon" aria-hidden="true">
        {STATUS_GLYPH[item.status]}
      </span>
      <div>
        <p className="checklist-title">
          {guide.title(item, year)}
          <span className="visually-hidden">: {STATUS_LABEL[item.status]}</span>
        </p>
        <p className="checklist-hint">{hintFor(item)}</p>
        {todo ? (
          <p className="checklist-hint todo-text">
            <strong>Was tun:</strong> {todo}
          </p>
        ) : null}
        {item.documents.length > 0 || bankLines.length > 0 || action ? (
          <p className="checklist-links">
            {item.documents.map((ref) => (
              <Link key={ref.id} href={`/documents/${ref.id}`}>
                {refLabel(ref)}
              </Link>
            ))}
            {bankLines.map((line) =>
              line.transaction_id ? (
                <Link key={line.allocation_id} href={`/inbox?umsatz=${line.transaction_id}`}>
                  {bankLineLabel(line)}
                </Link>
              ) : (
                <span key={line.allocation_id}>{bankLineLabel(line)}</span>
              ),
            )}
            {action}
          </p>
        ) : null}
      </div>
    </li>
  );
}
