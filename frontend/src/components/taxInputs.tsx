"use client";

// Steuerliche Angaben (docs/phases/phase-3.md §7, §10, §11, D58): the inputs a person types
// for the Anlage V draft, written through POST /properties/{id}/tax-inputs.
//
// - Rows are append-only. A change is a new row naming the current one (supersedes_id); "keine
//   Angabe" after an answer is a withdrawal (withdrawn = true, repeating the current value).
//   The server refuses a write against a row that is no longer current (tax_input.not_current),
//   e.g. after a change in another tab; the form then says so and reloads.
// - On a confirmed allocation (Erhaltung / Renovierung): the §82b spread, the treatment of a
//   renovation and the exclusion from the 15 % test. The reason "Erweiterung" is offered only
//   on a renovation treated as Herstellungskosten, and while it is saved the treatment cannot
//   leave Herstellungskosten (D78; the server refuses both with
//   tax_input.extension_needs_herstellung).
// - On the unit page: the ortsübliche Miete. For an ETW or EFH it is the flat's (the property's,
//   unit_id null, D71); for an MFH each unit's.
// - On the tenancy form: the rent's due day (D67) and the running-cost terms (D72).
// - On the Steuern page, beside the AfA: a shorter useful life from a Gutachten (D79), the
//   years and the first tax year, its document required; the server refuses a life not
//   shorter than the statutory one (tax_input.useful_life_not_shorter).
// - On the Steuern page: the Einbauküche (D80), its value, useful life (default 10 years),
//   "im Kaufpreis enthalten" (checked and locked: a kitchen bought separately needs its own
//   acquisition date and is refused, tax_input.movable_asset_separate_unsupported) and an
//   optional Beleg; the server refuses a first year other than the purchase year
//   (tax_input.movable_asset_year), a value not below the building's share
//   (tax_input.movable_asset_exceeds_building), a missing price split
//   (tax_input.movable_asset_no_split) and a missing handover date
//   (tax_input.movable_asset_no_purchase_date).
// - On Steuern › Eure Angaben: the seller's renovation inside the purchase price (phase 3 D287),
//   claimed openly; the card explains the saving, BFH IX R 34/03 and a refusal; the server
//   refuses work begun before the handover (tax_input.price_renovation_before_handover), an
//   amount not below the building's share (tax_input.price_renovation_exceeds_building) and a
//   property without a handover date (tax_input.price_renovation_no_purchase_date).
// - On the Steuern page: the Grundsteuer schedule (phase-5.md D126, §11), the Bescheid's annual
//   amount, its instalments (dates and amounts), the rhythm and the payment mode, the Bescheid
//   required; the server refuses instalments outside the first year, out of order or not
//   summing to the annual amount (tax_input.grundsteuer_schedule_invalid).
//
// The labels live here, not in labels.ts (phase 3 step 2 scope).
import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError, RULE_CODE_TEXT, ruleCodes } from "@/api/errors";
import type { components } from "@/api/types";
import { translate, useFormat, useT } from "@/i18n";
import { common } from "@/i18n/common";
import { localized } from "@/i18n/locale";
import {
  EXCLUSION_REASON_DE,
  EXCLUSION_REASON_EN,
  MARKET_RENT_SOURCE_DE,
  MARKET_RENT_SOURCE_EN,
  OPERATING_COSTS_TERMS_DE,
  OPERATING_COSTS_TERMS_EN,
  PRICE_RENOVATION_STATUS_DE,
  PRICE_RENOVATION_STATUS_EN,
  RENOVATION_TREATMENT_DE,
  RENOVATION_TREATMENT_EN,
  taxInputsText,
} from "@/i18n/taxInputs";

import { DocumentPicker } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { todayIso } from "./format";
import { decimalInput, toMoneyString } from "./input";
import { GRUNDSTEUER_MODE, GRUNDSTEUER_RHYTHM } from "./labels";
import { Loading } from "./ui";
import { useLoad } from "./useLoad";

type Schemas = components["schemas"];
export type TaxInput = Schemas["TaxInput"];
export type TaxInputCreate = Schemas["TaxInputCreate"];
export type TaxInputKind = Schemas["TaxInputKind"];
export type OperatingCostsTerms = Schemas["OperatingCostsTerms"];
type MarketRentValue = Schemas["MarketRent"];
type AllocationLike = Pick<Schemas["Allocation"], "id" | "amount" | "category" | "property_id" | "status">;
type RenovationChoice = Schemas["RenovationTreatment"]["treatment"];
type ExclusionReason = Schemas["ExcludeFrom15Pct"]["reason"];
type MarketRentSource = MarketRentValue["source"];

// --- labels ------------------------------------------------------------------------------------

// The German and English records live in i18n/taxInputs.ts; localized() reads them in the
// language chosen when shown (phase 10 D284).
export const OPERATING_COSTS_TERMS: Record<OperatingCostsTerms, string> = localized(OPERATING_COSTS_TERMS_DE, OPERATING_COSTS_TERMS_EN);

const RENOVATION_TREATMENT: Record<RenovationChoice, string> = localized(RENOVATION_TREATMENT_DE, RENOVATION_TREATMENT_EN);

const EXCLUSION_REASON: Record<ExclusionReason, string> = localized(EXCLUSION_REASON_DE, EXCLUSION_REASON_EN);

const MARKET_RENT_SOURCE: Record<MarketRentSource, string> = localized(MARKET_RENT_SOURCE_DE, MARKET_RENT_SOURCE_EN);

const SPREAD_YEARS = [2, 3, 4, 5] as const;

/** Codes the server answers with, by the field they belong to (else the banner). */
// The two rule-code texts are getters, so they are read in the language chosen when shown,
// not the one of the moment this module loaded (phase 10 D284).
const CODE_MESSAGES: Record<string, string> = localized(
  {
    "tax_input.not_current":
      "Diese Angabe wurde inzwischen geändert (z. B. in einem anderen Fenster). Bitte die Seite neu laden und erneut prüfen.",
    "tax_input.allocation_not_confirmed": "Steuerliche Angaben gibt es nur für eine bestätigte Zuordnung.",
    "tax_input.scope_invalid": "Diese Angabe passt nicht zu dieser Zuordnung oder Einheit.",
    get "tax_input.extension_needs_herstellung"() {
      return RULE_CODE_TEXT["tax_input.extension_needs_herstellung"] ?? "";
    },
    get "tax_input.useful_life_not_shorter"() {
      return RULE_CODE_TEXT["tax_input.useful_life_not_shorter"] ?? "";
    },
  },
  {
    "tax_input.not_current":
      "This detail has been changed in the meantime (e.g. in another window). Please reload the page and check again.",
    "tax_input.allocation_not_confirmed": "Tax details exist only for a confirmed categorisation.",
    "tax_input.scope_invalid": "This detail does not fit this categorisation or unit.",
    get "tax_input.extension_needs_herstellung"() {
      return RULE_CODE_TEXT["tax_input.extension_needs_herstellung"] ?? "";
    },
    get "tax_input.useful_life_not_shorter"() {
      return RULE_CODE_TEXT["tax_input.useful_life_not_shorter"] ?? "";
    },
  },
);

function entries<K extends string>(labels: Record<K, string>): [K, string][] {
  return Object.entries(labels) as [K, string][];
}

