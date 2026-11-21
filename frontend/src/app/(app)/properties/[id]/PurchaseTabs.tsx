"use client";

// Nebenkosten (Anschaffungsnebenkosten, itemised so the AfA basis is traceable) and
// Kaufpreisaufteilung (building/land split candidates, one selected as the AfA basis).
// A Nebenkosten row can be edited (PATCH, e.g. to attach its Beleg later) or removed when it
// was entered by mistake; each change is an event in the Protokoll. Split candidates stay
// append-only (D8): a wrong split is replaced by a new candidate that is then selected.
// Phase 10 (mockup 25): both tables became rows (what it is, a short line, the amount or share)
// that open every field and the row's buttons in the detail panel; the edit form slides in.
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { AcquisitionCost, AcquisitionCostKind, Document, PriceSplit, PriceSplitSource, Property, PurchaseSummary } from "@/api/schemas";
import { DetailLayout, DetailPanel, useDetail } from "@/components/DetailPanel";
import { DocumentPicker, documentLabel } from "@/components/documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { blankToNull, decimalInput, toMoneyString, toRate } from "@/components/input";
import { ACQUISITION_COST_KIND, options, PRICE_SPLIT_SOURCE } from "@/components/labels";
import { SheetDialog } from "@/components/SheetDialog";
import { ActionDialog, Facts, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { propertyPage } from "@/i18n/propertyPage";
import { Icon } from "@/lib/icons";

import "@/components/pageRows.css";

import { PropertyCard } from "./PropertyCard";

const loadDocuments = () => ok(api.GET("/api/documents"));

/** The Nebenkosten's Summe and the AfA line of the selected split, as the server computes
 * them from the Anlage V draft's own facts (phase 10, mockup 25); nothing is summed here. */
function usePurchaseSummary(propertyId: string) {
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/purchase-summary", { params: { path: { property_id: propertyId } } })),
    [propertyId],
  );
  return useLoad(load);
}

function DocumentRef({ id, documents }: { id: string | null | undefined; documents: Document[] | undefined }) {
  const t = useT(propertyPage);
  if (!id) return <>–</>;
  const document = documents?.find((d) => d.id === id);
  return <Link href={`/documents/${id}`}>{document ? documentLabel(document) : t.document}</Link>;
}

// --- Nebenkosten ------------------------------------------------------------------------

const COST_KEYS = ["kind", "amount", "paid_on", "document_id", "note"] as const;

/** `onChanged` tells the page a row was added, edited or removed: the AfA basis beside it (in
 * SplitsTab) includes the Nebenkosten, so it reloads too (phase 10: both cards on one section). */
