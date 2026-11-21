"use client";

// Mietstaffel (§4.5, D12): dated rent rows, never edited. A wrong row is superseded by a
// correction row (struck through here); a rent change is a new row. "Gilt heute" is the
// API's answer (GET …/rent?at=today), not computed here. On a flat_rate tenancy the amount is
// the Nebenkostenpauschale (D155 (5)); a to-do's link (?mietaenderung=pauschale) opens
// „Mietänderung“ pre-filled with the new Pauschale and its start from the backend (D155 (4)).
// Phase 10 (mockup 25): each row keeps its date and base rent; the rest and „korrigieren“ open
// in the detail panel, so nothing scrolls sideways on a phone.
import Link from "next/link";
import { useCallback, useState, type FormEvent, type ReactNode } from "react";

import { api, ok } from "@/api/client";
import type { RentChangeReason, RentRowCorrection, RentScheduleEntry, Tenancy } from "@/api/schemas";
import { DocumentPicker } from "@/components/documents";
import { loadPauschaleChange, type PauschaleChange } from "@/components/flatRate";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { DetailPanel, useDetail } from "@/components/DetailPanel";
import { todayIso } from "@/components/format";
import { decimalInput, toMoneyString } from "@/components/input";
import { RENT_CHANGE_REASON } from "@/components/labels";
import { ActionDialog, Facts, Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { tenancies } from "@/i18n/tenancies";
import { Icon } from "@/lib/icons";

import "@/components/pageRows.css";

const KEYS = ["effective_from", "cold_rent", "operating_costs_prepayment", "reason", "document_id"] as const;
const CODE_FIELDS = { "rent_schedule.outside_tenancy": "effective_from" };

/** Reasons for a new row: not the first row (that comes with the tenancy) and not a
 * correction (that supersedes a row, via "korrigieren"). */
const CHANGE_REASONS = (Object.keys(RENT_CHANGE_REASON) as RentChangeReason[]).filter(
  (reason) => reason !== "initial" && reason !== "correction",
);

/** The Mietstaffel as rows (from-date with „gilt heute“, the reason, the base rent) and, for the
 * row whose hash is open, its panel (the running costs, the receipt, when recorded, and
 * „korrigieren“). A hook, so the page puts the panel into its own DetailLayout. */
export function useRentSchedule({
  tenancy,
  onChanged,
  prefillPauschale = false,
  onPrefillDone,
}: {
  tenancy: Tenancy;
  onChanged: () => void;
  /** Open „Mietänderung“ pre-filled with the Pauschale change of today (a to-do's link). */
  prefillPauschale?: boolean;
  /** Called once a pre-filled change was saved, so the page can drop the link's parameter. */
  onPrefillDone?: () => void;
}): { body: ReactNode; panel: ReactNode } {
  const t = useT(tenancies);
  const f = useFormat();
  const detail = useDetail("rentrow");
  const today = todayIso();
  const flatRate = tenancy.operating_costs_terms === "flat_rate";
  // The move-in is a dependency: correcting it also moves the first rent row (§10.8 item 6),
  // so the schedule reloads.
  const startDate = tenancy.start_date;
  const load = useCallback(async () => {
    void startDate;
    const path = { params: { path: { tenancy_id: tenancy.id } } };
    const [rows, current] = await Promise.all([
      ok(api.GET("/api/tenancies/{tenancy_id}/rent-schedule", path)),
      ok(api.GET("/api/tenancies/{tenancy_id}/rent", { params: { path: { tenancy_id: tenancy.id }, query: { at: today } } })),
    ]);
    return { rows, current };
  }, [tenancy.id, today, startDate]);
  const schedule = useLoad(load);
  const prefillOn = prefillPauschale && flatRate;
  const loadPrefill = useCallback(
    (): Promise<PauschaleChange | null> => (prefillOn ? loadPauschaleChange(tenancy.id) : Promise.resolve(null)),
    [prefillOn, tenancy.id],
  );
  const prefill = useLoad(loadPrefill);
  // The pre-filled dialog mounts open once the change has loaded (or is known to be none, then
  // unfilled) and the schedule too, since the form takes the Kaltmiete from the latest row only
  // when it mounts; keyed so it replaces the closed one rather than reusing its state. Opening
  // before the schedule had loaded left the Kaltmiete empty and the save refused.
  const prefillReady = prefillOn && prefill.data !== undefined && schedule.data !== undefined;
  const [note, setNote] = useState<string | null>(null);
  const rows = schedule.data?.rows ?? [];
  const latest = latestRow(rows);
  const superseded = new Set(rows.map((row) => row.supersedes_id).filter(Boolean));
  const costsLabel = flatRate ? t.flatRate : t.prepayment;

  function changed(message: string) {
    setNote(message);
    schedule.reload();
    onChanged();
  }

  const open = detail.id ? rows.find((row) => row.id === detail.id) : undefined;
  const panel = open ? (
    <DetailPanel title={t.rowPanel(f.date(open.effective_from))} backLabel={t.schedule} onClose={detail.close}>
      <Facts
        rows={[
          [t.from, f.date(open.effective_from)],
          [t.coldRent, f.money(open.cold_rent)],
          [costsLabel, f.money(open.operating_costs_prepayment)],
          [t.reason, RENT_CHANGE_REASON[open.reason]],
          [t.receipt, open.document_id ? <Link href={`/documents/${open.document_id}`}>{t.receipt}</Link> : "–"],
          [t.recorded, f.dateTime(open.recorded_at)],
        ]}
      />
      {superseded.has(open.id) ? (
        <p className="muted">{t.replaced}</p>
      ) : (
        <div className="panel-actions">
          <ActionDialog label={t.correct} title={t.correctTitle} buttonClass="secondary">
            {(close) => (
              <CorrectRowForm
                row={open}
                flatRate={flatRate}
                onDone={(message) => {
                  close();
                  detail.close();
                  changed(message);
                }}
              />
            )}
          </ActionDialog>
        </div>
      )}
    </DetailPanel>
  ) : null;

  const body = (
    <div className="stack">
      {note ? <Notice>{note}</Notice> : null}
      <Loading state={schedule}>
        {({ rows: list, current }) =>
          list.length === 0 ? (
            <p className="muted">{t.noRent}</p>
          ) : (
            <ul className="detail-rows" aria-label={t.schedule}>
              {list.map((row) => (
                <li key={row.id} className={superseded.has(row.id) ? "superseded" : undefined}>
                  <a {...detail.linkProps(row.id)} className="detail-row">
                    <span className="detail-row-text">
                      <span className="detail-row-title">
                        {t.rowTitle(f.date(row.effective_from))}
                        {current?.id === row.id ? <span className="badge">{t.currentBadge}</span> : null}
                      </span>
                      <span className="detail-row-sub">
                        {superseded.has(row.id) ? t.replaced : RENT_CHANGE_REASON[row.reason]}
                      </span>
                    </span>
                    <span className="num row-amount">{t.rowAmount(f.money(row.cold_rent), f.money(row.operating_costs_prepayment))}</span>
                    <Icon name="chevronRight" size={18} />
                  </a>
                </li>
              ))}
            </ul>
          )
        }
      </Loading>
      <div>
        <ActionDialog
          key={prefillReady ? "prefill" : "plain"}
          label={t.rentChange}
          title={t.rentChangeTitle}
          buttonClass=""
          disabled={tenancy.end_date !== null && tenancy.end_date < today}
          initialOpen={prefillReady}
        >
          {(close) => (
            <RentChangeForm
              tenancy={tenancy}
              latest={latest}
              initial={prefillReady ? (prefill.data ?? undefined) : undefined}
              onDone={() => {
                close();
                changed(t.rentChangeSaved);
                if (prefillReady) onPrefillDone?.();
              }}
            />
          )}
        </ActionDialog>
      </div>
    </div>
  );
  return { body, panel };
}

/** The row in force last: the latest effective_from among the rows no correction replaced. */
function latestRow(rows: RentScheduleEntry[]): RentScheduleEntry | null {
  const superseded = new Set(rows.map((row) => row.supersedes_id).filter(Boolean));
  return rows
    .filter((row) => !superseded.has(row.id))
    .reduce<RentScheduleEntry | null>((best, row) => (best === null || row.effective_from >= best.effective_from ? row : best), null);
}

/** A rent change starts from the latest row's amounts, so changing one of them keeps the
 * other (a blank NK-Vorauszahlung or Pauschale would otherwise be saved as 0 €). `initial`
 * pre-fills a Pauschale change (start, new Pauschale, reason) from the backend's figures. */
function RentChangeForm({
  tenancy,
  latest,
  initial,
  onDone,
}: {
  tenancy: Tenancy;
  latest: RentScheduleEntry | null;
  initial?: PauschaleChange;
  onDone: () => void;
}) {
  const t = useT(tenancies);
  const f = useFormat();
  const flatRate = tenancy.operating_costs_terms === "flat_rate";
  const [effectiveFrom, setEffectiveFrom] = useState(initial?.effectiveFrom ?? "");
  const [cold, setCold] = useState(latest ? decimalInput(latest.cold_rent) : "");
  const [prepayment, setPrepayment] = useState(initial?.prepayment ?? (latest ? decimalInput(latest.operating_costs_prepayment) : ""));
  const [reason, setReason] = useState<RentChangeReason>(
    initial?.reason ?? (tenancy.rent_model === "staffel" ? "staffel_step" : tenancy.rent_model === "index" ? "index_adjustment" : "rent_increase"),
  );
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const coldRent = values.take("cold_rent", toMoneyString(cold));
    const prepaid = values.take("operating_costs_prepayment", toMoneyString(prepayment || "0"));
    if (!effectiveFrom) values.messages.effective_from = [t.dateMissing];
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
      {initial ? (
        <p className="muted">{t.prefillPauschale}</p>
      ) : latest ? (
        <p className="muted">{t.prefillLatest(f.date(latest.effective_from))}</p>
      ) : null}
      <Field
        id="rent-change-from"
        label={t.validFrom}
        description={t.validFromDescription}
        errors={form.of("effective_from")}
      >
        <input type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} required />
      </Field>
      <Field
        id="rent-change-cold"
        label={t.coldField}
        description={t.coldDescription}
        errors={form.of("cold_rent")}
      >
        <input type="text" inputMode="decimal" value={cold} onChange={(e) => setCold(e.target.value)} required autoComplete="off" />
      </Field>
      <Field
        id="rent-change-prepayment"
        label={flatRate ? t.flatRateField : t.prepaymentField}
        description={flatRate ? t.flatRateDescription : t.prepaymentDescription}
        errors={form.of("operating_costs_prepayment")}
      >
        <input type="text" inputMode="decimal" value={prepayment} onChange={(e) => setPrepayment(e.target.value)} autoComplete="off" />
      </Field>
      <Field
        id="rent-change-reason"
        label={t.reason}
        description={t.reasonDescription}
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
        label={t.receiptField}
        description={t.receiptDescription}
        errors={form.of("document_id")}
      >
        <DocumentPicker value={documentId} onChange={setDocumentId} />
      </Field>
      <Submit busy={busy}>{t.save}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** Supersede a wrong row. Only the fields that differ from the row are sent: an absent field
 * keeps the wrong row's value (RentRowCorrection). The API answers 200 when nothing changed. */
function CorrectRowForm({ row, flatRate, onDone }: { row: RentScheduleEntry; flatRate: boolean; onDone: (message: string) => void }) {
  const t = useT(tenancies);
  const f = useFormat();
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
    if (!effectiveFrom) values.messages.effective_from = [t.dateMissing];
    if (!values.ok || coldRent === undefined || prepaid === undefined) return form.invalid(values.messages);
    const body: RentRowCorrection = {};
    if (effectiveFrom !== row.effective_from) body.effective_from = effectiveFrom;
    if (coldRent !== row.cold_rent) body.cold_rent = coldRent;
    if (prepaid !== row.operating_costs_prepayment) body.operating_costs_prepayment = prepaid;
    if (documentId !== (row.document_id ?? null)) body.document_id = documentId;
    if (Object.keys(body).length === 0) return onDone(t.noChange);
    setBusy(true);
    form.clear();
    try {
      const { response } = await api.POST("/api/rent-schedule/{row_id}/corrections", { params: { path: { row_id: row.id } }, body });
      onDone(response.status === 200 ? t.noChange : t.rowCorrected);
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <p className="muted">{t.correctIntro(f.date(row.effective_from))}</p>
      <Field
        id={`correct-${row.id}-from`}
        label={t.validFrom}
        description={t.correctFromDescription}
        errors={form.of("effective_from")}
      >
        <input type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} required />
      </Field>
      <Field
        id={`correct-${row.id}-cold`}
        label={t.coldField}
        description={t.correctColdDescription}
        errors={form.of("cold_rent")}
      >
        <input type="text" inputMode="decimal" value={cold} onChange={(e) => setCold(e.target.value)} required autoComplete="off" />
      </Field>
      <Field
        id={`correct-${row.id}-prepayment`}
        label={flatRate ? t.flatRateField : t.prepaymentField}
        description={flatRate ? t.correctFlatRateDescription : t.correctPrepaymentDescription}
        errors={form.of("operating_costs_prepayment")}
      >
        <input type="text" inputMode="decimal" value={prepayment} onChange={(e) => setPrepayment(e.target.value)} autoComplete="off" />
      </Field>
      <Field
        id={`correct-${row.id}-document`}
        label={t.receiptField}
        description={t.correctReceiptDescription}
        errors={form.of("document_id")}
      >
        <DocumentPicker value={documentId} onChange={setDocumentId} />
      </Field>
      <Submit busy={busy}>{t.saveCorrection}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
