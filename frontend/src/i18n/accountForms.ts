// The account's forms and its page (phase 10 D284, step 4c): components/ownershipShares.tsx,
// app/(app)/accounts/[id]/AccountOwnership.tsx, app/(app)/accounts/AccountForm.tsx and
// app/(app)/accounts/[id]/page.tsx. German first, English beside it (glossary.md).
import { defineMessages } from "./core";

export const accountForms = defineMessages({
  de: {
    // --- ownershipShares.tsx ---
    sharesLegend: "Anteile (als Bruch oder Prozent, z. B. 1/2 oder 50 %; leer = kein Anteil)",
    shareDescription:
      "Der Anteil dieser Person am Konto, z. B. 1/2 bei einem gemeinsamen Konto, 1/1 bei einem eigenen. Alle Anteile zusammen müssen genau 1 (100 %) ergeben.",
    shareRequired: "Mindestens einen Anteil eingeben.",
    sharesNotOne: "Die Anteile müssen zusammen genau 1 (100 %) ergeben, z. B. 1/2 und 1/2.",

    // --- AccountOwnership.tsx ---
    owners: "Eigentümer",
    noOwnersBefore: "Noch keine Eigentümer erfasst. Zuerst unter ",
    noOwnersLink: "Eigentümer",
    noOwnersAfter: " anlegen.",
    setOwnersLead: "Eigentümer festlegen:",
    setOwnersText:
      "Wem gehört dieses Konto, und zu welchem Anteil? Ein gemeinsames Konto gehört meist beiden je zur Hälfte (1/2), ein eigenes Konto einer Person ganz (1/1).",
    changeShares: "Anteile ändern",
    reasonOptional: "Begründung (optional)",
    reasonHint: "z. B. „gemeinsames Konto“ oder „vom gemeinsamen Konto bezahlt“.",
    reasonDescription:
      "Warum das Konto diesen Personen zu diesen Anteilen gehört. Hausbuch speichert es mit den Anteilen, damit es später nachvollziehbar bleibt.",
    validFromOptional: "Gültig ab (optional)",
    validFromHint: "Leer lassen: ab heute.",
    validFromDescription: "Ab wann diese Anteile gelten. Die bisherigen Anteile bleiben im Protokoll erhalten.",
    saveShares: "Anteile speichern",

    // --- AccountForm.tsx ---
    conflictCard: "Eine Karte mit dieser Kundennummer gibt es in diesem Haushalt schon.",
    conflictDepot: "Ein Depot mit dieser IBAN gibt es in diesem Haushalt schon.",
    conflictExchange: "Dieses Börsenkonto gibt es in diesem Haushalt schon.",
    conflictWallet: "Diese Wallet gibt es in diesem Haushalt schon.",
    conflictBank: "Ein Konto mit dieser IBAN gibt es in diesem Haushalt schon.",
    openingDateRequired: "Bitte das Datum des Anfangssaldos eingeben.",
    exchangeRequired: "Bitte die Börse wählen.",
    walletProviderRequired: "Bitte den Anbieter der Wallet eingeben, z. B. Tangem.",
    walletOwnersRequired: "Bitte wählen, wem die Wallet gehört.",
    ibanRequired: "Bitte die IBAN des Kontos eingeben.",
    kind: "Kontoart",
    kindDescription:
      "Girokonto, Sparkonto (auch Tagesgeld), Kreditkarte, Depot, Börse oder Wallet. Eine Kreditkarte wird aus ihren monatlichen Abrechnungen (PDF) gefüllt, ein Depot aus den Dokumenten seiner Bank (Kontoauszüge, Abrechnungen, Berichte), eine Krypto-Börse (BISON, Binance) aus ihren Exporten, eine Wallet (z. B. Tangem, Phantom) aus ihren öffentlichen Adressen.",
    purpose: "Wofür",
    purposeDescription:
      "Das Mietkonto ist das Konto, über das die Wohnung läuft: seine Umsätze gehen in die Anlage V. Ein Privatkonto bekommt Haushaltskategorien und zählt nie in die Anlage V; Überweisungen zwischen dem Mietkonto und einem Privatkonto sind Privateinlagen und -entnahmen.",
    label: "Bezeichnung",
    labelHintCard: "z. B. Kreditkarte",
    labelHintDepot: "z. B. Depot",
    labelHintExchange: "z. B. BISON",
    labelHintWallet: "z. B. Tangem",
    labelHintBank: "z. B. Mietkonto Musterstraße",
    labelDescription: "Ein frei wählbarer Name, unter dem Sie das Konto in Hausbuch wiederfinden.",
    exchange: "Börse",
    exchangeDescription:
      "Die Börse, deren Exporte Sie hochladen. Hausbuch liest die Exporte von BISON und Binance; eine andere Börse kommt erst, wenn ihr Export bekannt ist.",
    pleaseChoose: "Bitte wählen",
    provider: "Anbieter",
    walletProviderHint: "z. B. Tangem, Phantom, Ledger",
    walletProviderDescription:
      "Die Wallet-App oder das Gerät, in dem die Coins liegen. Hausbuch liest nur die öffentlichen Adressen, die Sie auf der Seite der Wallet eintragen.",
    bank: "Bank",
    bankDescriptionCard: "Die Bank, die die Karte herausgibt, wie auf der Abrechnung.",
    bankDescriptionDepot: "Die Bank, bei der das Depot geführt wird, wie auf seinem Kontoauszug.",
    bankDescriptionBank: "Die Bank, bei der das Konto geführt wird, wie auf dem Kontoauszug.",
    cardProviderHint: "Optional, z. B. der Markenname der Karte.",
    cardProviderDescription:
      "Der Name, unter dem die Karte auftritt, wenn er von der Bank abweicht. Die Karte hat keine eigene IBAN: Hausbuch erkennt ihre Abrechnungen an der Kundennummer, die die erste Abrechnung mitbringt.",
    iban: "IBAN",
    ibanHintDepot: "Optional; wird nur maskiert angezeigt.",
    ibanHint: "Wird nur maskiert angezeigt.",
    ibanDescriptionDepot:
      "Die IBAN des Verrechnungskontos, falls Sie sie zur Hand haben. Sonst leer lassen: der erste Kontoauszug bringt sie mit, und Sie bestätigen sie auf der Seite des Depots.",
    ibanDescription:
      "Die IBAN des Kontos laut Kontoauszug. Hausbuch zeigt sie nur maskiert und prüft damit, dass ein hochgeladener Kontoauszug zu diesem Konto gehört.",
    opening: "Anfangssaldo (€)",
    openingHintCard: "Der „Alte Saldo“ der ersten Abrechnung, die Sie hochladen; ein offener Betrag mit Minus.",
    openingHintDepot:
      "Der „alte Saldo“ des ersten Kontoauszugs, den Sie hochladen. Unbekannt? 0,00 lassen und nach dem Hochladen „Anfangssaldo daraus übernehmen“ wählen.",
    openingHintBank: "Der Kontostand am Ende des Tages unten, laut Bank. Bei einem neuen Konto 0,00; negativ mit Minus.",
    openingDescription:
      "Der Kontostand laut Bank am Ende des Tages, den Sie darunter angeben. Von ihm aus prüft Hausbuch, ob jeder importierte Kontoauszug aufgeht: Anfangssaldo plus Umsätze gleich Endsaldo.",
    openingOn: "Anfangssaldo am",
    openingOnHintCard: "Ein Tag vor der ersten Buchung dieser Abrechnung, z. B. der letzte Tag des Vormonats.",
    openingOnHintDepot: "Ein Tag vor der ersten Buchung des ersten Kontoauszugs, z. B. der letzte Tag des Vormonats.",
    openingOnHintBank:
      "Ein Tag vor dem ersten Umsatz, der importiert wird: Umsätze an oder vor diesem Tag passen nicht zum Anfangssaldo.",
    openingOnDescription: "Der Tag, an dem dieser Kontostand galt, laut Kontoauszug oder Online-Banking.",
    cardNote:
      "Eine Kreditkarte ist immer ein Privatkonto: ihre Umsätze bekommen Haushaltskategorien, Zinsen und Gebühren der Karte ordnet Hausbuch selbst zu. Wem die Karte gehört, legen Sie nach dem Anlegen auf ihrer Seite fest.",
    depotNote:
      "Ein Depot ist immer ein Privatkonto. Legen Sie eins je Konto Ihrer Depotbank an, das auf deren Kontoauszügen erscheint (bei quirion das Verrechnungskonto und jedes Vermögensverwaltungskonto). Die Kontonummer bringt der erste Kontoauszug mit; wem das Depot gehört, legen Sie nach dem Anlegen auf seiner Seite fest.",
    exchangeNote:
      "Ein Börsenkonto ist immer ein Privatkonto und hat keine IBAN und keinen Anfangssaldo: nach dem Anlegen laden Sie auf seiner Seite den Export der Börse hoch, und Hausbuch zeigt je Coin, was er ergibt. Wem das Konto gehört (bei BISON die Person, auf deren Namen es läuft), legen Sie nach dem Anlegen auf seiner Seite fest.",
    walletOwnersLegend: "Wem gehört die Wallet?",
    walletJoint: "Gemeinsam, je 1/2 (50/50)",
    walletAlone: (name: string) => `${name} allein (100 %)`,
    walletReasonHint: "Vorgeschlagen, weil die Coins meist vom gemeinsamen Konto bezahlt wurden.",
    walletReasonDescription:
      "Warum die Wallet diesen Personen zu diesen Anteilen gehört, z. B. von welchem Konto die Coins bezahlt wurden. Hausbuch speichert es mit den Anteilen, damit es später nachvollziehbar bleibt.",
    walletNote:
      "Eine Wallet ist immer ein Privatkonto und hat keine IBAN und keinen Anfangssaldo. Nach dem Anlegen tragen Sie auf ihrer Seite die öffentlichen Adressen ein (nie die Seed-Phrase oder einen privaten Schlüssel).",
    walletNoteOwners: " Wem sie gehört, legen Sie nach dem Anlegen auf ihrer Seite fest.",
    ibanFixedNote:
      "Die IBAN lässt sich nicht ändern: eine andere IBAN ist ein anderes Konto. Wird der Anfangssaldo geändert, prüft Hausbuch alle Kontoauszüge dieses Kontos neu.",
    createAccount: "Konto anlegen",

    // --- accounts/[id]/page.tsx ---
    tabStatements: "Kontoauszüge",
    tabLines: "Umsätze",
    tabDuplicates: "Mögliche Doppelbuchungen",
    tabDuplicatesCount: (count: number) => `Mögliche Doppelbuchungen (${count})`,
    breadcrumbAccounts: "Konten",
    edit: "Bearbeiten",
    privateNote:
      "Privatkonto: seine Umsätze bekommen Haushaltskategorien und zählen nie in die Anlage V. Überweisungen zwischen diesem Konto und dem Mietkonto sind Privateinlagen und -entnahmen.",
    rulesOff: (count: number) =>
      `${count === 1 ? "1 gelernte Regel" : `${count} gelernte Regeln`} für diese Kontonummer abgeschaltet:`,
    rulesOffDepot: " Überweisungen auf das Depot sind jetzt Überträge.",
    rulesOffCard: " Zahlungen an die Karte sind jetzt Überträge.",
    exchangePageNote:
      "Börsenkonto: Hausbuch liest die Exporte der Börse selbst, ohne KI, und zeigt je Coin, was sie ergeben. Ein Kauf, ein Verkauf und eine Gebühr sind damit gebucht; nur Auszahlungen und Eingänge, deren Ziel oder Herkunft der Export nicht nennt, fragt Hausbuch nach.",
    duplicatesWaiting: (count: number) =>
      `${count === 1 ? "1 mögliche Doppelbuchung wartet" : `${count} mögliche Doppelbuchungen warten`} auf eine Entscheidung: `,
    duplicatesCheck: "prüfen",
    importedStatements: "Importierte Kontoauszüge",
    factExchange: "Börse",
    factKind: "Kontoart",
    factProvider: "Anbieter",
    factBank: "Bank",
    factCard: "Karte",
    factCustomerNumber: "Kundennummer",
    factPaymentIban: "Zahlungs-IBAN",
    factOpening: "Anfangssaldo",
    factIban: "IBAN",
    factPurpose: "Wofür",
    cardEndsIn: (digits: string) => `endet auf ${digits}`,
    comesWithFirstStatement: "kommt mit der ersten Abrechnung",
    openingOnDay: (amount: string, date: string) => `${amount} am ${date}`,
    depotIbanTitle: "IBAN des Depots bestätigen",
    depotIbanBefore: "Der Kontoauszug nennt die IBAN ",
    depotIbanAfter: " für dieses Depot.",
    confirmedSoFar: (iban: string) => ` Bisher bestätigt ist ${iban}.`,
    depotIbanExplain:
      " Stimmt sie, bestätigen Sie sie hier: Hausbuch bucht dann eine Überweisung von Ihrem Girokonto auf diese IBAN als Umbuchung zwischen eigenen Konten, nicht als Ausgabe, und verknüpft sie mit der Einlage auf dem Kontoauszug des Depots.",
    confirmIban: "IBAN bestätigen",
    paymentIbanTitle: "Zahlungs-IBAN bestätigen",
    paymentIbanBefore: "Die Abrechnung nennt unter „Angaben bei Einzahlungen“ die IBAN ",
    paymentIbanAfter: ", auf die Sie die Karte bezahlen.",
    paymentIbanExplain:
      " Stimmt sie, bestätigen Sie sie hier: Hausbuch bucht dann die Zahlung vom Girokonto an diese IBAN als Umbuchung zwischen eigenen Konten und verknüpft sie mit der „Bezahlung“ auf der Abrechnung.",
    confirmPaymentIban: "Zahlungs-IBAN bestätigen",
  },
  en: {
    sharesLegend: "Shares (as a fraction or percentage, e.g. 1/2 or 50%; empty = no share)",
    shareDescription:
      "This person's share of the account, e.g. 1/2 for a joint account, 1/1 for their own. All shares together must add up to exactly 1 (100%).",
    shareRequired: "Enter at least one share.",
    sharesNotOne: "The shares must add up to exactly 1 (100%), e.g. 1/2 and 1/2.",

    owners: "Owners",
    noOwnersBefore: "No owners recorded yet. Add them first under ",
    noOwnersLink: "Owners",
    noOwnersAfter: ".",
    setOwnersLead: "Set owners:",
    setOwnersText:
      "Who owns this account, and in what share? A joint account usually belongs to both, half each (1/2); an account of one person belongs to them in full (1/1).",
    changeShares: "Change shares",
    reasonOptional: "Reason (optional)",
    reasonHint: "e.g. “joint account” or “paid from the joint account”.",
    reasonDescription:
      "Why the account belongs to these people in these shares. Hausbuch stores it with the shares so it can be traced later.",
    validFromOptional: "Valid from (optional)",
    validFromHint: "Leave empty: from today.",
    validFromDescription: "From when these shares apply. The previous shares stay in the history.",
    saveShares: "Save shares",

    conflictCard: "A card with this customer number already exists in this household.",
    conflictDepot: "A securities account with this IBAN already exists in this household.",
    conflictExchange: "This exchange account already exists in this household.",
    conflictWallet: "This wallet already exists in this household.",
    conflictBank: "An account with this IBAN already exists in this household.",
    openingDateRequired: "Please enter the date of the opening balance.",
    exchangeRequired: "Please choose the exchange.",
    walletProviderRequired: "Please enter the wallet's provider, e.g. Tangem.",
    walletOwnersRequired: "Please choose who owns the wallet.",
    ibanRequired: "Please enter the account's IBAN.",
    kind: "Account kind",
    kindDescription:
      "Current account, savings account (including instant access), credit card, securities account, exchange or wallet. A credit card is filled from its monthly statements (PDF), a securities account from its bank's documents (statements, trade confirmations, reports), a crypto exchange (BISON, Binance) from its exports, a wallet (e.g. Tangem, Phantom) from its public addresses.",
    purpose: "Used for",
    purposeDescription:
      "The rental account is the account the flat runs through: its transactions go into the Anlage V (rental income form). A private account gets household categories and never counts in the Anlage V; transfers between the rental account and a private account are private deposits and withdrawals.",
    label: "Name",
    labelHintCard: "e.g. Credit card",
    labelHintDepot: "e.g. Securities account",
    labelHintExchange: "e.g. BISON",
    labelHintWallet: "e.g. Tangem",
    labelHintBank: "e.g. Rental account Sample Street",
    labelDescription: "A name of your choice under which you find the account in Hausbuch.",
    exchange: "Exchange",
    exchangeDescription:
      "The exchange whose exports you upload. Hausbuch reads the exports of BISON and Binance; another exchange follows only once its export is known.",
    pleaseChoose: "Please choose",
    provider: "Provider",
    walletProviderHint: "e.g. Tangem, Phantom, Ledger",
    walletProviderDescription:
      "The wallet app or device that holds the coins. Hausbuch reads only the public addresses you enter on the wallet's page.",
    bank: "Bank",
    bankDescriptionCard: "The bank that issues the card, as on the statement.",
    bankDescriptionDepot: "The bank that holds the securities account, as on its statement.",
    bankDescriptionBank: "The bank that holds the account, as on the statement.",
    cardProviderHint: "Optional, e.g. the card's brand name.",
    cardProviderDescription:
      "The name the card goes by, if it differs from the bank. The card has no IBAN of its own: Hausbuch recognises its statements by the customer number the first statement brings.",
    iban: "IBAN",
    ibanHintDepot: "Optional; shown masked only.",
    ibanHint: "Shown masked only.",
    ibanDescriptionDepot:
      "The IBAN of the cash account (Verrechnungskonto), if you have it to hand. Otherwise leave it empty: the first statement brings it, and you confirm it on the securities account's page.",
    ibanDescription:
      "The account's IBAN as on the statement. Hausbuch shows it masked only and uses it to check that an uploaded statement belongs to this account.",
    opening: "Opening balance (€)",
    openingHintCard: "The “Alter Saldo” (previous balance) of the first statement you upload; an amount owed with a minus.",
    openingHintDepot:
      "The “alter Saldo” (previous balance) of the first statement you upload. Unknown? Leave 0,00 and after the upload choose “Take the opening balance from it” („Anfangssaldo daraus übernehmen“).",
    openingHintBank: "The balance at the end of the day below, according to the bank. 0,00 for a new account; negative with a minus.",
    openingDescription:
      "The balance according to the bank at the end of the day you give below. From it Hausbuch checks that every imported statement adds up: opening balance plus transactions equals closing balance.",
    openingOn: "Opening balance on",
    openingOnHintCard: "One day before the first entry of this statement, e.g. the last day of the previous month.",
    openingOnHintDepot: "One day before the first entry of the first statement, e.g. the last day of the previous month.",
    openingOnHintBank:
      "One day before the first transaction to be imported: transactions on or before this day do not fit the opening balance.",
    openingOnDescription: "The day this balance applied, according to the statement or online banking.",
    cardNote:
      "A credit card is always a private account: its transactions get household categories, and Hausbuch categorises the card's interest and fees itself. Who owns the card you set on its page after creating it.",
    depotNote:
      "A securities account is always a private account. Create one for each account of your broker that appears on its statements (at quirion the cash account and each asset management account). The first statement brings the account number; who owns the securities account you set on its page after creating it.",
    exchangeNote:
      "An exchange account is always a private account and has no IBAN and no opening balance: after creating it you upload the exchange's export on its page, and Hausbuch shows what it adds up to for each coin. Who owns the account (at BISON the person in whose name it runs) you set on its page after creating it.",
    walletOwnersLegend: "Who owns the wallet?",
    walletJoint: "Jointly, 1/2 each (50/50)",
    walletAlone: (name: string) => `${name} alone (100%)`,
    walletReasonHint: "Suggested because the coins were usually paid from the joint account.",
    walletReasonDescription:
      "Why the wallet belongs to these people in these shares, e.g. which account paid for the coins. Hausbuch stores it with the shares so it can be traced later.",
    walletNote:
      "A wallet is always a private account and has no IBAN and no opening balance. After creating it you enter the public addresses on its page (never the seed phrase or a private key).",
    walletNoteOwners: " Who owns it you set on its page after creating it.",
    ibanFixedNote:
      "The IBAN cannot be changed: a different IBAN is a different account. If the opening balance changes, Hausbuch checks all of this account's statements again.",
    createAccount: "Add account",

    tabStatements: "Statements",
    tabLines: "Transactions",
    tabDuplicates: "Possible duplicates",
    tabDuplicatesCount: (count: number) => `Possible duplicates (${count})`,
    breadcrumbAccounts: "Accounts",
    edit: "Edit",
    privateNote:
      "Private account: its transactions get household categories and never count in the Anlage V (rental income form). Transfers between this account and the rental account are private deposits and withdrawals.",
    rulesOff: (count: number) =>
      `${count === 1 ? "1 learned rule" : `${count} learned rules`} for this account number switched off:`,
    rulesOffDepot: " transfers to the securities account are now transfers between own accounts.",
    rulesOffCard: " payments to the card are now transfers between own accounts.",
    exchangePageNote:
      "Exchange account: Hausbuch reads the exchange's exports itself, without AI, and shows what they add up to for each coin. A purchase, a sale and a fee are booked that way; Hausbuch only asks about withdrawals and deposits whose destination or origin the export does not name.",
    duplicatesWaiting: (count: number) =>
      `${count === 1 ? "1 possible duplicate is waiting" : `${count} possible duplicates are waiting`} for a decision: `,
    duplicatesCheck: "review",
    importedStatements: "Imported statements",
    factExchange: "Exchange",
    factKind: "Account kind",
    factProvider: "Provider",
    factBank: "Bank",
    factCard: "Card",
    factCustomerNumber: "Customer number",
    factPaymentIban: "Payment IBAN",
    factOpening: "Opening balance",
    factIban: "IBAN",
    factPurpose: "Used for",
    cardEndsIn: (digits: string) => `ending ${digits}`,
    comesWithFirstStatement: "comes with the first statement",
    openingOnDay: (amount: string, date: string) => `${amount} on ${date}`,
    depotIbanTitle: "Confirm the securities account's IBAN",
    depotIbanBefore: "The statement names the IBAN ",
    depotIbanAfter: " for this securities account.",
    confirmedSoFar: (iban: string) => ` Confirmed so far: ${iban}.`,
    depotIbanExplain:
      " If it is right, confirm it here: Hausbuch then books a transfer from your current account to this IBAN as a transfer between own accounts, not as spending, and links it to the deposit on the securities account's statement.",
    confirmIban: "Confirm IBAN",
    paymentIbanTitle: "Confirm payment IBAN",
    paymentIbanBefore: "Under “Angaben bei Einzahlungen” (payment details) the statement names the IBAN ",
    paymentIbanAfter: " to which you pay the card.",
    paymentIbanExplain:
      " If it is right, confirm it here: Hausbuch then books the payment from the current account to this IBAN as a transfer between own accounts and links it to the “Bezahlung” (payment) on the statement.",
    confirmPaymentIban: "Confirm payment IBAN",
  },
});
