// Haushalt (mockup 19, step 4b); routes /household. German first, English beside it
// (glossary.md). Category and group names come from components/labels.ts and stay German until
// one shared category catalogue carries them (they are shared with Zu prüfen and Konten).
import { defineMessages } from "./core";

const de = {
  title: "Haushalt",
  subtitle: "Wohin das Geld geht",
  month: "Monat",
  running: " (läuft noch)",
  changed: "seit dem Abschluss geändert",
  changedTitle: "Seit dem Abschluss geändert",
  changedText: (month: string) =>
    `Seit dem Abschluss von ${month} hat sich eine Buchung des Monats geändert. Die festgehaltenen Kennzahlen bleiben, wie sie waren; diese Seite zeigt den Monat, wie er heute gebucht ist.`,
  figure: "Zahl",
  atClosing: "Beim Abschluss",
  today: "Heute",
  incomeWithFlat: "Einnahmen mit der Wohnung",
  saved: "Gespart",

  income: "Einnahmen",
  spending: "Ausgaben",
  savingsRate: "Sparquote",
  savingsRateHow: "Gespart ÷ Einnahmen, mit dem Ergebnis der Wohnung",
  uncategorised: "Ohne Kategorie",
  openLines: (n: number) => (n === 1 ? "1 Umsatz" : `${n} Umsätze`),
  answerInInbox: "In Zu prüfen beantworten ›",
  allAssigned: "Alles zugeordnet",
  mean: (n: number) => `Ø ${n} ${n === 1 ? "Monat" : "Monate"}`,
  meanShort: (n: number) => `Ø ${n} Mon.`,
  basis: (n: number, range: string) => `Basis der Durchschnitte: ${n} abgeschlossene Monate, ${range}.`,
  noMeansTitle: "Noch keine Durchschnitte",
  noMeansText: (n: number) =>
    `Die Durchschnitte erscheinen ab 3 abgeschlossenen Monaten in Folge; bisher ${n === 1 ? "ist es 1" : `sind es ${n}`}.`,

  surplusTitle: "Überschuss je Monat",
  surplusHow: "Einnahmen minus Ausgaben",
  investedOf: (amount: string) => `Davon angelegt: ${amount}`,
  shortMonths: ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"].join(","),

  byCategory: "Ausgaben nach Kategorie",
  plannedOnly: (saved: string) => `„Geplant“ steht nur bei den Kategorien, für die ihr in eurem Szenario etwas geplant habt${saved}`,
  savedOn: (day: string) => `: gespeichert am ${day}`,
  under: " unter ",
  planningBudget: "Planung › Budget",
  noSpending: "In diesem Monat ist keine Ausgabe bestätigt.",
  category: "Kategorie",
  share: "Anteil",
  soFar: " bisher",
  planned: "Geplant",
  difference: "Unterschied",
  sum: "Summe",
  sumSoFar: "Summe bisher",
  plannedCount: (n: number) => `${n} geplant`,
  openGroup: (group: string) => `${group} aufklappen`,
  showAllGroups: (n: number) => `Alle ${n} anzeigen`,
  notFinal:
    "Der Monat ist noch nicht abgeschlossen: „bisher“ zählt die bestätigten Umsätze bis heute. Der Vergleich mit „Geplant“ ist erst nach dem Monatsabschluss endgültig.",
  waiting: (lines: string, n: number) => `${lines} ${n === 1 ? "wartet" : "warten"} in Zu prüfen`,

  selectedCategory: "Ausgewählte Kategorie",
  monthByCategory: (month: string) => `${month} · Ausgaben nach Kategorie`,
  shareOfSpending: "Anteil an den Ausgaben",
  meanOver: (n: number, range: string) => `Ø ${n} ${n === 1 ? "Monat" : "Monate"} (${range})`,
  subcategories: "Unterkategorien",
  plannedLine: (amount: string, text: string) => `Geplant ${amount} · ${text}`,
  planFooter: (day: string) => `Geplant laut eurem Szenario vom ${day}. Ändern unter `,
  planFooterNone: "Planen könnt ihr unter ",

  noIncome: "In diesem Monat ist keine Einnahme bestätigt.",
  flatResult: "Wohnung (Ergebnis des Mietkontos)",
  flatHint: "Das Ergebnis der Wohnung vor Tilgung zählt zu den Einnahmen des Haushalts; die Sparquote rechnet damit.",

  savedTitle: "Gespart, nicht ausgegeben",
  savedText: "Überweisungen an eigene Konten zählen nicht als Ausgaben.",
  savedAmount: (amount: string) => ` Auf Spar-, Depot- und Börsenkonten und in Geldanlagen gingen ${amount}.`,
  stayed: (surplus: string, stayed: string) => `Vom Überschuss (${surplus}) sind ${stayed} auf den Girokonten geblieben.`,
  overInvested: (amount: string) => `Es wurde ${amount} mehr angelegt, als in diesem Monat übrig blieb.`,
};

