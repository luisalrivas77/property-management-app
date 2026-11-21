// "Datum korrigieren" (components/corrections.tsx): the correction form of a milestone date.
import { defineMessages } from "./core";

export const corrections = defineMessages({
  de: {
    priceLabel: "Kaufpreis",
    priceHint: "Vorbelegt mit dem gespeicherten Kaufpreis; nur ändern, wenn er falsch ist.",
    description:
      "Das richtige Datum laut Dokument. Die Korrektur wird als eigener Eintrag gespeichert; der Verlauf zeigt das ursprüngliche Datum weiter an.",
    stored: (date: string, price: string | null) =>
      `Gespeichert: ${date}${price ?? ""}. Die Korrektur wird als eigener Eintrag protokolliert; der Verlauf zeigt danach das neue Datum mit dem Hinweis auf das ursprüngliche.`,
    storedPrice: (label: string, amount: string) => `, ${label} ${amount}`,
    priceField: (label: string) => `${label} (€)`,
    priceDescription: (label: string) => `Nur ändern, wenn auch der Betrag (${label}) falsch erfasst ist: der richtige Betrag laut Vertrag.`,
    dateMissing: "Bitte ein Datum eingeben.",
    sameBoth: "Datum und Betrag sind schon gespeichert.",
    sameDate: "Das ist schon das gespeicherte Datum.",
    submit: "Korrektur speichern",
  },
  en: {
    priceLabel: "Purchase price",
    priceHint: "Pre-filled with the stored purchase price; change it only if it is wrong.",
    description:
      "The correct date from the document. The correction is saved as an entry of its own; the history keeps showing the original date.",
    stored: (date: string, price: string | null) =>
      `Stored: ${date}${price ?? ""}. The correction is recorded as an entry of its own; the history then shows the new date with a note of the original one.`,
    storedPrice: (label: string, amount: string) => `, ${label} ${amount}`,
    priceField: (label: string) => `${label} (€)`,
    priceDescription: (label: string) => `Change only if the amount (${label}) was also entered wrongly: the correct amount from the contract.`,
    dateMissing: "Please enter a date.",
    sameBoth: "This date and amount are already stored.",
    sameDate: "This is already the stored date.",
    submit: "Save correction",
  },
});
