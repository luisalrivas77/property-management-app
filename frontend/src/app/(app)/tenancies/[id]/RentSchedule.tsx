"use client";

// Mietstaffel (§4.5, D12): dated rent rows, never edited. A wrong row is superseded by a
// correction row (struck through here); a rent change is a new row. "Gilt heute" is the
// API's answer (GET …/rent?at=today), not computed here.
import Link from "next/link";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { RentChangeReason, RentRowCorrection, RentScheduleEntry, Tenancy } from "@/api/schemas";
import { DocumentPicker } from "@/components/documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatDate, formatDateTime, formatMoney, todayIso } from "@/components/format";
import { decimalInput, toMoneyString } from "@/components/input";
import { RENT_CHANGE_REASON } from "@/components/labels";
import { ActionDialog, Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const KEYS = ["effective_from", "cold_rent", "operating_costs_prepayment", "reason", "document_id"] as const;
const CODE_FIELDS = { "rent_schedule.outside_tenancy": "effective_from" };

/** Reasons for a new row: not the first row (that comes with the tenancy) and not a
 * correction (that supersedes a row, via "korrigieren"). */
const CHANGE_REASONS = (Object.keys(RENT_CHANGE_REASON) as RentChangeReason[]).filter(
  (reason) => reason !== "initial" && reason !== "correction",
);

export function RentSchedule({ tenancy, onChanged }: { tenancy: Tenancy; onChanged: () => void }) {
  const today = todayIso();
  const load = useCallback(async () => {
    const path = { params: { path: { tenancy_id: tenancy.id } } };
    const [rows, current] = await Promise.all([
      ok(api.GET("/api/tenancies/{tenancy_id}/rent-schedule", path)),
      ok(api.GET("/api/tenancies/{tenancy_id}/rent", { params: { path: { tenancy_id: tenancy.id }, query: { at: today } } })),
    ]);
    return { rows, current };
  }, [tenancy.id, today]);
  const schedule = useLoad(load);
  const [note, setNote] = useState<string | null>(null);

  function changed(message: string) {
    setNote(message);
    schedule.reload();
    onChanged();
  }

  return (
    <div className="stack">
      {note ? <Notice>{note}</Notice> : null}
      <Loading state={schedule}>
        {({ rows, current }) => {
          const superseded = new Set(rows.map((row) => row.supersedes_id).filter(Boolean));
          return rows.length === 0 ? (
            <p className="muted">Noch keine Miete erfasst.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Ab</th>
                    <th className="num">Kaltmiete</th>
                    <th className="num">NK-Vorauszahlung</th>
                    <th>Grund</th>
                    <th>Beleg</th>
                    <th>Erfasst</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const isSuperseded = superseded.has(row.id);
                    return (
                      <tr key={row.id} className={isSuperseded ? "superseded" : undefined}>
                        <td>
                          {formatDate(row.effective_from)}
                          {current?.id === row.id ? <span className="badge"> gilt heute</span> : null}
                        </td>
                        <td className="num">{formatMoney(row.cold_rent)}</td>
                        <td className="num">{formatMoney(row.operating_costs_prepayment)}</td>
                        <td>{RENT_CHANGE_REASON[row.reason]}</td>
                        <td>{row.document_id ? <Link href={`/documents/${row.document_id}`}>Beleg</Link> : "–"}</td>
                        <td>{formatDateTime(row.recorded_at)}</td>
                        <td className="actions">
                          {isSuperseded ? (
                            <span className="muted">ersetzt</span>
                          ) : (
                            <ActionDialog label="korrigieren" title="Mietzeile korrigieren" buttonClass="link">
                              {(close) => (
                                <CorrectRowForm
                                  row={row}
                                  onDone={(message) => {
                                    close();
                                    changed(message);
                                  }}
                                />
                              )}
                            </ActionDialog>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        }}
      </Loading>
      <div>
        <ActionDialog label="Mietänderung" title="Mietänderung erfassen" buttonClass="" disabled={tenancy.end_date !== null && tenancy.end_date < today}>
          {(close) => (
            <RentChangeForm
              tenancy={tenancy}
              onDone={() => {
                close();
                changed("Mietänderung gespeichert.");
              }}
            />
          )}
        </ActionDialog>
      </div>
    </div>
  );
}

function RentChangeForm({ tenancy, onDone }: { tenancy: Tenancy; onDone: () => void }) {
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [cold, setCold] = useState("");
  const [prepayment, setPrepayment] = useState("");
  const [reason, setReason] = useState<RentChangeReason>(tenancy.rent_model === "staffel" ? "staffel_step" : tenancy.rent_model === "index" ? "index_adjustment" : "rent_increase");
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const coldRent = values.take("cold_rent", toMoneyString(cold));
    const prepaid = values.take("operating_costs_prepayment", toMoneyString(prepayment || "0"));
    if (!effectiveFrom) values.messages.effective_from = ["Bitte das Datum eingeben."];
    if (!values.ok || coldRent === undefined || prepaid === undefined) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.POST("/api/rent-schedule", {
          body: {
            tenancy_id: tenancy.id,
            effective_from: effectiveFrom,
            cold_rent: coldRent,
            operating_costs_prepayment: prepaid,
            reason,
            document_id: documentId,
          },
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
        id="rent-change-from"
        label="Gültig ab"
        description="Der erste Tag, für den die neue Miete gilt, laut Mieterhöhung, Staffel oder Vereinbarung."
        errors={form.of("effective_from")}
      >
        <input type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} required />
      </Field>
      <Field
        id="rent-change-cold"
        label="Kaltmiete (€ / Monat)"
        description="Die neue monatliche Miete ohne Nebenkosten. Ab dem Gültigkeitstag erkennt Hausbuch Mietzahlungen an diesem Betrag."
        errors={form.of("cold_rent")}
      >
        <input type="text" inputMode="decimal" value={cold} onChange={(e) => setCold(e.target.value)} required autoComplete="off" />
      </Field>
      <Field
        id="rent-change-prepayment"
        label="NK-Vorauszahlung (€ / Monat)"
        description="Die neue monatliche Vorauszahlung auf Betriebs- und Heizkosten, beide zusammen. Leer, wenn keine vereinbart ist."
        errors={form.of("operating_costs_prepayment")}
      >
        <input type="text" inputMode="decimal" value={prepayment} onChange={(e) => setPrepayment(e.target.value)} autoComplete="off" />
      </Field>
      <Field
        id="rent-change-reason"
        label="Grund"
        description="Warum sich die Miete ändert: Staffelstufe, Indexanpassung, Mieterhöhung, Modernisierung oder eine Vereinbarung."
        errors={form.of("reason")}
      >
        <select value={reason} onChange={(e) => setReason(e.target.value as RentChangeReason)}>
          {CHANGE_REASONS.map((value) => (
            <option key={value} value={value}>
              {RENT_CHANGE_REASON[value]}
            </option>
          ))}
        </select>
      </Field>
      <Field
        id="rent-change-document"
        label="Beleg (optional)"
        description="Das Schreiben, das die Änderung belegt, z. B. Mieterhöhungsverlangen, Indexmitteilung oder der Mietvertrag mit der Staffel."
        errors={form.of("document_id")}
      >
        <DocumentPicker value={documentId} onChange={setDocumentId} />
      </Field>
      <Submit busy={busy}>Speichern</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** Supersede a wrong row. Only the fields that differ from the row are sent: an absent field
 * keeps the wrong row's value (RentRowCorrection). The API answers 200 when nothing changed. */
function CorrectRowForm({ row, onDone }: { row: RentScheduleEntry; onDone: (message: string) => void }) {
  const [effectiveFrom, setEffectiveFrom] = useState(row.effective_from);
  const [cold, setCold] = useState(decimalInput(row.cold_rent));
  const [prepayment, setPrepayment] = useState(decimalInput(row.operating_costs_prepayment));
  const [documentId, setDocumentId] = useState<string | null>(row.document_id ?? null);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const coldRent = values.take("cold_rent", toMoneyString(cold));
    const prepaid = values.take("operating_costs_prepayment", toMoneyString(prepayment || "0"));
    if (!effectiveFrom) values.messages.effective_from = ["Bitte das Datum eingeben."];
    if (!values.ok || coldRent === undefined || prepaid === undefined) return form.invalid(values.messages);
    const body: RentRowCorrection = {};
    if (effectiveFrom !== row.effective_from) body.effective_from = effectiveFrom;
    if (coldRent !== row.cold_rent) body.cold_rent = coldRent;
    if (prepaid !== row.operating_costs_prepayment) body.operating_costs_prepayment = prepaid;
    if (documentId !== (row.document_id ?? null)) body.document_id = documentId;
    if (Object.keys(body).length === 0) return onDone("Keine Änderung.");
    setBusy(true);
    form.clear();
    try {
      const { response } = await api.POST("/api/rent-schedule/{row_id}/corrections", { params: { path: { row_id: row.id } }, body });
      onDone(response.status === 200 ? "Keine Änderung." : "Mietzeile korrigiert; die alte Zeile ist ersetzt.");
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <p className="muted">
        Die Zeile ab {formatDate(row.effective_from)} wird durch eine Korrekturzeile ersetzt und bleibt durchgestrichen sichtbar.
      </p>
      <Field
        id={`correct-${row.id}-from`}
        label="Gültig ab"
        description="Der richtige erste Tag, für den diese Miete gilt."
        errors={form.of("effective_from")}
      >
        <input type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} required />
      </Field>
      <Field
        id={`correct-${row.id}-cold`}
        label="Kaltmiete (€ / Monat)"
        description="Die richtige monatliche Miete ohne Nebenkosten."
        errors={form.of("cold_rent")}
      >
        <input type="text" inputMode="decimal" value={cold} onChange={(e) => setCold(e.target.value)} required autoComplete="off" />
      </Field>
      <Field
        id={`correct-${row.id}-prepayment`}
        label="NK-Vorauszahlung (€ / Monat)"
        description="Die richtige monatliche Vorauszahlung auf Betriebs- und Heizkosten. Leer, wenn keine vereinbart ist."
        errors={form.of("operating_costs_prepayment")}
      >
        <input type="text" inputMode="decimal" value={prepayment} onChange={(e) => setPrepayment(e.target.value)} autoComplete="off" />
      </Field>
      <Field
        id={`correct-${row.id}-document`}
        label="Beleg (optional)"
        description="Das Schreiben, das diese Miete belegt, z. B. der Mietvertrag."
        errors={form.of("document_id")}
      >
        <DocumentPicker value={documentId} onChange={setDocumentId} />
      </Field>
      <Submit busy={busy}>Korrektur speichern</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
