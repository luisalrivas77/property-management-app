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
} from "@/api/schemas";
import { ChecklistTab } from "@/app/(app)/properties/[id]/ChecklistTab";
import { formatter, localized, translate, useFormat, useT } from "@/i18n";
import { common } from "@/i18n/common";
import { taxDraft } from "@/i18n/taxDraft";
import { ErrorNotice } from "@/lib/ErrorNotice";

import { formatShare, todayIso } from "./format";
import { TaxEffectLine } from "./income";
import { ALLOCATION_CATEGORY, TAX_SOURCE_KIND, WARNING_TEXT, taxNote } from "./labels";
import { ActionDialog, Loading } from "./ui";
import { GrundsteuerSection, MovableAssetSection, UsefulLifeSection } from "./taxInputs";
import { AnlageVTodos, type GuideContext, WarningActionView, WhatToDo, resolveAction } from "./todos";
import { useLoad, type Loaded } from "./useLoad";

import "./tax.css";

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

/** Where a source can be opened today. Payments, transactions, tax inputs, return inputs and
 * the rule sets have no page of their own; they are listed in the sources drawer only. A
 * property ref opens its own property when it names one (the Gesamt card cites several), else
 * the screen's. */
export function linkFor(ref: TaxSourceRef, propertyId?: string): Action | null {
  const t = translate(taxDraft);
  const property = (ref.kind === "property" ? ref.id : null) ?? propertyId;
  switch (ref.kind) {
    case "document":
      return ref.id ? { href: `/documents/${ref.id}`, label: t.openDocument } : null;
    case "loan":
      return ref.id ? { href: `/loans/${ref.id}`, label: t.openLoan } : null;
    case "property":
      return property ? { href: `/properties/${property}?tab=uebersicht`, label: t.masterData } : null;
    case "price_split":
      return property ? { href: `/properties/${property}?tab=kauf#card-aufteilung`, label: t.priceSplit } : null;
    case "acquisition_cost":
      return property ? { href: `/properties/${property}?tab=kauf`, label: t.acquisitionCosts } : null;
    case "income_input":
      // The household's pay inputs, on Steuern › Eure Angaben since phase 10 D281.
      return { href: "/tax?tab=angaben", label: t.seeIncome };
    default:
      return null;
  }
}

/** A warning without an action of its own in components/todos.tsx (D136) still opens the first
 * source it cites that has a page. */
function refAction(warning: DraftWarning, propertyId: string): Action | null {
  for (const ref of warning.refs ?? []) {
    const link = linkFor(ref, propertyId);
    if (link) return link;
  }
  return null;
}

// --- The screen ---------------------------------------------------------------------------

/** `summary` (the household's Gesamt card, phase-4b.md §10) goes between the header and the
 * tabs; `tabs` replaces the plain tab bar (the page adds Anlage N and the Jahresfragen). */
