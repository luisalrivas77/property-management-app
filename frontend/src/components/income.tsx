"use client";

// Household income and the tax effect of a rental result (docs/phases/phase-4.md §8, §9, D82–D84).
//
// - IncomeSection (Eigentümer page): per owner and year the expected gross pay and the
//   deductions (the computed default, or a typed override); for the household the zvE from an
//   Einkommensteuerbescheid (with its document) and a typed tax rate. Rows are append-only: a
//   change is a new row naming the current one (supersedes_id), "Entfernen" is a withdrawal
//   (withdrawn = true, repeating the current value). The server refuses a write against a row
//   that is no longer current (income_input.not_current), e.g. after a change in another tab.
// - Phase 4b (phase-4b.md §10): the gross pay's source chip and the Kinder and Haushalt blocks
//   with the links to the Anlage N and the Jahresfragen (components/householdReturn.tsx), and
//   per owner and year „Kein Arbeitslohn in <Jahr>“ or, while it is in force, „Kein
//   Arbeitslohn angegeben“ with „Aufheben“ (D195, components/noPay.tsx).
// - TaxEffectLine (Steuern page): the "Steuerwirkung" of the draft's result, from
//   GET /api/tax-rate, with its source (Schätzung / Bescheid / manuell).
//
// Every euro figure and rate shown here is one the backend computed (principle 1): the default
// deductions, the zvE, the effect and the effective rate. The screen formats and links.
import Link from "next/link";
import { useCallback, useId, useMemo, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError, ruleCodes } from "@/api/errors";
import type {
  IncomeInput,
  IncomeInputCreate,
  IncomeInputKind,
  Owner,
  TaxRateResult,
  ZveEstimate,
} from "@/api/schemas";
import { useFormat, useT, type Formatter, type Translation } from "@/i18n";
import { income } from "@/i18n/income";

import { DocumentPicker, ReadUpload } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { todayIso } from "./format";
import { GrossSourceChip, HouseholdReturnBlocks } from "./householdReturn";
import {
  decimalInput,
  percentInput,
  toMoneyString,
  toRate,
  toSignedMoneyString,
  type Parsed,
} from "./input";
import { NoPayMarker } from "./noPay";
import { useReturnInputsSync } from "./returnInputsSync";
import { Loading } from "./ui";
import { payInputsHref } from "./taxYear";
import { useLoad } from "./useLoad";

type T = Translation<(typeof income)["de"]>;

function codesOf(error: unknown): string[] {
  return error instanceof ApiError && error.body ? ruleCodes(error.body) : [];
}

/** "33.8" (a percent from the API, already rounded) → "33,8 %". */
function percentText(value: string, f: Formatter): string {
  return `${f.decimal(value)}${f.locale === "de" ? " %" : "%"}`;
}

/** The source chip: "Schätzung 2026 · 81.993,00 € zvE", "Bescheid 2025 · …", "manuell 2026". */
function sourceText(zve: ZveEstimate, t: T, f: Formatter): string {
  const head = `${t.rateSource(zve.source)} ${zve.tax_year}`;
  return zve.amount != null ? `${head} · ${f.money(zve.amount)} zvE` : head;
}

// --- the API ---------------------------------------------------------------------------------

function record(body: IncomeInputCreate): Promise<IncomeInput> {
  return ok(api.POST("/api/income-inputs", { body }));
}

function loadRate(
  taxYear: number,
  result: string,
  propertyId: string | null,
): Promise<TaxRateResult> {
  return ok(
    api.GET("/api/tax-rate", {
      params: {
        query: {
          tax_year: taxYear,
          result,
          ...(propertyId ? { property_id: propertyId } : {}),
        },
      },
    }),
  );
}

// --- the Steuern page: Steuerwirkung under the result ---------------------------------------

/** null: no tariff for the year (tariff.no_file), so no effect can be shown. */
async function loadEffect(
  taxYear: number,
  result: string,
  propertyId: string,
): Promise<TaxRateResult | null> {
  try {
    return await loadRate(taxYear, result, propertyId);
  } catch (error) {
    if (codesOf(error).includes("tariff.no_file")) return null;
    throw error;
  }
}

/**
 * "Steuerwirkung" of the draft's result (§9, step 1): the change in the household's income tax,
 * Soli and church tax that the result causes, its effective rate (or the tariff's slope at a
 * result of 0) and the source of the household income behind it. Without any income:
 * "— · Einkommen eintragen", linking to the Eigentümer page.
 */
