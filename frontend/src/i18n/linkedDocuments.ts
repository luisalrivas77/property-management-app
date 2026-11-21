// The document pieces several screens share (components/documents.tsx): the upload box, the
// document picker, the linked documents of an entity, a depot document's explanation box, and
// „Auf Konto importieren“ / „Konto anlegen und einlesen“ for a stored statement. German first,
// English beside it (glossary.md). The explanation's own sentences come from the server.
import { defineMessages } from "./core";

type StatementWords = { file: string; account: string; number: string };

const STATEMENT_WORDS_DE: Record<string, StatementWords> = {
  bank_current: { file: "ein Kontoauszug oder Umsatz-Export einer Bank", account: "Girokonto", number: "IBAN" },
  bank_savings: { file: "ein Kontoauszug oder Umsatz-Export einer Bank", account: "Sparkonto oder Tagesgeld", number: "IBAN" },
  credit_card: { file: "eine Kreditkartenabrechnung", account: "Kreditkarte", number: "Kundennummer" },
  depot: { file: "ein Kontoauszug des Depot-Verrechnungskontos", account: "Depot", number: "Kontonummer" },
};
const STATEMENT_FALLBACK_DE: StatementWords = { file: "ein Kontoauszug", account: "Konto", number: "Nummer" };

const STATEMENT_WORDS_EN: Record<string, StatementWords> = {
  bank_current: { file: "a statement or transaction export from a bank", account: "Current account", number: "IBAN" },
  bank_savings: { file: "a statement or transaction export from a bank", account: "Savings or instant-access account", number: "IBAN" },
  credit_card: { file: "a credit card statement", account: "Credit card", number: "customer number" },
  depot: { file: "a statement of the securities account's cash account", account: "Securities account (Depot)", number: "account number" },
};
const STATEMENT_FALLBACK_EN: StatementWords = { file: "a statement", account: "Account", number: "number" };

