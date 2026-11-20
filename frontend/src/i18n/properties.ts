// Immobilien and the property, unit, tenancy, tenant, owner and loan pages (mockups 14-C2, 25;
// step 4b). Step 2 added the interim list behind „Mehr“ › Immobilien (/properties), which step 4b
// replaces with one card per property; its coder owns this file from then on.
import { defineMessages } from "./core";

export const properties = defineMessages({
  de: {
    title: "Immobilien",
    add: "Immobilie anlegen",
    owners: "Eigentümer",
    ownersHint: "Anteile, Kontakt",
    tenants: "Mieter",
    tenantsHint: "Kontakt, Mietverhältnisse",
    none: "Noch keine Immobilie angelegt.",
    selected: "Ausgewählt",
    address: "Adresse",
    purchaseDate: "Übergabe",
    notarizedOn: "Beurkundet",
    livingArea: "Wohnfläche",
    openProperty: "Immobilie öffnen",
  },
  en: {
    title: "Properties",
    add: "Add a property",
    owners: "Owners",
    ownersHint: "Shares, contact",
    tenants: "Tenants",
    tenantsHint: "Contact, tenancies",
    none: "No property yet.",
    selected: "Selected",
    address: "Address",
    purchaseDate: "Handover",
    notarizedOn: "Notarised",
    livingArea: "Living area",
    openProperty: "Open property",
  },
});
