"use client";

// Mieter form (§9.1): Name, E-Mail, Telefon, IBAN. The IBAN is write-only: the API never
// returns it, only the mask (DE12 **** 3456). Editing: an empty IBAN field keeps the stored
// one; "IBAN entfernen" clears it. Personal data: messages never repeat what was typed.
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Tenant, TenantPatch } from "@/api/schemas";
import { Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { blankToNull } from "@/components/input";

const KEYS = ["full_name", "email", "phone", "iban"] as const;
const CODE_FIELDS = { "iban.": "iban" };

export function TenantForm({
  tenant,
  idPrefix = "tenant",
  onSaved,
  submitLabel,
}: {
  tenant?: Tenant;
  idPrefix?: string;
  onSaved: (tenant: Tenant) => void;
  submitLabel?: string;
}) {
  const [fullName, setFullName] = useState(tenant?.full_name ?? "");
  const [email, setEmail] = useState(tenant?.email ?? "");
  const [phone, setPhone] = useState(tenant?.phone ?? "");
  const [iban, setIban] = useState("");
  const [removeIban, setRemoveIban] = useState(false);
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    // An inline tenant form sits inside the tenancy form's dialog: keep the event here.
    event.preventDefault();
    event.stopPropagation();
    setBusy(true);
    form.clear();
    try {
      const base = { full_name: fullName.trim(), email: blankToNull(email), phone: blankToNull(phone) };
      if (tenant) {
        const body: TenantPatch = { ...base };
        if (removeIban) body.iban = null;
        else if (iban.trim() !== "") body.iban = iban.trim();
        const saved = await ok(api.PATCH("/api/tenants/{tenant_id}", { params: { path: { tenant_id: tenant.id } }, body }));
        setIban("");
        setRemoveIban(false);
        onSaved(saved);
      } else {
        const saved = await ok(api.POST("/api/tenants", { body: { ...base, iban: blankToNull(iban) } }));
        onSaved(saved);
      }
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate>
      <div className="grid">
        <Field
          id={`${idPrefix}-name`}
          label="Name"
          description="Der Name der Mieterin oder des Mieters, wie im Mietvertrag."
          errors={form.of("full_name")}
        >
          <input type="text" value={fullName} onChange={(e) => setFullName(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id={`${idPrefix}-email`}
          label="E-Mail (optional)"
          description="Eine Kontaktangabe, z. B. aus dem Mietvertrag."
          errors={form.of("email")}
        >
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id={`${idPrefix}-phone`}
          label="Telefon (optional)"
          description="Eine Kontaktangabe, z. B. aus dem Mietvertrag."
          errors={form.of("phone")}
        >
          <input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id={`${idPrefix}-iban`}
          label={tenant ? "Neue IBAN (optional)" : "IBAN (optional)"}
          hint={
            tenant
              ? `Gespeichert: ${tenant.iban_masked ?? "keine"}. Leer lassen, um sie zu behalten.`
              : "Zum Zuordnen der Mietzahlungen; angezeigt wird danach nur noch eine Maske."
          }
          description="Die IBAN des Kontos, von dem die Miete kommt, z. B. aus einem Kontoauszug mit einer Mietzahlung. Gespeichert wird sie nur maskiert und als nicht umkehrbarer Prüfwert; daran erkennt Hausbuch die Mietzahlungen."
          errors={form.of("iban")}
        >
          <input
            type="text"
            value={iban}
            onChange={(e) => setIban(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            disabled={removeIban}
          />
        </Field>
      </div>
      {tenant?.iban_masked ? (
        <label className="inline">
          <input type="checkbox" checked={removeIban} onChange={(e) => setRemoveIban(e.target.checked)} />
          Gespeicherte IBAN entfernen
        </label>
      ) : null}
      <Submit busy={busy}>{submitLabel ?? (tenant ? "Speichern" : "Mieter anlegen")}</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}
