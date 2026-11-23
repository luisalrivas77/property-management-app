// The to-do lists outside a form's own guide (phase 10 D284, step 4c): Übersicht's „Was noch
// fehlt“ and the „Kein Arbeitslohn“ button. German first, English beside it (glossary.md);
// tax words stay German in both (Arbeitslohn is explained, Jahresfragen named as the screen).
import { defineMessages } from "./core";

export const todosText = defineMessages({
  de: {
    seeOnTax: "Auf Steuern ansehen",
    noPayTitle: (year: number) => `Arbeitslohn ${year} fehlt`,
    noPayText:
      "Die letzte Gehaltsabrechnung hochladen: Sie zeigt die Summen des Jahres bis dahin, die App liest sie aus und rechnet damit. " +
      "Wer keine hochladen möchte, trägt unter Steuern › Eure Angaben das erwartete Bruttogehalt ein; beides erledigt diesen Punkt. ",
    uploadPayslip: "Gehaltsabrechnung hochladen",
    typeGrossPay: "Bruttogehalt eintragen",
    household: "Haushalt",
    questionsTitle: (open: number, year: number) =>
      `${open} ${open === 1 ? "Jahresfrage" : "Jahresfragen"} ${year} offen`,
    questionsText:
      "Die Jahresfragen beantworten; sie dauern meist nur ein paar Minuten, und die App rechnet die Antworten ein.",
    answerQuestions: "Jahresfragen beantworten",
    loadFailed: "Die offenen Punkte konnten gerade nicht geladen werden.",
    nothingOpen: "Nichts offen: alle Unterlagen, Angaben und Fragen sind erledigt.",
    // noPay.tsx
    noPayHint:
      "Wer im Jahr kein Gehalt hatte (etwa in Elternzeit), klickt hier; Elterngeld und andere Lohnersatzleistungen zählen trotzdem.",
    saving: "Speichert …",
    noPayButton: (year: number) => `Kein Arbeitslohn in ${year}`,
    withdraw: "Aufheben",
    noPayRecorded: "Kein Arbeitslohn angegeben",
    // todos.tsx: the lists and the checklist rows
    whatToDo: "Was tun:",
    stillMissing: "Was noch fehlt",
    openCount: (n: number) => `${n} offen`,
    why: "Warum?",
    showAll: (n: number) => `Alle ${n} anzeigen`,
    openItem: "Offener Punkt",
    missing: "fehlt",
    taxYearMissing: "Steuerjahr fehlt",
    notDueYet: (missing: string) => `Noch nicht fällig. ${missing}`,
    incompleteTodo: "Das Dokument öffnen und eintragen, für welches Steuerjahr es gilt.",
    questionOpen: "Jahresfrage offen",
    answerQuestion: "Die Frage beantworten; die App rechnet die Antwort ein.",
    answer: "Beantworten",
    nothingOpenDraft: "Für diesen Entwurf ist nichts offen.",
    // ChecklistTab.tsx (Unterlagen)
    statusPresent: "vorhanden",
    statusExpected: "noch nicht fällig",
    document: "Dokument",
    received: (date: string) => `eingegangen ${date}`,
    bankLine: (date: string, amount: string) => `Bankumsatz vom ${date} · ${amount}`,
    enterTaxYear: "Steuerjahr am Dokument eintragen",
    expectedBy: (base: string, date: string) => `${base} · erwartet bis ${date}`,
    bmfStored: "Ergebnis der BMF-Arbeitshilfe gespeichert",
    provenByPayment: "Nachgewiesen durch die Zahlung auf dem Konto",
    checklistTitle: "Unterlagen für die Steuererklärung",
    taxYear: "Steuerjahr",
    noDocumentsNeeded: (year: number) => `Für ${year} sind keine Unterlagen nötig.`,
    planningAid: "Planungshilfe, keine Steuerberatung.",
  },
  en: {
    seeOnTax: "View under Taxes",
    noPayTitle: (year: number) => `Employment income (Arbeitslohn) ${year} missing`,
    noPayText:
      "Upload the latest payslip: it shows the year's totals up to then, and the app reads it and calculates with it. " +
      "If you would rather not upload one, enter the expected gross pay under Taxes › Your details; either one settles this item. ",
    uploadPayslip: "Upload payslip",
    typeGrossPay: "Enter gross pay",
    household: "Household",
    questionsTitle: (open: number, year: number) =>
      `${open} open ${open === 1 ? "question" : "questions"} in the Jahresfragen ${year}`,
    questionsText:
      "Answer the Jahresfragen (the yearly questions); they usually take only a few minutes, and the app includes the answers in its calculation.",
    answerQuestions: "Answer the Jahresfragen",
    loadFailed: "The open items could not be loaded just now.",
    nothingOpen: "Nothing open: all documents, details and questions are done.",
    noPayHint:
      "If you had no salary in the year (e.g. on parental leave), click here; Elterngeld and other wage-replacement benefits still count.",
    saving: "Saving …",
    noPayButton: (year: number) => `No employment income in ${year}`,
    withdraw: "Undo",
    noPayRecorded: "No employment income recorded",
    whatToDo: "What to do:",
    stillMissing: "Still missing",
    openCount: (n: number) => `${n} open`,
    why: "Why?",
    showAll: (n: number) => `Show all ${n}`,
    openItem: "Open item",
    missing: "missing",
    taxYearMissing: "tax year missing",
    notDueYet: (missing: string) => `Not due yet. ${missing}`,
    incompleteTodo: "Open the document and enter which tax year it applies to.",
    questionOpen: "Jahresfrage open",
    answerQuestion: "Answer the question; the app includes the answer in its calculation.",
    answer: "Answer",
    nothingOpenDraft: "Nothing is open for this draft.",
    statusPresent: "present",
    statusExpected: "not due yet",
    document: "Document",
    received: (date: string) => `received ${date}`,
    bankLine: (date: string, amount: string) => `Bank payment of ${date} · ${amount}`,
    enterTaxYear: "Enter the tax year at the document",
    expectedBy: (base: string, date: string) => `${base} · expected by ${date}`,
    bmfStored: "Result of the BMF calculation aid (BMF-Arbeitshilfe) saved",
    provenByPayment: "Proven by the payment on the account",
    checklistTitle: "Documents for the tax return",
    taxYear: "Tax year",
    noDocumentsNeeded: (year: number) => `No documents are needed for ${year}.`,
    planningAid: "A planning aid, not tax advice.",
  },
});

