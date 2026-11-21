// One document's page, app/(app)/documents/[id]/page.tsx: the original, the typing form, the
// links, the retention and „Dokument löschen“. The suggestion card above it is
// components/proposals.tsx with its own texts.
import { defineMessages } from "./core";

export const documentPage = defineMessages({
  de: {
    documents: "Dokumente",
    typing: "Typisierung",
    reviewProposalFirst: "Prüfen Sie den Vorschlag oben; nach dem Übernehmen oder Verwerfen können Sie hier Werte ändern.",
    typingSaved: "Typisierung gespeichert.",
    loading: "Lädt …",
    links: "Verknüpfungen",
    retention: "Aufbewahrung",
    delete: "Löschen",
    // Original
    file: "Datei",
    fileType: "Dateityp",
    received: "Eingang",
    checksum: "Prüfsumme (SHA-256)",
    openOriginal: "Original öffnen",
    downloadOriginal: "Original herunterladen",
    downloadOnly: "Dieser Dateityp wird nicht im Browser angezeigt, nur heruntergeladen.",
    originalUnchanged: "Das Original bleibt unverändert; Typ, Felder und Verknüpfungen liegen als eigene Ebenen darüber.",
    // TypingForm
    chooseType: "Bitte einen Typ wählen.",
    editTyping: "Typisierung bearbeiten",
    type: "Typ",
    typeDescription:
      "Welche Art Dokument das ist, z. B. Rechnung, Kontoauszug oder Bescheid. Danach richtet sich, welche Werte gelesen werden und wofür das Dokument in der Unterlagen-Checkliste zählt.",
    choose: "– wählen –",
    subtype: "Untertyp (optional)",
    subtypeHint: "Bestimmt die Aufbewahrung, z. B. Kaufvertrag.",
    subtypeDescription:
      "Genauer als der Typ, z. B. Kaufvertrag, Mietvertrag oder Grundsteuerbescheid. Danach richten sich die Aufbewahrungsfrist und die Unterlagen-Checkliste.",
    noSubtype: "– keiner –",
    title: "Titel (optional)",
    titleDescription: "Ein kurzer Name, unter dem Sie das Dokument wiederfinden.",
    documentDate: "Datum des Dokuments (optional)",
    documentDateDescription:
      "Das Datum, das auf dem Dokument steht, z. B. das Rechnungs- oder Bescheiddatum. Ohne Steuerjahr zählt die Aufbewahrungsfrist ab dem Jahr dieses Datums.",
    taxYear: "Steuerjahr (optional)",
    taxYearDescription:
      "Das Jahr, für das das Dokument gilt, z. B. 2026 bei der Zinsbescheinigung 2026. Die Unterlagen-Checkliste ordnet Jahresunterlagen danach zu, und die Aufbewahrungsfrist zählt ab diesem Jahr.",
    handFields: "Felder (von Hand)",
    aiReadingValues: (handChanged: boolean, version: number, date: string) =>
      `Die Werte stammen aus der KI-Auslesung${handChanged ? ", von Hand geändert" : ""} (Version ${version} vom ${date}). Eine Änderung legt eine neue Version an; die übrigen gelesenen Felder bleiben erhalten.`,
    aiReadingNoHandFields: (version: number) =>
      `Für diese Dokumentart gibt es keine Felder von Hand; die KI-Auslesung (Version ${version}) bleibt unverändert.`,
    manualVersion: (version: number, date: string) =>
      `Version ${version} vom ${date}. Eine Änderung legt eine neue Version an.`,
    monthlyFee: "Vergütung pro Monat (€)",
    totalAmount: "Betrag gesamt (€)",
    monthlyFeeDescription:
      "Die Verwaltervergütung je Monat mit MwSt. Hausbuch rechnet sie aus dem Nettobetrag laut Vertrag; steht dort „zzgl. MwSt.“ ohne Satz, mit 19 %. Gebucht wird sie aus den Zahlungen im Kontoauszug, nicht aus dem Vertrag.",
    totalAmountDescription:
      "Der Gesamtbetrag laut Dokument, bei einer Rechnung brutto. Bei Rechnungen und Bescheiden sucht Hausbuch damit die passende Zahlung im Kontoauszug.",
    counterparty: "Absender / Gegenpartei",
    counterpartyDescription:
      "Wer das Dokument ausgestellt hat oder an wen gezahlt wird, z. B. Handwerker, Stadtkasse oder Hausverwaltung.",
    periodStart: "Zeitraum von",
    periodStartDescription:
      "Der Beginn des Zeitraums, für den das Dokument gilt, z. B. des Abrechnungsjahrs einer Hausgeldabrechnung.",
    periodEnd: "Zeitraum bis",
    periodEndDescription: "Das Ende dieses Zeitraums.",
    reference: "Referenz (Rechnungs-/Aktenzeichen)",
    referenceDescription:
      "Die Rechnungsnummer oder das Aktenzeichen laut Dokument. Steht sie im Verwendungszweck einer Überweisung, hilft das, die Zahlung diesem Beleg zuzuordnen.",
    notes: "Notizen",
    notesDescription: "Freier Text für Sie.",
    saveTyping: "Typisierung speichern",
    // Links
    confirmUnlink: "Verknüpfung entfernen? Das Dokument bleibt im Archiv.",
    notLinkedYet: "Noch nicht verknüpft. Ein Dokument belegt etwas: eine Immobilie, ein Mietverhältnis, ein Darlehen …",
    openDocument: "Dokument öffnen",
    since: (date: string) => `(seit ${date})`,
    unlink: "entfernen",
    linkPointer:
      "Die vorgeschlagene Verknüpfung prüfen Sie im Vorschlag oben; danach können Sie hier verknüpfen oder Verknüpfungen entfernen.",
    addLink: "Verknüpfung hinzufügen",
    linkWith: "Verknüpfen mit",
    linkWithDescription:
      "Worauf sich das Dokument bezieht, z. B. die Immobilie, ein Darlehen oder ein Mietverhältnis. Die Unterlagen-Checkliste zählt ein Dokument erst, wenn es mit dem passenden Eintrag verknüpft ist.",
    linkTargetDescription: "Der Eintrag, mit dem das Dokument verknüpft wird.",
    nothingToChoose: "– nichts zur Auswahl –",
    linking: "Verknüpft …",
    link: "Verknüpfen",
    // Retention
    retentionClass: "Klasse",
    afterTyping: "– (erst nach der Typisierung)",
    retainFor: "Aufbewahren",
    until: (date: string) => `bis ${date}`,
    // DeleteDocument
    confirmDelete: "Dokument endgültig löschen? Die Datei wird entfernt.",
    cannotDelete: (reason: string) => `Kann nicht gelöscht werden: ${reason}`,
    cannotDeleteList: "Kann nicht gelöscht werden:",
    deletable: "Nichts hängt an diesem Dokument. Wurde es versehentlich hochgeladen, kann es gelöscht werden.",
    deleting: "Löscht …",
    deleteDocument: "Dokument löschen",
  },
  en: {
    documents: "Documents",
    typing: "Typing",
    reviewProposalFirst: "Review the suggestion above; once you apply or discard it, you can change values here.",
    typingSaved: "Typing saved.",
    loading: "Loading …",
    links: "Links",
    retention: "Retention",
    delete: "Delete",
    // Original
    file: "File",
    fileType: "File type",
    received: "Received",
    checksum: "Checksum (SHA-256)",
    openOriginal: "Open original",
    downloadOriginal: "Download original",
    downloadOnly: "This file type is not shown in the browser, only downloaded.",
    originalUnchanged: "The original stays unchanged; type, fields and links are separate layers on top of it.",
    // TypingForm
    chooseType: "Please choose a type.",
    editTyping: "Edit typing",
    type: "Type",
    typeDescription:
      "What kind of document this is, e.g. an invoice, a statement or a Bescheid (official notice). It decides which values are read and what the document counts for in the paperwork checklist.",
    choose: "– choose –",
    subtype: "Subtype (optional)",
    subtypeHint: "Sets the retention, e.g. purchase contract.",
    subtypeDescription:
      "More precise than the type, e.g. purchase contract, lease or Grundsteuer (property tax) notice. It sets the retention period and the paperwork checklist.",
    noSubtype: "– none –",
    title: "Title (optional)",
    titleDescription: "A short name to find the document again by.",
    documentDate: "Date of the document (optional)",
    documentDateDescription:
      "The date printed on the document, e.g. the invoice or notice date. Without a tax year, the retention period counts from this date's year.",
    taxYear: "Tax year (optional)",
    taxYearDescription:
      "The year the document is for, e.g. 2026 for the 2026 interest certificate. The paperwork checklist files yearly documents by it, and the retention period counts from this year.",
    handFields: "Fields (by hand)",
    aiReadingValues: (handChanged: boolean, version: number, date: string) =>
      `The values come from the AI reading${handChanged ? ", changed by hand" : ""} (version ${version} of ${date}). A change creates a new version; the other fields read stay as they are.`,
    aiReadingNoHandFields: (version: number) =>
      `This kind of document has no fields to fill by hand; the AI reading (version ${version}) stays unchanged.`,
    manualVersion: (version: number, date: string) => `Version ${version} of ${date}. A change creates a new version.`,
    monthlyFee: "Fee per month (€)",
    totalAmount: "Total amount (€)",
    monthlyFeeDescription:
      "The property manager's monthly fee including VAT. Hausbuch works it out from the net amount in the contract; if it says “plus VAT” without a rate, at 19%. It is booked from the payments on the statement, not from the contract.",
    totalAmountDescription:
      "The total according to the document, gross for an invoice. For invoices and notices, Hausbuch uses it to find the matching payment on the statement.",
    counterparty: "Sender / counterparty",
    counterpartyDescription:
      "Who issued the document or who is paid, e.g. a tradesperson, the town treasury or the property management.",
    periodStart: "Period from",
    periodStartDescription:
      "The start of the period the document is for, e.g. the accounting year of a Hausgeldabrechnung (owners' association annual statement).",
    periodEnd: "Period to",
    periodEndDescription: "The end of this period.",
    reference: "Reference (invoice or file number)",
    referenceDescription:
      "The invoice number or file reference on the document. If it appears in a transfer's payment reference, it helps to match the payment to this receipt.",
    notes: "Notes",
    notesDescription: "Free text for you.",
    saveTyping: "Save typing",
    // Links
    confirmUnlink: "Remove link? The document stays in the archive.",
    notLinkedYet: "Not linked yet. A document proves something: a property, a tenancy, a loan …",
    openDocument: "Open document",
    since: (date: string) => `(since ${date})`,
    unlink: "remove",
    linkPointer: "Check the proposed link in the suggestion above; afterwards you can add or remove links here.",
    addLink: "Add link",
    linkWith: "Link with",
    linkWithDescription:
      "What the document relates to, e.g. the property, a loan or a tenancy. The paperwork checklist counts a document only once it is linked to the matching entry.",
    linkTargetDescription: "The entry the document is linked to.",
    nothingToChoose: "– nothing to choose –",
    linking: "Linking …",
    link: "Link",
    // Retention
    retentionClass: "Class",
    afterTyping: "– (only once it is typed)",
    retainFor: "Keep",
    until: (date: string) => `until ${date}`,
    // DeleteDocument
    confirmDelete: "Delete the document for good? The file will be removed.",
    cannotDelete: (reason: string) => `Cannot be deleted: ${reason}`,
    cannotDeleteList: "Cannot be deleted:",
    deletable: "Nothing depends on this document. If it was uploaded by mistake, it can be deleted.",
    deleting: "Deleting …",
    deleteDocument: "Delete document",
  },
});
