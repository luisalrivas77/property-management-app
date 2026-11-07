"use client";

// Eigentümer: the ownership shares of the property as fractions (n/d), PUT ownership (§4.2).
// Whether the shares add up to 1 is the API's rule (ownership.shares_must_sum_to_one); its
// German message appears under the shares.
import Link from "next/link";
import { useCallback, useState, type FormEvent } from "react";

import { api, ok } from "@/api/client";
import { ApiError } from "@/api/errors";
import type { Owner, OwnerShare, PropertyOwnership } from "@/api/schemas";
import { collector, Field, FormBanner, Submit, useFormErrors } from "@/components/form";
import { formatFraction, formatShare } from "@/components/format";
import { toFraction } from "@/components/input";
import { Loading, Notice } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

interface OwnershipData {
  owners: Owner[];
  ownership: PropertyOwnership | null;
}

const KEYS = ["shares", "effective_date"] as const;
const CODE_FIELDS = { "ownership.shares_must_sum_to_one": "shares" };

export function OwnershipTab({ propertyId, justCreated }: { propertyId: string; justCreated: boolean }) {
  const load = useCallback(async (): Promise<OwnershipData> => {
    const owners = await ok(api.GET("/api/owners"));
    let ownership: PropertyOwnership | null = null;
    try {
      ownership = await ok(api.GET("/api/properties/{property_id}/ownership", { params: { path: { property_id: propertyId } } }));
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) throw error; // 404: none set yet
    }
    return { owners, ownership };
  }, [propertyId]);
  const data = useLoad(load);
  const [saved, setSaved] = useState(false);

  return (
    <div className="stack">
      {justCreated && !saved ? <Notice>Immobilie angelegt. Als Nächstes die Eigentumsanteile festlegen.</Notice> : null}
      {saved ? <Notice>Eigentumsanteile gespeichert.</Notice> : null}
      <Loading state={data}>
        {({ owners, ownership }) =>
          owners.length === 0 ? (
            <p>
              Noch keine Eigentümer erfasst. Zuerst unter <Link href="/owners">Eigentümer</Link> anlegen.
            </p>
          ) : (
            <OwnershipForm
              key={JSON.stringify(ownership?.shares ?? [])}
              propertyId={propertyId}
              owners={owners}
              ownership={ownership}
              onSaved={() => {
                setSaved(true);
                data.reload();
              }}
            />
          )
        }
      </Loading>
    </div>
  );
}

function OwnershipForm({
  propertyId,
  owners,
  ownership,
  onSaved,
}: {
  propertyId: string;
  owners: Owner[];
  ownership: PropertyOwnership | null;
  onSaved: () => void;
}) {
  const [shares, setShares] = useState<Record<string, string>>(() =>
    Object.fromEntries(owners.map((owner) => [owner.id, shareInput(ownership, owner.id)])),
  );
  const [effectiveDate, setEffectiveDate] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useFormErrors(KEYS, CODE_FIELDS);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = collector();
    const body: OwnerShare[] = [];
    for (const owner of owners) {
      const raw = shares[owner.id] ?? "";
      if (raw.trim() === "") continue;
      const share = values.take(`share-${owner.id}`, toFraction(raw));
      if (share) body.push({ owner_id: owner.id, share });
    }
    if (body.length === 0) values.messages.shares = ["Mindestens einen Anteil eingeben."];
    if (!values.ok) return form.invalid(values.messages);
    setBusy(true);
    form.clear();
    try {
      await ok(
        api.PUT("/api/properties/{property_id}/ownership", {
          params: { path: { property_id: propertyId } },
          body: { shares: body, effective_date: effectiveDate || null },
        }),
      );
      onSaved();
    } catch (error) {
      form.fail(error);
    } finally {
      setBusy(false);
    }
  }

  const sharesErrors = form.of("shares");
  return (
    <form className="stack" onSubmit={submit} noValidate>
      {ownership ? (
        <p>
          Aktuell:{" "}
          {ownership.shares
            .map((share) => `${owners.find((o) => o.id === share.owner_id)?.full_name ?? "?"} ${formatShare(share.share)}`)
            .join(", ")}
        </p>
      ) : (
        <p className="muted">Noch keine Anteile festgelegt.</p>
      )}
      <fieldset className="stack" aria-describedby={sharesErrors.length > 0 ? "shares-error" : undefined}>
        <legend>Anteile (als Bruch oder Prozent, z. B. 1/2 oder 50 %; leer = kein Anteil)</legend>
        <div className="grid">
          {owners.map((owner) => (
            <Field
              key={owner.id}
              id={`share-${owner.id}`}
              label={owner.full_name}
              description="Der Anteil dieser Person an der Immobilie laut Kaufvertrag oder Grundbuch, z. B. 1/2 bei „je zur Hälfte“. Alle Anteile zusammen müssen genau 1 (100 %) ergeben."
              errors={form.of(`share-${owner.id}`)}
            >
              <input
                type="text"
                value={shares[owner.id] ?? ""}
                onChange={(e) => setShares((current) => ({ ...current, [owner.id]: e.target.value }))}
                placeholder="1/2"
                autoComplete="off"
              />
            </Field>
          ))}
        </div>
        {sharesErrors.length > 0 ? (
          <ul className="field-errors" id="shares-error">
            {sharesErrors.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        ) : null}
      </fieldset>
      <Field
        id="ownership-effective"
        label="Gültig ab (optional)"
        hint="Leer lassen beim Kauf; ein Datum, wenn sich die Anteile später ändern."
        description="Ab wann diese Anteile gelten. Beim Kauf leer lassen; bei einer späteren Änderung, z. B. einer Übertragung, das Datum aus dem Vertrag."
        errors={form.of("effective_date")}
      >
        <input type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} />
      </Field>
      <Submit busy={busy}>Anteile speichern</Submit>
      <FormBanner errors={form.errors} />
    </form>
  );
}

/** The owner's current share as the text to prefill its input (round-trips through toFraction). */
function shareInput(ownership: PropertyOwnership | null, ownerId: string): string {
  const share = ownership?.shares.find((s) => s.owner_id === ownerId)?.share;
  return share ? formatFraction(share) : "";
}