export function CostsTab({ propertyId, onChanged }: { propertyId: string; onChanged?: () => void }) {
  const t = useT(propertyPage);
  const f = useFormat();
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/acquisition-costs", { params: { path: { property_id: propertyId } } })),
    [propertyId],
  );
  const costs = useLoad(load);
  const documents = useLoad(loadDocuments);
  const summary = usePurchaseSummary(propertyId);
  const detail = useDetail("cost");
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  function changed() {
    costs.reload();
    documents.reload();
    summary.reload();
    onChanged?.();
  }

  async function remove(cost: AcquisitionCost) {
    const what = t.costWhat(ACQUISITION_COST_KIND[cost.kind], f.money(cost.amount));
    if (!window.confirm(t.removeConfirm(what))) return;
    setRemoving(cost.id);
    setError(null);
    try {
      await ok(api.DELETE("/api/acquisition-costs/{cost_id}", { params: { path: { cost_id: cost.id } } }));
      detail.close();
      changed();
    } catch (caught) {
      setError(caught);
    } finally {
      setRemoving(null);
    }
  }

  const open = detail.id ? costs.data?.find((cost) => cost.id === detail.id) : undefined;
  const panel = open ? (
    <DetailPanel title={ACQUISITION_COST_KIND[open.kind]} backLabel={t.tabCosts} onClose={detail.close}>
      <Facts
        rows={[
          [t.costKind, ACQUISITION_COST_KIND[open.kind]],
          [t.amount, f.money(open.amount)],
          [t.paidOn, f.date(open.paid_on)],
          [t.receipt, <DocumentRef key="receipt" id={open.document_id} documents={documents.data} />],
          [t.note, open.note ?? ""],
        ]}
      />
      {error ? <FormBanner errors={{ fields: {}, banner: error }} /> : null}
      <div className="row">
        <SheetDialog label={t.edit} title={t.editCostTitle}>
          {(close) => (
            <CostForm
              key={open.id}
              propertyId={propertyId}
              cost={open}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </SheetDialog>
        <button type="button" className="link" onClick={() => void remove(open)} disabled={removing !== null}>
          {removing === open.id ? t.removing : t.remove}
        </button>
      </div>
    </DetailPanel>
  ) : null;

  return (
    <DetailLayout panel={panel}>
      <PropertyCard card="nebenkosten" title={t.tabCosts}>
        <p className="muted">{t.costsIntro}</p>
        <Loading state={costs}>
          {(list) =>
            list.length === 0 ? (
              <p className="muted">{t.noCosts}</p>
            ) : (
              <ul className="detail-rows" aria-label={t.tabCosts}>
                {list.map((cost) => (
                  <li key={cost.id}>
                    <a {...detail.linkProps(cost.id)} className="detail-row">
                      <span className="detail-row-text">
                        <span className="detail-row-title">{ACQUISITION_COST_KIND[cost.kind]}</span>
                        <span className="detail-row-sub">
                          {[cost.paid_on ? t.rowPaid(f.date(cost.paid_on)) : null, cost.document_id ? t.rowWithReceipt : t.rowWithoutReceipt]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      </span>
                      <span className="row-amount">{f.money(cost.amount)}</span>
                      <Icon name="chevronRight" size={18} />
                    </a>
                  </li>
                ))}
              </ul>
            )
          }
        </Loading>
        {summary.data && summary.data.acquisition_cost_count > 0 ? (
          <div className="page-total">
            <span>{t.costsTotal}</span>
            <span>{f.money(summary.data.acquisition_costs_total)}</span>
          </div>
        ) : null}
        {error && !open ? <FormBanner errors={{ fields: {}, banner: error }} /> : null}
        <ActionDialog label={t.addCost} title={t.addCostTitle} buttonClass="">
          {(close) => (
            <CostForm
              propertyId={propertyId}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </ActionDialog>
      </PropertyCard>
    </DetailLayout>
  );
}

/** Add a row, or with `cost` edit it (every field, including the Beleg: pick, swap or
 * "– kein Dokument –" to clear it). */
function CostForm({ propertyId, cost, onDone }: { propertyId: string; cost?: AcquisitionCost; onDone: () => void }) {
  const t = useT(propertyPage);
  const [kind, setKind] = useState<AcquisitionCostKind>(cost?.kind ?? "real_estate_transfer_tax");
  const [amount, setAmount] = useState(decimalInput(cost?.amount));
  const [paidOn, setPaidOn] = useState(cost?.paid_on ?? "");
  const [documentId, setDocumentId] = useState<string | null>(cost?.document_id ?? null);
  const [note, setNote] = useState(cost?.note ?? "");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(COST_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const money = values.take("amount", toMoneyString(amount));
    if (!values.ok || money === undefined) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    const fields = { kind, amount: money, paid_on: paidOn || null, document_id: documentId, note: blankToNull(note) };
    try {
      if (cost) {
        await ok(api.PATCH("/api/acquisition-costs/{cost_id}", { params: { path: { cost_id: cost.id } }, body: fields }));
      } else {
        await ok(api.POST("/api/acquisition-costs", { body: { property_id: propertyId, ...fields } }));
      }
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <Field
        id="cost-kind"
        label={t.costKind}
        description={t.fKindDesc}
        errors={form.of("kind")}
      >
        <select value={kind} onChange={(e) => setKind(e.target.value as AcquisitionCostKind)}>
          {options(ACQUISITION_COST_KIND).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </Field>
      <Field
        id="cost-amount"
        label={t.fAmount}
        description={t.fAmountDesc}
        errors={form.of("amount")}
      >
        <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required autoComplete="off" />
      </Field>
      <Field
        id="cost-paid"
        label={t.fPaid}
        description={t.fPaidDesc}
        errors={form.of("paid_on")}
      >
        <input type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
      </Field>
      <Field
        id="cost-document"
        label={t.receipt}
        description={t.fReceiptDesc}
        errors={form.of("document_id")}
      >
        <DocumentPicker value={documentId} onChange={setDocumentId} />
      </Field>
      <Field
        id="cost-note"
        label={t.fNote}
        description={t.fCostNoteDesc}
        errors={form.of("note")}
      >
        <input type="text" value={note} onChange={(e) => setNote(e.target.value)} autoComplete="off" />
      </Field>
      <Submit busy={busy}>{cost ? t.save : t.add}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- Kaufpreisaufteilung ----------------------------------------------------------------

const SPLIT_KEYS = ["source", "building_share", "evidence_document_id", "note", "inputs"] as const;

/** `refresh` changes when a Nebenkosten row changed (CostsTab's `onChanged`): the AfA line reloads. */
export function SplitsTab({ property, onSelected, refresh = 0 }: { property: Property; onSelected: () => void; refresh?: number }) {
  const t = useT(propertyPage);
  const f = useFormat();
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/price-splits", { params: { path: { property_id: property.id } } })),
    [property.id],
  );
  const splits = useLoad(load);
  const documents = useLoad(loadDocuments);
  const summary = usePurchaseSummary(property.id);
  const reloadSummary = summary.reload;
  useEffect(() => {
    if (refresh > 0) reloadSummary();
  }, [refresh, reloadSummary]);
  const detail = useDetail("split");
  const [selecting, setSelecting] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function select(splitId: string) {
    setSelecting(splitId);
    setError(null);
    try {
      await ok(
        api.POST("/api/properties/{property_id}/price-splits/{split_id}/select", {
          params: { path: { property_id: property.id, split_id: splitId } },
        }),
      );
      onSelected();
      summary.reload();
    } catch (caught) {
      setError(caught);
    } finally {
      setSelecting(null);
    }
  }

  function chooser(split: PriceSplit) {
    return property.selected_price_split_id === split.id ? (
      <span className="badge">{t.afaBadge}</span>
    ) : (
      <button type="button" className="secondary" onClick={() => void select(split.id)} disabled={selecting !== null}>
        {selecting === split.id ? t.choosing : t.choose}
      </button>
    );
  }

  const open = detail.id ? splits.data?.find((split) => split.id === detail.id) : undefined;
  const panel = open ? (
    <DetailPanel title={PRICE_SPLIT_SOURCE[open.source]} backLabel={t.tabSplit} onClose={detail.close}>
      <Facts
        rows={[
          [t.source, PRICE_SPLIT_SOURCE[open.source]],
          [t.buildingShare, f.percent(open.building_share)],
          [t.evidence, <DocumentRef key="evidence" id={open.evidence_document_id} documents={documents.data} />],
          [t.note, open.note ?? ""],
          [t.recorded, f.dateTime(open.recorded_at)],
        ]}
      />
      {error ? <FormBanner errors={{ fields: {}, banner: error }} /> : null}
      <div>{chooser(open)}</div>
    </DetailPanel>
  ) : null;

  return (
    <DetailLayout panel={panel}>
      <PropertyCard card="aufteilung" title={t.tabSplit}>
        <p className="muted">{t.splitsIntro}</p>
        <Loading state={splits}>
          {(list) =>
            list.length === 0 ? (
              <p className="muted">{t.noSplit}</p>
            ) : (
              <ul className="detail-rows" aria-label={t.splitsList}>
                {list.map((split) => (
                  <li key={split.id}>
                    <a {...detail.linkProps(split.id)} className="detail-row">
                      <span className="detail-row-text">
                        <span className="detail-row-title">{PRICE_SPLIT_SOURCE[split.source]}</span>
                        <span className="detail-row-sub">
                          {property.selected_price_split_id === split.id ? t.afaBadge : t.rowRecorded(f.dateTime(split.recorded_at))}
                        </span>
                      </span>
                      <span className="row-amount">{f.percent(split.building_share)}</span>
                      <Icon name="chevronRight" size={18} />
                    </a>
                  </li>
                ))}
              </ul>
            )
          }
        </Loading>
        {summary.data ? <AfaFigures summary={summary.data} property={property} /> : null}
        {error && !open ? <FormBanner errors={{ fields: {}, banner: error }} /> : null}
        <ActionDialog label={t.addSplit} title={t.addSplitTitle} buttonClass="">
          {(close) => (
            <SplitForm
              propertyId={property.id}
              onDone={() => {
                close();
                splits.reload();
                documents.reload();
                summary.reload();
                onSelected(); // D133: the first split becomes the AfA basis on its own
              }}
            />
          )}
        </ActionDialog>
      </PropertyCard>
    </DetailLayout>
  );
}

/** The AfA line under the splits: its basis and rate from the month of the handover, or the
 * Gutachten's useful life; without a selected split a to-do naming the buttons, never 0 €. */
function AfaFigures({ summary, property }: { summary: PurchaseSummary; property: Property }) {
  const t = useT(propertyPage);
  const f = useFormat();
  const afa = summary.afa;
  if (!afa) return null;
  if (afa.basis === null) return <p className="muted">{t.afaNoSplit(t.addSplit, t.choose)}</p>;
  const from = property.purchase_date ? f.month(property.purchase_date) : null;
  return (
    <div className="stack">
      <dl className="page-figs">
        <dt>{t.afaBasisAmount}</dt>
        <dd>{f.money(afa.basis)}</dd>
        {afa.method === "useful_life" && afa.useful_life_years ? (
          <>
            <dt>{t.afaUsefulLife}</dt>
            <dd>{t.afaYears(afa.useful_life_years)}</dd>
          </>
        ) : afa.rate !== null ? (
          <>
            <dt>{t.afaRate}</dt>
            <dd>{t.afaRateFrom(f.percent(afa.rate), from)}</dd>
          </>
        ) : null}
      </dl>
      {Number(afa.movable_deducted) !== 0 ? <p className="muted">{t.afaKitchen(f.money(afa.movable_deducted))}</p> : null}
    </div>
  );
}

function SplitForm({ propertyId, onDone }: { propertyId: string; onDone: () => void }) {
  const t = useT(propertyPage);
  const [source, setSource] = useState<PriceSplitSource>("purchase_contract");
  const [share, setShare] = useState("");
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(SPLIT_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const buildingShare = values.take("building_share", toRate(share, 6));
    if (!values.ok || buildingShare === undefined) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.POST("/api/price-splits", {
          body: { property_id: propertyId, source, building_share: buildingShare, evidence_document_id: documentId, note: blankToNull(note) },
        }),
      );
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <Field
        id="split-source"
        label={t.source}
        description={t.sSourceDesc}
        errors={form.of("source")}
      >
        <select value={source} onChange={(e) => setSource(e.target.value as PriceSplitSource)}>
          {options(PRICE_SPLIT_SOURCE).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </Field>
      <Field
        id="split-share"
        label={t.sShare}
        hint={t.sShareHint}
        description={t.sShareDesc}
        errors={form.of("building_share")}
      >
        <input type="text" inputMode="decimal" value={share} onChange={(e) => setShare(e.target.value)} required autoComplete="off" />
      </Field>
      <Field
        id="split-document"
        label={t.sEvidence}
        description={t.sEvidenceDesc}
        errors={form.of("evidence_document_id")}
      >
        <DocumentPicker value={documentId} onChange={setDocumentId} />
      </Field>
      <Field
        id="split-note"
        label={t.fNote}
        description={t.sNoteDesc}
        errors={form.of("note")}
      >
        <input type="text" value={note} onChange={(e) => setNote(e.target.value)} autoComplete="off" />
      </Field>
      <Submit busy={busy}>{t.add}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
