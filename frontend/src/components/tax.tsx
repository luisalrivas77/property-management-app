"use client";

// The Anlage V draft of one property and tax year (phase-3.md §11, docs/mockups/04-tax-year.png).
// GET /properties/{id}/anlage-v computes it on every read; every euro figure on this screen is
// one the backend computed (principle 1). The screen formats, counts and links, nothing more:
// no sum, share or rate is computed here. No tenant name appears (D5): a payment is its date,
// category and amount, a counterparty only its masked IBAN.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useId, useState, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import { RULE_CODE_TEXT } from "@/api/errors";
import type {
  AllocationFact,
  AnlageVDraft,
  DraftLine,
  DraftWarning,
  LimitStatus,
  Owner,
  Property,
  TaxExport,
  TaxExportKind,
  TaxExportState,
  TaxSourceRef,
  WarningCode,
} from "@/api/schemas";
import { ChecklistTab } from "@/app/(app)/properties/[id]/ChecklistTab";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { formatDate, formatDateTime, formatDecimal, formatMoney, formatPercent, formatShare, todayIso } from "./format";
import { ALLOCATION_CATEGORY, TAX_SOURCE_KIND, WARNING_TEXT, taxNote } from "./labels";
import { ActionDialog, Loading } from "./ui";
import { UsefulLifeSection } from "./taxInputs";
import { useLoad, type Loaded } from "./useLoad";

import "./tax.css";

const FOOTER = "Ein Entwurf zum Prüfen, keine Steuerberatung. Jede Zahl führt zu ihren Zahlungen und Belegen.";

/** "0", "0.00", "-0.0": a decimal string that is zero. */
function isZero(value: string | null | undefined): boolean {
  return value == null || /^-?0*(\.0*)?$/.test(value);
}

function addressLine(property: Property): string {
  const a = property.address;
  return `${a.street} ${a.house_number}, ${a.postal_code} ${a.city}`;
}

/** "07/2026": the month a date falls in. */
function monthYear(iso: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(iso);
  return match ? `${match[2]}/${match[1]}` : iso;
}

interface Action {
  href: string;
  label: string;
}

/** Where a source can be opened today. Payments, transactions, tax inputs and the rule set
 * have no page of their own; they are listed in the sources drawer only. */
export function linkFor(ref: TaxSourceRef, propertyId: string): Action | null {
  switch (ref.kind) {
    case "document":
      return ref.id ? { href: `/documents/${ref.id}`, label: "Dokument öffnen" } : null;
    case "loan":
      return ref.id ? { href: `/loans/${ref.id}`, label: "Darlehen öffnen" } : null;
    case "property":
      return { href: `/properties/${propertyId}?tab=stammdaten`, label: "Stammdaten" };
    case "price_split":
      return { href: `/properties/${propertyId}?tab=aufteilung`, label: "Kaufpreisaufteilung" };
    case "acquisition_cost":
      return { href: `/properties/${propertyId}?tab=nebenkosten`, label: "Nebenkosten" };
    default:
      return null;
  }
}

const propertyTab = (tab: string, label: string) => (_: DraftWarning, propertyId: string) => ({
  href: `/properties/${propertyId}?tab=${tab}`,
  label,
});
const toInbox = () => ({ href: "/inbox", label: "Zu prüfen öffnen" });

/** The action of an Offene-Punkte entry, where a page exists for it. Warnings about single
 * payments (renovation, other income, the ten-day rule) open their payments in the drawer. */
const WARNING_ACTION: Partial<Record<WarningCode, (warning: DraftWarning, propertyId: string) => Action | null>> = {
  "afa.no_split": propertyTab("aufteilung", "Kaufpreisaufteilung wählen"),
  "afa.cost_row_unpaid": propertyTab("nebenkosten", "Nebenkosten ansehen"),
  "afa.acquisition_cost_without_row": propertyTab("nebenkosten", "Unter Nebenkosten erfassen"),
  "afa.purchase_price_mismatch": propertyTab("stammdaten", "Kaufpreis ansehen"),
  "interest.certificate_mismatch": (warning, propertyId) => {
    const loan = warning.refs?.find((ref) => ref.kind === "loan" && ref.id);
    return loan ? { href: `/loans/${loan.id}`, label: "Darlehen öffnen" } : propertyTab("darlehen", "Darlehen ansehen")(warning, propertyId);
  },
  "hausgeld.abrechnung_pending": propertyTab("dokumente", "Hausgeldabrechnung ablegen"),
  "income.living_area_missing": propertyTab("stammdaten", "Stammdaten ansehen"),
  "umlagen.mixed_terms": (warning) => {
    const first = warning.params?.tenancies?.split(",")[0]?.trim();
    return first ? { href: `/tenancies/${first}`, label: "Mietverhältnis öffnen" } : null;
  },
  "reduced_rent.no_market_rent": propertyTab("einheiten", "Bei der Einheit eintragen"),
  "reduced_rent.prognosis_needed": propertyTab("einheiten", "Einheiten ansehen"),
  "reduced_rent.partial": propertyTab("einheiten", "Einheiten ansehen"),
  "draft.open_lines_excluded": toInbox,
  "draft.allocation_without_property": toInbox,
};

