"use client";

// Nebenkosten (Anschaffungsnebenkosten, itemised so the AfA basis is traceable) and
// Kaufpreisaufteilung (building/land split candidates, one selected as the AfA basis).
// Both are append-only lists: rows are added, never edited (§4.2).
import Link from "next/link";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { AcquisitionCostKind, Document, PriceSplitSource, Property } from "@/api/schemas";
import { DocumentPicker, documentLabel } from "@/components/documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatDate, formatDateTime, formatMoney, formatPercent } from "@/components/format";
import { blankToNull, toMoneyString, toRate } from "@/components/input";
import { ACQUISITION_COST_KIND, options, PRICE_SPLIT_SOURCE } from "@/components/labels";
import { ActionDialog, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const loadDocuments = () => ok(api.GET("/api/documents"));

function DocumentRef({ id, documents }: { id: string | null | undefined; documents: Document[] | undefined }) {
  if (!id) return <>–</>;
  const document = documents?.find((d) => d.id === id);
  return <Link href={`/documents/${id}`}>{document ? documentLabel(document) : "Dokument"}</Link>;
}

// --- Nebenkosten ------------------------------------------------------------------------

const COST_KEYS = ["kind", "amount", "paid_on", "document_id", "note"] as const;

export function CostsTab({ propertyId }: { propertyId: string }) {
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/acquisition-costs", { params: { path: { property_id: propertyId } } })),
    [propertyId],
  );
  const costs = useLoad(load);
  const documents = useLoad(loadDocuments);

  return (
    <div className="stack">
      <p className="muted">Grunderwerbsteuer, Notar, Grundbuch, Makler: jede Position einzeln, mit Beleg.</p>
      <Loading state={costs}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch keine Nebenkosten erfasst.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Art</th>
                    <th className="num">Betrag</th>
                    <th>Bezahlt am</th>
                    <th>Beleg</th>
                    <th>Notiz</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((cost) => (
                    <tr key={cost.id}>
                      <td>{ACQUISITION_COST_KIND[cost.kind]}</td>
                      <td className="num">{formatMoney(cost.amount)}</td>
                      <td>{formatDate(cost.paid_on)}</td>
                      <td>
                        <DocumentRef id={cost.document_id} documents={documents.data} />
                      </td>
                      <td>{cost.note ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
      <ActionDialog label="Position hinzufügen" title="Nebenkosten hinzufügen" buttonClass="">
        {(close) => (
          <CostForm
            propertyId={propertyId}
            onDone={() => {
              close();
              costs.reload();
              documents.reload();
            }}
          />
        )}
      </ActionDialog>
    </div>
  );
}

function CostForm({ propertyId, onDone }: { propertyId: string; onDone: () => void }) {
  const [kind, setKind] = useState<AcquisitionCostKind>("real_estate_transfer_tax");
  const [amount, setAmount] = useState("");
  const [paidOn, setPaidOn] = useState("");
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(COST_KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const money = values.take("amount", toMoneyString(amount));
    if (!values.ok || money === undefined) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.POST("/api/acquisition-costs", {
          body: { property_id: propertyId, kind, amount: money, paid_on: paidOn || null, document_id: documentId, note: blankToNull(note) },
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
      <Field id="cost-kind" label="Art" errors={form.of("kind")}>
        <select value={kind} onChange={(e) => setKind(e.target.value as AcquisitionCostKind)}>
          {options(ACQUISITION_COST_KIND).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </Field>
      <Field id="cost-amount" label="Betrag (€)" errors={form.of("amount")}>
        <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required autoComplete="off" />
      </Field>
      <Field id="cost-paid" label="Bezahlt am (optional)" errors={form.of("paid_on")}>
        <input type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
      </Field>
      <Field id="cost-document" label="Beleg" errors={form.of("document_id")}>
        <DocumentPicker value={documentId} onChange={setDocumentId} />
      </Field>
      <Field id="cost-note" label="Notiz (optional)" errors={form.of("note")}>
        <input type="text" value={note} onChange={(e) => setNote(e.target.value)} autoComplete="off" />
      </Field>
      <Submit busy={busy}>Hinzufügen</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- Kaufpreisaufteilung ----------------------------------------------------------------

const SPLIT_KEYS = ["source", "building_share", "evidence_document_id", "note", "inputs"] as const;

export function SplitsTab({ property, onSelected }: { property: Property; onSelected: () => void }) {
  const load = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/price-splits", { params: { path: { property_id: property.id } } })),
    [property.id],
  );
  const splits = useLoad(load);
  const documents = useLoad(loadDocuments);
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
    } catch (caught) {
      setError(caught);
    } finally {
      setSelecting(null);
    }
  }

  return (
    <div className="stack">
      <p className="muted">
        Der Gebäudeanteil am Kaufpreis bestimmt die AfA. Quellen: Kaufvertrag, BMF-Arbeitshilfe oder Gutachten. Mehrere
        Kandidaten sind möglich; einer wird als AfA-Basis gewählt.
      </p>
      <Loading state={splits}>
        {(list) =>
          list.length === 0 ? (
            <p className="muted">Noch keine Aufteilung erfasst.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Quelle</th>
                    <th className="num">Gebäudeanteil</th>
                    <th>Nachweis</th>
                    <th>Notiz</th>
                    <th>Erfasst</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((split) => (
                    <tr key={split.id}>
                      <td>{PRICE_SPLIT_SOURCE[split.source]}</td>
                      <td className="num">{formatPercent(split.building_share)}</td>
                      <td>
                        <DocumentRef id={split.evidence_document_id} documents={documents.data} />
                      </td>
                      <td>{split.note ?? ""}</td>
                      <td>{formatDateTime(split.recorded_at)}</td>
                      <td>
                        {property.selected_price_split_id === split.id ? (
                          <span className="badge">AfA-Basis</span>
                        ) : (
                          <button type="button" className="secondary" onClick={() => void select(split.id)} disabled={selecting !== null}>
                            {selecting === split.id ? "Wählt …" : "als AfA-Basis wählen"}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loading>
      {error ? <FormBanner errors={{ fields: {}, banner: error }} /> : null}
      <ActionDialog label="Aufteilung hinzufügen" title="Kaufpreisaufteilung hinzufügen" buttonClass="">
        {(close) => (
          <SplitForm
            propertyId={property.id}
            onDone={() => {
              close();
              splits.reload();
              documents.reload();
            }}
          />
        )}
      </ActionDialog>
    </div>
  );
}

function SplitForm({ propertyId, onDone }: { propertyId: string; onDone: () => void }) {
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
      <Field id="split-source" label="Quelle" errors={form.of("source")}>
        <select value={source} onChange={(e) => setSource(e.target.value as PriceSplitSource)}>
          {options(PRICE_SPLIT_SOURCE).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </Field>
      <Field id="split-share" label="Gebäudeanteil (%)" hint="z. B. 78,5" errors={form.of("building_share")}>
        <input type="text" inputMode="decimal" value={share} onChange={(e) => setShare(e.target.value)} required autoComplete="off" />
      </Field>
      <Field id="split-document" label="Nachweis (Kaufvertrag, Arbeitshilfe, Gutachten)" errors={form.of("evidence_document_id")}>
        <DocumentPicker value={documentId} onChange={setDocumentId} />
      </Field>
      <Field id="split-note" label="Notiz (optional)" errors={form.of("note")}>
        <input type="text" value={note} onChange={(e) => setNote(e.target.value)} autoComplete="off" />
      </Field>
      <Submit busy={busy}>Hinzufügen</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
