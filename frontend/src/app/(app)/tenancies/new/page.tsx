"use client";

// Mietverhältnis anlegen (§9.1): Parteien (with "Mieter anlegen" inline), Vertragsdatum,
// Einzug, Befristung, Mietmodell, Kaution incl. Kautionskonto-IBAN, Anfangsmiete. One request,
// POST /tenancies, which records the tenancy, its move-in and the first rent row together.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { DepositForm, InitialRent, Property, RentModel, Tenant, TenancyRole, Unit } from "@/api/schemas";
import { TenantForm } from "@/app/(app)/tenants/TenantForm";
import { DocumentPicker } from "@/components/documents";
import { tenantName } from "@/components/entities";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { blankToNull, toMoneyString } from "@/components/input";
import { DEPOSIT_FORM, options, RENT_MODEL, TENANCY_ROLE } from "@/components/labels";
import { ActionDialog, Breadcrumbs, Loading } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

const KEYS = [
  "unit_id",
  "parties",
  "signed_on",
  "start_date",
  "end_date",
  "fixed_term_until",
  "rent_model",
  "deposit_amount",
  "deposit_form",
  "deposit_bank",
  "deposit_iban",
  "initial_rent",
  "initial_rent.cold_rent",
  "initial_rent.operating_costs_prepayment",
  "initial_rent.effective_from",
  "initial_rent.document_id",
] as const;

const CODE_FIELDS = {
  "tenancy.exactly_one_primary": "parties",
  "tenancy.no_tenant": "parties",
  "tenancy.overlap": "start_date",
  "rent_schedule.outside_tenancy": "initial_rent.effective_from",
  "iban.": "deposit_iban",
};

interface Party {
  tenantId: string;
  role: TenancyRole;
}

export default function NewTenancyPage() {
  return (
    <Suspense fallback={<p className="muted">Lädt …</p>}>
      <NewTenancy />
    </Suspense>
  );
}

function NewTenancy() {
  const unitId = useSearchParams().get("unit");
  const load = useCallback(async () => {
    if (!unitId) return null;
    const unit = await ok(api.GET("/api/units/{unit_id}", { params: { path: { unit_id: unitId } } }));
    const property = await ok(api.GET("/api/properties/{property_id}", { params: { path: { property_id: unit.property_id } } }));
    return { unit, property };
  }, [unitId]);
  const data = useLoad(load);

  return (
    <section>
      <Loading state={data}>
        {(found) =>
          found === null ? (
            <p>
              Ein Mietverhältnis wird von einer Einheit aus angelegt: <Link href="/">Übersicht</Link> › Immobilie › Einheit ›
              „Mietverhältnis anlegen“.
            </p>
          ) : (
            <>
              <Breadcrumbs
                items={[
                  { href: `/properties/${found.property.id}`, label: found.property.label },
                  { href: `/units/${found.unit.id}`, label: found.unit.label },
                  { label: "Mietverhältnis anlegen" },
                ]}
              />
              <h1>Mietverhältnis anlegen</h1>
              <TenancyForm unit={found.unit} property={found.property} />
            </>
          )
        }
      </Loading>
    </section>
  );
}

const loadTenants = () => ok(api.GET("/api/tenants"));