// The direct actions of the to-dos in components/todos.tsx (the dialogs and inline forms beside
// a guide). The English button names are the ones components/todosEn.ts quotes in its guides.
export const todoActions = defineMessages({
  de: {
    saving: "Speichert …",
    changeSaved: "Die Änderung wird mit Datum gespeichert und lässt sich bei der Zahlung wieder ändern.",
    paymentGone: "Diese Zahlung wurde inzwischen anders zugeordnet. Bitte die Zahlung öffnen und dort prüfen.",
    // AttachProofAction
    attachProof: "Vorhandenen Nachweis zuordnen",
    attachProofTitle: "Vorhandenen Zahlungsnachweis zuordnen",
    attachProofText: (amount: string | null) =>
      `Der schon hochgeladene Zahlungsnachweis${amount ? ` über ${amount}` : ""} wird dieser Rechnung zugeordnet und gilt dann als Beleg, dass sie bezahlt wurde. Wurde sie laut Nachweis in einem anderen Jahr bezahlt, öffnet sich danach der Nachweis mit dem Vorschlag, sie in diesem Jahr zu zählen. `,
    viewProof: "Nachweis ansehen",
    yesAssign: "Ja, zuordnen",
    // SplitAction
    openSplit: "Kaufpreisaufteilung öffnen",
    uploadContract: "Kaufvertrag hochladen",
    enterSplit: "Aufteilung selbst eintragen",
    chooseSplit: (n: number) => `Eine der ${n} Aufteilungen wählen`,
    applying: "Wird übernommen …",
    applySplitFromContract: (share: string) => `Gebäudeanteil ${share} aus dem Kaufvertrag übernehmen`,
    applySplitFromSource: (share: string, source: string) => `Gebäudeanteil ${share} aus der Quelle „${source}“ übernehmen`,
    // DepositSplitAction
    tenancy: "Mietverhältnis",
    unit: "Einheit",
    tenancyFrom: (unit: string, date: string) => `${unit}, ab ${date}`,
    depositPayment: (date: string, category: string, amount: string) => `Zahlung vom ${date} (${category}, ${amount})`,
    splitMany: (payment: string, n: number, each: string | null) =>
      `Die ${payment} auf ${n} Mietverhältnisse aufteilen${each ? `: je ${each}` : ""}?`,
    splitPart: (payment: string, part: string, rest: string) =>
      `Von der ${payment} ${part} diesem Mietverhältnis zuordnen? Der Rest von ${rest} bleibt als Kaution ohne Mietverhältnis stehen.`,
    splitOne: (payment: string) => `Die ${payment} diesem Mietverhältnis zuordnen?`,
    assignDeposit: "Kaution aus Bankumsatz zuordnen",
    assignDepositTitle: "Kaution zuordnen",
    depositProofNote:
      "Die Zahlung gilt dann als Nachweis der Kaution. Die Änderung wird mit Datum gespeichert und lässt sich bei der Zahlung wieder ändern.",
    // RebookAsRentAction
    bookAsRent: "Als Miete buchen",
    bookAsRentTitle: "Einnahme als Miete buchen",
    alreadyRent: (date: string, amount: string) =>
      `Die Zahlung vom ${date} über ${amount} zählt in der Steuer schon als Miete und wird jetzt auch als Kaltmiete gebucht. Die Steuerzahlen ändern sich dadurch nicht.`,
    becomesRent: (date: string, amount: string) =>
      `Die Zahlung vom ${date} über ${amount} wird als Kaltmiete gebucht und zählt dann in der Zeile der Miete statt bei den sonstigen Einnahmen.`,
    yesBookAsRent: "Ja, als Miete buchen",
    // PreHandoverAction
    countAsRent: "Als Miete zählen",
    countAsRentTitle: "Einnahmen vor der Übergabe",
    preHandoverText:
      "Diese Einnahmen kamen, bevor die Wohnung Ihnen übergeben wurde. Wenn Sie in diesen Monaten schon als Vermieter im Mietvertrag standen, sind sie Ihre Mieteinnahmen und kommen in die Zeile der Miete. Die Entscheidung wird mit Datum gespeichert und lässt sich später ändern.",
    concernsPayments: "Das betrifft diese Zahlungen:",
    fromIban: (iban: string) => ` · von ${iban}`,
    willBeRent: " · wird als Kaltmiete gebucht",
    bookedAs: (category: string) => ` · gebucht als ${category}`,
    notRentHint: "Ist eine davon keine Miete, zum Beispiel eine Gutschrift der Bank, erst bei der Zahlung richtig zuordnen.",
    notBooked: (n: number) =>
      `Die Entscheidung ist gespeichert. ${n === 1 ? "Eine Zahlung konnte" : `${n} Zahlungen konnten`} nicht als Kaltmiete gebucht werden; sie stehen unter „Was noch fehlt“ mit „Als Miete buchen“.`,
    yesCountAsRent: "Ja, als Miete zählen",
    // LivingAreaAction
    uploadAreaCalculation: "Wohnflächenberechnung hochladen",
    livingArea: "Wohnfläche (m²)",
    saveLivingArea: "Wohnfläche speichern",
    // PriceRenovationAnswer (phase 3 D287)
    priceRenovationYes: "Ja, anerkannt",
    priceRenovationNo: "Nein, abgelehnt",
    priceRenovationTitle: "Renovierung im Kaufpreis",
    priceRenovationYesText: (amount: string | null) =>
      `Das Finanzamt hat die Renovierung${amount ? ` über ${amount}` : ""} als Erhaltungsaufwand anerkannt. Die Angabe bleibt, wie sie ist; die Frage endet.`,
    priceRenovationNoText: (amount: string | null) =>
      `Das Finanzamt hat die Renovierung${amount ? ` über ${amount}` : ""} nicht anerkannt. Der Betrag zählt dann wieder zum Kaufpreis und wird mit dem Gebäude abgeschrieben (AfA); die bisherigen Entwürfe bleiben im Verlauf.`,
    priceRenovationGone: "Die Angabe wurde inzwischen geändert. Bitte die Seite neu laden.",
  },
  en: {
    saving: "Saving …",
    changeSaved: "The change is saved with its date and can be changed again at the payment.",
    paymentGone: "This payment has been assigned differently in the meantime. Please open the payment and check it there.",
    attachProof: "Link existing proof",
    attachProofTitle: "Link an existing proof of payment",
    attachProofText: (amount: string | null) =>
      `The proof of payment already uploaded${amount ? ` for ${amount}` : ""} is linked to this invoice and then counts as the receipt showing it was paid. If the proof shows it was paid in another year, the proof opens next with the suggestion to count it in that year. `,
    viewProof: "View proof",
    yesAssign: "Yes, link it",
    openSplit: "Open the purchase price split",
    uploadContract: "Upload purchase contract (Kaufvertrag)",
    enterSplit: "Enter the split yourself",
    chooseSplit: (n: number) => `Choose one of the ${n} splits`,
    applying: "Applying …",
    applySplitFromContract: (share: string) => `Apply building share ${share} from the purchase contract`,
    applySplitFromSource: (share: string, source: string) => `Apply building share ${share} from the source “${source}”`,
    tenancy: "Tenancy",
    unit: "Unit",
    tenancyFrom: (unit: string, date: string) => `${unit}, from ${date}`,
    depositPayment: (date: string, category: string, amount: string) => `payment of ${date} (${category}, ${amount})`,
    splitMany: (payment: string, n: number, each: string | null) =>
      `Split the ${payment} across ${n} tenancies${each ? `: ${each} each` : ""}?`,
    splitPart: (payment: string, part: string, rest: string) =>
      `Assign ${part} of the ${payment} to this tenancy? The rest of ${rest} stays as a deposit without a tenancy.`,
    splitOne: (payment: string) => `Assign the ${payment} to this tenancy?`,
    assignDeposit: "Assign deposit from bank line",
    assignDepositTitle: "Assign deposit",
    depositProofNote:
      "The payment then counts as proof of the deposit (Kaution). The change is saved with its date and can be changed again at the payment.",
    bookAsRent: "Book as rent",
    bookAsRentTitle: "Book income as rent",
    alreadyRent: (date: string, amount: string) =>
      `The payment of ${date} for ${amount} already counts as rent for tax and is now booked as base rent too. The tax figures do not change.`,
    becomesRent: (date: string, amount: string) =>
      `The payment of ${date} for ${amount} is booked as base rent and then counts on the rent line instead of under other income.`,
    yesBookAsRent: "Yes, book as rent",
    countAsRent: "Count as rent",
    countAsRentTitle: "Income before the handover",
    preHandoverText:
      "This income arrived before the flat was handed over to you. If you were already named as landlord in the lease in those months, it is your rental income and goes on the rent line. The decision is saved with its date and can be changed later.",
    concernsPayments: "This concerns these payments:",
    fromIban: (iban: string) => ` · from ${iban}`,
    willBeRent: " · will be booked as base rent",
    bookedAs: (category: string) => ` · booked as ${category}`,
    notRentHint: "If one of them is not rent, for example a credit from the bank, first assign it correctly at the payment.",
    notBooked: (n: number) =>
      `The decision is saved. ${n === 1 ? "One payment" : `${n} payments`} could not be booked as base rent; ${n === 1 ? "it is" : "they are"} listed under “Still missing” with “Book as rent”.`,
    yesCountAsRent: "Yes, count as rent",
    uploadAreaCalculation: "Upload the living-area calculation (Wohnflächenberechnung)",
    livingArea: "Living area (m²)",
    saveLivingArea: "Save living area",
    priceRenovationYes: "Yes, accepted",
    priceRenovationNo: "No, refused",
    priceRenovationTitle: "Renovation in the purchase price",
    priceRenovationYesText: (amount: string | null) =>
      `The Finanzamt accepted the renovation${amount ? ` of ${amount}` : ""} as maintenance costs (Erhaltungsaufwand). The entry stays as it is; the question ends.`,
    priceRenovationNoText: (amount: string | null) =>
      `The Finanzamt did not accept the renovation${amount ? ` of ${amount}` : ""}. The amount then counts as part of the purchase price again and is depreciated with the building (AfA); the earlier drafts stay in the history.`,
    priceRenovationGone: "The entry has been changed in the meantime. Please reload the page.",
  },
});
