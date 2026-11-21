// Übersicht's net worth and cashflow cards (phase 10 D284, step 4c): components/netWorth.tsx and
// components/cashflow.tsx; the month-end capture's sentences are shared with healthFigures.tsx.
// German first, English beside it (glossary.md). The lines' notes and the to-dos come from the
// server and are shown as sent.
import { defineMessages } from "./core";

export const cashflowText = defineMessages({
  de: {
    title: (month: string) => `Cashflow ${month}`,
    noBookingBefore: "Für diesen Monat ist noch keine private Buchung bestätigt. Die Privatkonten werden unter",
    accounts: "Konten",
    noBookingAfter: " importiert.",
    inReview: (lines: string) => `${lines} in Zu prüfen`,
    inOut: (income: string, spent: string) => `${income} rein · ${spent} raus`,
    savingsRate: (rate: string) => ` · Sparquote ${rate}`,
    toHousehold: "Zum Haushalt",
    uncategorised: (lines: string) => `${lines} ohne Kategorie`,
    openLines: (count: number) => (count === 1 ? "1 Umsatz" : `${count} Umsätze`),
  },
  en: {
    title: (month: string) => `Cashflow ${month}`,
    noBookingBefore: "No private transaction is confirmed for this month yet. The private accounts are imported under",
    accounts: "Accounts",
    noBookingAfter: ".",
    inReview: (lines: string) => `${lines} to review`,
    inOut: (income: string, spent: string) => `${income} in · ${spent} out`,
    savingsRate: (rate: string) => ` · savings rate ${rate}`,
    toHousehold: "Go to Household",
    uncategorised: (lines: string) => `${lines} without a category`,
    openLines: (count: number) => (count === 1 ? "1 transaction" : `${count} transactions`),
  },
});

