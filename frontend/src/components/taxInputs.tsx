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
//
// The labels live here, not in labels.ts (phase 3 step 2 scope).
import { useCallback, useId, useMemo, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError, RULE_CODE_TEXT, ruleCodes } from "@/api/errors";
import type { components } from "@/api/types";

import { DocumentPicker } from "./documents";
import { collector, Field, FormBanner, Submit, useFormErrors } from "./form";
import { formatDate, formatMoney, todayIso } from "./format";
import { decimalInput, toMoneyString } from "./input";
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

export const OPERATING_COSTS_TERMS: Record<OperatingCostsTerms, string> = {
  prepayment: "Vorauszahlung mit jährlicher Abrechnung",
  flat_rate: "Pauschale (keine Abrechnung)",
  included: "In der Miete enthalten (Warmmiete, nicht gesondert vereinbart)",
};

const RENOVATION_TREATMENT: Record<RenovationChoice, string> = {
  erhaltung: "Erhaltungsaufwand",
  herstellung: "Herstellungskosten",
};

const EXCLUSION_REASON: Record<ExclusionReason, string> = {
  annual_maintenance: "Jährlich übliche Erhaltungsarbeit",
  extension: "Erweiterung (z. B. Anbau, Aufstockung)",
  damage_after_acquisition: "Schaden, der erst nach dem Kauf entstand",
};

const MARKET_RENT_SOURCE: Record<MarketRentSource, string> = {
  mietspiegel: "Mietspiegel",
  gutachten: "Gutachten",
  portal: "Mietpreisportal",
  other: "Andere Quelle",
};

const SPREAD_YEARS = [2, 3, 4, 5] as const;

/** Codes the server answers with, by the field they belong to (else the banner). */
const CODE_MESSAGES: Record<string, string> = {
  "tax_input.not_current":
    "Diese Angabe wurde inzwischen geändert (z. B. in einem anderen Fenster). Bitte die Seite neu laden und erneut prüfen.",
  "tax_input.allocation_not_confirmed": "Steuerliche Angaben gibt es nur für eine bestätigte Zuordnung.",
  "tax_input.scope_invalid": "Diese Angabe passt nicht zu dieser Zuordnung oder Einheit.",
  "tax_input.extension_needs_herstellung": RULE_CODE_TEXT["tax_input.extension_needs_herstellung"] ?? "",
};

const EXTENSION_HINT =
  "Eine Erweiterung (Anbau, Aufstockung) ist immer Herstellungskosten: die Zahlung als Renovierung buchen und „Herstellungskosten“ wählen.";
