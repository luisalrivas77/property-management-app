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
//   with the links to the Anlage N and the Jahresfragen (components/householdReturn.tsx).
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

import { DocumentPicker, ReadUpload } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatDecimal, formatMoney, formatPercent, todayIso } from "./format";
import { GrossSourceChip, HouseholdReturnBlocks } from "./householdReturn";
import {
  decimalInput,
  percentInput,
  toMoneyString,
  toRate,
  toSignedMoneyString,
  type Parsed,
} from "./input";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

type RateSource = ZveEstimate["source"];

const SOURCE_LABEL: Record<RateSource, string> = {
  estimate: "Schätzung",
  bescheid: "Bescheid",
  override: "manuell",
};

const KIND_LABEL: Record<IncomeInputKind, string> = {
  expected_gross_pay: "Erwartetes Bruttogehalt",
  deductions_override: "Abzüge",
  bescheid_zve: "zvE laut Bescheid",
  marginal_rate_override: "Steuersatz manuell",
};

function codesOf(error: unknown): string[] {
  return error instanceof ApiError && error.body ? ruleCodes(error.body) : [];
}

/** "33.8" (a percent from the API, already rounded) → "33,8 %". */
function percentText(value: string): string {
  return `${formatDecimal(value)} %`;
}

/** The source chip: "Schätzung 2026 · 81.993,00 € zvE", "Bescheid 2025 · …", "manuell 2026". */
function sourceText(zve: ZveEstimate): string {
  const head = `${SOURCE_LABEL[zve.source]} ${zve.tax_year}`;
  return zve.amount != null ? `${head} · ${formatMoney(zve.amount)} zvE` : head;
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
  if (state.error != null && state.data === undefined) {
    return (
      <p className="muted tax-subline">
        Steuerwirkung: konnte nicht berechnet werden.
      </p>
    );
  }
  if (state.data === undefined) return null;
  const rate = state.data;
  if (rate === null) {
    return (
      <p className="muted tax-subline">
        Steuerwirkung: für {taxYear} ist kein Einkommensteuertarif hinterlegt.
      </p>
    );
  }
  const effect = rate.effect;
  if (rate.missing || !effect || !rate.zve) {
    return (
      <p className="tax-subline tax-effect">
        <span>Steuerwirkung</span> <strong>—</strong> ·{" "}
        <Link href="/owners">Einkommen eintragen</Link>
      </p>
    );
  }
  const negative = effect.effect.startsWith("-");
  const zero = /^-?0+(\.0+)?$/.test(effect.effect);
  const rateText = effect.effective_rate
    ? `${percentText(effect.effective_rate)} effektiv`
    : effect.slope
      ? `Grenzsteuersatz ${percentText(effect.slope)}`
      : null;
  const tariffNote =
    rate.tariff.reused_from != null ? `Tarif ${rate.tariff.reused_from}` : null;
  return (
    <p className="tax-subline tax-effect">
      <span>
        Steuerwirkung (
        {effect.source === "estimate"
          ? "geschätzt"
          : SOURCE_LABEL[effect.source]}
        )
      </span>{" "}
      <strong>{formatMoney(effect.effect)}</strong>
      {zero ? null : (
        <span className="muted"> {negative ? "weniger" : "mehr"} Steuer</span>
      )}
      {rateText ? <span className="muted"> · {rateText}</span> : null}
      {" · "}
      <Link
        href="/owners"
        className="badge"
        title="Einkommen und Steuersatz auf der Seite Eigentümer"
      >
        {sourceText(rate.zve)}
        {rate.zve.carried_forward ? " (fortgeschrieben)" : null}
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
export function IncomeSection({ owners }: { owners: Owner[] }) {
  const thisYear = Number(todayIso().slice(0, 4));
  const [year, setYear] = useState(thisYear);
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

  function changed() {
    rows.reload();
    rate.reload();
  }

  return (
    <div className="stack">
      <div className="row">
        <label htmlFor={`${ids}-year`} className="inline">
          Steuerjahr
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
      <p className="muted">
        Aus dem erwarteten Bruttogehalt schätzt die App das zu versteuernde
        Einkommen (zvE) und damit, wie sich das Ergebnis der Vermietung auf die
        Steuer auswirkt. Angaben gelten auch für die Folgejahre, bis ein
        späteres Jahr eigene Angaben hat; ein Jahr mit eigenen Angaben nutzt nur
        diese.
      </p>
      <Loading state={rows}>
        {(list) => (
          <>
            <ZveSummary rate={rate.data} error={rate.error} year={year} />
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Eigentümer</th>
                    <th>Erwartetes Bruttogehalt {year}</th>
                    <th>Abzüge {year}</th>
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
                                  ? `aus Anlage N und Vorsorge: ${formatMoney(estimate.deductions)}`
                                  : `geschätzt: ${formatMoney(estimate.deductions)}`
                                : "geschätzt, sobald ein Bruttogehalt eingetragen ist"
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
            <ReadUpload label="Lohnsteuerbescheinigung oder Gehaltsabrechnung hochladen" />
            <p className="muted">
              Bestätigte Zahlen einer Lohnsteuerbescheinigung oder
              Gehaltsabrechnung ersetzen das erwartete Bruttogehalt dieses
              Jahres; der Steuersatz wird dann daraus berechnet.
            </p>
            <h3>Haushalt (gemeinsame Veranlagung)</h3>
            <dl className="stack">
              <div>
                <dt>zvE laut Bescheid ({year}, Beleg)</dt>
                <dd>
                  <InputCell
                    kind="bescheid_zve"
                    year={year}
                    ownerId={null}
                    current={current(list, "bescheid_zve", null)}
                    defaultText="kein Bescheid erfasst"
                    onChanged={changed}
                  />
                </dd>
              </div>
              <div>
                <dt>Steuersatz manuell ({year})</dt>
                <dd>
                  <InputCell
                    kind="marginal_rate_override"
                    year={year}
                    ownerId={null}
                    current={current(list, "marginal_rate_override", null)}
                    defaultText="nicht gesetzt: der Satz wird aus dem Einkommen berechnet"
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
  if (rate === undefined) {
    if (error == null) return null;
    const codes = codesOf(error);
    const text = codes.includes("tariff.no_file")
      ? `Für ${year} ist kein Einkommensteuertarif hinterlegt; die Angaben werden trotzdem gespeichert.`
      : "Das zvE konnte gerade nicht berechnet werden.";
    return <p className="muted">{text}</p>;
  }
  if (rate.missing || !rate.zve) {
    return (
      <p className="muted">
        Für {year} sind noch keine Einkommensangaben erfasst.
      </p>
    );
  }
  const zve = rate.zve;
  return (
    <p>
      {zve.source === "bescheid" && !zve.carried_forward
        ? "zvE laut Bescheid"
        : "zvE"}{" "}
      {year}:{" "}
      <strong>{zve.amount != null ? formatMoney(zve.amount) : "—"}</strong>{" "}
      <span className="badge">{SOURCE_LABEL[zve.source]}</span>
      {zve.carried_forward ? (
        <span className="muted">
          {" "}
          · aus den Angaben für {zve.tax_year} fortgeschrieben
        </span>
      ) : null}
    </p>
  );
}

function valueText(row: IncomeInput): string {
  const value = row.value as { amount?: string; rate?: string };
  if (row.kind === "marginal_rate_override") return formatPercent(value.rate);
  return formatMoney(value.amount);
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
          ? "Die Angabe wurde inzwischen geändert; die Anzeige ist neu geladen."
          : "Das Entfernen hat nicht geklappt. Bitte erneut versuchen.",
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
            {valueText(current)}
            {kind === "deductions_override" ? (
              <span className="muted"> (manuell)</span>
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
          {current || kind === "deductions_override" ? "ändern" : "Eintragen"}
        </button>
        {current ? (
          <button
            type="button"
            className="link"
            onClick={() => void withdraw(current)}
            disabled={busy}
          >
            {kind === "deductions_override"
              ? "Schätzung verwenden"
              : "Entfernen"}
          </button>
        ) : null}
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
    </div>
  );
}

const FORM_KEYS = ["value.amount", "value.rate", "document_id"] as const;

const FIELD_HELP: Record<IncomeInputKind, string> = {
  expected_gross_pay:
    "Der erwartete Bruttoarbeitslohn des ganzen Jahres, wie er später auf der Lohnsteuerbescheinigung steht. Eine Schätzung genügt.",
  deductions_override:
    "Ersetzt die geschätzten Abzüge ganz: Werbungskosten, Sozialversicherung und Sonderausgaben zusammen, so wie sie vom Bruttogehalt bis zum zvE abgehen.",
  bescheid_zve:
    "Das zu versteuernde Einkommen laut Einkommensteuerbescheid (gemeinsame Veranlagung). Es ersetzt die Schätzung für dieses Jahr; das Ergebnis der Vermietung, das darin steckt, rechnet die App heraus.",
  marginal_rate_override:
    "Ein fester Steuersatz einschließlich Solidaritätszuschlag und Kirchensteuer. Er ersetzt für dieses Jahr die Berechnung aus dem Einkommen.",
};

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

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const value = values.take(valueKey, parseValue(kind, raw));
    if (kind === "bescheid_zve" && documentId === null) {
      values.messages["document_id"] = [
        "Bitte den Einkommensteuerbescheid als Beleg auswählen.",
      ];
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
            ? "Für dieses Jahr ist schon ein Wert erfasst. Bitte abbrechen; die Anzeige zeigt ihn nach dem Neuladen."
            : "Der Wert wurde inzwischen geändert. Bitte abbrechen und erneut ändern.",
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
          label={`${KIND_LABEL[kind]} ${year}${isRate ? " (%)" : " (€)"}`}
          description={FIELD_HELP[kind]}
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
            label="Einkommensteuerbescheid"
            description="Der Bescheid, auf dem das zvE steht; er wird als Beleg verknüpft."
            errors={form.of("document_id")}
          >
            <DocumentPicker value={documentId} onChange={setDocumentId} />
          </Field>
        ) : null}
        <Field
          id={`${base}-note`}
          label="Notiz (optional)"
          description="Zum Beispiel woher der Wert stammt."
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
        <p className="muted hint">Die bisherige Angabe bleibt im Verlauf.</p>
      ) : null}
      <div className="row">
        <Submit busy={busy}>
          {replaces === null ? "Eintragen" : "Speichern"}
        </Submit>
        <button type="button" className="link" onClick={onCancel}>
          Abbrechen
        </button>
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}
