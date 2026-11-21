"use client";

// Stammdaten: the property's fields, with the two milestone dates and their correction
// dialogs. A corrected date says so, with the original date from History (§9.4). Below them,
// the current tenancy per unit (CurrentTenancies).
// Phase 10 (mockup 25): „Bearbeiten“ slides the property form in from the right (its PATCH body
// unchanged); the edit route stays.
import Link from "next/link";
import { useCallback } from "react";

import { api, ok } from "@/api/client";
import type { Property } from "@/api/schemas";
import { DateCorrectionForm } from "@/components/corrections";
import { correctedFrom, findMilestone } from "@/components/history";
import { SheetDialog } from "@/components/SheetDialog";
import { ActionDialog, CorrectedNote, Facts } from "@/components/ui";
import { useLoad } from "@/components/useLoad";
import { useFormat, useT } from "@/i18n";
import { propertyPage } from "@/i18n/propertyPage";

import { PropertyForm } from "../PropertyForm";
import { CurrentTenancies } from "./CurrentTenancies";

export function MasterDataTab({ property, onChanged }: { property: Property; onChanged: () => void }) {
  const t = useT(propertyPage);
  const f = useFormat();
  const loadHistory = useCallback(
    () => ok(api.GET("/api/properties/{property_id}/history", { params: { path: { property_id: property.id } } })),
    [property.id],
  );
  const history = useLoad(loadHistory);
  const notarizedFrom = correctedFrom(findMilestone(history.data, "property.purchase_notarized", property.id), property.notarized_on);
  const possessionFrom = correctedFrom(
    findMilestone(history.data, "property.possession_transferred", property.id),
    property.purchase_date,
  );
  const path = { params: { path: { property_id: property.id } } };

  function changed() {
    onChanged();
    history.reload();
  }

  return (
    <div className="stack">
      <Facts
        rows={[
          [t.label, property.label],
          [t.kindLabel, t.kind(property.kind)],
          [
            t.address,
            `${property.address.street} ${property.address.house_number}, ${property.address.postal_code} ${property.address.city}`,
          ],
          [t.buildYear, String(property.build_year)],
          [
            t.notarized,
            <>
              {f.date(property.notarized_on)}
              {notarizedFrom ? <CorrectedNote original={f.date(notarizedFrom)} /> : null}
            </>,
          ],
          [
            t.possession,
            <>
              {f.date(property.purchase_date)}
              {possessionFrom ? <CorrectedNote original={f.date(possessionFrom)} /> : null}
            </>,
          ],
          [t.price, f.money(property.purchase_price)],
          [
            t.afaBasis,
            <Link key="afa" href={`/properties/${property.id}?tab=aufteilung`}>
              {property.selected_price_split_id ? t.selected : t.notSelected}
            </Link>,
          ],
          [t.ownershipEnded, f.date(property.ownership_ended_on)],
        ]}
      />
      <div className="row">
        <SheetDialog label={t.edit} title={t.editTitle} buttonClass="secondary">
          {(close) => (
            <PropertyForm
              key={property.updated_at}
              property={property}
              onCancel={close}
              onDone={() => {
                close();
                changed();
              }}
            />
          )}
        </SheetDialog>
        <ActionDialog label={t.correctNotarized} title={t.correctNotarized}>
          {(close) => (
            <DateCorrectionForm
              idPrefix="notarized"
              label={t.rightNotarized}
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
        <ActionDialog label={t.correctPossession} title={t.correctPossession}>
          {(close) => (
            <DateCorrectionForm
              idPrefix="possession"
              label={t.rightPossession}
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
        {t.milestoneNote} <Link href={`/properties/${property.id}?tab=protokoll`}>{t.tabAudit}</Link>
        {t.milestoneNoteEnd}
      </p>
      <CurrentTenancies propertyId={property.id} />
    </div>
  );
}