export const linkedDocumentsText = defineMessages({
  de: {
    // a file stored as a document by another upload box
    storedExisted: "Die Datei liegt schon unter Dokumente.",
    storedSaved: "Sie ist unter Dokumente gespeichert.",
    storedReading: "Sie ist unter Dokumente gespeichert, und die KI liest sie gerade und schlägt dort vor, was daraus übernommen wird.",
    openDocument: "Dokument öffnen",
    // documentLabel / documentKind
    documentFallback: (hash: string) => `Dokument ${hash}`,
    untyped: "nicht typisiert",
    // the upload box
    upload: "Datei hochladen",
    uploading: "Lädt hoch …",
    uploadHint: "PDF, Foto, CSV, XML oder E-Mail; Datei hierher ziehen oder auswählen.",
    autoRead: "Automatisch auslesen",
    tenantData: "Enthält Mieterdaten",
    closeUpload: "Hochladen schließen",
    existedPrefix: "Bereits vorhanden: ",
    uploadedPrefix: "Hochgeladen: ",
    readingBefore: " – wird automatisch ausgelesen. Der Vorschlag erscheint unter ",
    toReview: "Zu prüfen",
    readingAfter: "; jeden Wert bestätigen, dann zählt er.",
    aiOff: " Automatisches Auslesen ist auf dem Server nicht eingeschaltet.",
    // the picker
    noDocument: "– kein Dokument –",
    uploadNew: "Neues Dokument hochladen",
    pickedExisting: "Bereits vorhanden, ausgewählt.",
    pickedUploaded: "Hochgeladen und ausgewählt.",
    // the linked documents
    direct: "direkt",
    unlinkConfirm: (label: string) => `Verknüpfung mit „${label}“ entfernen? Das Dokument bleibt im Archiv.`,
    noneLinked: "Noch keine Dokumente verknüpft.",
    colDocument: "Dokument",
    colKind: "Art",
    colReceived: "Eingang",
    colVia: "Verknüpft über",
    unlink: "Verknüpfung entfernen",
    linkLegend: "Dokument verknüpfen",
    document: "Dokument",
    linking: "Verknüpft …",
    link: "Verknüpfen",
    // a depot document's explanation
    explanationAria: "Was ist dieses Dokument?",
    whatIsIt: "Was ist das?",
    forTheAccount: "Was es für das Konto heißt:",
    whatTheAppDoes: "Was die App damit tut:",
    // where a stored statement went
    importedOn: "Importiert auf ",
    importedReconciled: ".",
    importedCheck: " – bitte dort den Kontoauszug prüfen.",
    othersImported: (n: number) =>
      n === 1 ? "Ein weiterer Kontoauszug dieses Kontos wurde eingelesen." : `Weitere ${n} Kontoauszüge dieses Kontos wurden eingelesen.`,
    othersRefused: (n: number) =>
      n === 1
        ? "Ein weiterer Kontoauszug dieses Kontos ließ sich nicht einlesen"
        : `${n} weitere Kontoauszüge dieses Kontos ließen sich nicht einlesen`,
    refusedStay: "; sie bleiben unter ",
    documents: "Dokumente",
    refusedHow: " und lassen sich dort mit „Auf Konto importieren“ einlesen:",
    openStatement: "Kontoauszug öffnen",
    unreadable: "Die Datei ließ sich nicht einlesen.",
    // „Auf Konto importieren“
    statementFile: (kind: string) => (STATEMENT_WORDS_DE[kind] ?? STATEMENT_FALLBACK_DE).file,
    statementAccount: (kind: string) => (STATEMENT_WORDS_DE[kind] ?? STATEMENT_FALLBACK_DE).account,
    statementNumber: (kind: string) => (STATEMENT_WORDS_DE[kind] ?? STATEMENT_FALLBACK_DE).number,
    byHandBefore: "Lieber alles selbst eintragen: ",
    byHandLink: "Konto von Hand anlegen",
    thenImport: " und danach hier „Auf Konto importieren“ wählen.",
    newAccountIntro: (file: string) =>
      `Das ist ${file}. Hausbuch liest die Datei selbst, ohne KI, auf ihrem Konto ein; dieses Konto gibt es in Hausbuch noch nicht. ` +
      "Hausbuch legt es aus der Datei an und liest sie gleich ein.",
    noAccountIntro: (file: string, account: string) =>
      `Das ist ${file}. Hausbuch liest die Datei selbst, ohne KI, auf ihrem Konto ein; dafür gibt es noch kein passendes Konto der Art „${account}“.`,
    createAccount: "Konto anlegen",
    importIntro: (file: string) =>
      `Das ist ${file}. Hausbuch liest die Datei selbst, ohne KI, und importiert sie direkt aus der gespeicherten Datei; ein zweites Hochladen ` +
      "ist nicht nötig.",
    accountColon: "Konto: ",
    account: "Konto",
    chooseAccount: "Konto wählen …",
    importing: "Importiert …",
    importToAccount: "Auf Konto importieren",
    notOnTheseIntro: (number: string) =>
      `Gehört die Datei zu keinem dieser Konten (ihre ${number} steht bei keinem), legt Hausbuch das Konto aus der Datei an und liest sie ` +
      "gleich ein.",
    newInstead: "Stattdessen neues Konto anlegen",
    // „Konto anlegen und einlesen“
    labelRequired: "Bitte eine Bezeichnung eingeben.",
    bankRequired: "Bitte die Bank eingeben.",
    createAndImport: "Konto anlegen und einlesen",
    kindColon: "Kontoart: ",
    fromFile: " (aus der Datei)",
    openingBalance: "Anfangssaldo",
    onDate: (date: string) => ` am ${date}`,
    otherStatements: (n: number, number: string) =>
      n === 1
        ? `Unter Dokumente wartet noch ein weiterer Kontoauszug mit dieser ${number}; er wird gleich mit eingelesen.`
        : `Unter Dokumente warten noch ${n} weitere Kontoauszüge mit dieser ${number}; sie werden gleich mit eingelesen, der Reihe nach.`,
    placeholderOpening:
      "Die Datei nennt keinen Anfangssaldo. 0,00 € ist deshalb nur ein Platzhalter: Nach dem Anlegen auf der Seite des Kontos unter " +
      "„Kontoauszüge“ mit „Endsaldo eingeben“ den Kontostand nach dem letzten Umsatz der Datei eintragen, wie ihn das Online-Banking zeigt. " +
      "„Anfangssaldo daraus übernehmen“ setzt dann den richtigen Anfangssaldo.",
    label: "Bezeichnung",
    labelDescription: "Ein frei wählbarer Name, unter dem Sie das Konto in Hausbuch wiederfinden.",
    bank: "Bank",
    bankDescription: "Die Bank, bei der das Konto geführt wird, wie auf dem Kontoauszug.",
    provider: "Anbieter (optional)",
    providerDescription: "Der Name, unter dem das Konto auftritt, wenn er von der Bank abweicht, z. B. der Markenname einer Karte.",
    purpose: "Wofür",
    purposeDescription:
      "Das Mietkonto ist das Konto, über das die Wohnung läuft: seine Umsätze gehen in die Anlage V. Ein Privatkonto bekommt Haushaltskategorien und zählt nie in die Anlage V.",
    noOwners: "Noch keine Eigentümer erfasst. Wem das Konto gehört, lässt sich später auf seiner Seite festlegen („Eigentümer festlegen“).",
    taken: (number: string) =>
      `Inzwischen gibt es schon ein Konto mit dieser ${number}. Bitte die Seite neu laden und die Datei mit „Auf Konto importieren“ darauf ` +
      "einlesen.",
    creating: "Legt an und liest ein …",
  },
  en: {
    storedExisted: "The file is already under Documents.",
    storedSaved: "It is stored under Documents.",
    storedReading: "It is stored under Documents; the AI is reading it now and will suggest there what to take from it.",
    openDocument: "Open document",
    documentFallback: (hash: string) => `Document ${hash}`,
    untyped: "not typed",
    upload: "Upload file",
    uploading: "Uploading …",
    uploadHint: "PDF, photo, CSV, XML or email; drag the file here or choose it.",
    autoRead: "Read automatically",
    tenantData: "Contains tenant data",
    closeUpload: "Close upload",
    existedPrefix: "Already stored: ",
    uploadedPrefix: "Uploaded: ",
    readingBefore: " – being read automatically. The suggestion appears under ",
    toReview: "To review",
    readingAfter: "; confirm each value, then it counts.",
    aiOff: " Automatic reading is not switched on on the server.",
    noDocument: "– no document –",
    uploadNew: "Upload new document",
    pickedExisting: "Already stored, selected.",
    pickedUploaded: "Uploaded and selected.",
    direct: "directly",
    unlinkConfirm: (label: string) => `Remove the link to “${label}”? The document stays in the archive.`,
    noneLinked: "No documents linked yet.",
    colDocument: "Document",
    colKind: "Type",
    colReceived: "Received",
    colVia: "Linked through",
    unlink: "Remove link",
    linkLegend: "Link a document",
    document: "Document",
    linking: "Linking …",
    link: "Link",
    explanationAria: "What is this document?",
    whatIsIt: "What is it?",
    forTheAccount: "What it means for the account:",
    whatTheAppDoes: "What the app does with it:",
    importedOn: "Imported to ",
    importedReconciled: ".",
    importedCheck: " – please check the statement there.",
    othersImported: (n: number) =>
      n === 1 ? "One more statement of this account was imported." : `${n} more statements of this account were imported.`,
    othersRefused: (n: number) =>
      n === 1 ? "One more statement of this account could not be imported" : `${n} more statements of this account could not be imported`,
    refusedStay: "; they stay under ",
    documents: "Documents",
    refusedHow: " and can be imported there with “Import to account”:",
    openStatement: "Open statement",
    unreadable: "The file could not be read.",
    statementFile: (kind: string) => (STATEMENT_WORDS_EN[kind] ?? STATEMENT_FALLBACK_EN).file,
    statementAccount: (kind: string) => (STATEMENT_WORDS_EN[kind] ?? STATEMENT_FALLBACK_EN).account,
    statementNumber: (kind: string) => (STATEMENT_WORDS_EN[kind] ?? STATEMENT_FALLBACK_EN).number,
    byHandBefore: "Rather enter everything yourself: ",
    byHandLink: "Create the account by hand",
    thenImport: " and then choose “Import to account” here.",
    newAccountIntro: (file: string) =>
      `This is ${file}. Hausbuch reads the file itself, without AI, onto its account; this account does not exist in Hausbuch yet. ` +
      "Hausbuch creates it from the file and imports the file straight away.",
    noAccountIntro: (file: string, account: string) =>
      `This is ${file}. Hausbuch reads the file itself, without AI, onto its account; there is no matching account of the kind “${account}” yet.`,
    createAccount: "Create account",
    importIntro: (file: string) =>
      `This is ${file}. Hausbuch reads the file itself, without AI, and imports it straight from the stored file; there is no need to upload it again.`,
    accountColon: "Account: ",
    account: "Account",
    chooseAccount: "Choose account …",
    importing: "Importing …",
    importToAccount: "Import to account",
    notOnTheseIntro: (number: string) =>
      `If the file belongs to none of these accounts (none of them has its ${number}), Hausbuch creates the account from the file and imports the file straight away.`,
    newInstead: "Create a new account instead",
    labelRequired: "Please enter a name.",
    bankRequired: "Please enter the bank.",
    createAndImport: "Create account and import",
    kindColon: "Account kind: ",
    fromFile: " (from the file)",
    openingBalance: "Opening balance",
    onDate: (date: string) => ` on ${date}`,
    otherStatements: (n: number, number: string) =>
      n === 1
        ? `One more statement with this ${number} is waiting under Documents; it is imported along with this one.`
        : `${n} more statements with this ${number} are waiting under Documents; they are imported along with this one, in order.`,
    placeholderOpening:
      "The file names no opening balance, so €0.00 is only a placeholder: after creating the account, on its page under " +
      "“Statements” use “Enter closing balance” to enter the balance after the file's last transaction, as online banking shows it. " +
      "“Use it as the opening balance” then sets the right opening balance.",
    label: "Name",
    labelDescription: "A name of your choice under which you find the account in Hausbuch.",
    bank: "Bank",
    bankDescription: "The bank that holds the account, as on the statement.",
    provider: "Provider (optional)",
    providerDescription: "The name the account appears under if it differs from the bank, e.g. a card's brand name.",
    purpose: "Used for",
    purposeDescription:
      "The rental account is the account the flat runs through: its transactions go into the Anlage V (rental income form). A private account gets household categories and never counts in the Anlage V.",
    noOwners: "No owners entered yet. Whose account it is can be set later on its page (“Set owners”).",
    taken: (number: string) =>
      `An account with this ${number} exists by now. Please reload the page and import the file onto it with “Import to account”.`,
    creating: "Creating and importing …",
  },
});
