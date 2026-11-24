// The household's key figures on Haushalt and Anlagen › Übersicht (phase 10 D284, step 4c;
// §7.6): components/healthFigures.tsx,
// with the figure and colour names of components/healthShared.ts. German first, English beside it
// (glossary.md). A mark's sentences and a to-do's text come from the server and are shown as sent.
import { defineMessages } from "./core";

export const healthFigures = defineMessages({
  de: {
    changeLimits: "Grenzen ändern",

    // The card a month-end to-do opens.
    monthEndTitle: (date: string) => `Monatsabschluss zum ${date}`,
    monthEndMissing:
      "Für diesen Tag ist noch kein Monatsabschluss gespeichert. Hausbuch hält ihn am nächsten Morgen selbst fest; mit dem Knopf sofort.",

    // Liquiditätsreserve
    reserveMonths: (months: string, need: string) => `reicht für ${months} Monate eures Bedarfs von ${need} im Monat`,
    reserveNoNeed: "Ohne Bedarf im Monat lässt sich die Reserve nicht in Monaten angeben.",
    ownTarget: (amount: string, months: string) => `eigenes Ziel: ${amount} = ${months} Monate ·`,
    change: "ändern",
    reserveSources: "Woraus die Reserve besteht",
    account: "Konto",
    originDefault: "zählt zur Reserve",
    originAdded: "von euch hinzugefügt",
    originRemoved: "von euch herausgenommen",
    originAsOf: (origin: string, date: string) => `${origin} · Stand ${date}`,
    needExplained: (range: string) =>
      `Der Bedarf ist der Schnitt der regelmäßigen Ausgaben plus das, was die Wohnung im Monat nicht selbst trägt, aus den abgeschlossenen Monaten ${range}.`,
    changeReserveAccounts: "Konten der Reserve ändern",
    reserveLater: "Die Reserve erscheint, sobald drei Monate in Folge abgeschlossen sind; was dafür fehlt, steht unter „Kennzahlen“.",

    // Ampel-Grenzen festlegen
    bandsSet: "Ampel-Grenzen festlegen",
    bandsIntro:
      "Jede Kennzahl misst Hausbuch an einer Standard-Grenze, solange ihr keine eigene eintragt. Ein leeres Feld heißt: die Standard-Grenze gilt; eine Zahl darin ist eure eigene Grenze und gilt statt ihr.",
    bandsForm: "Ampel-Grenzen",
    reserveGreenBefore: (months: string, target: string | null) =>
      `Grün ab eurem eigenen Reserveziel von ${months} Monaten${target ? ` (${target})` : ""}, das ihr unter `,
    planning: "Haushalt › Reserve",
    reserveGreenAfter: " einstellt.",
    introSavingsRate: "Welcher Teil eures Einkommens übrig bleibt: angespannt unter der Grenze für Rot, gut ab der für Grün.",
    introDebtService: "Welcher Teil eures Einkommens für Zins und Tilgung an die Bank geht.",
    fieldLabel: (label: string, unit: string) => `${label} (${unit})`,
    redBelowLabel: "Rot unter",
    redAboveLabel: "Rot über",
    greenFromLabel: "Grün ab",
    greenUpToLabel: "Grün bis",
    unitOfTarget: "% des Ziels",
    unitPercent: "%",
    emptyStandard: (limit: string | null) => (limit ? `leer: Standard-Grenze ${limit}` : "leer: Standard-Grenze"),
    reference: "Bezugspunkt",
    scenarioRefusedBefore:
      "Die Grenzen werden im Standard-Szenario gespeichert, und dessen Annahmen lassen sich so gerade nicht speichern. Bitte das Szenario unter ",
    planningHousehold: "Haushalt › Annahmen",
    scenarioRefusedAfter: " prüfen und dort speichern; danach lassen sich die Grenzen hier übernehmen.",
    savedIn: "Gespeichert in eurem Szenario; ändern könnt ihr sie jederzeit mit „Grenzen ändern“.",

    // Kennzahlen
    figuresTitle: "Kennzahlen",
    holdingsOn: (date: string) => `Bestände am ${date} · `,
    coloursOwn: "Punkte nach euren eigenen Grenzen, wo ihr welche festgelegt habt, sonst nach den Standard-Grenzen",
    coloursStandard: "Punkte nach den Standard-Grenzen",
    appliedBy: (who: string | null, date: string) => `, übernommen${who ? ` von ${who}` : ""} am ${date}`,
    aMember: "einem Mitglied",
    annualised: (months: number) => `; hochgerechnet aus ${months} Monaten`,
    withoutOneOffs: "ohne Einmalausgaben",
    ofWhichPrincipal: "davon Tilgung",
    withoutPrincipal: "ohne Tilgung",
    savingsExplained: (range: string) =>
      `Wie viel von eurem Einkommen übrig blieb: Einkommen minus Ausgaben, geteilt durch das Einkommen. ${range}. Die Tilgung des Darlehens zählt als gespart, weil sie eure Schulden senkt.`,
    viewMonths: "Monate ansehen",
    savedOf: (saved: string, income: string, oneOff: string) => `Gespart ${saved} von ${income} Einkommen; Einmalausgaben ${oneOff}.`,
    debtServiceExplained: (annualised: string, service: string, income: string) =>
      `Zins und Tilgung geteilt durch euer Einkommen vor diesen Zahlungen${annualised} (${service} von ${income} im Jahr).`,
    madeOf: "Woraus die Zahl besteht",
    interestIn: (range: string) => `Zinsen in ${range}`,
    principalIn: (range: string) => `Tilgung in ${range}`,
    fromRentalBefore: "Aus den Buchungen des Mietkontos; ",
    planningProperty: "Anlagen › Immobilie",
    fromRentalAfter: " zeigt die Raten je Jahr.",
    splitTitle: "Aufteilung des Vermögens",
    splitExplained: (total: string | null, date: string | null) =>
      `Welcher Teil von allem, was ihr besitzt${total ? ` (${total})` : ""}, in welcher Anlageklasse steckt.${date ? ` Am ${date}.` : ""} Eine Aufteilung ist keine Bewertung und bekommt nie eine Farbe.`,
    splitProperty: "Immobilien",
    splitDepot: "Fonds und Wertpapiere",
    splitBank: "Bankguthaben",
    splitCrypto: "Krypto",

    // Vermögen im Verlauf
    seriesTitle: "Vermögen im Verlauf",
    seriesIntro:
      "Aus den gespeicherten Monatsabschlüssen. „Gespart“ ist Einkommen minus Ausgaben des Monats; der Rest der Veränderung kommt aus Kursen, dem geschätzten Wert der Wohnung und Sonstigem.",
    month: "Monat",
    netWorthAtMonthEnd: "Vermögen am Monatsende",
    changeColumn: "Veränderung",
    ofWhichSaved: "davon gespart",
    otherChanges: "Wertänderungen und Sonstiges",
  },
  en: {
    changeLimits: "Change limits",

    monthEndTitle: (date: string) => `Month-end of ${date}`,
    monthEndMissing: "No month-end is stored for this day yet. Hausbuch records it itself the next morning; the button does it now.",

    reserveMonths: (months: string, need: string) => `lasts ${months} months of your need of ${need} a month`,
    reserveNoNeed: "Without a monthly need, the reserve cannot be given in months.",
    ownTarget: (amount: string, months: string) => `own target: ${amount} = ${months} months ·`,
    change: "change",
    reserveSources: "What the reserve is made of",
    account: "Account",
    originDefault: "counts towards the reserve",
    originAdded: "added by you",
    originRemoved: "taken out by you",
    originAsOf: (origin: string, date: string) => `${origin} · as of ${date}`,
    needExplained: (range: string) =>
      `The need is the average regular spending plus what the flat does not carry itself each month, from the closed months ${range}.`,
    changeReserveAccounts: "Change the reserve's accounts",
    reserveLater: "The reserve appears once three months in a row are closed; what is still missing for that is listed under “Key figures”.",

    bandsSet: "Set traffic-light limits",
    bandsIntro:
      "Hausbuch measures each key figure against a standard limit as long as you enter none of your own. An empty field means the standard limit applies; a figure in it is your own limit and applies instead.",
    bandsForm: "Traffic-light limits",
    reserveGreenBefore: (months: string, target: string | null) =>
      `Green from your own reserve target of ${months} months${target ? ` (${target})` : ""}, which you set under `,
    planning: "Household › Reserve",
    reserveGreenAfter: ".",
    introSavingsRate: "Which part of your income is left: strained below the red limit, good from the green one.",
    introDebtService: "Which part of your income goes to the bank for interest and repayment.",
    fieldLabel: (label: string, unit: string) => `${label} (${unit})`,
    redBelowLabel: "Red below",
    redAboveLabel: "Red above",
    greenFromLabel: "Green from",
    greenUpToLabel: "Green up to",
    unitOfTarget: "% of the target",
    unitPercent: "%",
    emptyStandard: (limit: string | null) => (limit ? `empty: standard limit ${limit}` : "empty: standard limit"),
    reference: "Reference point",
    scenarioRefusedBefore:
      "The limits are stored in the default scenario, and its assumptions cannot be saved as they stand. Please check the scenario under ",
    planningHousehold: "Household › Assumptions",
    scenarioRefusedAfter: " and save it there; then the limits can be applied here.",
    savedIn: "Stored in your scenario; you can change them at any time with “Change limits”.",

    figuresTitle: "Key figures",
    holdingsOn: (date: string) => `holdings on ${date} · `,
    coloursOwn: "dots by your own limits where you set them, otherwise by the standard limits",
    coloursStandard: "dots by the standard limits",
    appliedBy: (who: string | null, date: string) => `, applied${who ? ` by ${who}` : ""} on ${date}`,
    aMember: "a member",
    annualised: (months: number) => `; projected from ${months} months`,
    withoutOneOffs: "without one-off spending",
    ofWhichPrincipal: "of which loan repayment",
    withoutPrincipal: "without loan repayment",
    savingsExplained: (range: string) =>
      `How much of your income was left: income minus spending, divided by income. ${range}. The loan repayment counts as saved because it lowers your debt.`,
    viewMonths: "View months",
    savedOf: (saved: string, income: string, oneOff: string) => `Saved ${saved} of ${income} income; one-off spending ${oneOff}.`,
    debtServiceExplained: (annualised: string, service: string, income: string) =>
      `Interest and repayment divided by your income before these payments${annualised} (${service} of ${income} a year).`,
    madeOf: "What the figure is made of",
    interestIn: (range: string) => `Interest in ${range}`,
    principalIn: (range: string) => `Repayment in ${range}`,
    fromRentalBefore: "From the rental account's transactions; ",
    planningProperty: "Assets › Property",
    fromRentalAfter: " shows the instalments per year.",
    splitTitle: "Split of the assets",
    splitExplained: (total: string | null, date: string | null) =>
      `Which part of everything you own${total ? ` (${total})` : ""} sits in which asset class.${date ? ` On ${date}.` : ""} A split is not a rating and never gets a colour.`,
    splitProperty: "Property",
    splitDepot: "Funds and securities",
    splitBank: "Bank balances",
    splitCrypto: "Crypto",

    seriesTitle: "Net worth over time",
    seriesIntro:
      "From the stored month-ends. “Saved” is the month's income minus spending; the rest of the change comes from prices, the flat's estimated value and other items.",
    month: "Month",
    netWorthAtMonthEnd: "Net worth at month-end",
    changeColumn: "Change",
    ofWhichSaved: "of which saved",
    otherChanges: "Value changes and other",
  },
});
