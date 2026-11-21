// The few words of the route pages themselves (app/(app)/tax/[year], its Jahresfragen,
// planning and loans/new): a year that is not one, and what to do before a property exists.
import { defineMessages } from "./core";

export const pages = defineMessages({
  de: {
    invalidYear: "Kein gültiges Steuerjahr.",
    currentYear: "Zum aktuellen Jahr",
    anlageVEyebrow: (year: number) => `Steuerjahr ${year} · Anlage V (Entwurf)`,
    noProperty: "Noch keine Immobilie.",
    createProperty: "Immobilie anlegen",
    anlageVRest: ", dann entsteht hier der Entwurf der Anlage V.",
    planningRest: ", dann entsteht hier die Planung bis zum Verkauf.",
    loanNeedsProperty: "Ein Darlehen gehört zu einer Immobilie: dort unter „Darlehen“ anlegen (",
    overview: "Übersicht",
    loanNeedsPropertyEnd: ").",
  },
  en: {
    invalidYear: "Not a valid tax year.",
    currentYear: "To the current year",
    anlageVEyebrow: (year: number) => `Tax year ${year} · Anlage V (draft)`,
    noProperty: "No property yet.",
    createProperty: "Add a property",
    anlageVRest: ", then the draft of the Anlage V (rental income form) is made here.",
    planningRest: ", then the planning through to the sale is made here.",
    loanNeedsProperty: "A loan belongs to a property: add it there under “Loans” (",
    overview: "Overview",
    loanNeedsPropertyEnd: ").",
  },
});
