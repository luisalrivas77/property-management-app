"use client";

// "Mieter ändern" (§4.5): the tenancy's whole party list in one request (PUT …/parties): add a
// tenant, remove one, or make another the Hauptmieter. Exactly one Hauptmieter and at least
// one party; the API checks it again (422 tenancy.exactly_one_primary). The event records
// tenant ids and roles only (D5). A tenant whose data was erased stays selectable only while
// already a party.
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Tenancy, TenancyRole, Tenant } from "@/api/schemas";
import { tenantName } from "@/components/entities";
import { Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { options, TENANCY_ROLE } from "@/components/labels";

interface Party {
  tenantId: string;
  role: TenancyRole;
}

const KEYS = ["parties", "effective_date"] as const;
const CODE_FIELDS = {
  "tenancy.exactly_one_primary": "parties",
  "tenancy.no_tenant": "parties",
};

export function PartiesForm({ tenancy, tenants, onDone }: { tenancy: Tenancy; tenants: Tenant[]; onDone: () => void }) {
  const [parties, setParties] = useState<Party[]>(tenancy.parties.map((p) => ({ tenantId: p.tenant_id, role: p.role })));
  const [effectiveDate, setEffectiveDate] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);
  const current = new Set(tenancy.parties.map((p) => p.tenant_id));
  const choices = tenants.filter((tenant) => !tenant.erased_at || current.has(tenant.id));

  function setParty(index: number, change: Partial<Party>) {
    setParties((list) => list.map((party, i) => (i === index ? { ...party, ...change } : party)));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const chosen = parties.filter((party) => party.tenantId !== "");
    const messages: Record<string, string[]> = {};
    if (chosen.length === 0) messages.parties = ["Bitte mindestens einen Mieter wählen."];
    else if (chosen.filter((party) => party.role === "primary").length !== 1) messages.parties = ["Bitte genau einen Hauptmieter wählen."];
    else if (new Set(chosen.map((party) => party.tenantId)).size !== chosen.length) messages.parties = ["Jeder Mieter nur einmal."];
    if (Object.keys(messages).length > 0) return form.invalid(messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.PUT("/api/tenancies/{tenancy_id}/parties", {
          params: { path: { tenancy_id: tenancy.id } },
          body: { parties: chosen.map((party) => ({ tenant_id: party.tenantId, role: party.role })), effective_date: effectiveDate || null },
        }),
      );
      onDone();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  const partyErrors = form.of("parties");
  return (
    <form className="stack" onSubmit={submit} noValidate>
      {parties.map((party, index) => (
        <div key={index} className="grid">
          <Field id={`parties-${index}-tenant`} label={`Mieter ${index + 1}`}>
            <select value={party.tenantId} onChange={(e) => setParty(index, { tenantId: e.target.value })}>
              <option value="">– wählen –</option>
              {choices.map((tenant) => (
                <option key={tenant.id} value={tenant.id}>
                  {tenantName(tenant)}
                </option>
              ))}
            </select>
          </Field>
          <Field id={`parties-${index}-role`} label="Rolle">
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
              <button type="button" className="link" onClick={() => setParties((list) => list.filter((_, i) => i !== index))}>
                Mieter {index + 1} entfernen
              </button>
            </div>
          ) : null}
        </div>
      ))}
      {partyErrors.length > 0 ? (
        <ul className="field-errors">
          {partyErrors.map((message, i) => (
            <li key={i}>{message}</li>
          ))}
        </ul>
      ) : null}
      <div className="row">
        <button type="button" className="secondary" onClick={() => setParties((list) => [...list, { tenantId: "", role: "co_tenant" }])}>
          Weiteren Mieter hinzufügen
        </button>
      </div>
      <Field
        id="parties-effective"
        label="Gilt ab (optional)"
        hint="Mit Datum erscheint die Änderung im Verlauf. Neue Mieter legen Sie unter „Mieter“ an."
        errors={form.of("effective_date")}
      >
        <input type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} />
      </Field>
      <Submit busy={busy}>Speichern</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
