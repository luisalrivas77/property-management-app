"use client";

// Unterlagen (spec amendment 3): the documents a tax year of this property needs, as
// GET /properties/{id}/checklist computes them (rules.document_checklist). Status, labels and
// hints only; nothing is decided here. German texts are keyed by requirement code. Styled
// like the "Paperwork for this return" card of docs/mockups/04-tax-year.png.
import Link from "next/link";
import { useCallback, useId, useState } from "react";

import { api, ok } from "@/api/client";
import type { ChecklistDocumentRef, ChecklistStatus, DocumentChecklistItem, RequirementCode } from "@/api/schemas";
import { formatDate, formatMoney } from "@/components/format";
import { ACQUISITION_COST_KIND, ALLOCATION_CATEGORY, DOCUMENT_SUBTYPE, DOCUMENT_TYPE } from "@/components/labels";
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

interface Requirement {
  /** The row's title; `year` is the checklist's tax year. */
  title: (item: DocumentChecklistItem, year: number) => string;
  hint: string;
  /** Where a missing item is fixed: a property tab, or the loan's or tenancy's page. */
  fix: (item: DocumentChecklistItem, propertyId: string) => { href: string; label: string };
}

function scopeSuffix(item: DocumentChecklistItem): string {
  const scope = item.scope;
  if (scope.type === "loan") return scope.lender ? ` · ${scope.lender}` : "";
  if (scope.type === "tenancy") return ` · ${scope.unit_label ?? "Einheit"}, ab ${formatDate(scope.tenancy_start)}`;
  return "";
}

const propertyTab = (tab: string, label: string) => (_: DocumentChecklistItem, propertyId: string) => ({
  href: `/properties/${propertyId}?tab=${tab}`,
  label,
});
const loanPage = (item: DocumentChecklistItem) => ({ href: `/loans/${item.scope.id}`, label: "Beim Darlehen verknüpfen" });
const tenancyPage = (item: DocumentChecklistItem) => ({
  href: `/tenancies/${item.scope.id}`,
  label: "Beim Mietverhältnis verknüpfen",
});

const REQUIREMENTS: Record<RequirementCode, Requirement> = {
  purchase_contract: {
    title: () => "Kaufvertrag",
    hint: "Grundlage für AfA und Spekulationsfrist",
    fix: propertyTab("dokumente", "Unter Dokumente verknüpfen"),
  },
  price_split: {
    title: () => "Kaufpreisaufteilung",
    hint: "Gebäudeanteil als AfA-Basis",
    fix: propertyTab("aufteilung", "Kaufpreisaufteilung wählen"),
  },
  acquisition_cost_evidence: {
    title: (item) =>
      `Beleg ${item.scope.cost_kind ? ACQUISITION_COST_KIND[item.scope.cost_kind] : "Nebenkosten"} ${formatMoney(item.scope.cost_amount)}`,
    hint: "Nebenkosten gehören in die AfA-Basis",
    fix: propertyTab("nebenkosten", "Beleg unter Nebenkosten zuordnen"),
  },
  grundsteuerbescheid: {
    title: () => "Grundsteuerbescheid",
    hint: "Gilt, bis ein neuer Bescheid kommt",
    fix: propertyTab("dokumente", "Unter Dokumente verknüpfen"),
  },
  loan_contract: {
    title: (item) => `Darlehensvertrag${scopeSuffix(item)}`,
    hint: "Zinsen sind Werbungskosten, Tilgung nicht",
    fix: loanPage,
  },
  zinsbescheinigung: {
    title: (item, year) => `Zinsbescheinigung ${year}${scopeSuffix(item)}`,
    hint: "Die Bank schickt sie meist im Januar",
    fix: loanPage,
  },
  lease: {
    title: (item) => `Mietvertrag${scopeSuffix(item)}`,
    hint: "Grundlage der Mieteinnahmen",
    fix: tenancyPage,
  },
  handover_protocol: {
    title: (item) => `Übergabeprotokoll${scopeSuffix(item)}`,
    hint: "Zustand der Wohnung beim Einzug",
    fix: tenancyPage,
  },
  deposit_record: {
    title: (item) => `Kautionsnachweis${scopeSuffix(item)}`,
    hint: "Kautionskonto oder Bürgschaft",
    fix: tenancyPage,
  },
  wirtschaftsplan: {
    title: (_, year) => `Wirtschaftsplan ${year} (WEG)`,
    hint: "Für die genaue Aufteilung des Hausgelds",
    fix: propertyTab("dokumente", "Unter Dokumente verknüpfen"),
  },
  hausgeldabrechnung: {
    title: (_, year) => `Hausgeldabrechnung ${year} (WEG)`,
    hint: "Kommt meist im Frühjahr des Folgejahres",
    fix: propertyTab("dokumente", "Unter Dokumente verknüpfen"),
  },
  // Phase 3 (§8, D64); the Steuern screen and its inputs come in steps 1 and 2.
  cost_receipt: {
    title: (item) =>
      `Beleg ${item.scope.category ? ALLOCATION_CATEGORY[item.scope.category] : "Zahlung"} ${formatDate(item.scope.booking_date)}`,
    hint: "Rechnung zur Zahlung, falls das Finanzamt fragt",
    fix: propertyTab("dokumente", "Unter Dokumente verknüpfen"),
  },
  market_rent: {
    title: (item) => `Ortsübliche Miete${item.scope.type === "unit" ? ` · ${item.scope.unit_label ?? "Einheit"}` : ""}`,
    hint: "Mietspiegel-Wert für den Vergleich mit der vereinbarten Miete",
    fix: propertyTab("dokumente", "Mietspiegel unter Dokumente ablegen"),
  },
};

