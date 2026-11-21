// The Nebenkostenpauschale rise test outside Planung (components/flatRate.tsx): its to-dos, the
// buttons and the form for the letter to a tenant. German first, English beside it (glossary.md).
// The letter itself is a paper for the tenant and stays German (the backend renders it).
import { defineMessages } from "./core";

export const flatRateText = defineMessages({
  de: {
    notAdvice:
      "Ob und ab wann eine Pauschale steigt oder sinkt, entscheidet § 560 BGB; die erste Erhöhung lohnt eine Prüfung beim Vermieterverein.",
    since: (date: string) => `seit die Pauschale am ${date} festgelegt wurde`,
    raiseText: (since: string, reasons: string, rise: string, earliest: string, notAdvice: string) =>
      `Die umlagefähigen Kosten der Wohnung sind gestiegen, ${since}${reasons ? ` (${reasons})` : ""}. ` +
      `Auf dieses Zimmer entfallen ${rise} pro Monat; frühester Beginn ist der ` +
      `${earliest}, wenn das Schreiben diesen Monat zugeht. ` +
      `„Schreiben erstellen“ macht den Brief an den Mieter, „Mietänderung“ trägt die neue Pauschale ein. ${notAdvice}`,
    lowerText: (since: string, reasons: string, fall: string, fellOn: string, notAdvice: string) =>
      `Die umlagefähigen Kosten der Wohnung sind gesunken, ${since}${reasons ? ` (${reasons})` : ""}. ` +
      `Die Pauschale dieses Zimmers sinkt um ${fall} pro Monat, ab dem ${fellOn}, ` +
      `und der Mieter ist unverzüglich zu informieren (§ 560 Abs. 3 BGB). ` +
      `„Schreiben erstellen“ macht den Brief, „Mietänderung“ trägt die neue Pauschale ein. ${notAdvice}`,
    todoTitle: (unit: string, raise: boolean) => `Nebenkostenpauschale ${unit} ${raise ? "kann steigen" : "senken"}`,
    rentChange: "Mietänderung",
    costsAndSources: "Kosten und Quellen ansehen",
    writeLetter: "Schreiben erstellen",
    letterTitle: "Schreiben an den Mieter",
    dateRequired: "Bitte das Datum eingeben.",
    senderRequired: "Bitte den Namen des Absenders eingeben.",
    maxChars: (n: number) => `Höchstens ${n} Zeichen.`,
    maxLines: (n: number) => `Höchstens ${n} Zeilen.`,
    maxCharsPerLine: (n: number) => `Höchstens ${n} Zeichen je Zeile.`,
    sentOn: "Datum des Schreibens",
    sentOnDescription:
      "Der Tag, an dem das Schreiben dem Mieter zugeht. Eine Erhöhung gilt ab dem übernächsten Monatsersten danach (§ 560 Abs. 2 BGB).",
    sender: "Absender",
    senderDescription: "Der oder die Vermieter, wie im Mietvertrag. Vorbelegt mit den Eigentümern.",
    senderAddress: "Anschrift des Absenders",
    senderAddressHint: "Bis zu vier Zeilen, z. B. Straße und Hausnummer, Postleitzahl und Ort.",
    senderAddressDescription: "Wird nur auf das Schreiben gedruckt und nicht in Hausbuch gespeichert.",
    rememberAddress: "Anschrift in diesem Browser merken",
    creating: "Erstellt …",
    createPdf: "PDF erstellen",
    downloaded: (file: string) => `„${file}“ heruntergeladen; Hausbuch speichert das Schreiben nicht.`,
    previewHeading: (raise: boolean, unit: string, from: string, to: string, start: string) =>
      `${raise ? "Erhöhung" : "Herabsetzung"} der Nebenkostenpauschale ${unit}: ${from} → ${to} pro Monat, ab dem ${start}.`,
    previewText: (total: string, cold: string, reasons: string, share: string) =>
      `Neue Gesamtmiete ${total} (Kaltmiete ${cold} und Pauschale). Begründung im Schreiben: ${reasons} pro Monat ` +
      `für die Wohnung, davon ${share} % für dieses Zimmer.`,
  },
  en: {
    notAdvice:
      "Whether and from when a flat rate rises or falls is decided by § 560 BGB; before the first increase, a check with a landlords' association (Vermieterverein) is worth it.",
    since: (date: string) => `since the flat rate was set on ${date}`,
    raiseText: (since: string, reasons: string, rise: string, earliest: string, notAdvice: string) =>
      `The flat's recoverable running costs have risen ${since}${reasons ? ` (${reasons})` : ""}. ` +
      `This room's share is ${rise} per month; the earliest start is ` +
      `${earliest}, if the letter reaches the tenant this month. ` +
      `“Write letter” drafts the letter to the tenant, “Rent change” records the new flat rate. ${notAdvice}`,
    lowerText: (since: string, reasons: string, fall: string, fellOn: string, notAdvice: string) =>
      `The flat's recoverable running costs have fallen ${since}${reasons ? ` (${reasons})` : ""}. ` +
      `This room's flat rate falls by ${fall} per month, from ${fellOn}, ` +
      `and the tenant must be told without delay (§ 560 Abs. 3 BGB). ` +
      `“Write letter” drafts the letter, “Rent change” records the new flat rate. ${notAdvice}`,
    todoTitle: (unit: string, raise: boolean) =>
      raise
        ? `Nebenkostenpauschale (flat-rate service charge) for ${unit} can rise`
        : `Lower the Nebenkostenpauschale (flat-rate service charge) for ${unit}`,
    rentChange: "Rent change",
    costsAndSources: "See costs and sources",
    writeLetter: "Write letter",
    letterTitle: "Letter to the tenant",
    dateRequired: "Please enter the date.",
    senderRequired: "Please enter the sender's name.",
    maxChars: (n: number) => `At most ${n} characters.`,
    maxLines: (n: number) => `At most ${n} lines.`,
    maxCharsPerLine: (n: number) => `At most ${n} characters per line.`,
    sentOn: "Date of the letter",
    sentOnDescription:
      "The day the letter reaches the tenant. An increase applies from the first day of the month after next (§ 560 Abs. 2 BGB).",
    sender: "Sender",
    senderDescription: "The landlord or landlords, as in the lease. Pre-filled with the owners.",
    senderAddress: "Sender's address",
    senderAddressHint: "Up to four lines, e.g. street and house number, postcode and town.",
    senderAddressDescription: "Printed on the letter only, not stored in Hausbuch.",
    rememberAddress: "Remember the address in this browser",
    creating: "Creating …",
    createPdf: "Create PDF",
    downloaded: (file: string) => `“${file}” downloaded; Hausbuch does not store the letter.`,
    previewHeading: (raise: boolean, unit: string, from: string, to: string, start: string) =>
      `${raise ? "Increase" : "Reduction"} of the Nebenkostenpauschale (flat-rate service charge) for ${unit}: ${from} → ${to} per month, from ${start}.`,
    previewText: (total: string, cold: string, reasons: string, share: string) =>
      `New total rent ${total} (base rent ${cold} plus the flat rate). Reasons given in the letter: ${reasons} per month ` +
      `for the flat, of which ${share}% for this room.`,
  },
});
