// Steuern › Eure Angaben: the owner's typed facts (components/returnInputs.tsx), a row's evidence
// (components/returnInputEvidence.tsx) and the Werbungskosten block rows of the Anlage N
// (components/anlageNBlocks.tsx). German first, English beside it (glossary.md).
import { defineMessages } from "./core";

export const evidence = defineMessages({
  de: {
    receipt: "Beleg",
    sources: "Fundstellen",
    scrubbed: "Die Fundstellen wurden 90 Tage nach der Übernahme gelöscht (Datenschutz); der Beleg bleibt.",
    byHand: "Von Hand eingetragen; keine Fundstellen aus einem Vorschlag.",
    noValues: "Keine Werte im Vorschlag.",
    edited: " geändert",
    cleared: " geleert",
    openOriginal: (page: number) => `Original auf S. ${page} öffnen`,
    page: (page: number) => `S. ${page}`,
    noSource: " · ohne Fundstelle",
  },
  en: {
    receipt: "Receipt",
    sources: "Where it was read",
    scrubbed: "The passages were deleted 90 days after the values were applied (data protection); the receipt stays.",
    byHand: "Typed by hand; no passages from a suggestion.",
    noValues: "No values in the suggestion.",
    edited: " changed",
    cleared: " cleared",
    openOriginal: (page: number) => `Open the original on p. ${page}`,
    page: (page: number) => `p. ${page}`,
    noSource: " · no passage",
  },
});
