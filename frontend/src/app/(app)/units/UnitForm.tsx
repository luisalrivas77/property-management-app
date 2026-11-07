"use client";

// Einheit form (§9.1): Bezeichnung, Wohnfläche, Zimmer, MEA (Miteigentumsanteil as n/d).
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Unit } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatFraction } from "@/components/format";
import { decimalInput, toDecimalString, toFraction } from "@/components/input";

const KEYS = ["label", "floor_area_m2", "rooms", "mea"] as const;

export function UnitForm({ propertyId, unit }: { propertyId: string; unit?: Unit }) {
  const router = useRouter();
  const [label, setLabel] = useState(unit?.label ?? "");
  const [area, setArea] = useState(decimalInput(unit?.floor_area_m2));
  const [rooms, setRooms] = useState(decimalInput(unit?.rooms));
  const [mea, setMea] = useState(unit?.mea ? formatFraction(unit.mea) : "");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const floorArea = values.take("floor_area_m2", toDecimalString(area, 2));
    const roomCount = values.optional("rooms", rooms, (raw) => toDecimalString(raw, 1));
    const share = values.optional("mea", mea, toFraction);
    if (!values.ok || floorArea === undefined || roomCount === undefined || share === undefined) return form.invalid(values.messages);
    const fields = { label: label.trim(), floor_area_m2: floorArea, rooms: roomCount, mea: share };
    setBusy(true);
    form.clear();
    try {
      if (unit) {
        await ok(api.PATCH("/api/units/{unit_id}", { params: { path: { unit_id: unit.id } }, body: fields }));
        router.push(`/units/${unit.id}`);
      } else {
        await ok(api.POST("/api/units", { body: { property_id: propertyId, ...fields } }));
        router.push(`/properties/${propertyId}?tab=einheiten`);
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
        <Field id="unit-label" label="Bezeichnung" hint="z. B. „WE 3“ oder „TG-Stellplatz 12“" errors={form.of("label")}>
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} required autoComplete="off" />
        </Field>
        <Field id="unit-area" label="Fläche (m²)" errors={form.of("floor_area_m2")}>
          <input type="text" inputMode="decimal" value={area} onChange={(e) => setArea(e.target.value)} required autoComplete="off" />
        </Field>
        <Field id="unit-rooms" label="Zimmer (optional)" hint="z. B. 2,5" errors={form.of("rooms")}>
          <input type="text" inputMode="decimal" value={rooms} onChange={(e) => setRooms(e.target.value)} autoComplete="off" />
        </Field>
        <Field id="unit-mea" label="MEA (optional)" hint="Miteigentumsanteil laut Teilungserklärung, z. B. 85/1000" errors={form.of("mea")}>
          <input type="text" value={mea} onChange={(e) => setMea(e.target.value)} autoComplete="off" />
        </Field>
      </div>
      <div className="row">
        <Submit busy={busy}>{unit ? "Speichern" : "Einheit anlegen"}</Submit>
        <Link href={unit ? `/units/${unit.id}` : `/properties/${propertyId}?tab=einheiten`}>Abbrechen</Link>
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}