const EXTENSION_LOCKS_TREATMENT = "Erst die Ausnahme „Erweiterung“ zurücknehmen.";

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
  return (
    <section className="stack tax-inputs">
      <h3>Steuerliche Angaben</h3>
      {rows.map((row) => (
        <AllocationQuestions key={row.id} allocation={row} label={rows.length > 1 ? formatMoney(row.amount) : null} />
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
      {label ? <p className="cell-detail">Zeile über {label}</p> : null}
      <div className="grid">
        <Field
          id={`${base}-spread`}
          label="Auf 2–5 Jahre verteilen (§ 82b EStDV)"
          description="Größere Erhaltungsaufwendungen dürfen gleichmäßig auf zwei bis fünf Jahre verteilt werden, statt sie im Jahr der Zahlung ganz abzuziehen. Eine Wahl, keine Pflicht."
        >
          <select value={spread} onChange={(e) => setSpread(e.target.value)}>
            <option value="">Nicht verteilen (im Jahr der Zahlung)</option>
            {SPREAD_YEARS.map((years) => (
              <option key={years} value={String(years)}>
                Auf {years} Jahre verteilen
              </option>
            ))}
          </select>
        </Field>
        {renovation ? (
          <Field
            id={`${base}-treatment`}
            label="Renovierung ist"
            hint={extensionSaved ? EXTENSION_LOCKS_TREATMENT : undefined}
            description="Erhaltungsaufwand erhält oder erneuert, was schon da ist, und ist sofort abziehbar. Herstellungskosten schaffen etwas Neues oder heben den Standard deutlich; sie gehen in die AfA ein."
          >
            <select value={treatment} onChange={(e) => changeTreatment(e.target.value as RenovationChoice | "")}>
              <option value="" disabled={extensionSaved}>
                Noch nicht eingeordnet
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
          label="Zählt nicht zur 15 %-Grenze (Grund)"
          hint={extensionAllowed ? undefined : EXTENSION_HINT}
          description="In den ersten drei Jahren nach dem Kauf zählen Renovierungen zur 15 %-Grenze der anschaffungsnahen Herstellungskosten. Ausgenommen sind jährlich übliche Erhaltungsarbeiten, Erweiterungen (die ohnehin Herstellungskosten sind) und Schäden, die erst nach dem Kauf entstanden."
        >
          <select value={exclusion} onChange={(e) => setExclusion(e.target.value as ExclusionReason | "")}>
            <option value="">Zählt zur Grenze</option>
            {exclusionReasons.map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="row">
        <Submit busy={busy}>Angaben speichern</Submit>
        {saved ? <span className="muted">Gespeichert. Die bisherige Angabe bleibt im Verlauf.</span> : null}
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
      setBanner(messageOf(error) ?? "Das Zurückziehen hat nicht geklappt. Bitte erneut versuchen.");
    } finally {
      setBusy(false);
      state.reload();
    }
  }

  return (
    <div className="stack">
      {perUnit ? null : (
        <p className="muted">Gilt für die ganze Wohnung, auch wenn sie zimmerweise vermietet ist.</p>
      )}
      <Loading state={state}>
        {(rows) => {
          const sorted = [...rows].sort((a, b) =>
            (b.value as MarketRentValue).valid_from.localeCompare((a.value as MarketRentValue).valid_from),
          );
          return sorted.length === 0 ? (
            <p className="muted">Noch keine ortsübliche Miete erfasst. Sie wird für den Vergleich mit der vereinbarten Miete gebraucht (§ 21 Abs. 2 EStG).</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Gültig ab</th>
                    <th>Kaltmiete je m²</th>
                    <th>Quelle</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((row) => {
                    const value = row.value as MarketRentValue;
                    return (
                      <tr key={row.id}>
                        <td>{formatDate(value.valid_from)}</td>
                        <td>{formatMoney(value.cold_rent_per_m2)}</td>
                        <td>{MARKET_RENT_SOURCE[value.source]}</td>
                        <td>
                          <div className="row">
                            <button type="button" className="link" onClick={() => setEditing(row)} disabled={busy}>
                              Korrigieren
                            </button>
                            <button type="button" className="link" onClick={() => void withdraw(row)} disabled={busy}>
                              Zurückziehen
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
            Ortsübliche Miete eintragen
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
  const old = replaces?.value as MarketRentValue | undefined;
  const [rent, setRent] = useState(old ? decimalInput(old.cold_rent_per_m2) : "");
  const [validFrom, setValidFrom] = useState(old?.valid_from ?? todayIso());
  const [source, setSource] = useState<MarketRentSource>(old?.source ?? "mietspiegel");
  const [documentId, setDocumentId] = useState<string | null>(replaces?.document_id ?? null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const form = useFormErrors(useMemo(() => [...MARKET_KEYS], []));
  const base = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const values = collector();
    const perM2 = values.take("value.cold_rent_per_m2", toMoneyString(rent));
    if (!validFrom) values.messages["value.valid_from"] = ["Bitte das Datum eingeben, ab dem der Wert gilt."];
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
        setBanner("Für dieses Datum ist schon ein Wert erfasst. Bitte diesen korrigieren.");
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
          label="Ortsübliche Miete (kalt, €/m², netto laut Mietspiegel)"
          description="Die ortsübliche Kaltmiete je Quadratmeter ohne Nebenkosten, z. B. der Mittelwert der passenden Spalte im Mietspiegel. Liegt die vereinbarte Miete unter 50 % bzw. 66 % davon, ist der Werbungskostenabzug gefährdet."
          errors={form.of("value.cold_rent_per_m2")}
        >
          <input type="text" inputMode="decimal" value={rent} onChange={(e) => setRent(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id={`${base}-from`}
          label="Gültig ab"
          description="Ab wann der Wert gilt, z. B. das Datum, ab dem der Mietspiegel gilt."
          errors={form.of("value.valid_from")}
        >
          <input type="date" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} disabled={replaces !== null} />
        </Field>
        <Field id={`${base}-source`} label="Quelle" description="Woher der Wert stammt." errors={form.of("value.source")}>
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
          label="Beleg (optional)"
          description="Der Mietspiegel, das Gutachten oder ein Ausdruck des Portals, als Nachweis für das Finanzamt."
          errors={form.of("document_id")}
        >
          <DocumentPicker value={documentId} onChange={setDocumentId} />
        </Field>
        <Field id={`${base}-note`} label="Notiz (optional)" description="Zum Beispiel die Spalte oder das Feld im Mietspiegel.">
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} autoComplete="off" />
        </Field>
      </div>
      {replaces !== null ? (
        <p className="muted hint">Eine Korrektur ersetzt den Wert ab {formatDate(old?.valid_from)}; der bisherige bleibt im Verlauf.</p>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{replaces === null ? "Eintragen" : "Korrektur speichern"}</Submit>
        <button type="button" className="link" onClick={onCancel}>
          Abbrechen
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
    : { ok: false, message: "Bitte einen Tag von 1 bis 28 eingeben." };
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
  return (
    <>
      <Field
        id={`${idPrefix}-due-day`}
        label="Miete fällig am Tag"
        hint="Meist der 3. (spätestens am dritten Werktag, § 556b BGB)."
        description="Der Tag im Monat, an dem die Miete laut Mietvertrag fällig ist. Er entscheidet, ob eine Miete, die um den Jahreswechsel gezahlt wird, steuerlich ins alte oder ins neue Jahr gehört (Zehn-Tage-Regel, § 11 EStG)."
        errors={errors("rent_due_day")}
      >
        <input type="text" inputMode="numeric" value={dueDay} onChange={(e) => onDueDay(e.target.value)} autoComplete="off" />
      </Field>
      <Field
        id={`${idPrefix}-terms`}
        label="Nebenkosten laut Mietvertrag"
        description="Wie der Mietvertrag die Nebenkosten regelt: als Vorauszahlung mit jährlicher Abrechnung, als Pauschale ohne Abrechnung, oder gar nicht gesondert (eine Warmmiete, in der alles enthalten ist). Davon hängt ab, in welcher Zeile der Anlage V die Beträge stehen."
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
