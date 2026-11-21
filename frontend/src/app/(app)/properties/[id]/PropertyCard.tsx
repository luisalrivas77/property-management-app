// One card of the property page (phase 10, mockup 25): a former tab, with its heading and the
// stable id an old ?tab= link or a #card-… hash scrolls to (components/propertySections.ts).
// A card with a detail panel renders this inside its DetailLayout's list column, so the panel
// stays its own surface beside the card instead of inside it.
import type { ReactNode } from "react";

import { type PropertyCard as Card, propertyCardId } from "@/components/propertySections";

import "@/components/pageRows.css";

export function PropertyCard({ card, title, children }: { card: Card; title: string; children: ReactNode }) {
  const id = propertyCardId(card);
  return (
    <section id={id} className="card page-card property-anchor" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>{title}</h2>
      {children}
    </section>
  );
}

/** The anchor for a card that draws its own card surfaces (the checklist, the loans, the
 * current tenancies): the id only, no second surface around them. */
export function PropertyAnchor({ card, children }: { card: Card; children: ReactNode }) {
  return (
    <div id={propertyCardId(card)} className="property-anchor">
      {children}
    </div>
  );
}
