// The categorisation panel for one bank line (components/allocations.tsx: AllocationPanel, its rows,
// the learned-rule box and the earlier payments a rule would re-book).

import { defineMessages } from "./core";

export const allocationsText = defineMessages({
  de: {
    categoryMissing: "Bitte eine Kategorie wählen.",
    patternEmpty: "Bitte einen Anfang eintragen oder „Künftig immer so zuordnen“ abwählen.",
    patternTooLong: "Der Anfang ist zu lang; bitte kürzen.",
    badgeAi: "KI",
    badgeAuto: "Auto",
    suggestion: (method: string) => `Vorschlag: ${method}`,
    noCounterparty: "Ohne Gegenseite",
    bookingDay: (date: string) => `Buchungstag ${date}`,
    valueDay: (date: string) => ` · Wert ${date}`,
    payee: (label: string) => `Empfänger: ${label}`,
    noPurpose: "Kein Verwendungszweck",
    confirmed: (at: string | null, method: string) =>
      `Bestätigt${at ? ` am ${at}` : ""} (${method}). Eine Änderung ersetzt diese Zuordnung; die bisherige bleibt im Verlauf.`,
    addRow: "Zeile hinzufügen",
    rest: (amount: string) => `Rest: ${amount}`,
    amountsInvalid: "Bitte die Beträge prüfen: jede Zeile braucht einen Betrag, z. B. -180,00.",
    notBalanced: (amount: string) => `Die Zeilen müssen zusammen genau den Betrag der Buchung ergeben (${amount}).`,
    learnRule: "Künftig immer so zuordnen",
    learnHintMerchant: (merchant: string) =>
      `Weitere Zahlungen an ${merchant} werden dann ohne Rückfrage so zugeordnet, andere Händler über denselben Zahlungsdienst nicht.`,
    learnHint: "Weitere Buchungen dieser Gegenseite werden dann ohne Rückfrage so zugeordnet.",
    thisPayee: "dieser Gegenseite",
    earlier: (count: number, payee: string, category: string) =>
      count === 1
        ? `1 frühere Zahlung von ${payee} ist als ${category} bestätigt.`
        : `${count} frühere Zahlungen von ${payee} sind als ${category} bestätigt.`,
    rebook: (category: string) => `Diese auch auf ${category} umbuchen`,
    rebookHint:
      "Ohne Umbuchen lernt Hausbuch keine Regel, denn sie würde diesen früheren Zahlungen widersprechen. Jede umgebuchte Zahlung lässt sich danach einzeln ändern; die bisherige Zuordnung bleibt im Verlauf.",
    thisBankAccount: "dieser Bankverbindung",
    sameAmountQuestion: (payee: string, count: number, more: boolean, amounts: string, category: string) =>
      `Von ${payee} warten noch ${count === 1 ? "1 Zahlung mit einem anderen Betrag" : `${count} Zahlungen mit anderen Beträgen`}${
        more ? ", am häufigsten " : ": "
      }${amounts}. Gehören die auch zu ${category}?`,
    yesAll: (waiting: string) => `Ja, alle so zuordnen: die Regel gilt dann auch für ${waiting}`,
    noOnly: (amount: string) => `Nein, nur Zahlungen über ${amount}`,
    alsoFor: (waiting: string) => `: die Regel gilt dann auch für ${waiting}`,
    recognisedLabel: "Erkannt am Anfang des Verwendungszwecks",
    recognisedDescription:
      "Diese Buchung hat keine Kontonummer der Gegenseite. Hausbuch erkennt diese Art Buchung am Anfang ihres Verwendungszwecks; der Text lässt sich ändern.",
    patternLabel: "Muster im Verwendungszweck (optional)",
    patternHint:
      "Für diese Gegenseite lernt Hausbuch keine Regel allein aus der IBAN. Mit einem Muster (z. B. Abschluss) werden künftige Buchungen mit passendem Verwendungszweck so zugeordnet.",
    patternDescription:
      "Ein Text aus dem Verwendungszweck, an dem sich diese Art Buchung erkennen lässt, z. B. „Abschluss“ bei der Kontoabrechnung der Bank.",
    learnIban: (exchange: string) => `IBAN als Einzahlungskonto von ${exchange} merken`,
    confirming: "Bestätigt …",
    restNotZero: "Erst wenn der Rest 0,00 € ist.",
    answerFirst: "Erst die Frage zu den weiteren Zahlungen beantworten.",
    waitingCount: (count: number) => (count === 1 ? "1 wartende Zahlung" : `${count} wartende Zahlungen`),
    noStartNoRule: "Ohne einen Anfang lernt Hausbuch keine Regel.",
    suggestionHint: (start: string, waiting: string | null) =>
      `Die Regel gilt für Buchungen, deren Verwendungszweck mit „${start}“ beginnt${waiting ? ` – auch für ${waiting}` : ""}.`,
    waitingSame: (waiting: string, amount: string) => `Die Regel gilt dann auch für ${waiting} über ${amount}.`,
    row: (number: number) => `Zeile ${number}`,
    amount: "Betrag (€)",
    amountDescription:
      "Der Teil der Buchung, der in diese Kategorie fällt; Ausgaben mit Minus. Alle Zeilen zusammen ergeben genau den Betrag der Buchung.",
    category: "Kategorie",
    categoryRental:
      "Wofür das Geld war, z. B. Kaltmiete, Darlehenszinsen oder Hausgeld. Nach ihr ordnet der Anlage-V-Entwurf den Betrag ein.",
    categoryPrivate:
      "Wofür das Geld war, z. B. Lebensmittel, Gehalt oder Versicherung. Ein Privatkonto zählt nie in die Anlage V; „Kosten der Immobilie, privat bezahlt“ merkt eine Rechnung der Wohnung vor, die von hier bezahlt wurde.",
    choose: "– wählen –",
    noneFeminine: "– keine –",
    noneNeuter: "– keines –",
    property: "Immobilie",
    propertyDescription: "Zu welcher Immobilie der Betrag gehört.",
    unit: "Einheit",
    unitDescription: "Zu welcher Einheit der Betrag gehört, z. B. die Wohnung, deren Miete das ist.",
    tenancy: "Mietverhältnis",
    tenancyDescription: "Bei Miete, Nebenkosten oder Kaution: das Mietverhältnis, zu dem die Zahlung gehört.",
    tenancyOption: (unit: string, parties: string, start: string) => `${unit}: ${parties} (ab ${start})`,
    loan: "Darlehen",
    loanDescription: "Bei Zinsen, Tilgung oder Geldbeschaffungskosten: das Darlehen, zu dem die Zahlung gehört.",
    periodFrom: "Leistungszeitraum von",
    periodFromDescription:
      "Der Zeitraum, für den gezahlt wurde, z. B. 01.01. bis 31.01. für die Januarmiete. Er wird gespeichert, damit Zahlungen um den Jahreswechsel später dem richtigen Steuerjahr zugeordnet werden können.",
    periodTo: "bis",
    periodToDescription: "Das Ende des Leistungszeitraums.",
    recurring: "Wiederkehrend (z. B. monatliche Miete oder Rate)",
    receipt: "Beleg",
    receiptDescription: "Die Rechnung oder das Schreiben zu dieser Zahlung, falls es eines gibt.",
    openReceipt: "Beleg öffnen",
    removeRow: "Zeile entfernen",
    blocked: (count: number, payee: string) =>
      `${
        count === 1
          ? `Eine frühere Zahlung von ${payee} lässt sich nicht automatisch umbuchen. Sie muss zuerst von Hand geändert werden; danach lassen sich die übrigen hier umbuchen und die Regel lernen.`
          : `${count} frühere Zahlungen von ${payee} lassen sich nicht automatisch umbuchen. Sie müssen zuerst von Hand geändert werden; danach lassen sich die übrigen hier umbuchen und die Regel lernen.`
      } Oder diese Zahlung ohne „Künftig immer so zuordnen“ bestätigen.`,
    blocker: (date: string, amount: string, why: string) => `Zahlung vom ${date} über ${amount}: ${why}`,
    openPayment: "Zahlung öffnen",
    more: (count: number) => (count === 1 ? "Und 1 weitere." : `Und ${count} weitere.`),
  },
  en: {
    categoryMissing: "Please choose a category.",
    patternEmpty: "Please enter a start or untick “Always categorise like this”.",
    patternTooLong: "The start is too long; please shorten it.",
    badgeAi: "AI",
    badgeAuto: "Auto",
    suggestion: (method: string) => `Suggestion: ${method}`,
    noCounterparty: "No counterparty",
    bookingDay: (date: string) => `Booked ${date}`,
    valueDay: (date: string) => ` · value date ${date}`,
    payee: (label: string) => `Payee: ${label}`,
    noPurpose: "No payment reference",
    confirmed: (at: string | null, method: string) =>
      `Confirmed${at ? ` on ${at}` : ""} (${method}). A change replaces this categorisation; the previous one stays in the history.`,
    addRow: "Add row",
    rest: (amount: string) => `Remainder: ${amount}`,
    amountsInvalid: "Please check the amounts: every row needs an amount, e.g. -180,00.",
    notBalanced: (amount: string) => `Together the rows must come to exactly the transaction's amount (${amount}).`,
    learnRule: "Always categorise like this",
    learnHintMerchant: (merchant: string) =>
      `Further payments to ${merchant} are then categorised like this without asking, other merchants paid through the same payment service are not.`,
    learnHint: "Further transactions from this counterparty are then categorised like this without asking.",
    thisPayee: "this counterparty",
    earlier: (count: number, payee: string, category: string) =>
      count === 1
        ? `1 earlier payment from ${payee} is confirmed as ${category}.`
        : `${count} earlier payments from ${payee} are confirmed as ${category}.`,
    rebook: (category: string) => `Re-book these to ${category} as well`,
    rebookHint:
      "Without re-booking, Hausbuch learns no rule, because it would contradict these earlier payments. Each re-booked payment can be changed on its own afterwards; the previous categorisation stays in the history.",
    thisBankAccount: "this bank account",
    sameAmountQuestion: (payee: string, count: number, more: boolean, amounts: string, category: string) =>
      `From ${payee}, ${count === 1 ? "1 payment with a different amount is" : `${count} payments with other amounts are`} still waiting${
        more ? ", most often " : ": "
      }${amounts}. Do they also belong to ${category}?`,
    yesAll: (waiting: string) => `Yes, categorise all like this: the rule then also covers ${waiting}`,
    noOnly: (amount: string) => `No, only payments of ${amount}`,
    alsoFor: (waiting: string) => `: the rule then also covers ${waiting}`,
    recognisedLabel: "Recognised by the start of the payment reference",
    recognisedDescription:
      "This transaction has no account number for the counterparty. Hausbuch recognises this kind of transaction by the start of its payment reference; the text can be changed.",
    patternLabel: "Pattern in the payment reference (optional)",
    patternHint:
      "For this counterparty Hausbuch learns no rule from the IBAN alone. With a pattern (e.g. Abschluss), future transactions with a matching payment reference are categorised like this.",
    patternDescription:
      "A text from the payment reference that identifies this kind of transaction, e.g. “Abschluss” for the bank's account statement charges.",
    learnIban: (exchange: string) => `Remember the IBAN as ${exchange}'s deposit account`,
    confirming: "Confirming …",
    restNotZero: "Only once the remainder is €0.00.",
    answerFirst: "First answer the question about the other payments.",
    waitingCount: (count: number) => (count === 1 ? "1 waiting payment" : `${count} waiting payments`),
    noStartNoRule: "Without a start, Hausbuch learns no rule.",
    suggestionHint: (start: string, waiting: string | null) =>
      `The rule covers transactions whose payment reference starts with “${start}”${waiting ? ` – including ${waiting}` : ""}.`,
    waitingSame: (waiting: string, amount: string) => `The rule then also covers ${waiting} of ${amount}.`,
    row: (number: number) => `Row ${number}`,
    amount: "Amount (€)",
    amountDescription:
      "The part of the transaction that falls into this category; spending with a minus. All rows together come to exactly the transaction's amount.",
    category: "Category",
    categoryRental:
      "What the money was for, e.g. base rent, loan interest or Hausgeld (monthly payment to the owners' association). The Anlage V (rental income form) draft places the amount by it.",
    categoryPrivate:
      "What the money was for, e.g. groceries, salary or insurance. A private account never counts towards the Anlage V (rental income form); “Property cost, paid privately” notes a bill for the flat that was paid from here.",
    choose: "– choose –",
    noneFeminine: "– none –",
    noneNeuter: "– none –",
    property: "Property",
    propertyDescription: "Which property the amount belongs to.",
    unit: "Unit",
    unitDescription: "Which unit the amount belongs to, e.g. the flat whose rent this is.",
    tenancy: "Tenancy",
    tenancyDescription: "For rent, service charges or a Kaution (rent deposit): the tenancy the payment belongs to.",
    tenancyOption: (unit: string, parties: string, start: string) => `${unit}: ${parties} (from ${start})`,
    loan: "Loan",
    loanDescription: "For interest, repayment or financing costs: the loan the payment belongs to.",
    periodFrom: "Service period from",
    periodFromDescription:
      "The period that was paid for, e.g. 1 Jan to 31 Jan for January's rent. It is stored so that payments around the turn of the year can later be assigned to the right tax year.",
    periodTo: "to",
    periodToDescription: "The end of the service period.",
    recurring: "Recurring (e.g. monthly rent or instalment)",
    receipt: "Receipt",
    receiptDescription: "The invoice or letter for this payment, if there is one.",
    openReceipt: "Open receipt",
    removeRow: "Remove row",
    blocked: (count: number, payee: string) =>
      `${
        count === 1
          ? `An earlier payment from ${payee} cannot be re-booked automatically. It has to be changed by hand first; then the others can be re-booked here and the rule learned.`
          : `${count} earlier payments from ${payee} cannot be re-booked automatically. They have to be changed by hand first; then the others can be re-booked here and the rule learned.`
      } Or confirm this payment without “Always categorise like this”.`,
    blocker: (date: string, amount: string, why: string) => `Payment of ${date} for ${amount}: ${why}`,
    openPayment: "Open payment",
    more: (count: number) => (count === 1 ? "And 1 more." : `And ${count} more.`),
  },
});
