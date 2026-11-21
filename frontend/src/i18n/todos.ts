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