function warningAction(warning: DraftWarning, propertyId: string): Action | null {
  const specific = WARNING_ACTION[warning.code]?.(warning, propertyId);
  if (specific) return specific;
  for (const ref of warning.refs ?? []) {
    const link = linkFor(ref, propertyId);
    if (link) return link;
  }
  return null;
}

// --- The screen ---------------------------------------------------------------------------

export function AnlageVScreen({ property, properties, year }: { property: Property; properties: Property[]; year: number }) {
  const router = useRouter();
  const ids = useId();
  const href = useCallback(
    (y: number, propertyId: string) =>
      properties.length > 1 ? `/tax/${y}?immobilie=${encodeURIComponent(propertyId)}` : `/tax/${y}`,
    [properties.length],
  );

  // The years come from the checklist (the same available_years the draft is bounded by).
  const loadYears = useCallback(
    () =>
      ok(api.GET("/api/properties/{property_id}/checklist", { params: { path: { property_id: property.id } } })).then(
        (checklist) => checklist.available_years,
      ),
    [property.id],
  );
  const years = useLoad(loadYears);
  const loadDraft = useCallback(
    () =>
      ok(
        api.GET("/api/properties/{property_id}/anlage-v", {
          params: { path: { property_id: property.id }, query: { tax_year: year } },
        }),
      ),
    [property.id, year],
  );
  const draft = useLoad(loadDraft);
  const yearOptions = years.data && !years.data.includes(year) ? [...years.data, year].sort((a, b) => a - b) : (years.data ?? [year]);

  return (
    <>
      <div className="page-head tax-head">
        <div>
          <p className="eyebrow">Steuerjahr {year} · Anlage V (Entwurf)</p>
          <h1>{property.label}</h1>
          <span className="muted">{addressLine(property)}</span>
        </div>
        <span className="spacer" />
        <div className="row tax-pickers">
          {properties.length > 1 ? (
            <label htmlFor={`${ids}-property`} className="inline">
              Immobilie
              <select
                id={`${ids}-property`}
                value={property.id}
                onChange={(event) => router.push(href(year, event.target.value))}
              >
                {properties.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label htmlFor={`${ids}-year`} className="inline">
            Steuerjahr
            <select id={`${ids}-year`} value={year} onChange={(event) => router.push(href(Number(event.target.value), property.id))}>
              {yearOptions.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <nav aria-label="Formulare" className="tabs">
        <Link href={href(year, property.id)} aria-current="page" scroll={false}>
          Anlage V
        </Link>
        <span className="tab-disabled" aria-disabled="true">
          KAP <span className="tab-note">ab Phase 8</span>
        </span>
        <span className="tab-disabled" aria-disabled="true">
          SO <span className="tab-note">ab Phase 8</span>
        </span>
      </nav>
      <Loading state={draft}>{(data) => <DraftView draft={data} propertyId={property.id} onChanged={draft.reload} />}</Loading>
      <p className="muted tax-footer">{FOOTER}</p>
    </>
  );
}

const loadOwners = () => ok(api.GET("/api/owners"));

function DraftView({ draft, propertyId, onChanged }: { draft: AnlageVDraft; propertyId: string; onChanged: () => void }) {
  const owners = useLoad(loadOwners);
  const lines = draft.lines ?? [];
  const income = lines.filter((line) => line.key.startsWith("income.") && line.key !== "income.total");
  const costs = lines.filter((line) => line.key.startsWith("wk.") && line.key !== "wk.total");
  const incomeTotal = lines.find((line) => line.key === "income.total");
  const costsTotal = lines.find((line) => line.key === "wk.total");
  const warnings = (draft.warnings ?? []).filter((w) => w.code !== "draft.form_provisional");
  const loadPayments = useCallback(
    () =>
      ok(
        api.GET("/api/properties/{property_id}/anlage-v/sources", {
          params: { path: { property_id: propertyId }, query: { tax_year: draft.tax_year } },
        }),
      ).then((rows) => new Map(rows.map((row) => [row.allocation_id, row]))),
    [propertyId, draft.tax_year],
  );
  const payments = useLoad(loadPayments);

  return (
    <PaymentsContext.Provider value={payments}>
      <div className="stack">
        {draft.rule_set.form_status === "provisional" ? (
          <div className="notice notice-info" role="status">
            <p>
              Das Formular für {draft.tax_year} ist noch nicht veröffentlicht: Zeilen und Kennzahlen sind vorläufig
              (Regelwerk {draft.rule_set.version}). Die Beträge rechnet der Entwurf trotzdem nach den Regeln des Jahres.
            </p>
          </div>
        ) : null}
        <div className="tax-layout">
          <div className="stack tax-main">
            <ProgressCard draft={draft} owners={owners.data} />
            <LinesCard
              title="Einnahmen"
              lines={income}
              total={incomeTotal}
              totalAmount={draft.totals.income}
              draft={draft}
              propertyId={propertyId}
            />
            <LinesCard
              title="Werbungskosten"
              lines={costs}
              total={costsTotal}
              totalAmount={draft.totals.costs}
              draft={draft}
              propertyId={propertyId}
            />
            <section className="card stack" aria-labelledby="tax-useful-life">
              <h2 id="tax-useful-life">Kürzere Nutzungsdauer (Gutachten)</h2>
              <UsefulLifeSection propertyId={propertyId} defaultYear={draft.tax_year} onChanged={onChanged} />
            </section>
            <NotDeductible draft={draft} propertyId={propertyId} />
          </div>
          <aside className="stack tax-side" aria-label="Offene Punkte und Unterlagen">
            <OpenPoints warnings={warnings} propertyId={propertyId} />
            <ChecklistTab propertyId={propertyId} year={draft.tax_year} />
            <LimitCard limit={draft.limit_15 ?? null} />
            <HandOverCard key={draft.tax_year} propertyId={propertyId} taxYear={draft.tax_year} />
          </aside>
      </div>
    </div>
    </PaymentsContext.Provider>
  );
}

// --- Progress and the three figures -------------------------------------------------------

function ProgressCard({ draft, owners }: { draft: AnlageVDraft; owners: Owner[] | undefined }) {
  const { months_owned: owned, months_covered: covered, covered_through: through } = draft.coverage;
  const complete = owned > 0 && covered >= owned;
  const parts: ReactNode[] = [];
  const transfer = draft.header.transfer_date;
  if (transfer.slice(0, 4) === String(draft.tax_year)) parts.push(`Eigentum seit ${monthYear(transfer)}`);
  if (draft.header.sold) parts.push(`verkauft ${formatDate(draft.header.sold)}`);
  if (!complete) parts.push(through ? `Auszüge abgestimmt bis ${formatDate(through)}` : "noch kein abgestimmter Kontoauszug");
  if (draft.excluded_open.count > 0) {
    const n = draft.excluded_open.count;
    parts.push(
      <Link key="open" href="/inbox">
        {n} {n === 1 ? "Zeile" : "Zeilen"} in Zu prüfen nicht enthalten
      </Link>,
    );
  }
  const ownerName = (id: string) => owners?.find((o) => o.id === id)?.full_name ?? "Eigentümer";

  return (
    <section className="card tax-progress" aria-label="Stand des Entwurfs">
      <div className="row">
        <strong>
          Monate abgestimmt {covered} von {owned}
        </strong>
        {complete ? <span className="badge">vollständig</span> : null}
      </div>
      <div
        className="tax-bar"
        role="progressbar"
        aria-label="Abgestimmte Monate"
        aria-valuemin={0}
        aria-valuemax={owned}
        aria-valuenow={covered}
      >
        <span style={{ width: owned > 0 ? `${Math.min(100, (covered / owned) * 100)}%` : "0%" }} />
      </div>
      {parts.length > 0 ? (
        <p className="muted tax-subline">
          {parts.map((part, index) => (
            <span key={index}>
              {index > 0 ? " · " : null}
              {part}
            </span>
          ))}
        </p>
      ) : null}
      <dl className="tax-figures">
        <div>
          <dt>Einnahmen</dt>
          <dd>{formatMoney(draft.totals.income)}</dd>
        </div>
        <div>
          <dt>Werbungskosten</dt>
          <dd>{formatMoney(draft.totals.costs)}</dd>
        </div>
        <div>
          <dt>Ergebnis</dt>
          <dd className={draft.totals.result.startsWith("-") ? "tax-negative" : undefined}>{formatMoney(draft.totals.result)}</dd>
        </div>
      </dl>
      {(draft.owners ?? []).length > 0 ? (
        <ul className="tax-owners">
          {(draft.owners ?? []).map((owner) => (
            <li key={owner.owner_id}>
              <span>
                {ownerName(owner.owner_id)} · Anteil {formatShare(owner.share)}
              </span>
              <span className="num">{formatMoney(owner.amount)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {draft.tax_free_from ? (
        <p className="muted tax-subline">§ 23: steuerfrei ab {formatDate(draft.tax_free_from)}</p>
      ) : null}
    </section>
  );
}

// --- Lines --------------------------------------------------------------------------------

/** Whether a line belongs on the screen: an amount other than zero, a ticked flag, or a
 * provisional line (shown even at 0,00 €, so the open Abrechnung is visible). */
function shown(line: DraftLine): boolean {
  return line.provisional || line.ticked === true || !isZero(line.amount);
}

function lineSublabel(line: DraftLine, draft: AnlageVDraft): string[] {
  const parts: string[] = [];
  const afa = draft.afa;
  if (line.key === "wk.afa" && afa && afa.method === "useful_life" && afa.useful_life_years != null) {
    // D79: a Gutachten's shorter useful life replaces the statutory rate.
    parts.push(`${afa.useful_life_years} Jahre ab ${afa.useful_life_from_year ?? "?"} (Gutachten)`);
    if (afa.book_value_start != null && afa.remaining_months != null) {
      parts.push(`Restwert ${formatMoney(afa.book_value_start)} × ${afa.months}/${afa.remaining_months} Monate Restnutzungsdauer`);
    }
    if (!isZero(afa.additions)) parts.push(`Basis + ${formatMoney(afa.additions)} Herstellungskosten`);
  } else if (line.key === "wk.afa" && afa && afa.basis != null && afa.rate != null) {
    parts.push(`${formatMoney(afa.basis)} × ${formatPercent(afa.rate)} × ${afa.months}/12`);
    if (!isZero(afa.additions)) parts.push(`Basis + ${formatMoney(afa.additions)} Herstellungskosten`);
  }
  if ((line.einzelangaben ?? []).length > 0) parts.push((line.einzelangaben ?? []).join(", "));
  for (const note of line.notes ?? []) parts.push(taxNote(note));
  return parts;
}

function formRef(line: DraftLine): string | null {
  const zeile = line.zeile == null ? null : line.zeile_from != null && line.zeile_from !== line.zeile ? `Z. ${line.zeile_from}–${line.zeile}` : `Z. ${line.zeile}`;
  const kennzahl = line.kennzahl ? `Kz. ${line.kennzahl}` : null;
  return [zeile, kennzahl].filter(Boolean).join(" · ") || null;
}

function LinesCard({
  title,
  lines,
  total,
  totalAmount,
  draft,
  propertyId,
}: {
  title: string;
  lines: DraftLine[];
  total: DraftLine | undefined;
  totalAmount: string;
  draft: AnlageVDraft;
  propertyId: string;
}) {
  const visible = lines.filter(shown);
  // The Z13 rows go under the per-unit rent line, or under the rent total without one.
  const unitRowsUnder = visible.some((line) => line.key === "income.rent_units") ? "income.rent_units" : "income.rent_total";
  return (
    <section className="card tax-lines" aria-label={title}>
      <h2>{title}</h2>
      {visible.length === 0 ? (
        <p className="muted">Keine Beträge in {draft.tax_year}.</p>
      ) : (
        <ul className="tax-line-list">
          {visible.map((line) => (
            <li key={line.key} className="tax-line">
              <div className="tax-line-main">
                <p className="tax-line-label">
                  {line.label}
                  {line.provisional ? <span className="badge badge-warn">vorläufig</span> : null}
                </p>
                {lineSublabel(line, draft).map((text, index) => (
                  <p key={index} className="tax-line-sub">
                    {text}
                  </p>
                ))}
                {formRef(line) ? <p className="tax-line-ref">{formRef(line)}</p> : null}
                {line.key === unitRowsUnder ? <UnitRows draft={draft} propertyId={propertyId} /> : null}
              </div>
              <div className="tax-line-amount">
                <span className="num">{line.amount == null ? (line.ticked ? "angekreuzt" : "–") : formatMoney(line.amount)}</span>
                <SourcesButton refs={line.sources ?? []} title={line.label} propertyId={propertyId} />
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="tax-line tax-total">
        <p className="tax-line-label">{total?.label ?? `Summe ${title}`}</p>
        <span className="num">{formatMoney(total?.amount ?? totalAmount)}</span>
      </div>
    </section>
  );
}

/** The Z13 rows (D71): one per Wohnung, with its rooms and the part booked before the
 * handover. */
function UnitRows({ draft, propertyId }: { draft: AnlageVDraft; propertyId: string }) {
  const rows = draft.unit_rows ?? [];
  if (rows.length === 0) return null;
  return (
    <ul className="tax-unit-rows">
      {rows.map((row) => (
        <li key={row.id}>
          <div className="row">
            <span>
              {row.label} · {formatDecimal(row.area_m2, 2)} m²
              {row.area_source === "units" ? " (Summe der Einheiten)" : ""}
            </span>
            <span className="spacer" />
            <span className="num">{formatMoney(row.amount)}</span>
            <SourcesButton refs={row.sources ?? []} title={row.label} propertyId={propertyId} />
          </div>
          {!isZero(row.pre_handover) ? <p className="tax-line-sub">davon vor dem Übergang {formatMoney(row.pre_handover)}</p> : null}
          {(row.rooms ?? []).length > 0 ? (
            <p className="tax-line-sub">
              {(row.rooms ?? []).map((room) => `${room.label} ${formatMoney(room.amount)}`).join(" · ")}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** The grey footer: booked but not deductible this year, payments the ten-day rule moved to
 * or from another year, and income before the handover not yet assigned. */
function NotDeductible({ draft, propertyId }: { draft: AnlageVDraft; propertyId: string }) {
  const tracked = (draft.tracked_not_deductible ?? []).filter((t) => !isZero(t.amount));
  const moved = draft.moved_to_other_year ?? [];
  const preHandover = draft.pre_handover ?? [];
  const preHandoverWarning = (draft.warnings ?? []).find((w) => w.code === "income.pre_handover");
  if (tracked.length === 0 && moved.length === 0 && preHandover.length === 0) return null;
  return (
    <section className="card tax-tracked" aria-label="Nicht abziehbar">
      {tracked.length > 0 ? (
        <div>
          <p className="tax-tracked-head">Erfasst, aber dieses Jahr nicht abziehbar</p>
          <ul>
            {tracked.map((t) => (
              <li key={t.category}>
                <span>
                  {ALLOCATION_CATEGORY[t.category]} · {t.count} {t.count === 1 ? "Zahlung" : "Zahlungen"}
                </span>
                <span className="num">{formatMoney(t.amount)}</span>
                <SourcesButton refs={t.sources ?? []} title={ALLOCATION_CATEGORY[t.category]} propertyId={propertyId} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {moved.length > 0 ? (
        <div>
          <p className="tax-tracked-head">Nach der Zehn-Tage-Regel in einem anderen Jahr</p>
          <ul>
            {moved.map((m) => (
              <li key={m.allocation_id}>
                <span>
                  {formatDate(m.booking_date)} · {ALLOCATION_CATEGORY[m.category]} · zählt in {m.tax_year}
                </span>
                <span className="num">{formatMoney(m.amount)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {preHandover.length > 0 ? (
        <div>
          <p className="tax-tracked-head">Vor dem Übergang</p>
          <ul>
            <li>
              <span>
                {preHandover.length} {preHandover.length === 1 ? "Einnahme" : "Einnahmen"} vor der Übergabe, noch nicht
                zugeordnet
              </span>
              <span className="num">{preHandoverWarning?.params?.amount ? formatMoney(preHandoverWarning.params.amount) : ""}</span>
              <SourcesButton refs={preHandover} title="Vor dem Übergang" propertyId={propertyId} />
            </li>
          </ul>
        </div>
      ) : null}
    </section>
  );
}

// --- Sources drawer -----------------------------------------------------------------------

function sourcesLabel(refs: TaxSourceRef[]): string {
  const payments = refs.filter((ref) => ref.kind === "allocation").length;
  if (payments > 0) return `${payments} ${payments === 1 ? "Zahlung" : "Zahlungen"}`;
  const [only] = refs;
  return refs.length === 1 && only ? TAX_SOURCE_KIND[only.kind] : `${refs.length} Quellen`;
}

/** The payments the draft's lines cite (GET …/anlage-v/sources), by allocation id. */
const PaymentsContext = createContext<Loaded<Map<string, AllocationFact>> | null>(null);

/** "01.01.2027 – 31.01.2027" for a service period. */
function periodLabel(fact: AllocationFact): string | null {
  const period = fact.service_period;
  return period ? `Zeitraum ${formatDate(period.start)} – ${formatDate(period.end)}` : null;
}

/** One cited payment: date, amount, category, masked counterparty and period, with links to
 * its bank line (the allocation panel, as Konten › Umsätze links it) and its Beleg. No name
 * or purpose text: the API sends none (D5). */
function PaymentSource({ source, fact }: { source: TaxSourceRef; fact: AllocationFact | undefined }) {
  if (!fact) {
    return (
      <>
        <span>{TAX_SOURCE_KIND[source.kind]}</span>
        {source.amount != null ? <span className="num">{formatMoney(source.amount)}</span> : null}
      </>
    );
  }
  const details = [fact.counterparty_iban_masked, periodLabel(fact)].filter(Boolean).join(" · ");
  return (
    <div className="tax-payment">
      <p className="tax-payment-head">
        <span>{formatDate(fact.booking_date)}</span>
        <span>{ALLOCATION_CATEGORY[fact.category]}</span>
        <span className="num">{formatMoney(source.amount ?? fact.amount)}</span>
      </p>
      {details ? <p className="tax-line-sub">{details}</p> : null}
      <p className="tax-payment-links">
        {fact.transaction_id ? <Link href={`/inbox?umsatz=${fact.transaction_id}`}>Bankzeile öffnen</Link> : null}
        {fact.document_id ? <Link href={`/documents/${fact.document_id}`}>Beleg öffnen</Link> : null}
      </p>
    </div>
  );
}

/** "n Zahlungen": opens the line's sources (principle 2). A payment shows its date, amount,
 * category, masked counterparty and period with links to its bank line and Beleg; documents,
 * loans and the purchase records link to their pages; the rest show their kind and the amount
 * the rule stated. */
export function SourcesButton({ refs, title, propertyId }: { refs: TaxSourceRef[]; title: string; propertyId: string }) {
  const payments = useContext(PaymentsContext);
  if (refs.length === 0) return null;
  const hasPayments = refs.some((ref) => ref.kind === "allocation");
  return (
    <ActionDialog label={sourcesLabel(refs)} title={`Quellen: ${title}`} buttonClass="link tax-sources-button">
      {() => (
        <>
          {hasPayments && payments?.error != null ? <ErrorNotice error={payments.error} /> : null}
          {hasPayments && payments?.data === undefined && payments?.loading ? <p className="muted">Lädt …</p> : null}
          <ul className="tax-sources">
            {refs.map((ref, index) => {
              const key = `${ref.kind}-${ref.id ?? "rule"}-${index}`;
              if (ref.kind === "allocation") {
                return (
                  <li key={key}>
                    <PaymentSource source={ref} fact={ref.id ? payments?.data?.get(ref.id) : undefined} />
                  </li>
                );
              }
              const link = linkFor(ref, propertyId);
              return (
                <li key={key}>
                  <span>{TAX_SOURCE_KIND[ref.kind]}</span>
                  {ref.amount != null ? <span className="num">{formatMoney(ref.amount)}</span> : null}
                  {link ? <Link href={link.href}>{link.label}</Link> : null}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </ActionDialog>
  );
}

// --- Right column -------------------------------------------------------------------------

function OpenPoints({ warnings, propertyId }: { warnings: DraftWarning[]; propertyId: string }) {
  // Warnings first, then information; within each, the order the draft gave.
  const sorted = [...warnings.filter((w) => w.severity === "warning"), ...warnings.filter((w) => w.severity === "info")];
  return (
    <section className="card tax-open" aria-labelledby="tax-open-title">
      <h2 id="tax-open-title">Offene Punkte</h2>
      {sorted.length === 0 ? (
        <p className="muted">Keine offenen Punkte.</p>
      ) : (
        <ul className="tax-open-list">
          {sorted.map((warning, index) => {
            const action = warningAction(warning, propertyId);
            const payments = (warning.refs ?? []).filter((ref) => ref.kind === "allocation");
            return (
              <li key={`${warning.code}-${index}`} className={`tax-open-item tax-open-${warning.severity}`}>
                <span className={warning.severity === "warning" ? "badge badge-warn" : "badge badge-info"}>
                  {warning.severity === "warning" ? "Prüfen" : "Hinweis"}
                </span>
                <p>{WARNING_TEXT[warning.code](warning.params ?? {})}</p>
                {action || payments.length > 0 ? (
                  <p className="tax-open-actions">
                    {action ? <Link href={action.href}>{action.label}</Link> : null}
                    {payments.length > 0 ? (
                      <SourcesButton refs={warning.refs ?? []} title="Offener Punkt" propertyId={propertyId} />
                    ) : null}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function LimitCard({ limit }: { limit: LimitStatus | null }) {
  if (!limit) return null;
  const estimated = (limit.counted ?? []).some((c) => c.net_source === "estimated");
  const ratio = limit.ratio_percent;
  return (
    <section className="card tax-limit" aria-labelledby="tax-limit-title">
      <h2 id="tax-limit-title">15 %-Grenze</h2>
      {limit.limit == null ? (
        <p className="muted">Ohne gewählte Kaufpreisaufteilung lässt sich die Grenze nicht berechnen.</p>
      ) : (
        <>
          <div
            className={`tax-bar${limit.exceeded ? " tax-bar-over" : ""}`}
            role="progressbar"
            aria-label="Genutzter Anteil der 15 %-Grenze"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={ratio == null ? undefined : Math.min(100, Number(ratio))}
          >
            <span style={{ width: ratio == null ? "0%" : `${Math.min(100, Number(ratio))}%` }} />
          </div>
          <dl className="facts tax-limit-facts">
            <div>
              <dt>Genutzt (netto)</dt>
              <dd>
                {formatMoney(limit.used_net)}
                {ratio != null ? ` · ${formatDecimal(ratio)} %` : ""}
              </dd>
            </div>
            <div>
              <dt>Grenze</dt>
              <dd>{formatMoney(limit.limit)}</dd>
            </div>
            <div>
              <dt>Fenster</dt>
              <dd>
                {formatDate(limit.window_start)} bis {formatDate(limit.window_end)}
              </dd>
            </div>
          </dl>
          {limit.exceeded ? (
            <p className="tax-limit-note">
              Überschritten{limit.exceeded_in_year ? ` in ${limit.exceeded_in_year}` : ""}: die Kosten zählen als
              Herstellungskosten zur AfA.
            </p>
          ) : null}
          {estimated ? <p className="muted tax-limit-note">Enthält geschätzte Nettobeträge (Rechnung ohne Nettobetrag).</p> : null}
        </>
      )}
    </section>
  );
}

// --- § 23 in the property header (phase-3.md §11, D68) -------------------------------------
// The date comes from the draft (rules/tax/spekulation.py), never computed here: the current
// (Berlin) year's draft, or last year's when this year has no rule set yet. Nothing is shown
// when neither draft can be computed or the property has no notarized date.

async function taxFreeFrom(propertyId: string): Promise<string | null> {
  const year = Number(todayIso().slice(0, 4));
  for (const taxYear of [year, year - 1]) {
    try {
      const draft = await ok(
        api.GET("/api/properties/{property_id}/anlage-v", {
          params: { path: { property_id: propertyId }, query: { tax_year: taxYear } },
        }),
      );
      return draft.tax_free_from ?? null;
    } catch {
      // a year without a draft (before the purchase, no rule set yet): try the one before
    }
  }
  return null;
}

export function TaxFreeLine({ propertyId }: { propertyId: string }) {
  const load = useCallback(() => taxFreeFrom(propertyId), [propertyId]);
  const date = useLoad(load).data;
  return date ? <span className="muted">§ 23: steuerfrei ab {formatDate(date)}</span> : null;
}

// --- Übergabe: the exports (phase-3.md §10, §12) -------------------------------------------
// POST queues the export; the card polls GET /tax-exports/{id} until it is ready or failed,
// then starts the download. A download URL is signed and expires after a few minutes, so it
// is fetched right before each download, never kept.

const EXPORT_KIND: Record<TaxExportKind, string> = {
  field_sheet: "Ausfüllhilfe (PDF)",
  archive: "Jahresarchiv (ZIP)",
};
const EXPORT_POLL_MS = 2000;

async function downloadExport(exportId: string): Promise<void> {
  const state = await ok(api.GET("/api/tax-exports/{export_id}", { params: { path: { export_id: exportId } } }));
  if (state.download_url) window.location.assign(state.download_url);
}

function exportVersion(item: TaxExport): string {
  const form = item.form_status === "published" ? `Formular ${item.form_year}` : `Formular ${item.form_year}, vorläufig`;
  return `Regelwerk ${item.rule_set_version} · ${form}`;
}

function HandOverCard({ propertyId, taxYear }: { propertyId: string; taxYear: number }) {
  const loadHistory = useCallback(
    () =>
      ok(
        api.GET("/api/properties/{property_id}/anlage-v/exports", {
          params: { path: { property_id: propertyId }, query: { tax_year: taxYear } },
        }),
      ),
    [propertyId, taxYear],
  );
  const history = useLoad(loadHistory);
  const reloadHistory = history.reload;
  const [pending, setPending] = useState<TaxExportState | null>(null);
  const [failedCode, setFailedCode] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function start(kind: TaxExportKind) {
    setError(null);
    setFailedCode(null);
    try {
      const queued = await ok(
        api.POST("/api/properties/{property_id}/anlage-v/exports", {
          params: { path: { property_id: propertyId } },
          body: { tax_year: taxYear, kind },
        }),
      );
      setPending({ id: queued.export_id, property_id: propertyId, tax_year: taxYear, kind, status: "queued" });
    } catch (caught) {
      setError(caught);
    }
  }

  // Each answer is a new object, so the effect re-arms after every poll while queued.
  useEffect(() => {
    if (!pending || pending.status !== "queued") return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      ok(api.GET("/api/tax-exports/{export_id}", { params: { path: { export_id: pending.id } } })).then(
        (next) => {
          if (cancelled) return;
          if (next.status === "queued") {
            setPending(next);
            return;
          }
          setPending(null);
          reloadHistory();
          if (next.status === "failed") setFailedCode(next.error_code ?? "tax_export.failed");
          else if (next.download_url) window.location.assign(next.download_url);
        },
        (caught: unknown) => {
          if (cancelled) return;
          setPending(null);
          setError(caught);
        },
      );
    }, EXPORT_POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pending, reloadHistory]);

  async function download(exportId: string) {
    setError(null);
    try {
      await downloadExport(exportId);
    } catch (caught) {
      setError(caught);
    }
  }

  const busy = pending !== null;
  const items = history.data ?? [];
  return (
    <section className="card tax-handover" aria-labelledby="tax-handover-title">
      <h2 id="tax-handover-title">Übergabe</h2>
      <div className="row">
        <button type="button" disabled={busy} onClick={() => void start("field_sheet")}>
          Ausfüllhilfe PDF
        </button>
        <button type="button" className="secondary" disabled={busy} onClick={() => void start("archive")}>
          Archiv ZIP
        </button>
      </div>
      <p className="muted tax-handover-note">
        Die Ausfüllhilfe sagt, was in welche Zeile in ELSTER gehört. Das Archiv enthält sie, die Zeilen und Zahlungen
        als CSV und die Belege des Jahres im Original.
      </p>
      {pending ? (
        <p className="tax-handover-status" role="status">
          {EXPORT_KIND[pending.kind]} wird erstellt …
        </p>
      ) : null}
      {failedCode ? (
        <div className="notice notice-error" role="alert">
          <p>{RULE_CODE_TEXT[failedCode] ?? RULE_CODE_TEXT["tax_export.failed"]}</p>
        </div>
      ) : null}
      <ErrorNotice error={error ?? history.error} />
      {items.length > 0 ? (
        <>
          <h3 className="tax-handover-history-title">Bisherige Exporte</h3>
          <ul className="tax-handover-list">
            {items.map((item) => (
              <li key={item.id} className="tax-handover-item">
                <div>
                  <p>
                    {EXPORT_KIND[item.kind]} · {formatDateTime(item.created_at)}
                  </p>
                  <p className="muted">{exportVersion(item)}</p>
                </div>
                <button type="button" className="link" onClick={() => void download(item.id)}>
                  Herunterladen
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