// --- the API ---------------------------------------------------------------------------------

function loadInputs(propertyId: string, kind?: TaxInputKind): Promise<TaxInput[]> {
  return ok(
    api.GET("/api/properties/{property_id}/tax-inputs", {
      params: { path: { property_id: propertyId }, query: kind ? { kind } : {} },
    }),
  );
}

function record(propertyId: string, body: TaxInputCreate): Promise<TaxInput> {
  return ok(api.POST("/api/properties/{property_id}/tax-inputs", { params: { path: { property_id: propertyId } }, body }));
}

/**
 * One question's answer change, as at most one new row: nothing when it stays the same, a
 * withdrawal when it is cleared, a first answer or a superseding one otherwise.
 */
async function answer(
  propertyId: string,
  scope: Pick<TaxInputCreate, "kind" | "allocation_id" | "unit_id">,
  current: TaxInput | undefined,
  value: TaxInputCreate["value"] | null,
): Promise<boolean> {
  if (value === null) {
    if (current === undefined) return false;
    await record(propertyId, { property_id: propertyId, ...scope, value: current.value, supersedes_id: current.id, withdrawn: true });
    return true;
  }
  if (current !== undefined && JSON.stringify(current.value) === JSON.stringify(value)) return false;
  await record(propertyId, { property_id: propertyId, ...scope, value, supersedes_id: current?.id ?? null, withdrawn: false });
  return true;
}

function codesOf(error: unknown): string[] {
  return error instanceof ApiError && error.body ? ruleCodes(error.body) : [];
}

function messageOf(error: unknown): string | null {
  const code = codesOf(error).find((c) => c in CODE_MESSAGES);
  return code === undefined ? null : (CODE_MESSAGES[code] ?? null);
}

// --- on an allocation (components/allocations.tsx) ----------------------------------------

const DECIDABLE: ReadonlySet<string> = new Set(["repairs", "renovation"]);

/**
 * The tax questions of a line's confirmed rows that are repairs or a renovation (§11 "Inputs"):
 * Auf 2–5 Jahre verteilen (§ 82b EStDV), Renovierung ist …, Zählt nicht zur 15 %-Grenze.
 * Nothing for proposed rows: an answer belongs to a confirmed allocation.
 */
export function AllocationTaxInputs({ allocations }: { allocations: AllocationLike[] }) {
  const rows = allocations.filter(
    (a) => a.status === "confirmed" && a.property_id && DECIDABLE.has(a.category) && !/^-?0+(\.0+)?$/.test(a.amount),
  );
  if (rows.length === 0) return null;
  return <AllocationTaxInputsList rows={rows} />;
}

function AllocationTaxInputsList({ rows }: { rows: AllocationLike[] }) {
  const t = useT(taxInputsText);
  const f = useFormat();
  return (
    <section className="stack tax-inputs">
      <h3>{t.heading}</h3>
      {rows.map((row) => (
        <AllocationQuestions key={row.id} allocation={row} label={rows.length > 1 ? f.money(row.amount) : null} />
      ))}
    </section>
  );
}

function AllocationQuestions({ allocation, label }: { allocation: AllocationLike; label: string | null }) {
  const propertyId = allocation.property_id as string;
  const load = useCallback(
    async () => (await loadInputs(propertyId)).filter((row) => row.allocation_id === allocation.id),
    [propertyId, allocation.id],
  );
  const state = useLoad(load);
  return (
    <Loading state={state}>
      {(current) => (
        <AllocationQuestionsForm
          key={current.map((row) => row.id).join("|")}
          allocation={allocation}
          propertyId={propertyId}
          current={current}
          label={label}
          onSaved={state.reload}
        />
      )}
    </Loading>
  );
}

