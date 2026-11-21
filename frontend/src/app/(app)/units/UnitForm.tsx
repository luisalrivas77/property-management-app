"use client";

// Einheit form (§9.1): Bezeichnung, Wohnfläche, Zimmer, MEA (Miteigentumsanteil as n/d).
// With `onDone` (the unit page's sheet, phase 10) it calls back instead of navigating.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import type { Unit } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatFraction } from "@/components/format";
import { decimalInput, toDecimalString, toFraction } from "@/components/input";
import { useT } from "@/i18n";
import { units } from "@/i18n/units";

const KEYS = ["label", "floor_area_m2", "rooms", "mea"] as const;

export function UnitForm({
  propertyId,
  unit,
  onDone,
  onCancel,
}: {
  propertyId: string;
  unit?: Unit;
  /** Called after a save instead of navigating (the form sits in a sheet). */
  onDone?: () => void;
  /** Shown as „Abbrechen“ instead of the link back (the form sits in a sheet). */
  onCancel?: () => void;
}) {
  const t = useT(units);
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
        if (onDone) onDone();
        else router.push(`/units/${unit.id}`);
      } else {
        await ok(api.POST("/api/units", { body: { property_id: propertyId, ...fields } }));
        if (onDone) onDone();
        else router.push(`/properties/${propertyId}?tab=einheiten`);
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
          id="unit-label"
          label={t.label}
          hint={t.labelHint}
          description={t.labelDescription}
          errors={form.of("label")}
        >
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id="unit-area"
          label={t.areaField}
          description={t.areaDescription}
          errors={form.of("floor_area_m2")}
        >
          <input type="text" inputMode="decimal" value={area} onChange={(e) => setArea(e.target.value)} required autoComplete="off" />
        </Field>
        <Field
          id="unit-rooms"
          label={t.roomsField}
          hint={t.roomsHint}
          description={t.roomsDescription}
          errors={form.of("rooms")}
        >
          <input type="text" inputMode="decimal" value={rooms} onChange={(e) => setRooms(e.target.value)} autoComplete="off" />
        </Field>
        <Field
          id="unit-mea"
          label={t.meaField}
          hint={t.meaHint}
          description={t.meaDescription}
          errors={form.of("mea")}
        >
          <input type="text" value={mea} onChange={(e) => setMea(e.target.value)} autoComplete="off" />
        </Field>
      </div>
      <div className="row">
        <Submit busy={busy}>{unit ? t.save : t.create}</Submit>
        {onCancel ? (
          <button type="button" className="link" onClick={onCancel}>
            {t.cancel}
          </button>
        ) : (
          <Link href={unit ? `/units/${unit.id}` : `/properties/${propertyId}?tab=einheiten`}>{t.cancel}</Link>
        )}
      </div>
      <FormBanner errors={form.errors} />
    </form>
  );
}
