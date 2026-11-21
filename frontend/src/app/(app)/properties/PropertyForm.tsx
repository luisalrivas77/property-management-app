"use client";

// Immobilie form (§9.1). Create: everything PropertyCreate takes. Edit: PropertyPatch only
// (label, kind, address, build year). Notartermin, Übergang and Kaufpreis are milestones and
// change only through the corrections on the Stammdaten tab, so the edit form shows them
// read-only. With `onDone` (the property page's sheet, phase 10) it calls back instead of
// navigating after an edit.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Property, PropertyKind } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { decimalInput, toMoneyString, toYear } from "@/components/input";
import { options, PROPERTY_KIND } from "@/components/labels";
import { Facts } from "@/components/ui";
import { useFormat, useT } from "@/i18n";
import { propertyPage } from "@/i18n/propertyPage";

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

export function PropertyForm({
  property,
  onDone,
  onCancel,
}: {
  property?: Property;
  /** Called after an edit is saved instead of navigating (the form sits in a sheet). */
  onDone?: () => void;
  /** Shown as „Abbrechen“ instead of the link back (the form sits in a sheet). */
  onCancel?: () => void;
}) {
  const t = useT(propertyPage);
  const f = useFormat();
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
        if (onDone) onDone();
        else router.push(`/properties/${property.id}`);
      } else {
        const purchasePrice = values.take("purchase_price", toMoneyString(price));
        if (!notarizedOn) values.messages.notarized_on = [t.pfNotarizedMissing];
        if (!purchaseDate) values.messages.purchase_date = [t.pfPossessionMissing];
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
        router.push(`/properties/${created.id}?tab=uebersicht&neu=1#card-eigentuemer`);
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
          label={t.label}
          hint={t.pfLabelHint}
          description={t.pfLabelDesc}
          errors={form.of("label")}
        >
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id="property-kind"
          label={t.kindLabel}
          description={t.pfKindDesc}
          errors={form.of("kind")}
        >
          <select value={kind} onChange={(e) => setKind(e.target.value as PropertyKind)}>
            {options(PROPERTY_KIND).map(([value]) => (
              <option key={value} value={value}>
                {t.kind(value)}
              </option>
            ))}
          </select>
        </Field>
        <Field
          id="property-street"
          label={t.pfStreet}
          description={t.pfStreetDesc}
          errors={form.of("address.street")}
        >
          <input type="text" value={street} onChange={(e) => setStreet(e.target.value)} required autoComplete="address-line1" />
        </Field>
        <Field
          id="property-house"
          label={t.pfHouseNumber}
          description={t.pfHouseNumberDesc}
          errors={form.of("address.house_number")}
        >
          <input type="text" value={houseNumber} onChange={(e) => setHouseNumber(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id="property-postal"
          label={t.pfPostalCode}
          description={t.pfPostalCodeDesc}
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
          label={t.pfCity}
          description={t.pfCityDesc}
          errors={form.of("address.city")}
        >
          <input type="text" value={city} onChange={(e) => setCity(e.target.value)} required autoComplete="address-level2" />
        </Field>
        <Field
          id="property-year"
          label={t.buildYear}
          hint={t.pfBuildYearHint}
          description={t.pfBuildYearDesc}
          errors={form.of("build_year")}
        >
          <input type="text" inputMode="numeric" value={buildYear} onChange={(e) => setBuildYear(e.target.value)} required maxLength={4} />
        </Field>
        {property ? null : (
          <>
            <Field
              id="property-price"
              label={t.pfPrice}
              description={t.pfPriceDesc}
              errors={form.of("purchase_price")}
            >
              <input type="text" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} required autoComplete="off" />
            </Field>
            <Field
              id="property-notarized"
              label={t.pfNotarized}
              hint={t.pfNotarizedHint}
              description={t.pfNotarizedDesc}
              errors={form.of("notarized_on")}
            >
              <input type="date" value={notarizedOn} onChange={(e) => setNotarizedOn(e.target.value)} required />
            </Field>
            <Field
              id="property-purchase-date"
              label={t.pfPossession}
              hint={t.pfPossessionHint}
              description={t.pfPossessionDesc}
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
              [t.notarized, f.date(property.notarized_on)],
              [t.pfPossessionShort, f.date(property.purchase_date)],
              [t.price, f.money(property.purchase_price)],
            ]}
          />
          <p className="muted">
            {t.pfMilestones} <Link href={`/properties/${property.id}`}>{t.tabStammdaten}</Link>
            {t.pfMilestonesEnd}
          </p>
        </div>
      ) : null}
      <div className="row">
        <Submit busy={busy}>{property ? t.save : t.pfCreate}</Submit>
        {onCancel ? (
          <button type="button" className="link" onClick={onCancel}>
            {t.cancel}
          </button>
        ) : (
          <Link href={property ? `/properties/${property.id}` : "/"}>{t.cancel}</Link>
        )}
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}