export function TaxEffectLine({
  propertyId,
  taxYear,
  result,
}: {
  propertyId: string;
  taxYear: number;
  result: string;
}) {
  const load = useCallback(
    () => loadEffect(taxYear, result, propertyId),
    [taxYear, result, propertyId],
  );
  const state = useLoad(load);
  const t = useT(income);
  const f = useFormat();
  if (state.error != null && state.data === undefined) {
    return (
      <p className="muted tax-subline">
        {t.effectFailed}
      </p>
    );
  }
  if (state.data === undefined) return null;
  const rate = state.data;
  if (rate === null) {
    return (
      <p className="muted tax-subline">
        {t.effectNoTariff(taxYear)}
      </p>
    );
  }
  const effect = rate.effect;
  if (rate.missing || !effect || !rate.zve) {
    return (
      <p className="tax-subline tax-effect">
        <span>{t.effect}</span> <strong>—</strong> ·{" "}
        <Link href={payInputsHref(taxYear)}>{t.enterIncome}</Link>
      </p>
    );
  }
  const negative = effect.effect.startsWith("-");
  const zero = /^-?0+(\.0+)?$/.test(effect.effect);
  const rateText = effect.effective_rate
    ? t.effectEffective(percentText(effect.effective_rate, f))
    : effect.slope
      ? t.effectSlope(percentText(effect.slope, f))
      : null;
  const tariffNote =
    rate.tariff.reused_from != null ? t.tariff(rate.tariff.reused_from) : null;
  return (
    <p className="tax-subline tax-effect">
      <span>
        {t.effectWithSource(
          effect.source === "estimate" ? t.estimated : t.rateSource(effect.source),
        )}
      </span>{" "}
      <strong>{f.money(effect.effect)}</strong>
      {zero ? null : (
        <span className="muted">{negative ? t.lessTax : t.moreTax}</span>
      )}
      {rateText ? <span className="muted"> · {rateText}</span> : null}
      {" · "}
      <Link
        href={payInputsHref(taxYear)}
        className="badge"
        title={t.effectLinkTitle}
      >
        {sourceText(rate.zve, t, f)}
        {rate.zve.carried_forward ? t.carriedSuffix : null}
      </Link>
      {tariffNote ? <span className="muted"> · {tariffNote}</span> : null}
    </p>
  );
}

// --- the Eigentümer page --------------------------------------------------------------------

const loadProperties = () => ok(api.GET("/api/properties"));

function yearChoices(current: number): number[] {
  return [current - 2, current - 1, current, current + 1];
}

/**
 * Einkommen (§9): the year's income inputs of every owner and of the household, each with its
 * current value and a way to enter, change or remove it. The default deductions come from
 * /api/tax-rate (owner_estimates), computed by the backend.
 */
/** The household's income inputs of one year; with `fixedYear` (Steuern › Eure Angaben, phase 10
 * D281, whose page picks the year) no year picker of its own. */