/** "Kaufvertrag · 15.03.2024": by type and date, never by title (titles can name a tenant). */
function refLabel(ref: ChecklistDocumentRef): string {
  const kind = ref.subtype ? DOCUMENT_SUBTYPE[ref.subtype] : ref.type ? DOCUMENT_TYPE[ref.type] : "Dokument";
  const date = ref.document_date ? formatDate(ref.document_date) : `eingegangen ${formatDate(ref.received_at)}`;
  return `${kind} · ${date}`;
}

function hintFor(item: DocumentChecklistItem): string {
  const base = REQUIREMENTS[item.code].hint;
  if (item.status === "incomplete") return "Steuerjahr am Dokument eintragen";
  if (item.status === "expected" && item.due_on) return `${base} · erwartet bis ${formatDate(item.due_on)}`;
  if (item.code === "price_split" && item.status === "present" && item.documents.length === 0) {
    return "Ergebnis der BMF-Arbeitshilfe gespeichert";
  }
  return base;
}

export function ChecklistTab({ propertyId }: { propertyId: string }) {
  const [year, setYear] = useState<number | null>(null);
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
    [propertyId, year],
  );
  const checklist = useLoad(load);
  const shownYear = year ?? defaultYear;

  return (
    <section className="card checklist" aria-labelledby={`${selectId}-title`}>
      <div className="row checklist-head">
        <h2 id={`${selectId}-title`}>Unterlagen für die Steuererklärung</h2>
        <span className="spacer" />
        {years.length > 0 && shownYear !== null ? (
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

function ChecklistRow({ item, year, propertyId }: { item: DocumentChecklistItem; year: number; propertyId: string }) {
  const requirement = REQUIREMENTS[item.code];
  const fix = item.status === "missing" || item.status === "expected" ? requirement.fix(item, propertyId) : null;
  return (
    <li className={`checklist-item checklist-${item.status}`}>
      <span className="checklist-icon" aria-hidden="true">
        {STATUS_GLYPH[item.status]}
      </span>
      <div>
        <p className="checklist-title">
          {requirement.title(item, year)}
          <span className="visually-hidden">: {STATUS_LABEL[item.status]}</span>
        </p>
        <p className="checklist-hint">{hintFor(item)}</p>
        {item.documents.length > 0 || fix ? (
          <p className="checklist-links">
            {item.documents.map((ref) => (
              <Link key={ref.id} href={`/documents/${ref.id}`}>
                {refLabel(ref)}
              </Link>
            ))}
            {fix ? <Link href={fix.href}>{fix.label}</Link> : null}
          </p>
        ) : null}
      </div>
    </li>
  );
}