function AllocationQuestionsForm({
  allocation,
  propertyId,
  current,
  label,
  onSaved,
}: {
  allocation: AllocationLike;
  propertyId: string;
  current: TaxInput[];
  label: string | null;
  onSaved: () => void;
}) {
  const t = useT(taxInputsText);
  const of = (kind: TaxInputKind) => current.find((row) => row.kind === kind);
  const spreadRow = of("spread_82b");
  const treatmentRow = of("renovation_treatment");
  const exclusionRow = of("exclude_from_15pct");
  const [spread, setSpread] = useState(spreadRow ? String((spreadRow.value as Schemas["Spread82b"]).years) : "");
  const [treatment, setTreatment] = useState(
    treatmentRow ? (treatmentRow.value as Schemas["RenovationTreatment"]).treatment : "",
  );
  const [exclusion, setExclusion] = useState(exclusionRow ? (exclusionRow.value as Schemas["ExcludeFrom15Pct"]).reason : "");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const form = useFormErrors([]);
  const [banner, setBanner] = useState<string | null>(null);
  const base = useId();
  const renovation = allocation.category === "renovation";
  // D78: an Erweiterung leaves the 15 % count only as Herstellungskosten. The saved exclusion
  // locks the treatment (the server refuses giving it up), so it is withdrawn first.
  const extensionSaved = (exclusionRow?.value as Schemas["ExcludeFrom15Pct"] | undefined)?.reason === "extension";
  const extensionAllowed = renovation && treatment === "herstellung";
  const exclusionReasons = entries(EXCLUSION_REASON).filter(
    ([value]) => value !== "extension" || extensionAllowed || exclusion === "extension",
  );

  function changeTreatment(value: RenovationChoice | "") {
    setTreatment(value);
    // An unsaved Erweiterung choice goes with Herstellungskosten.
    if (value !== "herstellung" && exclusion === "extension" && !extensionSaved) setExclusion("");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    setBusy(true);
    setBanner(null);
    setSaved(false);
    form.clear();
    const scope = { allocation_id: allocation.id };
    try {
      let changed = await answer(propertyId, { kind: "spread_82b", ...scope }, spreadRow, spread ? { years: Number(spread) } : null);
      if (renovation) {
        changed =
          (await answer(
            propertyId,
            { kind: "renovation_treatment", ...scope },
            treatmentRow,
            treatment ? { treatment: treatment as RenovationChoice } : null,
          )) || changed;
      }
      changed =
        (await answer(
          propertyId,
          { kind: "exclude_from_15pct", ...scope },
          exclusionRow,
          exclusion ? { reason: exclusion as ExclusionReason } : null,
        )) || changed;
      setSaved(changed);
      if (changed) onSaved();
    } catch (error) {
      const message = messageOf(error);
      if (message) setBanner(message);
      else form.fail(error);
      onSaved();
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      {label ? <p className="cell-detail">{t.rowOf(label)}</p> : null}
      <div className="grid">
        <Field
          id={`${base}-spread`}
          label={t.spreadLabel}
          description={t.spreadDescription}
        >
          <select value={spread} onChange={(e) => setSpread(e.target.value)}>
            <option value="">{t.noSpread}</option>
            {SPREAD_YEARS.map((years) => (
              <option key={years} value={String(years)}>
                {t.spreadYears(years)}
              </option>
            ))}
          </select>
        </Field>
        {renovation ? (
          <Field
            id={`${base}-treatment`}
            label={t.treatmentLabel}
            hint={extensionSaved ? t.extensionLocksTreatment : undefined}
            description={t.treatmentDescription}
          >
            <select value={treatment} onChange={(e) => changeTreatment(e.target.value as RenovationChoice | "")}>
              <option value="" disabled={extensionSaved}>
                {t.notClassified}
              </option>
              {entries(RENOVATION_TREATMENT).map(([value, text]) => (
                <option key={value} value={value} disabled={extensionSaved && value !== "herstellung"}>
                  {text}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field
          id={`${base}-exclusion`}
          label={t.exclusionLabel}
          hint={extensionAllowed ? undefined : t.extensionHint}
          description={t.exclusionDescription}
        >
          <select value={exclusion} onChange={(e) => setExclusion(e.target.value as ExclusionReason | "")}>
            <option value="">{t.countsTowardLimit}</option>
            {exclusionReasons.map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="row">
        <Submit busy={busy}>{t.saveAnswers}</Submit>
        {saved ? <span className="muted">{t.savedNote}</span> : null}
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- on the unit page: ortsübliche Miete -------------------------------------------------------

const MARKET_KEYS = ["value.cold_rent_per_m2", "value.valid_from", "value.source", "document_id"] as const;

/**
 * The ortsübliche Miete of a Wohnung (D61, D71): the flat's for an ETW or EFH (unit_id null),
 * the unit's for an MFH. Lists the values in force (newest valid_from first); a new value from
 * another date stands beside the old one, a correction or a withdrawal replaces it.
 */
export function MarketRentSection({ propertyId, unitId, perUnit }: { propertyId: string; unitId: string; perUnit: boolean }) {
  const t = useT(taxInputsText);
  const f = useFormat();
  const scopeUnit = perUnit ? unitId : null;
  const load = useCallback(
    async () => (await loadInputs(propertyId, "market_rent")).filter((row) => (row.unit_id ?? null) === scopeUnit),
    [propertyId, scopeUnit],
  );
  const state = useLoad(load);
  const [editing, setEditing] = useState<TaxInput | "new" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function withdraw(row: TaxInput) {
    setBusy(true);
    setBanner(null);
    try {
      await answer(propertyId, { kind: "market_rent", unit_id: row.unit_id ?? null }, row, null);
    } catch (error) {
      setBanner(messageOf(error) ?? t.withdrawFailed);
    } finally {
      setBusy(false);
      state.reload();
    }
  }

  return (
    <div className="stack">
      {perUnit ? null : (
        <p className="muted">{t.wholeFlat}</p>
      )}
      <Loading state={state}>
        {(rows) => {
          const sorted = [...rows].sort((a, b) =>
            (b.value as MarketRentValue).valid_from.localeCompare((a.value as MarketRentValue).valid_from),
          );
          return sorted.length === 0 ? (
            <p className="muted">{t.noMarketRent}</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.validFrom}</th>
                    <th>{t.coldRentPerM2}</th>
                    <th>{t.source}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((row) => {
                    const value = row.value as MarketRentValue;
                    return (
                      <tr key={row.id}>
                        <td>{f.date(value.valid_from)}</td>
                        <td>{f.money(value.cold_rent_per_m2)}</td>
                        <td>{MARKET_RENT_SOURCE[value.source]}</td>
                        <td>
                          <div className="row">
                            <button type="button" className="link" onClick={() => setEditing(row)} disabled={busy}>
                              {t.correct}
                            </button>
                            <button type="button" className="link" onClick={() => void withdraw(row)} disabled={busy}>
                              {t.withdraw}
                            </button>
                          </div>
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
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      {editing === null ? (
        <div>
          <button type="button" className="secondary" onClick={() => setEditing("new")}>
            {t.enterMarketRent}
          </button>
        </div>
      ) : (
        <MarketRentForm
          key={editing === "new" ? "new" : editing.id}
          propertyId={propertyId}
          unitId={scopeUnit}
          replaces={editing === "new" ? null : editing}
          onDone={() => {
            setEditing(null);
            state.reload();
          }}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function MarketRentForm({
  propertyId,
  unitId,
  replaces,
  onDone,
  onCancel,
}: {
  propertyId: string;
  unitId: string | null;
  replaces: TaxInput | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const t = useT(taxInputsText);
  const c = useT(common);
  const f = useFormat();
  const old = replaces?.value as MarketRentValue | undefined;
  const [rent, setRent] = useState(old ? decimalInput(old.cold_rent_per_m2) : "");
  const [validFrom, setValidFrom] = useState(old?.valid_from ?? todayIso());
  const [source, setSource] = useState<MarketRentSource>(old?.source ?? "mietspiegel");
  const [documentId, setDocumentId] = useState<string | null>(replaces?.document_id ?? null);
  const [note, setNote] = useState(replaces?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const form = useFormErrors(useMemo(() => [...MARKET_KEYS], []));
  const base = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const perM2 = values.take("value.cold_rent_per_m2", toMoneyString(rent));
    if (!validFrom) values.messages["value.valid_from"] = [t.validFromRequired];
    if (!values.ok || perM2 === undefined) return form.invalid(values.messages);
    setBusy(true);
    setBanner(null);
    form.clear();
    try {
      await record(propertyId, {
        property_id: propertyId,
        kind: "market_rent",
        unit_id: unitId,
        document_id: documentId,
        value: { cold_rent_per_m2: perM2, valid_from: validFrom, source },
        note: note.trim() || null,
        supersedes_id: replaces?.id ?? null,
        withdrawn: false,
      });
      onDone();
    } catch (error) {
      if (codesOf(error).includes("tax_input.not_current") && replaces === null) {
        setBanner(t.marketDateTaken);
      } else {
        const message = messageOf(error);
        if (message) setBanner(message);
        else form.fail(error);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div className="grid">
        <Field
          id={`${base}-rent`}
          label={t.marketRentLabel}
          description={t.marketRentDescription}
          errors={form.of("value.cold_rent_per_m2")}
        >
          <input type="text" inputMode="decimal" value={rent} onChange={(e) => setRent(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id={`${base}-from`}
          label={t.validFrom}
          description={t.validFromDescription}
          errors={form.of("value.valid_from")}
        >
          <input type="date" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} disabled={replaces !== null} />
        </Field>
        <Field id={`${base}-source`} label={t.source} description={t.sourceDescription} errors={form.of("value.source")}>
          <select value={source} onChange={(e) => setSource(e.target.value as MarketRentSource)}>
            {entries(MARKET_RENT_SOURCE).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id={`${base}-document`}
          label={t.receiptOptional}
          description={t.marketReceiptDescription}
          errors={form.of("document_id")}
        >
          <DocumentPicker value={documentId} onChange={setDocumentId} />
        </Field>
        <Field id={`${base}-note`} label={t.noteOptional} description={t.marketNoteDescription}>
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} autoComplete="off" />
        </Field>
      </div>
      {replaces !== null ? (
        <p className="muted hint">{t.marketCorrectionHint(f.date(old?.valid_from))}</p>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{replaces === null ? t.enter : t.saveCorrection}</Submit>
        <button type="button" className="link" onClick={onCancel}>
          {c.cancel}
        </button>
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- on the Steuern page: a shorter useful life from a Gutachten (D79) --------------------------

type UsefulLifeValue = Schemas["UsefulLife"];

const LIFE_KEYS = ["value.years", "tax_year", "document_id"] as const;
const LIFE_CODE_FIELDS = { "tax_input.useful_life_not_shorter": "value.years" } as const;

/** A whole number from `min` up, or a message. */
function wholeNumber(raw: string, min: number, message: string): { ok: true; value: number } | { ok: false; message: string } {
  const text = raw.trim();
  if (!/^\d+$/.test(text) || Number(text) < min) return { ok: false, message };
  return { ok: true, value: Number(text) };
}

/**
 * The AfA over a shorter actual useful life (§ 7 Abs. 4 Satz 2 EStG, D79): the years a
 * Gutachten states and the first tax year they apply to. One value per first year; a later
 * Gutachten from another year stands beside the earlier one and restarts the count from the
 * book value of that year. The server refuses a life not shorter than the statutory one
 * (tax_input.useful_life_not_shorter). `onChanged` reloads the draft.
 */
export function UsefulLifeSection({
  propertyId,
  defaultYear,
  onChanged,
}: {
  propertyId: string;
  defaultYear: number;
  onChanged: () => void;
}) {
  const t = useT(taxInputsText);
  const load = useCallback(() => loadInputs(propertyId, "useful_life"), [propertyId]);
  const state = useLoad(load);
  const [editing, setEditing] = useState<TaxInput | "new" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function changed() {
    state.reload();
    onChanged();
  }

  async function withdraw(row: TaxInput) {
    setBusy(true);
    setBanner(null);
    try {
      await record(propertyId, {
        property_id: propertyId,
        kind: "useful_life",
        tax_year: row.tax_year,
        document_id: row.document_id,
        value: row.value,
        supersedes_id: row.id,
        withdrawn: true,
      });
    } catch (error) {
      setBanner(messageOf(error) ?? t.withdrawFailed);
    } finally {
      setBusy(false);
      changed();
    }
  }

  return (
    <div className="stack">
      <Loading state={state}>
        {(rows) => {
          const sorted = [...rows].sort((a, b) => (a.tax_year ?? 0) - (b.tax_year ?? 0));
          return sorted.length === 0 ? (
            <p className="muted">{t.statutoryAfa}</p>
          ) : (
            <ul className="stack">
              {sorted.map((row) => (
                <li key={row.id} className="row">
                  <span>{t.lifeRow((row.value as UsefulLifeValue).years, row.tax_year)}</span>
                  <span className="spacer" />
                  <button type="button" className="link" onClick={() => setEditing(row)} disabled={busy}>
                    {t.correct}
                  </button>
                  <button type="button" className="link" onClick={() => void withdraw(row)} disabled={busy}>
                    {t.withdraw}
                  </button>
                </li>
              ))}
            </ul>
          );
        }}
      </Loading>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      {editing === null ? (
        <div>
          <button type="button" className="secondary" onClick={() => setEditing("new")}>
            {t.enterLife}
          </button>
        </div>
      ) : (
        <UsefulLifeForm
          key={editing === "new" ? "new" : editing.id}
          propertyId={propertyId}
          defaultYear={defaultYear}
          replaces={editing === "new" ? null : editing}
          onDone={() => {
            setEditing(null);
            changed();
          }}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function UsefulLifeForm({
  propertyId,
  defaultYear,
  replaces,
  onDone,
  onCancel,
}: {
  propertyId: string;
  defaultYear: number;
  replaces: TaxInput | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const t = useT(taxInputsText);
  const c = useT(common);
  const old = replaces?.value as UsefulLifeValue | undefined;
  const [years, setYears] = useState(old ? String(old.years) : "");
  const [fromYear, setFromYear] = useState(String(replaces?.tax_year ?? defaultYear));
  const [documentId, setDocumentId] = useState<string | null>(replaces?.document_id ?? null);
  const [note, setNote] = useState(replaces?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const form = useFormErrors(useMemo(() => [...LIFE_KEYS], []), LIFE_CODE_FIELDS);
  const base = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const life = values.take("value.years", wholeNumber(years, 1, t.lifeYearsRequired));
    const year = values.take("tax_year", wholeNumber(fromYear, 2000, t.taxYearRequired(2026)));
    if (documentId === null) values.messages["document_id"] = [t.lifeDocumentRequired];
    if (!values.ok || life === undefined || year === undefined || documentId === null) return form.invalid(values.messages);
    setBusy(true);
    setBanner(null);
    form.clear();
    try {
      await record(propertyId, {
        property_id: propertyId,
        kind: "useful_life",
        tax_year: year,
        document_id: documentId,
        value: { years: life },
        note: note.trim() || null,
        supersedes_id: replaces?.id ?? null,
        withdrawn: false,
      });
      onDone();
    } catch (error) {
      const codes = codesOf(error);
      if (codes.includes("tax_input.not_current") && replaces === null) {
        setBanner(t.lifeYearTaken);
      } else if (codes.includes("tax_input.scope_invalid")) {
        setBanner(t.lifeBeforePurchase);
      } else if (codes.includes("tax_input.not_current")) {
        setBanner(CODE_MESSAGES["tax_input.not_current"] ?? null);
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
          id={`${base}-years`}
          label={t.lifeLabel}
          description={t.lifeDescription}
          errors={form.of("value.years")}
        >
          <input type="text" inputMode="numeric" value={years} onChange={(e) => setYears(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id={`${base}-from`}
          label={t.fromTaxYear}
          description={t.lifeFromDescription}
          errors={form.of("tax_year")}
        >
          <input
            type="text"
            inputMode="numeric"
            value={fromYear}
            onChange={(e) => setFromYear(e.target.value)}
            disabled={replaces !== null}
            autoComplete="off"
          />
        </Field>
        <Field
          id={`${base}-document`}
          label={t.appraisal}
          description={t.appraisalDescription}
          errors={form.of("document_id")}
        >
          <DocumentPicker value={documentId} onChange={setDocumentId} />
        </Field>
        <Field id={`${base}-note`} label={t.noteOptional} description={t.lifeNoteDescription}>
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} autoComplete="off" />
        </Field>
      </div>
      {replaces !== null ? (
        <p className="muted hint">{t.correctionFromYear(replaces.tax_year)}</p>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{replaces === null ? t.enter : t.saveCorrection}</Submit>
        <button type="button" className="link" onClick={onCancel}>
          {c.cancel}
        </button>
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- on the Steuern page: the Einbauküche, a movable asset with its own AfA (D80) -------------

type MovableAssetValue = Schemas["MovableAsset"];

const MOVABLE_KEYS = ["value.amount", "value.useful_life_years", "tax_year", "document_id"] as const;
const MOVABLE_CODE_FIELDS = {
  "tax_input.movable_asset_exceeds_building": "value.amount",
  "tax_input.movable_asset_year": "tax_year",
} as const;

/**
 * The Einbauküche (D80; BFH IX R 14/15): a movable asset with its own AfA over its useful life
 * (default 10 years), on its own Anlage V line. Bought with the flat, its value (plus its share
 * of the Nebenkosten) leaves the building's AfA basis and its first year is the purchase year;
 * bought later, it starts in the year it was bought. The document is optional (an estimate is
 * allowed; the draft warns without one). One kitchen per flat: a change supersedes it and keeps
 * its first year. `onChanged` reloads the draft.
 */
export function MovableAssetSection({
  propertyId,
  purchaseYear,
  onChanged,
}: {
  propertyId: string;
  purchaseYear: number;
  onChanged: () => void;
}) {
  const t = useT(taxInputsText);
  const f = useFormat();
  const load = useCallback(() => loadInputs(propertyId, "movable_asset"), [propertyId]);
  const state = useLoad(load);
  const [editing, setEditing] = useState<TaxInput | "new" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function changed() {
    state.reload();
    onChanged();
  }

  async function withdraw(row: TaxInput) {
    setBusy(true);
    setBanner(null);
    try {
      await record(propertyId, {
        property_id: propertyId,
        kind: "movable_asset",
        tax_year: row.tax_year,
        document_id: row.document_id,
        value: row.value,
        supersedes_id: row.id,
        withdrawn: true,
      });
    } catch (error) {
      setBanner(messageOf(error) ?? t.withdrawFailed);
    } finally {
      setBusy(false);
      changed();
    }
  }

  return (
    <div className="stack">
      <Loading state={state}>
        {(rows) =>
          rows.length === 0 ? (
            <p className="muted">{t.movableIntro}</p>
          ) : (
            <ul className="stack">
              {rows.map((row) => {
                const value = row.value as MovableAssetValue;
                return (
                  <li key={row.id} className="row">
                    <span>
                      {t.movableRow(
                        f.money(value.amount),
                        value.useful_life_years,
                        row.tax_year,
                        Boolean(value.in_purchase_price),
                        Boolean(row.document_id),
                      )}
                    </span>
                    <span className="spacer" />
                    <button type="button" className="link" onClick={() => setEditing(row)} disabled={busy}>
                      {t.correct}
                    </button>
                    <button type="button" className="link" onClick={() => void withdraw(row)} disabled={busy}>
                      {t.withdraw}
                    </button>
                  </li>
                );
              })}
            </ul>
          )
        }
      </Loading>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      {editing === null ? (
        state.data !== undefined && state.data.length === 0 ? (
          <div>
            <button type="button" className="secondary" onClick={() => setEditing("new")}>
              {t.enterKitchen}
            </button>
          </div>
        ) : null
      ) : (
        <MovableAssetForm
          key={editing === "new" ? "new" : editing.id}
          propertyId={propertyId}
          purchaseYear={purchaseYear}
          replaces={editing === "new" ? null : editing}
          onDone={() => {
            setEditing(null);
            changed();
          }}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function MovableAssetForm({
  propertyId,
  purchaseYear,
  replaces,
  onDone,
  onCancel,
}: {
  propertyId: string;
  purchaseYear: number;
  replaces: TaxInput | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const t = useT(taxInputsText);
  const c = useT(common);
  const old = replaces?.value as MovableAssetValue | undefined;
  const [amount, setAmount] = useState(old ? decimalInput(old.amount) : "");
  const [life, setLife] = useState(String(old?.useful_life_years ?? 10));
  const [documentId, setDocumentId] = useState<string | null>(replaces?.document_id ?? null);
  const [note, setNote] = useState(replaces?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const form = useFormErrors(useMemo(() => [...MOVABLE_KEYS], []), MOVABLE_CODE_FIELDS);
  const base = useId();
  // Only a kitchen bought with the flat is supported (D80 review F1: one bought separately
  // needs its own acquisition date, which the value does not carry yet), so its first year is
  // the purchase year; a correction keeps its year.
  const shownYear = String(replaces?.tax_year ?? purchaseYear);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const money = values.take("value.amount", toMoneyString(amount));
    const years = values.take("value.useful_life_years", wholeNumber(life, 1, t.kitchenLifeRequired));
    if (years !== undefined && years > 25) values.messages["value.useful_life_years"] = [t.max25Years];
    const year = values.take("tax_year", wholeNumber(shownYear, 2000, t.taxYearRequired(2026)));
    if (money !== undefined && Number(money) <= 0) values.messages["value.amount"] = [t.amountAboveZero];
    if (!values.ok || money === undefined || years === undefined || year === undefined) return form.invalid(values.messages);
    setBusy(true);
    setBanner(null);
    form.clear();
    try {
      await record(propertyId, {
        property_id: propertyId,
        kind: "movable_asset",
        tax_year: year,
        document_id: documentId,
        value: { label: "einbaukueche", amount: money, useful_life_years: years, in_purchase_price: true },
        note: note.trim() || null,
        supersedes_id: replaces?.id ?? null,
        withdrawn: false,
      });
      onDone();
    } catch (error) {
      const codes = codesOf(error);
      if (codes.includes("tax_input.not_current") && replaces === null) {
        setBanner(t.kitchenTaken);
      } else if (codes.includes("tax_input.not_current")) {
        setBanner(CODE_MESSAGES["tax_input.not_current"] ?? null);
      } else if (codes.includes("tax_input.scope_invalid")) {
        setBanner(t.kitchenKeepsYear);
      } else {
        form.fail(error);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <p className="muted hint">{t.movableEstimateHint}</p>
      <div className="grid">
        <Field
          id={`${base}-amount`}
          label={t.kitchenValue}
          description={t.kitchenValueDescription}
          errors={form.of("value.amount")}
        >
          <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id={`${base}-life`}
          label={t.lifeYears}
          description={t.kitchenLifeDescription}
          errors={form.of("value.useful_life_years")}
        >
          <input type="text" inputMode="numeric" value={life} onChange={(e) => setLife(e.target.value)} autoComplete="off" />
        </Field>
        <div className="field">
          <label className="inline" htmlFor={`${base}-in-price`}>
            <input id={`${base}-in-price`} type="checkbox" checked disabled readOnly />
            {t.inPurchasePrice}
          </label>
          <p className="muted hint">{t.inPurchasePriceHint}</p>
        </div>
        <Field
          id={`${base}-from`}
          label={t.fromTaxYear}
          description={t.kitchenFromDescription}
          errors={form.of("tax_year")}
        >
          <input
            type="text"
            inputMode="numeric"
            value={shownYear}
            readOnly
            disabled
            autoComplete="off"
          />
        </Field>
        <Field
          id={`${base}-document`}
          label={t.receiptOptional}
          description={t.kitchenReceiptDescription}
          errors={form.of("document_id")}
        >
          <DocumentPicker value={documentId} onChange={setDocumentId} />
        </Field>
        <Field id={`${base}-note`} label={t.noteOptional} description={t.kitchenNoteDescription}>
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} autoComplete="off" />
        </Field>
      </div>
      {replaces !== null ? (
        <p className="muted hint">{t.correctionFromYear(replaces.tax_year)}</p>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{replaces === null ? t.enter : t.saveCorrection}</Submit>
        <button type="button" className="link" onClick={onCancel}>
          {c.cancel}
        </button>
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- on Steuern › Eure Angaben: the seller's renovation in the price (phase 3 D287) -------------

type PriceRenovationValue = Schemas["PriceRenovation"];
type PriceRenovationStatus = PriceRenovationValue["status"];

const PRICE_RENOVATION_STATUS: Record<PriceRenovationStatus, string> = localized(PRICE_RENOVATION_STATUS_DE, PRICE_RENOVATION_STATUS_EN);

const RENOVATION_KEYS = [
  "value.amount",
  "value.amount_net",
  "value.period_start",
  "value.period_end",
  "value.paid_on",
  "value.clause",
  "value.status",
  "document_id",
] as const;
const RENOVATION_CODE_FIELDS = {
  "tax_input.price_renovation_before_handover": "value.period_start",
  "tax_input.price_renovation_paid_before_handover_year": "value.paid_on",
  "tax_input.price_renovation_exceeds_building": "value.amount",
} as const;

/** The anchor the to-dos tax.price_renovation_ask and _not_applied open (components/todos.tsx). */
export const PRICE_RENOVATION_ANCHOR = "tax-price-renovation";

function requiredDate(raw: string, message: string): { ok: true; value: string } | { ok: false; message: string } {
  return /^\d{4}-\d{2}-\d{2}$/.test(raw.trim()) ? { ok: true, value: raw.trim() } : { ok: false, message };
}

/**
 * The seller's renovation inside the purchase price (D287, Q-O180): the Kaufvertrag obliges the
 * seller to renovate after the handover, paid with the price; the household claims that part as
 * its own renovation, openly. The card explains what it saves, that BFH IX R 34/03 points the
 * other way, and what a refusal means (principle 7). One claim per property: entered (claimed),
 * corrected (a superseding row, its status changeable, e.g. after a mistaken „Nein“) or
 * withdrawn. The Finanzamt's answer is normally recorded by the to-do tax.price_renovation_ask.
 * The server refuses work begun before the handover, an amount not below the building's share
 * and a property without a handover date. `onChanged` reloads what reads the draft.
 */
export function PriceRenovationSection({
  propertyId,
  title,
  anchor,
  onChanged,
}: {
  propertyId: string;
  title: string;
  anchor: string;
  onChanged: () => void;
}) {
  const t = useT(taxInputsText);
  const f = useFormat();
  const load = useCallback(() => loadInputs(propertyId, "price_renovation"), [propertyId]);
  const state = useLoad(load);
  const [editing, setEditing] = useState<TaxInput | "new" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const loaded = state.data !== undefined;

  // The to-dos link here by its anchor; the card renders after its load, so the browser's own
  // jump to the hash may have happened before it existed.
  useEffect(() => {
    if (loaded && typeof window !== "undefined" && window.location.hash === `#${anchor}`) {
      document.getElementById(anchor)?.scrollIntoView();
    }
  }, [loaded, anchor]);

  function changed() {
    state.reload();
    onChanged();
  }

  async function withdraw(row: TaxInput) {
    setBusy(true);
    setBanner(null);
    try {
      await record(propertyId, {
        property_id: propertyId,
        kind: "price_renovation",
        document_id: row.document_id,
        value: row.value,
        supersedes_id: row.id,
        withdrawn: true,
      });
    } catch (error) {
      setBanner(messageOf(error) ?? t.withdrawFailed);
    } finally {
      setBusy(false);
      changed();
    }
  }

  return (
    <section className="card stack" aria-labelledby={anchor}>
      <h2 id={anchor}>{title}</h2>
      <p>{t.prWhat}</p>
      <p className="muted">{t.prSaves}</p>
      <p className="muted">{t.prRuling}</p>
      <p className="muted">{t.prRefused}</p>
      <Loading state={state}>
        {(rows) =>
          rows.length === 0 ? (
            <p className="muted">{t.prNone}</p>
          ) : (
            <ul className="stack">
              {rows.map((row) => {
                const value = row.value as PriceRenovationValue;
                return (
                  <li key={row.id} className="stack">
                    <div className="row">
                      <span>
                        {t.prRow(
                          f.money(value.amount),
                          f.money(value.amount_net),
                          f.date(value.period_start),
                          f.date(value.period_end),
                          f.date(value.paid_on),
                        )}
                      </span>
                      <span className="spacer" />
                      <button type="button" className="link" onClick={() => setEditing(row)} disabled={busy}>
                        {t.correct}
                      </button>
                      <button type="button" className="link" onClick={() => void withdraw(row)} disabled={busy}>
                        {t.withdraw}
                      </button>
                    </div>
                    <p className="muted">
                      {t.prStatusLabel}: {PRICE_RENOVATION_STATUS[value.status]}
                    </p>
                  </li>
                );
              })}
            </ul>
          )
        }
      </Loading>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      {editing === null ? (
        state.data !== undefined && state.data.length === 0 ? (
          <div>
            <button type="button" className="secondary" onClick={() => setEditing("new")}>
              {t.prEnter}
            </button>
          </div>
        ) : null
      ) : (
        <PriceRenovationForm
          key={editing === "new" ? "new" : editing.id}
          propertyId={propertyId}
          replaces={editing === "new" ? null : editing}
          onDone={() => {
            setEditing(null);
            changed();
          }}
          onCancel={() => setEditing(null)}
        />
      )}
    </section>
  );
}

function PriceRenovationForm({
  propertyId,
  replaces,
  onDone,
  onCancel,
}: {
  propertyId: string;
  replaces: TaxInput | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const t = useT(taxInputsText);
  const c = useT(common);
  const old = replaces?.value as PriceRenovationValue | undefined;
  const [amount, setAmount] = useState(old ? decimalInput(old.amount) : "");
  const [net, setNet] = useState(old ? decimalInput(old.amount_net) : "");
  const [start, setStart] = useState(old?.period_start ?? "");
  const [end, setEnd] = useState(old?.period_end ?? "");
  const [paid, setPaid] = useState(old?.paid_on ?? "");
  const [clause, setClause] = useState(old?.clause ?? "");
  const [status, setStatus] = useState<PriceRenovationStatus>(old?.status ?? "claimed");
  const [documentId, setDocumentId] = useState<string | null>(replaces?.document_id ?? null);
  const [note, setNote] = useState(replaces?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const form = useFormErrors(useMemo(() => [...RENOVATION_KEYS], []), RENOVATION_CODE_FIELDS);
  const base = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const gross = values.take("value.amount", toMoneyString(amount));
    const netAmount = values.take("value.amount_net", toMoneyString(net));
    const startOn = values.take("value.period_start", requiredDate(start, t.prDateRequired));
    const endOn = values.take("value.period_end", requiredDate(end, t.prDateRequired));
    const paidOn = values.take("value.paid_on", requiredDate(paid, t.prDateRequired));
    if (gross !== undefined && Number(gross) <= 0) values.messages["value.amount"] = [t.amountAboveZero];
    if (netAmount !== undefined && Number(netAmount) <= 0) values.messages["value.amount_net"] = [t.amountAboveZero];
    if (gross !== undefined && netAmount !== undefined && Number(netAmount) > Number(gross)) {
      values.messages["value.amount_net"] = [t.prNetAboveAmount];
    }
    if (startOn !== undefined && endOn !== undefined && endOn < startOn) values.messages["value.period_end"] = [t.prEndBeforeStart];
    if (documentId === null) values.messages["document_id"] = [t.prContractRequired];
    if (
      !values.ok ||
      gross === undefined ||
      netAmount === undefined ||
      startOn === undefined ||
      endOn === undefined ||
      paidOn === undefined ||
      documentId === null
    ) {
      return form.invalid(values.messages);
    }
    setBusy(true);
    setBanner(null);
    form.clear();
    try {
      await record(propertyId, {
        property_id: propertyId,
        kind: "price_renovation",
        document_id: documentId,
        value: {
          status,
          amount: gross,
          amount_net: netAmount,
          period_start: startOn,
          period_end: endOn,
          paid_on: paidOn,
          clause: clause.trim() || null,
        },
        note: note.trim() || null,
        supersedes_id: replaces?.id ?? null,
        withdrawn: false,
      });
      onDone();
    } catch (error) {
      const codes = codesOf(error);
      if (codes.includes("tax_input.not_current") && replaces === null) {
        setBanner(t.prTaken);
      } else if (codes.includes("tax_input.not_current")) {
        setBanner(CODE_MESSAGES["tax_input.not_current"] ?? null);
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
        <Field id={`${base}-amount`} label={t.prAmount} description={t.prAmountDescription} errors={form.of("value.amount")}>
          <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} autoComplete="off" />
        </Field>
        <Field id={`${base}-net`} label={t.prNet} description={t.prNetDescription} errors={form.of("value.amount_net")}>
          <input type="text" inputMode="decimal" value={net} onChange={(e) => setNet(e.target.value)} autoComplete="off" />
        </Field>
        <Field id={`${base}-start`} label={t.prStart} description={t.prStartDescription} errors={form.of("value.period_start")}>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field id={`${base}-end`} label={t.prEnd} description={t.prEndDescription} errors={form.of("value.period_end")}>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Field id={`${base}-paid`} label={t.prPaid} description={t.prPaidDescription} errors={form.of("value.paid_on")}>
          <input type="date" value={paid} onChange={(e) => setPaid(e.target.value)} />
        </Field>
        {replaces !== null ? (
          <Field id={`${base}-status`} label={t.prStatusLabel} description={t.prStatusDescription} errors={form.of("value.status")}>
            <select value={status} onChange={(e) => setStatus(e.target.value as PriceRenovationStatus)}>
              {entries(PRICE_RENOVATION_STATUS).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field id={`${base}-document`} label={t.prContract} description={t.prContractDescription} errors={form.of("document_id")}>
          <DocumentPicker value={documentId} onChange={setDocumentId} />
        </Field>
        <Field id={`${base}-clause`} label={t.prClause} description={t.prClauseDescription} errors={form.of("value.clause")}>
          <textarea rows={2} maxLength={1000} value={clause} onChange={(e) => setClause(e.target.value)} autoComplete="off" />
        </Field>
        <Field id={`${base}-note`} label={t.noteOptional}>
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} autoComplete="off" />
        </Field>
      </div>
      {replaces !== null ? <p className="muted hint">{t.prCorrectionHint}</p> : null}
      <div className="row">
        <Submit busy={busy}>{replaces === null ? t.enter : t.saveCorrection}</Submit>
        <button type="button" className="link" onClick={onCancel}>
          {c.cancel}
        </button>
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- on the Steuern page: the Grundsteuer schedule from the Bescheid (phase-5.md D126, §11) ----

type GrundsteuerValue = Schemas["GrundsteuerSchedule"];
type GrundsteuerRhythm = NonNullable<GrundsteuerValue["rhythm"]>;
type GrundsteuerMode = NonNullable<GrundsteuerValue["payment_mode"]>;

const GRUNDSTEUER_SLOTS = 4;
const GRUNDSTEUER_KEYS = ["tax_year", "value.annual_amount", "value.instalments", "document_id"] as const;

/**
 * Grundsteuer (D126): the Bescheid's annual amount and its instalments (dates and amounts),
 * the rhythm and the payment mode, with the Bescheid it comes from. A row applies from its first
 * year until a newer one; the reminders repeat its dates in later years. Filled by the Bescheid's
 * proposal in the review, or typed here; every figure is the Bescheid's, the server checks that
 * the instalments fall in the first year, in order, and sum to the annual amount.
 */
export function GrundsteuerSection({ propertyId, defaultYear }: { propertyId: string; defaultYear: number }) {
  const t = useT(taxInputsText);
  const f = useFormat();
  const load = useCallback(() => loadInputs(propertyId, "grundsteuer_schedule"), [propertyId]);
  const state = useLoad(load);
  const [editing, setEditing] = useState<TaxInput | "new" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function withdraw(row: TaxInput) {
    setBusy(true);
    setBanner(null);
    try {
      await record(propertyId, {
        property_id: propertyId,
        kind: "grundsteuer_schedule",
        tax_year: row.tax_year,
        document_id: row.document_id,
        value: row.value,
        supersedes_id: row.id,
        withdrawn: true,
      });
    } catch (error) {
      setBanner(messageOf(error) ?? t.withdrawFailed);
    } finally {
      setBusy(false);
      state.reload();
    }
  }

  return (
    <div className="stack">
      <Loading state={state}>
        {(rows) => {
          const sorted = [...rows].sort((a, b) => (a.tax_year ?? 0) - (b.tax_year ?? 0));
          return sorted.length === 0 ? (
            <p className="muted">{t.noGrundsteuer}</p>
          ) : (
            <ul className="stack">
              {sorted.map((row) => {
                const value = row.value as GrundsteuerValue;
                return (
                  <li key={row.id} className="stack">
                    <div className="row">
                      <strong>
                        {t.grundsteuerRow(
                          row.tax_year,
                          f.money(value.annual_amount),
                          value.rhythm ? GRUNDSTEUER_RHYTHM[value.rhythm] : null,
                        )}
                      </strong>
                      <span className="spacer" />
                      <button type="button" className="link" onClick={() => setEditing(row)} disabled={busy}>
                        {t.correct}
                      </button>
                      <button type="button" className="link" onClick={() => void withdraw(row)} disabled={busy}>
                        {t.withdraw}
                      </button>
                    </div>
                    <span className="muted">
                      {value.instalments.map((i) => `${f.date(i.due_on)}: ${f.money(i.amount)}`).join(" · ")}
                    </span>
                    <span className="muted">
                      {t.paymentModeIs(GRUNDSTEUER_MODE[value.payment_mode ?? "auto"])}
                      {row.document_id ? (
                        <>
                          {" · "}
                          <Link href={`/documents/${row.document_id}`}>{t.openBescheid}</Link>
                        </>
                      ) : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          );
        }}
      </Loading>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      {editing === null ? (
        <div>
          <button type="button" className="secondary" onClick={() => setEditing("new")}>
            {t.enterGrundsteuer}
          </button>
        </div>
      ) : (
        <GrundsteuerForm
          key={editing === "new" ? "new" : editing.id}
          propertyId={propertyId}
          defaultYear={defaultYear}
          replaces={editing === "new" ? null : editing}
          onDone={() => {
            setEditing(null);
            state.reload();
          }}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function GrundsteuerForm({
  propertyId,
  defaultYear,
  replaces,
  onDone,
  onCancel,
}: {
  propertyId: string;
  defaultYear: number;
  replaces: TaxInput | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const t = useT(taxInputsText);
  const c = useT(common);
  const old = replaces?.value as GrundsteuerValue | undefined;
  const [fromYear, setFromYear] = useState(String(replaces?.tax_year ?? defaultYear));
  const [annual, setAnnual] = useState(old ? decimalInput(old.annual_amount) : "");
  const [slots, setSlots] = useState(() =>
    Array.from({ length: GRUNDSTEUER_SLOTS }, (_, n) => ({
      dueOn: old?.instalments[n]?.due_on ?? "",
      amount: old?.instalments[n] ? decimalInput(old.instalments[n].amount) : "",
    })),
  );
  const [rhythm, setRhythm] = useState<GrundsteuerRhythm | "">(old?.rhythm ?? "");
  const [mode, setMode] = useState<GrundsteuerMode | "auto">(old?.payment_mode ?? "auto");
  const [documentId, setDocumentId] = useState<string | null>(replaces?.document_id ?? null);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const form = useFormErrors(useMemo(() => [...GRUNDSTEUER_KEYS], []));
  const base = useId();

  function setSlot(n: number, patch: Partial<{ dueOn: string; amount: string }>) {
    setSlots((current) => current.map((slot, i) => (i === n ? { ...slot, ...patch } : slot)));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const year = values.take("tax_year", wholeNumber(fromYear, 2000, t.taxYearRequired(2027)));
    const money = values.take("value.annual_amount", toMoneyString(annual));
    const instalments: { due_on: string; amount: string }[] = [];
    for (const slot of slots) {
      if (!slot.dueOn && !slot.amount.trim()) continue;
      const amount = toMoneyString(slot.amount);
      if (!slot.dueOn || !amount.ok) {
        values.messages["value.instalments"] = [t.instalmentIncomplete];
        continue;
      }
      instalments.push({ due_on: slot.dueOn, amount: amount.value });
    }
    if (instalments.length === 0 && !values.messages["value.instalments"]) {
      values.messages["value.instalments"] = [t.instalmentRequired];
    }
    if (documentId === null) values.messages["document_id"] = [t.bescheidRequired];
    if (!values.ok || year === undefined || money === undefined || documentId === null || values.messages["value.instalments"]) {
      return form.invalid(values.messages);
    }
    setBusy(true);
    setBanner(null);
    form.clear();
    try {
      await record(propertyId, {
        property_id: propertyId,
        kind: "grundsteuer_schedule",
        tax_year: year,
        document_id: documentId,
        value: {
          annual_amount: money,
          instalments,
          rhythm: rhythm === "" ? null : rhythm,
          payment_mode: mode === "auto" ? null : mode,
        },
        supersedes_id: replaces?.id ?? null,
        withdrawn: false,
      });
      onDone();
    } catch (error) {
      const codes = codesOf(error);
      if (codes.includes("tax_input.not_current") && replaces === null) {
        setBanner(t.grundsteuerYearTaken);
      } else if (codes.includes("tax_input.not_current")) {
        setBanner(CODE_MESSAGES["tax_input.not_current"] ?? null);
      } else if (error instanceof ApiError && JSON.stringify(error.body ?? "").includes("tax_input.grundsteuer_schedule_invalid")) {
        setBanner(t.instalmentsInvalid);
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
          id={`${base}-from`}
          label={t.fromTaxYear}
          description={t.grundsteuerFromDescription}
          errors={form.of("tax_year")}
        >
          <input
            type="text"
            inputMode="numeric"
            value={fromYear}
            onChange={(e) => setFromYear(e.target.value)}
            disabled={replaces !== null}
            autoComplete="off"
          />
        </Field>
        <Field id={`${base}-annual`} label={t.annualAmount} description={t.asInBescheid} errors={form.of("value.annual_amount")}>
          <input type="text" inputMode="decimal" value={annual} onChange={(e) => setAnnual(e.target.value)} autoComplete="off" />
        </Field>
      </div>
      <fieldset className="stack">
        <legend>{t.dueDates}</legend>
        {slots.map((slot, n) => (
          <div key={n} className="row">
            <label className="inline" htmlFor={`${base}-due-${n}`}>
              {t.instalmentOn(n + 1)}
              <input id={`${base}-due-${n}`} type="date" value={slot.dueOn} onChange={(e) => setSlot(n, { dueOn: e.target.value })} />
            </label>
            <label className="inline" htmlFor={`${base}-amount-${n}`}>
              {t.amountEuro}
              <input
                id={`${base}-amount-${n}`}
                type="text"
                inputMode="decimal"
                value={slot.amount}
                onChange={(e) => setSlot(n, { amount: e.target.value })}
                autoComplete="off"
              />
            </label>
          </div>
        ))}
        {form.of("value.instalments").length > 0 ? (
          <ul className="field-errors">
            {form.of("value.instalments").map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        ) : null}
      </fieldset>
      <div className="grid">
        <Field id={`${base}-rhythm`} label={t.rhythmOptional} description={t.rhythmDescription}>
          <select value={rhythm} onChange={(e) => setRhythm(e.target.value as GrundsteuerRhythm | "")}>
            <option value="">{t.noAnswer}</option>
            {entries(GRUNDSTEUER_RHYTHM).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id={`${base}-mode`}
          label={t.paymentMode}
          description={t.paymentModeDescription}
        >
          <select value={mode} onChange={(e) => setMode(e.target.value as GrundsteuerMode | "auto")}>
            {entries(GRUNDSTEUER_MODE).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${base}-document`} label={t.grundsteuerBescheid} errors={form.of("document_id")}>
          <DocumentPicker value={documentId} onChange={setDocumentId} />
        </Field>
      </div>
      {replaces !== null ? (
        <p className="muted hint">{t.bescheidCorrectionHint(replaces.tax_year)}</p>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{replaces === null ? t.enter : t.saveCorrection}</Submit>
        <button type="button" className="link" onClick={onCancel}>
          {c.cancel}
        </button>
      </div>
      {banner ? <p className="notice notice-error">{banner}</p> : null}
      <FormBanner errors={form.errors} />
    </form>
  );
}

// --- on the tenancy form: due day and running-cost terms -------------------------------------

/** "3" → 3; anything but a whole day 1–28 is a message (D67). */
export function toDueDay(raw: string): { ok: true; value: number } | { ok: false; message: string } {
  const day = Number(raw.trim());
  return Number.isInteger(day) && day >= 1 && day <= 28
    ? { ok: true, value: day }
    : { ok: false, message: translate(taxInputsText).dueDayInvalid };
}

export function RentTermsFields({
  idPrefix,
  dueDay,
  onDueDay,
  terms,
  onTerms,
  errors,
}: {
  idPrefix: string;
  dueDay: string;
  onDueDay: (value: string) => void;
  terms: OperatingCostsTerms;
  onTerms: (value: OperatingCostsTerms) => void;
  errors: (key: "rent_due_day" | "operating_costs_terms") => string[];
}) {
  const t = useT(taxInputsText);
  return (
    <>
      <Field
        id={`${idPrefix}-due-day`}
        label={t.dueDay}
        hint={t.dueDayHint}
        description={t.dueDayDescription}
        errors={errors("rent_due_day")}
      >
        <input type="text" inputMode="numeric" value={dueDay} onChange={(e) => onDueDay(e.target.value)} autoComplete="off" />
      </Field>
      <Field
        id={`${idPrefix}-terms`}
        label={t.terms}
        description={t.termsDescription}
        errors={errors("operating_costs_terms")}
      >
        <select value={terms} onChange={(e) => onTerms(e.target.value as OperatingCostsTerms)}>
          {entries(OPERATING_COSTS_TERMS).map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </Field>
    </>
  );
}
