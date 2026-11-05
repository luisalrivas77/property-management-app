"use client";

// Stammdaten: the property's fields, with the two milestone dates and their correction
// dialogs. A corrected date says so, with the original date from History (§9.4).
import Link from "next/link";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { Property } from "@/api/schemas";
import { DateCorrectionForm } from "@/components/corrections";
import { formatDate, formatMoney } from "@/components/format";
import { findMilestone } from "@/components/history";
import { PROPERTY_KIND } from "@/components/labels";
import { ActionDialog, CorrectedNote, Facts } from "@/components/ui";
import { useLoad } from "@/components/useLoad";

export function MasterDataTab({ property, onChanged }: { property: Property; onChanged: () => void }) {
  const loadHistory = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/history", { params: { path: { property_id: property.id } } })),
    [property.id],
  );
  const history = useLoad(loadHistory);
  const notarized = findMilestone(history.data, "property.purchase_notarized", property.id);
  const possession = findMilestone(history.data, "property.possession_transferred", property.id);
  const path = { params: { path: { property_id: property.id } } };

  function changed() {
    onChanged();
    history.reload();
  }

  return (
    <div className="stack">
      <Facts
        rows={[
          ["Bezeichnung", property.label],
          ["Art", PROPERTY_KIND[property.kind]],
          [
            "Adresse",
            `${property.address.street} ${property.address.house_number}, ${property.address.postal_code} ${property.address.city}`,
          ],
          ["Baujahr", String(property.build_year)],
          [
            "Notartermin",
            <>
              {formatDate(property.notarized_on)}
              {notarized?.corrected && notarized.original_effective_date ? (
                <CorrectedNote original={formatDate(notarized.original_effective_date)} />
              ) : null}
            </>,
          ],
          [
            "Übergang (Besitz, Nutzen, Lasten)",
            <>
              {formatDate(property.purchase_date)}
              {possession?.corrected && possession.original_effective_date ? (
                <CorrectedNote original={formatDate(possession.original_effective_date)} />
              ) : null}
            </>,
          ],
          ["Kaufpreis", formatMoney(property.purchase_price)],
          [
            "AfA-Basis (Aufteilung)",
            property.selected_price_split_id ? (
              <Link href={`/properties/${property.id}?tab=aufteilung`}>ausgewählt</Link>
            ) : (
              <Link href={`/properties/${property.id}?tab=aufteilung`}>noch nicht gewählt</Link>
            ),
          ],
          ["Eigentum beendet", formatDate(property.ownership_ended_on)],
        ]}
      />
      <div className="row">
        <Link href={`/properties/${property.id}/edit`} className="button-link secondary">
          Bearbeiten
        </Link>
        <ActionDialog label="Notartermin korrigieren" title="Notartermin korrigieren">
          {(close) => (
            <DateCorrectionForm
              idPrefix="notarized"
              label="Richtiger Notartermin"
              current={property.notarized_on}
              currentPrice={property.purchase_price}
              onSubmit={(values) => ok(api.POST("/api/properties/{property_id}/corrections/notarized-on", { ...path, body: values }))}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </ActionDialog>
        <ActionDialog label="Übergang korrigieren" title="Übergang korrigieren">
          {(close) => (
            <DateCorrectionForm
              idPrefix="possession"
              label="Richtiges Datum des Übergangs"
              current={property.purchase_date}
              onSubmit={(values) =>
                ok(api.POST("/api/properties/{property_id}/corrections/purchase-date", { ...path, body: { new_date: values.new_date } }))
              }
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </ActionDialog>
      </div>
      <p className="muted">
        Notartermin und Übergang werden nie überschrieben: eine Korrektur ist ein eigener Eintrag im{" "}
        <Link href={`/properties/${property.id}?tab=protokoll`}>Protokoll</Link>.
      </p>
    </div>
  );
}
