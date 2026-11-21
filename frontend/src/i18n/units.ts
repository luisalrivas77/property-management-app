// The unit page and its form (phase 10 step 4b, mockup 25).
import { defineMessages } from "./core";

export const units = defineMessages({
  de: {
    edit: "Bearbeiten",
    editTitle: "Einheit bearbeiten",
    create: "Einheit anlegen",
    area: "Fläche",
    rooms: "Zimmer",
    mea: "MEA",
    tenancies: "Mietverhältnisse",
    noTenancy: "Noch kein Mietverhältnis.",
    addTenancy: "Mietverhältnis anlegen",
    period: (start: string, end: string | null) => (end ? `${start} – ${end}` : `ab ${start}`),
    marketRent: "Ortsübliche Miete",
    history: "Verlauf",
    documents: "Dokumente",
    // UnitForm
    label: "Bezeichnung",
    labelHint: "z. B. „WE 3“ oder „TG-Stellplatz 12“",
    labelDescription:
      "Wie die Einheit in Teilungserklärung oder Kaufvertrag heißt, z. B. „WE 3“ für Wohnung Nr. 3 oder ein Tiefgaragenstellplatz.",
    areaField: "Fläche (m²)",
    areaDescription:
      "Die Wohnfläche in Quadratmetern laut Mietvertrag, Kaufvertrag oder Wohnflächenberechnung. Hausbuch rechnet noch nicht damit; gebraucht wird sie später z. B. für die Betriebskostenabrechnung.",
    roomsField: "Zimmer (optional)",
    roomsHint: "z. B. 2,5",
    roomsDescription: "Die Zahl der Zimmer laut Mietvertrag oder Exposé; ein halbes Zimmer als ,5.",
    meaField: "MEA (optional)",
    meaHint: "Miteigentumsanteil laut Teilungserklärung, z. B. 85/1000",
    meaDescription:
      "Ihr Anteil am gemeinschaftlichen Eigentum der WEG (Grundstück, Dach, Treppenhaus …), laut Teilungserklärung oder Kaufvertrag, z. B. 56,18/1.000. Nach ihm verteilt die WEG meist die gemeinsamen Kosten (Hausgeld). Bei einzeln vermieteten Zimmern einer Wohnung leer lassen.",
    save: "Speichern",
    cancel: "Abbrechen",
  },
  en: {
    edit: "Edit",
    editTitle: "Edit unit",
    create: "Add unit",
    area: "Floor area",
    rooms: "Rooms",
    mea: "MEA (co-ownership share)",
    tenancies: "Tenancies",
    noTenancy: "No tenancy yet.",
    addTenancy: "Add tenancy",
    period: (start: string, end: string | null) => (end ? `${start} – ${end}` : `from ${start}`),
    marketRent: "Local comparable rent",
    history: "History",
    documents: "Documents",
    label: "Name",
    labelHint: "e.g. “WE 3” or “TG-Stellplatz 12”",
    labelDescription:
      "What the declaration of division or the purchase contract calls the unit, e.g. “WE 3” for flat no. 3, or an underground parking space.",
    areaField: "Floor area (m²)",
    areaDescription:
      "The living area in square metres per the lease, the purchase contract or the floor-area calculation. Hausbuch does not compute with it yet; it is needed later, e.g. for the running-cost settlement.",
    roomsField: "Rooms (optional)",
    roomsHint: "e.g. 2,5",
    roomsDescription: "The number of rooms per the lease or the listing; half a room as ,5 (with a comma).",
    meaField: "MEA (optional)",
    meaHint: "Co-ownership share per the declaration of division, e.g. 85/1000",
    meaDescription:
      "Your share in the WEG's common property (land, roof, stairwell …) per the declaration of division or the purchase contract, e.g. 56.18/1,000. The WEG usually splits the common costs (Hausgeld) by it. Leave empty for rooms of a flat let one by one.",
    save: "Save",
    cancel: "Cancel",
  },
});