export const netWorthText = defineMessages({
  de: {
    // The month-end capture's outcome (POST /net-worth/snapshots), here and in healthFigures.tsx.
    snapshotWritten: (date: string) => `Monatsabschluss zum ${date} festgehalten.`,
    snapshotRecaptured: (date: string) => `Monatsabschluss zum ${date} neu festgehalten: die Zahlen hatten sich seitdem geändert.`,
    snapshotUnchanged: (date: string) => `Der Monatsabschluss zum ${date} ist schon mit genau diesen Zahlen festgehalten.`,
    snapshotEmpty: "Nichts festzuhalten: noch kein Konto, keine Anlage und keine Immobilie mit einem Wert.",
    unvalued: (count: number) =>
      count === 1
        ? " 1 Position ohne Wert ist darin nicht enthalten (siehe offene Punkte)."
        : ` ${count} Positionen ohne Wert sind darin nicht enthalten (siehe offene Punkte).`,
    captureMonthEnd: "Monatsabschluss festhalten",

    // The card „Nettovermögen“.
    title: "Nettovermögen",
    emptyBefore: "Noch kein Konto, keine Anlage und keine Immobilie mit einem Wert. Konten werden unter ",
    accounts: "Konten",
    emptyAfter: " angelegt und importiert.",
    today: (date: string) => `Heute, ${date}`,
    changeSince: (change: string, date: string) => `${change} gegenüber dem Monatsabschluss vom ${date}`,
    noMonthEnd: " · noch kein Monatsabschluss festgehalten",
    owner: "Eigentümer",
    unassigned: " · ohne Eigentümer: siehe unten",
    due: (date: string) =>
      `Der Monatsabschluss zum ${date} ist noch nicht festgehalten. Hausbuch speichert ihn am Monatsersten automatisch, nach dem morgendlichen Abruf der Kontoumsätze und Kurse.`,
    drifted: (stored: string, now: string, date: string) =>
      `Das gespeicherte Monatsende weicht vom heutigen Stand ab: ${stored} statt ${now}. Seit dem Speichern ist für den ${date} noch etwas dazugekommen (ein Kontoauszug, ein Kurs oder ein geänderter Schätzwert).`,
    capturing: "Hält fest …",
    recapture: "Monatsabschluss neu speichern",
    balanceAsOf: (label: string, amount: string, date: string) => `„${label}“ ${amount} · Stand vom ${date}`,
    beforeHandover: "Vor der Übergabe der Wohnung:",
    loan: "Darlehen ",
    estimates: "Enthält Schätzwerte:",
    adjustValue: "Wert in der Planung anpassen",
    todos: (count: number) => `${count === 1 ? "1 offener Punkt" : `${count} offene Punkte`} im Nettovermögen`,

    // The bar „Vermögensaufteilung“.
    allocation: "Vermögensaufteilung",
    propertyNetOfLoans: "Immobilie abzüglich Darlehen",
    pricesOf: (date: string) => ` · Kurse vom ${date}`,
    stale: (count: number) => ` · ${count} Kurs${count === 1 ? "" : "e"} veraltet`,
    share: (percent: string) => `${percent} %`,
    sliceProperty: "Immobilie (netto)",
    sliceDepot: "Depot",
    sliceBank: "Bank",
    sliceCrypto: "Krypto",
    sliceCards: "Kreditkarten",
    openBalance: "offener Saldo",
    andMore: (names: string, more: number) => `${names} und ${more} weitere`,
    valueLessLoansPartly: "Wert minus Darlehen, teils geschätzt",
    advanceLessPayout: "Anzahlung minus ausgezahltes Darlehen",
    advance: "Anzahlung Kaufpreis",
    payout: "ausgezahltes Darlehen",
    valueLessLoansEstimated: "Wert minus Darlehen, geschätzt",
    valueLessLoans: "Wert minus Darlehen",
  },
  en: {
    snapshotWritten: (date: string) => `Month-end of ${date} recorded.`,
    snapshotRecaptured: (date: string) => `Month-end of ${date} recorded again: the figures had changed since.`,
    snapshotUnchanged: (date: string) => `The month-end of ${date} is already recorded with exactly these figures.`,
    snapshotEmpty: "Nothing to record: no account, investment or property with a value yet.",
    unvalued: (count: number) =>
      count === 1
        ? " 1 position without a value is not included (see open items)."
        : ` ${count} positions without a value are not included (see open items).`,
    captureMonthEnd: "Record month-end",

    title: "Net worth",
    emptyBefore: "No account, investment or property with a value yet. Accounts are created and imported under ",
    accounts: "Accounts",
    emptyAfter: ".",
    today: (date: string) => `Today, ${date}`,
    changeSince: (change: string, date: string) => `${change} compared with the month-end of ${date}`,
    noMonthEnd: " · no month-end recorded yet",
    owner: "Owner",
    unassigned: " · without an owner: see below",
    due: (date: string) =>
      `The month-end of ${date} is not recorded yet. Hausbuch stores it automatically on the first of the month, after the morning fetch of bank transactions and prices.`,
    drifted: (stored: string, now: string, date: string) =>
      `The stored month-end differs from today's figures: ${stored} instead of ${now}. Since it was stored, something was added for ${date} (a statement, a price or a changed estimate).`,
    capturing: "Recording …",
    recapture: "Store month-end again",
    balanceAsOf: (label: string, amount: string, date: string) => `“${label}” ${amount} · as of ${date}`,
    beforeHandover: "Before the flat's handover:",
    loan: "Loan ",
    estimates: "Includes estimates:",
    adjustValue: "Adjust the value in Planning",
    todos: (count: number) => `${count === 1 ? "1 open item" : `${count} open items`} in the net worth`,

    allocation: "Asset allocation",
    propertyNetOfLoans: "Property less loans",
    pricesOf: (date: string) => ` · prices of ${date}`,
    stale: (count: number) => ` · ${count} ${count === 1 ? "price" : "prices"} out of date`,
    share: (percent: string) => `${percent}%`,
    sliceProperty: "Property (net)",
    sliceDepot: "Depot",
    sliceBank: "Bank",
    sliceCrypto: "Crypto",
    sliceCards: "Credit cards",
    openBalance: "open balance",
    andMore: (names: string, more: number) => `${names} and ${more} more`,
    valueLessLoansPartly: "Value less loans, partly estimated",
    advanceLessPayout: "Down payment less loan paid out",
    advance: "Down payment on the price",
    payout: "loan paid out",
    valueLessLoansEstimated: "Value less loans, estimated",
    valueLessLoans: "Value less loans",
  },
});