export function IncomeSection({ owners, fixedYear }: { owners: Owner[]; fixedYear?: number }) {
  const thisYear = Number(todayIso().slice(0, 4));
  const [pickedYear, setYear] = useState(thisYear);
  const year = fixedYear ?? pickedYear;
  const ids = useId();
  const loadRows = useCallback(
    () =>
      ok(
        api.GET("/api/income-inputs", {
          params: { query: { tax_year: year } },
        }),
      ),
    [year],
  );
  const rows = useLoad(loadRows);
  // A Bescheid carried forward from an earlier year needs that year's rental result taken out
  // of its zvE (the property's draft); the first property names it. The owners' estimates
  // shown here do not depend on it.
  const loadYearRate = useCallback(
    () =>
      loadProperties().then((list) =>
        loadRate(year, "0.00", list[0]?.id ?? null),
      ),
    [year],
  );
  const rate = useLoad(loadYearRate);
  const t = useT(income);
  const f = useFormat();

  const reloadRows = rows.reload;
  const reloadRate = rate.reload;
  const reloadBoth = useCallback(() => {
    reloadRows();
    reloadRate();
  }, [reloadRows, reloadRate]);
  // A pay input moves the guide's points on Eure Angaben (anlage_n.no_pay), so a write there
  // reloads them too (components/returnInputsSync.ts).
  const changed = useReturnInputsSync(reloadBoth);

  return (
    <div className="stack">
      {fixedYear === undefined ? (
        <div className="row">
          <label htmlFor={`${ids}-year`} className="inline">
            {t.taxYear}
            <select
              id={`${ids}-year`}
              value={year}
              onChange={(event) => setYear(Number(event.target.value))}
            >
              {yearChoices(thisYear).map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}
      <p className="muted">{t.intro}</p>
      <Loading state={rows}>
        {(list) => (
          <>
            <ZveSummary rate={rate.data} error={rate.error} year={year} />
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.owner}</th>
                    <th>{t.grossHeader(year)}</th>
                    <th>{t.deductionsHeader(year)}</th>
                  </tr>
                </thead>
                <tbody>
                  {owners.map((owner) => {
                    const estimate = rate.data?.owner_estimates.find(
                      (o) => o.owner_id === owner.id,
                    );
                    return (
                      <tr key={owner.id}>
                        <td>{owner.full_name}</td>
                        <td>
                          <InputCell
                            kind="expected_gross_pay"
                            year={year}
                            ownerId={owner.id}
                            current={current(
                              list,
                              "expected_gross_pay",
                              owner.id,
                            )}
                            onChanged={changed}
                          />
                          <GrossSourceChip estimate={estimate} year={year} />
                          <NoPayMarker ownerId={owner.id} year={year} onChanged={changed} />
                        </td>
                        <td>
                          <InputCell
                            kind="deductions_override"
                            year={year}
                            ownerId={owner.id}
                            current={current(
                              list,
                              "deductions_override",
                              owner.id,
                            )}
                            defaultText={
                              estimate
                                ? estimate.deductions_source === "employment"
                                  ? t.fromAnlageN(f.money(estimate.deductions))
                                  : t.estimatedAmount(f.money(estimate.deductions))
                                : t.estimatedOnceGross
                            }
                            onChanged={changed}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <ReadUpload label={t.uploadPay} />
            <p className="muted">{t.confirmedReplace}</p>
            <h3>{t.household}</h3>
            <dl className="stack">
              <div>
                <dt>{t.zveBescheidTerm(year)}</dt>
                <dd>
                  <InputCell
                    kind="bescheid_zve"
                    year={year}
                    ownerId={null}
                    current={current(list, "bescheid_zve", null)}
                    defaultText={t.noBescheid}
                    onChanged={changed}
                  />
                  <ReadUpload label={t.uploadBescheid} />
                </dd>
              </div>
              <div>
                <dt>{t.rateTerm(year)}</dt>
                <dd>
                  <InputCell
                    kind="marginal_rate_override"
                    year={year}
                    ownerId={null}
                    current={current(list, "marginal_rate_override", null)}
                    defaultText={t.rateNotSet}
                    onChanged={changed}
                  />
                </dd>
              </div>
            </dl>
          </>
        )}
      </Loading>
      <HouseholdReturnBlocks year={year} />
    </div>
  );
}

function current(
  list: IncomeInput[],
  kind: IncomeInputKind,
  ownerId: string | null,
): IncomeInput | undefined {
  return list.find(
    (row) => row.kind === kind && (row.owner_id ?? null) === ownerId,
  );
}

/** The household zvE the year uses and where it comes from. */
function ZveSummary({
  rate,
  error,
  year,
}: {
  rate: TaxRateResult | undefined;
  error: unknown;
  year: number;
}) {
  const t = useT(income);
  const f = useFormat();
  if (rate === undefined) {
    if (error == null) return null;
    const codes = codesOf(error);
    const text = codes.includes("tariff.no_file")
      ? t.zveNoTariff(year)
      : t.zveFailed;
    return <p className="muted">{text}</p>;
  }
  if (rate.missing || !rate.zve) {
    return (
      <p className="muted">
        {t.noIncome(year)}
      </p>
    );
  }
  const zve = rate.zve;
  return (
    <p>
      {zve.source === "bescheid" && !zve.carried_forward
        ? t.zveBescheid
        : t.zve}{" "}
      {year}:{" "}
      <strong>{zve.amount != null ? f.money(zve.amount) : "—"}</strong>{" "}
      <span className="badge">{t.rateSource(zve.source)}</span>
      {zve.carried_forward ? (
        <span className="muted">
          {" "}
          {t.carried(zve.tax_year)}
        </span>
      ) : null}
    </p>
  );
}

function valueText(row: IncomeInput, f: Formatter): string {
  const value = row.value as { amount?: string; rate?: string };
  if (row.kind === "marginal_rate_override") return f.percent(value.rate);
  return f.money(value.amount);
}

/** One input's current value with "Eintragen" / "ändern" / "Entfernen", or its inline form. */
function InputCell({
  kind,
  year,
  ownerId,
  current,
  defaultText,
  onChanged,
}: {
  kind: IncomeInputKind;
  year: number;
  ownerId: string | null;
  current: IncomeInput | undefined;
  defaultText?: string;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const t = useT(income);
  const f = useFormat();

  async function withdraw(row: IncomeInput) {
    setBusy(true);
    setBanner(null);
    try {
      await record({
        kind: row.kind,
        tax_year: row.tax_year,
        owner_id: row.owner_id ?? null,
        document_id: row.document_id ?? null,
        value: row.value,
        supersedes_id: row.id,
        withdrawn: true,
      });
    } catch (error) {
      setBanner(
        codesOf(error).includes("income_input.not_current")
          ? t.notCurrent
          : t.removeFailed,
      );
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  if (editing) {
    return (
      <InputForm
        kind={kind}
        year={year}
        ownerId={ownerId}
        replaces={current ?? null}
        onDone={() => {
          setEditing(false);
          onChanged();
        }}
        onCancel={() => setEditing(false)}
      />
    );
  }
  return (
    <div className="stack">
      <div className="row">
        {current ? (
          <span>
            {valueText(current, f)}
            {kind === "deductions_override" ? (
              <span className="muted">{t.manual}</span>
            ) : null}
          </span>
        ) : (
          <span className="muted">{defaultText ?? "–"}</span>
        )}
        <button
          type="button"
          className="link"
          onClick={() => setEditing(true)}
          disabled={busy}
        >
          {current || kind === "deductions_override" ? t.change : t.enter}
        </button>
        {current ? (
          <button
            type="button"
            className="link"
            onClick={() => void withdraw(current)}
            disabled={busy}
          >
            {kind === "deductions_override"
              ? t.useEstimate
              : t.remove}
          </button>
        ) : null}
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
    </div>
  );
}

const FORM_KEYS = ["value.amount", "value.rate", "document_id"] as const;

function parseValue(
  kind: IncomeInputKind,
  raw: string,
): Parsed<IncomeInputCreate["value"]> {
  if (kind === "marginal_rate_override") {
    const rate = toRate(raw, 4);
    return rate.ok ? { ok: true, value: { rate: rate.value } } : rate;
  }
  const amount =
    kind === "bescheid_zve" ? toSignedMoneyString(raw) : toMoneyString(raw);
  return amount.ok ? { ok: true, value: { amount: amount.value } } : amount;
}

function InputForm({
  kind,
  year,
  ownerId,
  replaces,
  onDone,
  onCancel,
}: {
  kind: IncomeInputKind;
  year: number;
  ownerId: string | null;
  replaces: IncomeInput | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const old = replaces?.value as { amount?: string; rate?: string } | undefined;
  const isRate = kind === "marginal_rate_override";
  const valueKey = isRate ? "value.rate" : "value.amount";
  const [raw, setRaw] = useState(
    isRate ? percentInput(old?.rate) : decimalInput(old?.amount),
  );
  const [documentId, setDocumentId] = useState<string | null>(
    replaces?.document_id ?? null,
  );
  const [note, setNote] = useState(replaces?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const form = useFormErrors(useMemo(() => [...FORM_KEYS], []));
  const base = useId();
  const t = useT(income);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const value = values.take(valueKey, parseValue(kind, raw));
    if (kind === "bescheid_zve" && documentId === null) {
      values.messages["document_id"] = [t.pickBescheid];
    }
    if (!values.ok || value === undefined) return form.invalid(values.messages);
    setBusy(true);
    setBanner(null);
    form.clear();
    try {
      await record({
        kind,
        tax_year: year,
        owner_id: ownerId,
        document_id: kind === "bescheid_zve" ? documentId : null,
        value,
        note: note.trim() || null,
        supersedes_id: replaces?.id ?? null,
        withdrawn: false,
      });
      onDone();
    } catch (error) {
      if (codesOf(error).includes("income_input.not_current")) {
        setBanner(
          replaces === null
            ? t.alreadyRecorded
            : t.changedMeanwhile,
        );
      } else {
        form.fail(error);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div className="grid">
        <Field
          id={`${base}-value`}
          label={`${t.kindLabel(kind)} ${year}${isRate ? " (%)" : " (€)"}`}
          description={t.fieldHelp(kind)}
          errors={form.of(valueKey)}
        >
          <input
            type="text"
            inputMode="decimal"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            autoComplete="off"
          />
        </Field>
        {kind === "bescheid_zve" ? (
          <Field
            id={`${base}-document`}
            label={t.bescheid}
            description={t.bescheidDescription}
            errors={form.of("document_id")}
          >
            <DocumentPicker value={documentId} onChange={setDocumentId} />
          </Field>
        ) : null}
        <Field
          id={`${base}-note`}
          label={t.note}
          description={t.noteDescription}
        >
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={500}
            autoComplete="off"
          />
        </Field>
      </div>
      {replaces !== null ? (
        <p className="muted hint">{t.kept}</p>
      ) : null}
      <div className="row">
        <Submit busy={busy}>
          {replaces === null ? t.enter : t.save}
        </Submit>
        <button type="button" className="link" onClick={onCancel}>
          {t.cancel}
        </button>
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}