function TenancyForm({ unit }: { unit: Unit; property: Property }) {
  const router = useRouter();
  const tenants = useLoad(loadTenants);
  const [parties, setParties] = useState<Party[]>([{ tenantId: "", role: "primary" }]);
  const [signedOn, setSignedOn] = useState("");
  const [startDate, setStartDate] = useState("");
  const [fixedTermUntil, setFixedTermUntil] = useState("");
  const [endDate, setEndDate] = useState("");
  const [rentModel, setRentModel] = useState<RentModel>("fixed");
  const [depositAmount, setDepositAmount] = useState("");
  const [depositForm, setDepositForm] = useState<DepositForm>("none");
  const [depositBank, setDepositBank] = useState("");
  const [depositIban, setDepositIban] = useState("");
  const [withRent, setWithRent] = useState(true);
  const [coldRent, setColdRent] = useState("");
  const [prepayment, setPrepayment] = useState("");
  const [rentFrom, setRentFrom] = useState("");
  const [rentDocument, setRentDocument] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);

  function setParty(index: number, change: Partial<Party>) {
    setParties((current) => current.map((party, i) => (i === index ? { ...party, ...change } : party)));
  }

  function addCreatedTenant(tenant: Tenant) {
    tenants.reload();
    setParties((current) => {
      const empty = current.findIndex((party) => party.tenantId === "");
      if (empty >= 0) return current.map((party, i) => (i === empty ? { ...party, tenantId: tenant.id } : party));
      return [...current, { tenantId: tenant.id, role: current.some((p) => p.role === "primary") ? "co_tenant" : "primary" }];
    });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const chosen = parties.filter((party) => party.tenantId !== "");
    if (chosen.length === 0) values.messages.parties = ["Bitte mindestens einen Mieter wählen oder anlegen."];
    if (!startDate) values.messages.start_date = ["Bitte das Einzugsdatum eingeben."];
    const deposit = depositAmount.trim() === "" ? "0.00" : values.take("deposit_amount", toMoneyString(depositAmount));
    let initialRent: InitialRent | null = null;
    if (withRent) {
      const cold = values.take("initial_rent.cold_rent", toMoneyString(coldRent));
      const prepaid =
        prepayment.trim() === "" ? "0.00" : values.take("initial_rent.operating_costs_prepayment", toMoneyString(prepayment));
      if (cold !== undefined && prepaid !== undefined) {
        initialRent = { cold_rent: cold, operating_costs_prepayment: prepaid, effective_from: rentFrom || null, document_id: rentDocument };
      }
    }
    if (!values.ok || deposit === undefined) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      const created = await ok(
        api.POST("/api/tenancies", {
          body: {
            unit_id: unit.id,
            parties: chosen.map((party) => ({ tenant_id: party.tenantId, role: party.role })),
            signed_on: signedOn || null,
            start_date: startDate,
            end_date: endDate || null,
            fixed_term_until: fixedTermUntil || null,
            rent_model: rentModel,
            deposit_amount: deposit,
            deposit_form: depositForm,
            deposit_bank: blankToNull(depositBank),
            deposit_iban: blankToNull(depositIban),
            initial_rent: initialRent,
          },
        }),
      );
      router.push(`/tenancies/${created.id}`);
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  const partyErrors = form.of("parties");
  return (
    <div className="stack">
      <form className="stack" onSubmit={submit} noValidate>
        <fieldset className="stack">
          <legend>Parteien</legend>
          <Loading state={tenants}>
            {(list) => (
              <div className="stack">
                {parties.map((party, index) => (
                  <div key={index} className="grid">
                    <Field id={`party-${index}-tenant`} label={`Mieter ${index + 1}`}>
                      <select value={party.tenantId} onChange={(e) => setParty(index, { tenantId: e.target.value })}>
                        <option value="">– wählen –</option>
                        {list
                          .filter((tenant) => !tenant.erased_at)
                          .map((tenant) => (
                            <option key={tenant.id} value={tenant.id}>
                              {tenantName(tenant)}
                            </option>
                          ))}
                      </select>
                    </Field>
                    <Field id={`party-${index}-role`} label="Rolle">
                      <select value={party.role} onChange={(e) => setParty(index, { role: e.target.value as TenancyRole })}>
                        {options(TENANCY_ROLE).map(([value, text]) => (
                          <option key={value} value={value}>
                            {text}
                          </option>
                        ))}
                      </select>
                    </Field>
                    {parties.length > 1 ? (
                      <div className="wide">
                        <button type="button" className="link" onClick={() => setParties((current) => current.filter((_, i) => i !== index))}>
                          Mieter {index + 1} entfernen
                        </button>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </Loading>
          {partyErrors.length > 0 ? (
            <ul className="field-errors">
              {partyErrors.map((message, i) => (
                <li key={i}>{message}</li>
              ))}
            </ul>
          ) : null}
          <div className="row">
            <button
              type="button"
              className="secondary"
              onClick={() => setParties((current) => [...current, { tenantId: "", role: "co_tenant" }])}
            >
              Weiteren Mieter hinzufügen
            </button>
            <ActionDialog label="Mieter anlegen" title="Mieter anlegen">
              {(close) => (
                <TenantForm
                  idPrefix="inline-tenant"
                  onSaved={(tenant) => {
                    addCreatedTenant(tenant);
                    close();
                  }}
                />
              )}
            </ActionDialog>
          </div>
        </fieldset>

        <fieldset className="stack">
          <legend>Vertrag</legend>
          <div className="grid">
            <Field id="tenancy-signed" label="Vertragsdatum (unterschrieben, optional)" errors={form.of("signed_on")}>
              <input type="date" value={signedOn} onChange={(e) => setSignedOn(e.target.value)} />
            </Field>
            <Field id="tenancy-start" label="Einzug" errors={form.of("start_date")}>
              <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} required />
            </Field>
            <Field id="tenancy-fixed" label="Befristet bis (optional)" hint="Leer = unbefristet." errors={form.of("fixed_term_until")}>
              <input type="date" value={fixedTermUntil} onChange={(e) => setFixedTermUntil(e.target.value)} />
            </Field>
            <Field id="tenancy-model" label="Mietmodell" errors={form.of("rent_model")}>
              <select value={rentModel} onChange={(e) => setRentModel(e.target.value as RentModel)}>
                {options(RENT_MODEL).map(([value, text]) => (
                  <option key={value} value={value}>
                    {text}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              id="tenancy-end"
              label="Auszug (nur bei einem schon beendeten Mietverhältnis)"
              hint="Sonst leer lassen und später „Auszug“ erfassen."
              errors={form.of("end_date")}
            >
              <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
            </Field>
          </div>
        </fieldset>

        <fieldset className="stack">
          <legend>Kaution</legend>
          <div className="grid">
            <Field id="deposit-form" label="Art" errors={form.of("deposit_form")}>
              <select value={depositForm} onChange={(e) => setDepositForm(e.target.value as DepositForm)}>
                {options(DEPOSIT_FORM).map(([value, text]) => (
                  <option key={value} value={value}>
                    {text}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="deposit-amount" label="Betrag (€)" errors={form.of("deposit_amount")}>
              <input type="text" inputMode="decimal" value={depositAmount} onChange={(e) => setDepositAmount(e.target.value)} autoComplete="off" />
            </Field>
            <Field id="deposit-bank" label="Bank des Kautionskontos (optional)" errors={form.of("deposit_bank")}>
              <input type="text" value={depositBank} onChange={(e) => setDepositBank(e.target.value)} autoComplete="off" />
            </Field>
            <Field id="deposit-iban" label="IBAN des Kautionskontos (optional)" errors={form.of("deposit_iban")}>
              <input type="text" value={depositIban} onChange={(e) => setDepositIban(e.target.value)} autoComplete="off" spellCheck={false} />
            </Field>
          </div>
        </fieldset>

        <fieldset className="stack">
          <legend>Anfangsmiete</legend>
          <label className="inline">
            <input type="checkbox" checked={withRent} onChange={(e) => setWithRent(e.target.checked)} />
            Anfangsmiete mit erfassen
          </label>
          {withRent ? (
            <div className="grid">
              <Field id="rent-cold" label="Kaltmiete (€ / Monat)" errors={form.of("initial_rent.cold_rent")}>
                <input type="text" inputMode="decimal" value={coldRent} onChange={(e) => setColdRent(e.target.value)} required autoComplete="off" />
              </Field>
              <Field
                id="rent-prepayment"
                label="NK-Vorauszahlung (€ / Monat)"
                errors={form.of("initial_rent.operating_costs_prepayment")}
              >
                <input type="text" inputMode="decimal" value={prepayment} onChange={(e) => setPrepayment(e.target.value)} autoComplete="off" />
              </Field>
              <Field id="rent-from" label="Gültig ab (optional)" hint="Leer = ab Einzug." errors={form.of("initial_rent.effective_from")}>
                <input type="date" value={rentFrom} onChange={(e) => setRentFrom(e.target.value)} />
              </Field>
              <Field id="rent-document" label="Beleg (z. B. Mietvertrag, optional)" errors={form.of("initial_rent.document_id")}>
                <DocumentPicker value={rentDocument} onChange={setRentDocument} />
              </Field>
            </div>
          ) : null}
          {form.of("initial_rent").length > 0 ? (
            <ul className="field-errors">
              {form.of("initial_rent").map((message, i) => (
                <li key={i}>{message}</li>
              ))}
            </ul>
          ) : null}
        </fieldset>

        <div className="row">
          <Submit busy={busy}>Mietverhältnis anlegen</Submit>
          <Link href={`/units/${unit.id}`}>Abbrechen</Link>
        </div>
        <FormBanner errors={form.errors} />
      </form>
    </div>
  );
}
