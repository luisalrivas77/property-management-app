"use client";

// Immobilie form (§9.1). Create: everything PropertyCreate takes. Edit: PropertyPatch only
// (label, kind, address, build year). Notartermin, Übergang and Kaufpreis are milestones and
// change only through the corrections on the Stammdaten tab, so the edit form shows them
// read-only.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Property, PropertyKind } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatDate, formatMoney } from "@/components/format";
import { decimalInput, toMoneyString, toYear } from "@/components/input";
import { options, PROPERTY_KIND } from "@/components/labels";
import { Facts } from "@/components/ui";

const KEYS = [
  "label",
  "kind",
  "address.street",
  "address.house_number",
  "address.postal_code",
  "address.city",
  "address",
  "build_year",
  "notarized_on",
  "purchase_date",
  "purchase_price",
] as const;

export function PropertyForm({ property }: { property?: Property }) {
  const router = useRouter();
  const [label, setLabel] = useState(property?.label ?? "");
  const [kind, setKind] = useState<PropertyKind>(property?.kind ?? "etw");
  const [street, setStreet] = useState(property?.address.street ?? "");
  const [houseNumber, setHouseNumber] = useState(property?.address.house_number ?? "");
  const [postalCode, setPostalCode] = useState(property?.address.postal_code ?? "");
  const [city, setCity] = useState(property?.address.city ?? "");
  const [buildYear, setBuildYear] = useState(property ? String(property.build_year) : "");
  const [notarizedOn, setNotarizedOn] = useState("");
  const [purchaseDate, setPurchaseDate] = useState("");
  const [price, setPrice] = useState(decimalInput(property?.purchase_price));
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const year = values.take("build_year", toYear(buildYear));
    const address = {
      street: street.trim(),
      house_number: houseNumber.trim(),
      postal_code: postalCode.trim(),
      city: city.trim(),
      country_code: "DE",
    };
    setBusy(true);
    form.clear();
    try {
      if (property) {
        if (!values.ok || year === undefined) return form.invalid(values.messages);
        await ok(
          api.PATCH("/api/properties/{property_id}", {
            params: { path: { property_id: property.id } },
            body: { label: label.trim(), kind, address, build_year: year },
          }),
        );
        router.push(`/properties/${property.id}`);
      } else {
        const purchasePrice = values.take("purchase_price", toMoneyString(price));
        if (!notarizedOn) values.messages.notarized_on = ["Bitte das Datum des Notartermins eingeben."];
        if (!purchaseDate) values.messages.purchase_date = ["Bitte das Datum des Übergangs eingeben."];
        if (!values.ok || year === undefined || purchasePrice === undefined) return form.invalid(values.messages);
        const created = await ok(
          api.POST("/api/properties", {
            body: {
              label: label.trim(),
              kind,
              address,
              build_year: year,
              notarized_on: notarizedOn,
              purchase_date: purchaseDate,
              purchase_price: purchasePrice,
            },
          }),
        );
        // Ownership is its own command (§4.2, Q12): continue on the Eigentümer tab.
        router.push(`/properties/${created.id}?tab=eigentuemer&neu=1`);
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
          id="property-label"
          label="Bezeichnung"
          hint="z. B. „Wohnung Lindenstraße“"
          description="Ein frei wählbarer Name, unter dem Sie die Immobilie in Hausbuch wiederfinden."
          errors={form.of("label")}
        >
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id="property-kind"
          label="Art"
          description="Eigentumswohnung, Einfamilien- oder Mehrfamilienhaus, wie im Kaufvertrag beschrieben. Bei einer Eigentumswohnung fragt die Unterlagen-Checkliste zusätzlich nach Wirtschaftsplan und Hausgeldabrechnung der WEG."
          errors={form.of("kind")}
        >
          <select value={kind} onChange={(e) => setKind(e.target.value as PropertyKind)}>
            {options(PROPERTY_KIND).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="property-street"
          label="Straße"
          description="Die Straße der Immobilie, wie sie im Kaufvertrag oder Grundbuch steht."
          errors={form.of("address.street")}
        >
          <input type="text" value={street} onChange={(e) => setStreet(e.target.value)} required autoComplete="address-line1" />
        </Field>
        <Field
          id="property-house"
          label="Hausnummer"
          description="Die Hausnummer mit Zusatz, wie im Kaufvertrag, z. B. 12a oder 3-5, ohne die Wohnungsnummer (z. B. WE 03): die gehört in die Bezeichnung der Einheit. Mit Straße und Hausnummer findet Hausbuch die Immobilie in hochgeladenen Dokumenten wieder."
          errors={form.of("address.house_number")}
        >
          <input type="text" value={houseNumber} onChange={(e) => setHouseNumber(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id="property-postal"
          label="PLZ"
          description="Die fünfstellige Postleitzahl der Immobilie."
          errors={form.of("address.postal_code")}
        >
          <input
            type="text"
            inputMode="numeric"
            value={postalCode}
            onChange={(e) => setPostalCode(e.target.value)}
            required
            maxLength={5}
            autoComplete="postal-code"
          />
        </Field>
        <Field
          id="property-city"
          label="Ort"
          description="Die Stadt oder Gemeinde, in der die Immobilie liegt."
          errors={form.of("address.city")}
        >
          <input type="text" value={city} onChange={(e) => setCity(e.target.value)} required autoComplete="address-level2" />
        </Field>
        <Field
          id="property-year"
          label="Baujahr"
          hint="Bestimmt den AfA-Satz."
          description="Das Jahr, in dem das Gebäude fertiggestellt wurde, laut Kaufvertrag, Exposé oder Energieausweis. Danach richtet sich der AfA-Satz: vor 1925 2,5 %, von 1925 bis 2022 2 %, ab 2023 3 % im Jahr."
          errors={form.of("build_year")}
        >
          <input type="text" inputMode="numeric" value={buildYear} onChange={(e) => setBuildYear(e.target.value)} required maxLength={4} />
        </Field>
        {property ? null : (
          <>
            <Field
              id="property-price"
              label="Kaufpreis (€)"
              description="Der Kaufpreis laut Kaufvertrag, ohne Nebenkosten wie Grunderwerbsteuer, Notar oder Makler; die erfassen Sie danach unter „Nebenkosten“."
              errors={form.of("purchase_price")}
            >
              <input type="text" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} required autoComplete="off" />
            </Field>
            <Field
              id="property-notarized"
              label="Notartermin (Kaufvertrag beurkundet)"
              hint="Ab hier läuft die zehnjährige Spekulationsfrist."
              description="Der Tag, an dem der Kaufvertrag beim Notar beurkundet wurde: das Datum der Urkunde, oben auf dem Kaufvertrag. Ab diesem Tag laufen die zehn Jahre der Spekulationsfrist, nach denen ein Verkauf steuerfrei ist."
              errors={form.of("notarized_on")}
            >
              <input type="date" value={notarizedOn} onChange={(e) => setNotarizedOn(e.target.value)} required />
            </Field>
            <Field
              id="property-purchase-date"
              label="Übergang Besitz, Nutzen und Lasten"
              hint="Ab hier läuft die AfA."
              description="Der Tag, an dem die Immobilie laut Kaufvertrag auf Sie überging, meist nach Zahlung des Kaufpreises. Ab diesem Monat läuft die AfA; ab diesem Jahr fragt die Unterlagen-Checkliste nach Unterlagen."
              errors={form.of("purchase_date")}
            >
              <input type="date" value={purchaseDate} onChange={(e) => setPurchaseDate(e.target.value)} required />
            </Field>
          </>
        )}
      </div>
      {property ? (
        <div className="stack">
          <Facts
            rows={[
              ["Notartermin", formatDate(property.notarized_on)],
              ["Übergang", formatDate(property.purchase_date)],
              ["Kaufpreis", formatMoney(property.purchase_price)],
            ]}
          />
          <p className="muted">
            Diese Angaben sind Meilensteine und werden nicht überschrieben: Änderungen über „Datum korrigieren“ in den{" "}
            <Link href={`/properties/${property.id}`}>Stammdaten</Link>, damit der Verlauf die Korrektur zeigt.
          </p>
        </div>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{property ? "Speichern" : "Immobilie anlegen"}</Submit>
        <Link href={property ? `/properties/${property.id}` : "/"}>Abbrechen</Link>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}
