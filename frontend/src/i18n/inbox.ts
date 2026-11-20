// Zu prüfen and the capture page (mockup 18, step 4b); routes /inbox, /capture. German first,
// English beside it (glossary.md). The card texts the server writes (reasoning, question) stay as
// sent; the kind pills and category names come from components/labels.ts and stay German until
// one shared catalogue carries them.
import { defineMessages } from "./core";

const de = {
  title: "Zu prüfen",
  heading: "Hochladen, bestätigen, fertig",
  photo: "Beleg fotografieren",
  upload: "Rechnungen, Bescheide und Briefe hochladen",
  statementsAt: "Kontoauszüge werden beim jeweiligen Konto importiert: ",
  accounts: "Konten",
  existed: "Bereits vorhanden: ",
  uploaded: "Hochgeladen: ",
  reading: " – wird automatisch ausgelesen; der Vorschlag erscheint hier, sobald er fertig ist.",
  aiOff: " Automatisches Auslesen ist auf dem Server nicht eingeschaltet.",
  confirmed: "Zuordnung bestätigt.",
  ruleSaved:
    " Regel gespeichert: künftige Buchungen dieser Art werden automatisch so zugeordnet. Wartende Zahlungen, auf die sie passt, werden jetzt im Hintergrund neu geprüft; die Liste aktualisiert sich in den nächsten zwei Minuten von selbst.",
  rebooked: (n: number, category: string) => `${n === 1 ? "1 frühere Zahlung" : `${n} frühere Zahlungen`} auf ${category} umgebucht.`,
  sameCategory: "dieselbe Kategorie",
  todo: "Zu erledigen",
  summary: (n: number) => (n === 1 ? "1 offener Punkt" : `${n} offene Punkte`),
  empty: "Nichts zu prüfen. Neue Buchungen und gelesene Dokumente erscheinen hier, sobald etwas zu entscheiden ist.",
  selectedPayment: "Ausgewählte Zahlung",

  // The queue's groups (components/inbox.tsx SECTIONS).
  sectionLines: "Zahlungen zuordnen",
  sectionProposals: "KI-Vorschläge zu Dokumenten",
  sectionUnread: "Dokumente, die nicht gelesen wurden",
  sectionImports: "Kontoauszüge",
  sectionDuplicates: "Mögliche Doppelbuchungen",
  sectionAutomatic: "Automatisch",
  sectionTransfers: "Überweisungen zwischen eigenen Konten",
  sectionPropertyCosts: "Immobilie, privat bezahlt",
  sectionExchanges: "Börsen",
  sectionWallets: "Wallets",
  sectionOther: "Weitere Punkte",
  openAll: "Alle aufklappen",
  closeAll: "Alle zuklappen",
  searchPayee: "Empfänger suchen",
  searchPlaceholder: "Name oder Verwendungszweck",
  matchCount: (shown: number, all: number) => `${shown} von ${all}`,
  noMatch: (needle: string) => `Keine Zahlung passt zu „${needle}“.`,
  noCounterparty: "Ohne Gegenseite",
  noPurpose: "Kein Verwendungszweck",
  line: "Buchung",
  privateAccount: (label: string) => `Privatkonto · ${label}`,
};

type Messages = typeof de;

const en: Messages = {
  title: "To review",
  heading: "Upload, confirm, done",
  photo: "Photograph a receipt",
  upload: "Upload invoices, notices and letters",
  statementsAt: "Statements are imported at their account: ",
  accounts: "Accounts",
  existed: "Already there: ",
  uploaded: "Uploaded: ",
  reading: " – being read automatically; the suggestion appears here once it is ready.",
  aiOff: " Automatic reading is not switched on on the server.",
  confirmed: "Allocation confirmed.",
  ruleSaved:
    " Rule saved: future payments of this kind are categorised like this automatically. Waiting payments it fits are being checked again in the background; the list updates itself within the next two minutes.",
  rebooked: (n, category) => `${n === 1 ? "1 earlier payment" : `${n} earlier payments`} re-booked to ${category}.`,
  sameCategory: "the same category",
  todo: "To do",
  summary: (n) => (n === 1 ? "1 open item" : `${n} open items`),
  empty: "Nothing to review. New payments and read documents appear here as soon as something needs deciding.",
  selectedPayment: "Selected payment",

  sectionLines: "Categorise payments",
  sectionProposals: "AI suggestions for documents",
  sectionUnread: "Documents that were not read",
  sectionImports: "Statements",
  sectionDuplicates: "Possible duplicates",
  sectionAutomatic: "Automatic",
  sectionTransfers: "Transfers between own accounts",
  sectionPropertyCosts: "Property, paid privately",
  sectionExchanges: "Exchanges",
  sectionWallets: "Wallets",
  sectionOther: "Other items",
  openAll: "Expand all",
  closeAll: "Collapse all",
  searchPayee: "Search payee",
  searchPlaceholder: "Name or payment reference",
  matchCount: (shown, all) => `${shown} of ${all}`,
  noMatch: (needle) => `No payment matches „${needle}“.`,
  noCounterparty: "No counterparty",
  noPurpose: "No payment reference",
  line: "Transaction",
  privateAccount: (label) => `Private account · ${label}`,
};

export const inbox = defineMessages({ de, en });