type Messages = typeof de;

const en: Messages = {
  title: "Household",
  subtitle: "Where the money goes",
  month: "Month",
  running: " (still running)",
  changed: "changed since closing",
  changedTitle: "Changed since closing",
  changedText: (month) =>
    `Since ${month} was closed, one of the month's bookings has changed. The recorded key figures stay as they were; this page shows the month as it is booked today.`,
  figure: "Figure",
  atClosing: "At closing",
  today: "Today",
  incomeWithFlat: "Income with the flat",
  saved: "Saved",

  income: "Income",
  spending: "Spending",
  savingsRate: "Savings rate",
  savingsRateHow: "Saved ÷ income, with the flat's result",
  uncategorised: "Without a category",
  openLines: (n) => (n === 1 ? "1 transaction" : `${n} transactions`),
  answerInInbox: "Answer in To review ›",
  allAssigned: "Everything categorised",
  mean: (n) => `Ø ${n} ${n === 1 ? "month" : "months"}`,
  meanShort: (n) => `Ø ${n} mo.`,
  basis: (n, range) => `Basis of the averages: ${n} closed months, ${range}.`,
  noMeansTitle: "No averages yet",
  noMeansText: (n) => `The averages appear from 3 closed months in a row; so far there ${n === 1 ? "is 1" : `are ${n}`}.`,

  surplusTitle: "Surplus per month",
  surplusHow: "Income minus spending",
  investedOf: (amount) => `Of which invested: ${amount}`,
  shortMonths: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].join(","),

  byCategory: "Spending by category",
  plannedOnly: (saved) => `„Planned“ appears only for the categories you planned something for in your scenario${saved}`,
  savedOn: (day) => `: saved on ${day}`,
  under: " under ",
  planningBudget: "Planning › Budget",
  noSpending: "No spending is confirmed in this month.",
  category: "Category",
  share: "Share",
  soFar: " so far",
  planned: "Planned",
  difference: "Difference",
  sum: "Total",
  sumSoFar: "Total so far",
  plannedCount: (n) => `${n} planned`,
  openGroup: (group) => `Expand ${group}`,
  showAllGroups: (n) => `Show all ${n}`,
  notFinal:
    "The month is not closed yet: „so far“ counts the transactions confirmed up to today. The comparison with „Planned“ is final only after the month's closing.",
  waiting: (lines, n) => `${lines} ${n === 1 ? "is" : "are"} waiting in To review`,

  selectedCategory: "Selected category",
  monthByCategory: (month) => `${month} · Spending by category`,
  shareOfSpending: "Share of spending",
  meanOver: (n, range) => `Ø ${n} ${n === 1 ? "month" : "months"} (${range})`,
  subcategories: "Subcategories",
  plannedLine: (amount, text) => `Planned ${amount} · ${text}`,
  planFooter: (day) => `Planned in your scenario of ${day}. Change it under `,
  planFooterNone: "You can plan under ",

  noIncome: "No income is confirmed in this month.",
  flatResult: "Flat (rental account's result)",
  flatHint: "The flat's result before repayment counts as household income; the savings rate includes it.",

  savedTitle: "Saved, not spent",
  savedText: "Transfers to your own accounts do not count as spending.",
  savedAmount: (amount) => ` ${amount} went to savings, depot and exchange accounts and into investments.`,
  stayed: (surplus, stayed) => `Of the surplus (${surplus}), ${stayed} stayed on the current accounts.`,
  overInvested: (amount) => `${amount} more was invested than was left over this month.`,
};

export const household = defineMessages({ de, en });