export function AnlageVScreen({
  property,
  properties,
  year,
  summary,
  tabs,
}: {
  property: Property;
  properties: Property[];
  year: number;
  summary?: ReactNode;
  tabs?: ReactNode;
}) {
  const t = useT(taxDraft);
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
          <p className="eyebrow">{t.eyebrow(year)}</p>
          <h1>{property.label}</h1>
          <span className="muted">{addressLine(property)}</span>
        </div>
        <span className="spacer" />
        <div className="row tax-pickers">
          {properties.length > 1 ? (
            <label htmlFor={`${ids}-property`} className="inline">
              {t.property}
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
            {t.taxYear}
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
      {summary}
      {tabs ?? (
        <nav aria-label={t.forms} className="tabs">
          <Link href={href(year, property.id)} aria-current="page" scroll={false}>
            Anlage V
          </Link>
          <span className="tab-disabled" aria-disabled="true">
            KAP <span className="tab-note">{t.fromPhase8}</span>
          </span>
          <span className="tab-disabled" aria-disabled="true">
            SO <span className="tab-note">{t.fromPhase8}</span>
          </span>
        </nav>
      )}
      <Loading state={draft}>{(data) => <DraftView draft={data} propertyId={property.id} onChanged={draft.reload} />}</Loading>
      {/* Outside the draft (PR #139 review): a reminder links here for a year whose rule set is
          not published yet (2027 until the data PR, D124), and the schedule row is the only
          place its amount is shown (D122). */}
      <section className="card stack" aria-labelledby="tax-grundsteuer-title" id="tax-grundsteuer">
        <h2 id="tax-grundsteuer-title">{t.grundsteuer}</h2>
        <GrundsteuerSection propertyId={property.id} defaultYear={year} />
      </section>
      <p className="muted tax-footer">{t.footer}</p>
    </>
  );
}

const loadOwners = () => ok(api.GET("/api/owners"));

function DraftView({ draft, propertyId, onChanged }: { draft: AnlageVDraft; propertyId: string; onChanged: () => void }) {
  const t = useT(taxDraft);
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
  // An action of a to-do (a split selected, a document read) changes the checklist and the
  // questions too, which load on their own; the counter reloads them with the draft. The cited
  // payments reload too: a re-booking (D140) supersedes every row of its bank line, so a to-do
  // left on that line cites new allocation ids.
  const [refresh, setRefresh] = useState(0);
  const reloadPayments = payments.reload;
  const changed = useCallback(() => {
    setRefresh((n) => n + 1);
    reloadPayments();
    onChanged();
  }, [onChanged, reloadPayments]);
  const ctx = { year: draft.tax_year, propertyId, draft, payments: payments.data };

  return (
    <PaymentsContext.Provider value={payments}>
      <div className="stack">
        <AnlageVTodos
          propertyId={propertyId}
          year={draft.tax_year}
          draft={draft}
          payments={payments.data}
          refresh={refresh}
          onChanged={changed}
        />
        {draft.rule_set.form_status === "provisional" ? (
          <div className="notice notice-info" role="status">
            <p>{t.provisional(draft.tax_year, draft.rule_set.version)}</p>
          </div>
        ) : null}
        <div className="tax-layout">
          <div className="stack tax-main">
            <ProgressCard draft={draft} owners={owners.data} />
            <LinesCard
              title={t.income}
              lines={income}
              total={incomeTotal}
              totalAmount={draft.totals.income}
              draft={draft}
              propertyId={propertyId}
            />
            <LinesCard
              title={t.costs}
              lines={costs}
              total={costsTotal}
              totalAmount={draft.totals.costs}
              draft={draft}
              propertyId={propertyId}
            />
            <section className="card stack" aria-labelledby="tax-useful-life">
              <h2 id="tax-useful-life">{t.usefulLife}</h2>
              <UsefulLifeSection propertyId={propertyId} defaultYear={draft.tax_year} onChanged={changed} />
            </section>
            <section className="card stack" aria-labelledby="tax-movable-asset">
              <h2 id="tax-movable-asset">{t.movableAsset}</h2>
              <MovableAssetSection
                propertyId={propertyId}
                purchaseYear={Number(draft.header.transfer_date.slice(0, 4)) || draft.tax_year}
                onChanged={changed}
              />
            </section>
            <NotDeductible draft={draft} propertyId={propertyId} />
          </div>
          <aside className="stack tax-side" aria-label={t.sideLabel}>
            <OpenPoints warnings={warnings} ctx={ctx} onChanged={changed} />
            <ChecklistTab
              propertyId={propertyId}
              year={draft.tax_year}
              refresh={refresh}
              payments={payments.data}
              onChanged={changed}
            />
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
  const t = useT(taxDraft);
  const f = useFormat();
  const { months_owned: owned, months_covered: covered, covered_through: through } = draft.coverage;
  const complete = owned > 0 && covered >= owned;
  const parts: ReactNode[] = [];
  const transfer = draft.header.transfer_date;
  if (transfer.slice(0, 4) === String(draft.tax_year)) parts.push(t.ownedSince(monthYear(transfer)));
  if (draft.header.sold) parts.push(t.sold(f.date(draft.header.sold)));
  if (!complete) parts.push(through ? t.reconciledThrough(f.date(through)) : t.noReconciled);
  if (draft.excluded_open.count > 0) {
    const n = draft.excluded_open.count;
    parts.push(
      <Link key="open" href="/inbox">
        {t.excludedOpen(n)}
      </Link>,
    );
  }
  const ownerName = (id: string) => owners?.find((o) => o.id === id)?.full_name ?? t.owner;

  return (
    <section className="card tax-progress" aria-label={t.progressLabel}>
      <div className="row">
        <strong>{t.monthsReconciled(covered, owned)}</strong>
        {complete ? <span className="badge">{t.complete}</span> : null}
      </div>
      <div
        className="tax-bar"
        role="progressbar"
        aria-label={t.reconciledMonths}
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
          <dt>{t.income}</dt>
          <dd>{f.money(draft.totals.income)}</dd>
        </div>
        <div>
          <dt>{t.costs}</dt>
          <dd>{f.money(draft.totals.costs)}</dd>
        </div>
        <div>
          <dt>{t.result}</dt>
          <dd className={draft.totals.result.startsWith("-") ? "tax-negative" : undefined}>{f.money(draft.totals.result)}</dd>
        </div>
      </dl>
      <TaxEffectLine propertyId={draft.property_id} taxYear={draft.tax_year} result={draft.totals.result} />
      {(draft.owners ?? []).length > 0 ? (
        <ul className="tax-owners">
          {(draft.owners ?? []).map((owner) => (
            <li key={owner.owner_id}>
              <span>{t.ownerShare(ownerName(owner.owner_id), formatShare(owner.share))}</span>
              <span className="num">{f.money(owner.amount)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {draft.tax_free_from ? (
        <p className="muted tax-subline">{t.taxFreeFrom(f.date(draft.tax_free_from))}</p>
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
  const t = translate(taxDraft);
  const f = formatter();
  const parts: string[] = [];
  const afa = draft.afa;
  if (line.key === "wk.afa" && afa && afa.method === "useful_life" && afa.useful_life_years != null) {
    // D79: a Gutachten's shorter useful life replaces the statutory rate.
    parts.push(t.usefulLifeYears(afa.useful_life_years, String(afa.useful_life_from_year ?? "?")));
    if (afa.book_value_start != null && afa.remaining_months != null) {
      if (afa.months >= afa.remaining_months) {
        // the last year of the life takes the whole rest; after it nothing is left
        parts.push(t.residualWrittenOff(f.money(afa.book_value_start)));
      } else {
        parts.push(t.residualShare(f.money(afa.book_value_start), afa.months, afa.remaining_months));
      }
    }
    if (!isZero(afa.additions)) parts.push(t.basisAdditions(f.money(afa.additions)));
  } else if (line.key === "wk.afa" && afa && afa.basis != null && afa.rate != null) {
    parts.push(`${f.money(afa.basis)} × ${f.percent(afa.rate)} × ${afa.months}/12`);
    if (!isZero(afa.additions)) parts.push(t.basisAdditions(f.money(afa.additions)));
  }
  if (line.key === "wk.afa" && afa && !isZero(afa.movable_deducted ?? "0")) {
    // D80: the kitchen bought with the flat left the building's basis
    parts.push(t.basisWithoutKitchen(f.money(afa.movable_deducted ?? "0")));
  }
  if (line.key === "wk.afa_movable") {
    for (const asset of draft.movable_assets ?? []) {
      parts.push(
        asset.months >= asset.remaining_months
          ? t.kitchenWrittenOff(f.money(asset.book_value_start))
          : t.kitchenShare(f.money(asset.basis), asset.useful_life_years, asset.months),
      );
    }
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
  const t = useT(taxDraft);
  const f = useFormat();
  const visible = lines.filter(shown);
  // The Z13 rows go under the per-unit rent line, or under the rent total without one.
  const unitRowsUnder = visible.some((line) => line.key === "income.rent_units") ? "income.rent_units" : "income.rent_total";
  return (
    <section className="card tax-lines" aria-label={title}>
      <h2>{title}</h2>
      {visible.length === 0 ? (
        <p className="muted">{t.noAmounts(draft.tax_year)}</p>
      ) : (
        <ul className="tax-line-list">
          {visible.map((line) => (
            <li key={line.key} className="tax-line">
              <div className="tax-line-main">
                <p className="tax-line-label">
                  {line.label}
                  {line.provisional ? <span className="badge badge-warn">{t.provisionalBadge}</span> : null}
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
                <span className="num">{line.amount == null ? (line.ticked ? t.ticked : "–") : f.money(line.amount)}</span>
                <SourcesButton refs={line.sources ?? []} title={line.label} propertyId={propertyId} />
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="tax-line tax-total">
        <p className="tax-line-label">{total?.label ?? t.total(title)}</p>
        <span className="num">{f.money(total?.amount ?? totalAmount)}</span>
      </div>
    </section>
  );
}

/** The Z13 rows (D71): one per Wohnung, with its rooms and the part booked before the
 * handover. */
function UnitRows({ draft, propertyId }: { draft: AnlageVDraft; propertyId: string }) {
  const t = useT(taxDraft);
  const f = useFormat();
  const rows = draft.unit_rows ?? [];
  if (rows.length === 0) return null;
  return (
    <ul className="tax-unit-rows">
      {rows.map((row) => (
        <li key={row.id}>
          <div className="row">
            <span>
              {row.area_m2 != null ? (
                <>
                  {row.label} · {f.decimal(row.area_m2, 2)} m²
                  {row.area_source === "units" ? t.unitsSum : ""}
                </>
              ) : (
                t.areaMissing(row.label)
              )}
            </span>
            <span className="spacer" />
            <span className="num">{f.money(row.amount)}</span>
            <SourcesButton refs={row.sources ?? []} title={row.label} propertyId={propertyId} />
          </div>
          {!isZero(row.pre_handover) ? <p className="tax-line-sub">{t.preHandoverPart(f.money(row.pre_handover))}</p> : null}
          {(row.rooms ?? []).length > 0 ? (
            <p className="tax-line-sub">
              {(row.rooms ?? []).map((room) => `${room.label} ${f.money(room.amount)}`).join(" · ")}
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
  const t = useT(taxDraft);
  const f = useFormat();
  const tracked = (draft.tracked_not_deductible ?? []).filter((item) => !isZero(item.amount));
  const moved = draft.moved_to_other_year ?? [];
  const preHandover = draft.pre_handover ?? [];
  const preHandoverWarning = (draft.warnings ?? []).find((w) => w.code === "income.pre_handover");
  if (tracked.length === 0 && moved.length === 0 && preHandover.length === 0) return null;
  return (
    <section className="card tax-tracked" aria-label={t.notDeductible}>
      {tracked.length > 0 ? (
        <div>
          <p className="tax-tracked-head">{t.trackedHead}</p>
          <ul>
            {tracked.map((item) => (
              <li key={item.category}>
                <span>
                  {ALLOCATION_CATEGORY[item.category]} · {t.payments(item.count)}
                </span>
                <span className="num">{f.money(item.amount)}</span>
                <SourcesButton refs={item.sources ?? []} title={ALLOCATION_CATEGORY[item.category]} propertyId={propertyId} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {moved.length > 0 ? (
        <div>
          <p className="tax-tracked-head">{t.movedHead}</p>
          <ul>
            {moved.map((m) => (
              <li key={m.allocation_id}>
                <span>
                  {f.date(m.booking_date)} · {ALLOCATION_CATEGORY[m.category]} · {t.countsIn(m.tax_year)}
                </span>
                <span className="num">{f.money(m.amount)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {preHandover.length > 0 ? (
        <div>
          <p className="tax-tracked-head">{t.preHandoverHead}</p>
          <ul>
            <li>
              <span>{t.preHandoverIncome(preHandover.length)}</span>
              <span className="num">{preHandoverWarning?.params?.amount ? f.money(preHandoverWarning.params.amount) : ""}</span>
              <SourcesButton refs={preHandover} title={t.preHandoverHead} propertyId={propertyId} />
            </li>
          </ul>
        </div>
      ) : null}
    </section>
  );
}

// --- Sources drawer -----------------------------------------------------------------------

function sourcesLabel(refs: TaxSourceRef[]): string {
  const t = translate(taxDraft);
  const payments = refs.filter((ref) => ref.kind === "allocation").length;
  if (payments > 0) return t.payments(payments);
  const [only] = refs;
  return refs.length === 1 && only ? TAX_SOURCE_KIND[only.kind] : t.sources(refs.length);
}

/** The payments the draft's lines cite (GET …/anlage-v/sources), by allocation id. */
const PaymentsContext = createContext<Loaded<Map<string, AllocationFact>> | null>(null);

/** "01.01.2027 – 31.01.2027" for a service period. */
function periodLabel(fact: AllocationFact): string | null {
  const f = formatter();
  const period = fact.service_period;
  return period ? translate(taxDraft).period(f.date(period.start), f.date(period.end)) : null;
}

/** One cited payment: date, amount, category, masked counterparty and period, with links to
 * its bank line (the allocation panel, as Konten › Umsätze links it) and its Beleg. No name
 * or purpose text: the API sends none (D5). */
function PaymentSource({ source, fact }: { source: TaxSourceRef; fact: AllocationFact | undefined }) {
  const t = useT(taxDraft);
  const f = useFormat();
  if (!fact) {
    return (
      <>
        <span>{TAX_SOURCE_KIND[source.kind]}</span>
        {source.amount != null ? <span className="num">{f.money(source.amount)}</span> : null}
      </>
    );
  }
  const details = [fact.counterparty_iban_masked, periodLabel(fact)].filter(Boolean).join(" · ");
  return (
    <div className="tax-payment">
      <p className="tax-payment-head">
        <span>{f.date(fact.booking_date)}</span>
        <span>{ALLOCATION_CATEGORY[fact.category]}</span>
        <span className="num">{f.money(source.amount ?? fact.amount)}</span>
      </p>
      {details ? <p className="tax-line-sub">{details}</p> : null}
      <p className="tax-payment-links">
        {fact.transaction_id ? <Link href={`/inbox?umsatz=${fact.transaction_id}`}>{t.openBankLine}</Link> : null}
        {fact.document_id ? <Link href={`/documents/${fact.document_id}`}>{t.openReceipt}</Link> : null}
      </p>
    </div>
  );
}

/** "n Zahlungen": opens the line's sources (principle 2). A payment shows its date, amount,
 * category, masked counterparty and period with links to its bank line and Beleg; documents,
 * loans and the purchase records link to their pages; the rest show their kind and the amount
 * the rule stated. */
export function SourcesButton({ refs, title, propertyId }: { refs: TaxSourceRef[]; title: string; propertyId?: string }) {
  const t = useT(taxDraft);
  const c = useT(common);
  const f = useFormat();
  const payments = useContext(PaymentsContext);
  if (refs.length === 0) return null;
  const hasPayments = refs.some((ref) => ref.kind === "allocation");
  return (
    <ActionDialog label={sourcesLabel(refs)} title={t.sourcesTitle(title)} buttonClass="link tax-sources-button">
      {() => (
        <>
          {hasPayments && payments?.error != null ? <ErrorNotice error={payments.error} /> : null}
          {hasPayments && payments?.data === undefined && payments?.loading ? <p className="muted">{c.loading}</p> : null}
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
                  {ref.amount != null ? <span className="num">{f.money(ref.amount)}</span> : null}
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

/** Offene Punkte: every warning with what the rule found, what to do (D136) and its action; a
 * to-do is also listed under "Was noch fehlt" at the top of the tab. */
function OpenPoints({
  warnings,
  ctx,
  onChanged,
}: {
  warnings: DraftWarning[];
  ctx: GuideContext & { propertyId: string };
  onChanged: () => void;
}) {
  const t = useT(taxDraft);
  // Warnings first, then information; within each, the order the draft gave.
  const sorted = [...warnings.filter((w) => w.severity === "warning"), ...warnings.filter((w) => w.severity === "info")];
  return (
    <section className="card tax-open" aria-labelledby="tax-open-title">
      <h2 id="tax-open-title">{t.openPoints}</h2>
      {sorted.length === 0 ? (
        <p className="muted">{t.noOpenPoints}</p>
      ) : (
        <ul className="tax-open-list">
          {sorted.map((warning, index) => {
            const guided = resolveAction(warning, ctx) !== null;
            const fallback = guided ? null : refAction(warning, ctx.propertyId);
            const payments = (warning.refs ?? []).filter((ref) => ref.kind === "allocation");
            return (
              <li key={`${warning.code}-${index}`} className={`tax-open-item tax-open-${warning.severity}`}>
                <span className={warning.severity === "warning" ? "badge badge-warn" : "badge badge-info"}>
                  {warning.severity === "warning" ? t.check : t.note}
                </span>
                <p>{WARNING_TEXT[warning.code](warning.params ?? {})}</p>
                <WhatToDo warning={warning} />
                {guided || fallback || payments.length > 0 ? (
                  <div className="row tax-open-actions">
                    {guided ? <WarningActionView warning={warning} ctx={ctx} onChanged={onChanged} /> : null}
                    {fallback ? <Link href={fallback.href}>{fallback.label}</Link> : null}
                    {payments.length > 0 ? (
                      <SourcesButton refs={warning.refs ?? []} title={t.openPoint} propertyId={ctx.propertyId} />
                    ) : null}
                  </div>
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
  const t = useT(taxDraft);
  const f = useFormat();
  if (!limit) return null;
  const estimated = (limit.counted ?? []).some((c) => c.net_source === "estimated");
  const ratio = limit.ratio_percent;
  return (
    <section className="card tax-limit" aria-labelledby="tax-limit-title">
      <h2 id="tax-limit-title">{t.limitTitle}</h2>
      {limit.limit == null ? (
        <p className="muted">{t.limitNoSplit}</p>
      ) : (
        <>
          <div
            className={`tax-bar${limit.exceeded ? " tax-bar-over" : ""}`}
            role="progressbar"
            aria-label={t.limitUsedLabel}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={ratio == null ? undefined : Math.min(100, Number(ratio))}
          >
            <span style={{ width: ratio == null ? "0%" : `${Math.min(100, Number(ratio))}%` }} />
          </div>
          <dl className="facts tax-limit-facts">
            <div>
              <dt>{t.limitUsed}</dt>
              <dd>
                {f.money(limit.used_net)}
                {ratio != null ? t.limitRatio(f.decimal(ratio)) : ""}
              </dd>
            </div>
            <div>
              <dt>{t.limit}</dt>
              <dd>{f.money(limit.limit)}</dd>
            </div>
            <div>
              <dt>{t.limitWindow}</dt>
              <dd>{t.limitWindowRange(f.date(limit.window_start), f.date(limit.window_end))}</dd>
            </div>
          </dl>
          {limit.exceeded ? (
            <p className="tax-limit-note">{t.limitExceeded(limit.exceeded_in_year ?? null)}</p>
          ) : null}
          {estimated ? <p className="muted tax-limit-note">{t.limitEstimated}</p> : null}
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
  const t = useT(taxDraft);
  const f = useFormat();
  const date = useLoad(load).data;
  return date ? <span className="muted">{t.taxFreeFrom(f.date(date))}</span> : null;
}

// --- Übergabe: the exports (phase-3.md §10, §12) -------------------------------------------
// POST queues the export; the card polls GET /tax-exports/{id} until it is ready or failed,
// then starts the download. A download URL is signed and expires after a few minutes, so it
// is fetched right before each download, never kept.

const EXPORT_KIND: Record<TaxExportKind, string> = localized<TaxExportKind>(
  {
    field_sheet: "Ausfüllhilfe (PDF)",
    archive: "Jahresarchiv (ZIP)",
    anlage_n_sheet: "Feldblatt Anlage N (PDF)",
  },
  {
    field_sheet: "ELSTER field sheet (PDF)",
    archive: "Yearly archive (ZIP)",
    anlage_n_sheet: "Field sheet Anlage N (PDF)",
  },
);
const EXPORT_POLL_MS = 2000;

async function downloadExport(exportId: string): Promise<void> {
  const state = await ok(api.GET("/api/tax-exports/{export_id}", { params: { path: { export_id: exportId } } }));
  if (state.download_url) window.location.assign(state.download_url);
}

function exportVersion(item: TaxExport): string {
  const t = translate(taxDraft);
  const form = item.form_status === "published" ? t.form(item.form_year) : t.formProvisional(item.form_year);
  return t.exportVersion(item.rule_set_version, form);
}

export type ExportButton = { kind: TaxExportKind; label: string; secondary?: boolean };

/** The Übergabe card of a tax screen: its export buttons, the running export's status (polled
 * until ready or failed, then downloaded) and the stored exports. `loadHistory` and `queue`
 * must be stable (useCallback); `queue` posts one export and answers its id. Used for a
 * property's Anlage V exports and an owner's Anlage N sheet (phase 4b D104). */
export function ExportCard({
  titleId,
  taxYear,
  buttons,
  note,
  disabled = false,
  loadHistory,
  queue,
}: {
  titleId: string;
  taxYear: number;
  buttons: ExportButton[];
  note: ReactNode;
  disabled?: boolean;
  loadHistory: () => Promise<TaxExport[]>;
  queue: (kind: TaxExportKind) => Promise<string>;
}) {
  const t = useT(taxDraft);
  const f = useFormat();
  const history = useLoad(loadHistory);
  const reloadHistory = history.reload;
  const [pending, setPending] = useState<TaxExportState | null>(null);
  const [failedCode, setFailedCode] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function start(kind: TaxExportKind) {
    setError(null);
    setFailedCode(null);
    try {
      const exportId = await queue(kind);
      setPending({ id: exportId, tax_year: taxYear, kind, status: "queued" });
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

  const busy = pending !== null || disabled;
  const items = history.data ?? [];
  return (
    <section className="card tax-handover" aria-labelledby={titleId}>
      <h2 id={titleId}>{t.handOver}</h2>
      <div className="row">
        {buttons.map((button) => (
          <button
            key={button.kind}
            type="button"
            className={button.secondary ? "secondary" : undefined}
            disabled={busy}
            onClick={() => void start(button.kind)}
          >
            {button.label}
          </button>
        ))}
      </div>
      <p className="muted tax-handover-note">{note}</p>
      {pending ? (
        <p className="tax-handover-status" role="status">
          {t.exportRunning(EXPORT_KIND[pending.kind])}
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
          <h3 className="tax-handover-history-title">{t.previousExports}</h3>
          <ul className="tax-handover-list">
            {items.map((item) => (
              <li key={item.id} className="tax-handover-item">
                <div>
                  <p>
                    {EXPORT_KIND[item.kind]} · {f.dateTime(item.created_at)}
                  </p>
                  <p className="muted">{exportVersion(item)}</p>
                </div>
                <button type="button" className="link" onClick={() => void download(item.id)}>
                  {t.download}
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

function HandOverCard({ propertyId, taxYear }: { propertyId: string; taxYear: number }) {
  const t = useT(taxDraft);
  const buttons: ExportButton[] = [
    { kind: "field_sheet", label: t.fieldSheetButton },
    { kind: "archive", label: t.archiveButton, secondary: true },
  ];
  const loadHistory = useCallback(
    () =>
      ok(
        api.GET("/api/properties/{property_id}/anlage-v/exports", {
          params: { path: { property_id: propertyId }, query: { tax_year: taxYear } },
        }),
      ),
    [propertyId, taxYear],
  );
  const queue = useCallback(
    async (kind: TaxExportKind) => {
      const queued = await ok(
        api.POST("/api/properties/{property_id}/anlage-v/exports", {
          params: { path: { property_id: propertyId } },
          body: { tax_year: taxYear, kind },
        }),
      );
      return queued.export_id;
    },
    [propertyId, taxYear],
  );
  return (
    <ExportCard
      titleId="tax-handover-title"
      taxYear={taxYear}
      buttons={buttons}
      note={t.handOverNote}
      loadHistory={loadHistory}
      queue={queue}
    />
  );
}
