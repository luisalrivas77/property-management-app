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

  // „KI-Vorschläge holen“
  aiFetch: "KI-Vorschläge holen",
  aiSent: (n: number) => `${n === 1 ? "1 Zahlung" : `${n} Zahlungen`} an die KI geschickt.`,
  aiSoon: " Die Vorschläge erscheinen in ein paar Minuten.",
  aiHeldBack: (n: number) =>
    `${n === 1 ? "1 Zahlung privater Konten wurde" : `${n} Zahlungen privater Konten wurden`} nicht geschickt: die KI ` +
    "für private Konten ist ausgeschaltet. Einschalten unter ",
  settings: "Einstellungen",
  aiHeldBackAfter: " › „Private Konten mit KI zuordnen“, dann hier noch einmal „KI-Vorschläge holen“.",

  // the bulk confirm list
  bulkIntro: "Vorschläge für private Konten, nach Kategorie: eine ganze Gruppe lässt sich auf einmal bestätigen.",
  bulkListLabel: "Vorschläge privater Konten nach Kategorie",
  hidePayments: "Zahlungen ausblenden",
  showPayments: "Zahlungen ansehen",
  reviewAll: (n: number) => `Alle ${n} prüfen und bestätigen`,
  reviewAllTitle: (category: string) => `${category}: Zahlungen auswählen und bestätigen`,
  bulkConfirmed: (n: number) => `${n === 1 ? "1 Zahlung" : `${n} Zahlungen`} bestätigt.`,
  bulkSkipped: (n: number) =>
    ` ${n === 1 ? "1 Zahlung wurde" : `${n} Zahlungen wurden`} übersprungen, weil sich ihr Vorschlag inzwischen geändert hat; sie stehen weiter in der Liste.`,
  bulkExplain: (label: string) =>
    `Die angehakten Zahlungen werden als „${label}“ bestätigt, jede wie mit ihrem eigenen „Bestätigen“. Eine Zahlung ohne ` +
    "Haken bleibt in der Liste. Steht unter einer Zahlung eine Frage, nimm den Haken weg, wenn die Antwort eine andere " +
    "Kategorie ergibt. Es wird keine Regel gelernt; jede Zahlung lässt sich danach einzeln ändern.",
  paymentsAs: (label: string) => `Zahlungen als „${label}“`,
  selectAll: "Alle auswählen",
  date: "Datum",
  payee: "Empfänger oder Zahler",
  amount: "Betrag",
  account: "Konto",
  selectLine: (date: string, payee: string, amount: string) => `Auswählen: ${date} ${payee} ${amount}`,
  confirmSelected: (n: number) => `Ausgewählte bestätigen (${n})`,

  // a card's title
  questionnaire: (year: number | null | undefined) => `Jahresfragen ${year ?? ""}`,
  forwardedMail: "Weitergeleitete Mail",
  emailInbox: "Posteingang per E-Mail",
  secretsUnreadable: "Gespeicherte Zugangsschlüssel lassen sich nicht mehr lesen",
  exchange: "Börse",
  wallet: "Wallet",
  depotHoldings: "Depotbestand",
  assetPrice: "Kurs einer Anlage",
  bankSync: "Bankabruf",
  dueOn: (date: string) => `fällig am ${date}`,
  deadline: "Termin",
  privateBill: "Privat bezahlte Rechnung",
  statement: "Kontoauszug",
  transaction: "Umsatz",
  document: "Dokument",

  // a card's action
  starting: "Startet …",
  fetchNow: "Jetzt abrufen",
  openDocument: "Dokument öffnen",
  openAccounts: "Konten öffnen",
  reviewProposal: "Vorschlag prüfen",
  enterClosing: "Endsaldo eingeben",
  closingTitle: "Endsaldo laut Online-Banking",
  checkStatement: "Kontoauszug prüfen",
  answerQuestions: (year: number) => `Jahresfragen ${year} beantworten`,
  viewStatement: "Kontoauszug ansehen",
  toBankSync: "Zum Bankabruf",
  openAccount: "Konto öffnen",
  uploadWithdrawals: "Auszahlungsverlauf hochladen",
  addOrFetchWallet: "Wallet anlegen oder abrufen",
  importOtherAccount: "Anderes Konto importieren",
  openLine: "Buchung öffnen",
  openReceipt: "Beleg öffnen",
  enterHoldings: "Bestand laut Börse eingeben",
  answerSingly: "Einzeln beantworten",
  toExchange: "Zum Börsenkonto",
  toWallet: "Zur Wallet",
  answerOnWallet: "Auf der Wallet beantworten",
  uploadSettlement: "Abrechnung hochladen",
  openReport: "Bericht öffnen",
  toDepot: "Zum Depot",
  enterPrice: "Kurs eintragen",
  choosePriceSource: "Kursquelle wählen",
  openSecrets: "Zugangsschlüssel öffnen",

  // „Umsätze aus dem Bericht buchen“
  bookingTrades: "Bucht …",
  bookFromReport: (n: number) => `Umsätze aus dem Bericht buchen (${n})`,
  notFromReport: (names: string) =>
    `Nicht aus dem Bericht gebucht: ${names} (Bestand würde nicht zum Bericht passen). Dafür bitte die ` +
    "Wertpapierabrechnungen unter Dokumente hochladen.",
  and: "und",

  // transfers between own accounts
  learnDeposit: (from: string, sentOn: string, sent: string, to: string, receivedOn: string, received: string) =>
    `Die Adresse, an die ${from} am ${sentOn} ${sent} gesendet hat, als Einzahlungsadresse von ${to} merken (Einzahlung vom ${receivedOn}, ${received}); weitere Einzahlungen dorthin verknüpft die App dann selbst`,
  learnWithdrawal: (from: string, to: string, sentOn: string, sent: string, receivedOn: string, received: string) =>
    `Abhebungen von ${from} an diese Adresse von ${to} künftig automatisch verknüpfen (Abhebung vom ${sentOn}, ${sent}; Eingang vom ${receivedOn}, ${received})`,
  rebook: (debit: boolean, date: string, category: string | null, card: boolean) =>
    `${debit ? "Die Abbuchung" : "Die Gutschrift"} vom ${date} ` +
    `${category ? `ist bisher als „${category}“ gebucht und zählt damit als ${debit ? "Ausgabe" : "Einnahme"}` : "ist noch nicht gebucht"}` +
    `; sie wird als Übertrag gebucht, ${card ? "weil die Kartenumsätze selbst schon gezählt werden" : "weil das Geld nur zwischen zwei eigenen Konten wechselt"}.`,
  counterpart: "Gegenbuchung",
  linking: "Verknüpft …",
  linkDeposit: "Als Einzahlung verknüpfen",
  linkRebook: "Als Übertrag buchen und verknüpfen",
  link: "Verknüpfen",
  notTransfer: "Keine Überweisung – Zahlung anders zuordnen",
  ownWallet: "Eigene Wallet",
  pairedWaits: (from: string, to: string) =>
    `Abhebungen von ${from} an ${to} verknüpft die App selbst, sobald der abgerufene Kontostand der Wallet sie enthält. ` +
    "Diese wartet, weil der letzte Kontostand sie noch nicht enthält: Wallet abrufen, oder hier selbst zuordnen.",
  pairedMany: (from: string, n: number) =>
    `Abhebungen von ${from} sind an ${n} eigene Adressen auf dieser Chain gelernt. Die App kann nicht erkennen, an welche ` +
    "diese ging, und verknüpft sie deshalb nicht selbst: bitte hier zuordnen.",
  toOwnWallet: (label: string, chain: string, address: string) => `An eigene Wallet: ${label} (${chain}) · ${address}`,
  walletBooks: "Die App bucht die Einzahlung auf der Wallet selbst: die abgehobene Menge abzüglich der Gebühr der Börse, am Tag der Abhebung.",
  rememberPair: (from: string) => `Abhebungen von ${from} an diese Adresse künftig automatisch verknüpfen`,
  booking: "Wird gebucht …",
  bookToWallet: "Auf eigene Wallet buchen",

  // a returned payment
  returnedDebit: "Zurückgekommene Abbuchung",
  cameBack: (text: string) => `Kam zurück: ${text}`,
  returnCancels: "Abbuchung und Gutschrift heben sich auf: Zusammen zählen sie weder als Ausgabe noch als Einnahme noch als Überweisung.",
  returnRebooked: (category: string) => ` Die Abbuchung ist bisher als „${category}“ gebucht und wird umgebucht.`,
  bookReturn: "Als Rücküberweisung verbuchen",
  notReturn: "Keine Rücküberweisung – Zahlung anders zuordnen",

  // privately paid property costs
  taxYear: (year: number) => ` · Steuerjahr ${year}`,
  applying: "Übernimmt …",
  apply: "Übernehmen",
  moving: "Wird verschoben …",
  countIn: (year: number) => `In ${year} zählen`,
  yearPaid: "Privat bezahlte Werbungskosten zählen im Jahr der Zahlung (§ 11 Abs. 2 EStG).",
  uploadReceipt: "Beleg hochladen",
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
  noMatch: (needle) => `No payment matches “${needle}”.`,
  noCounterparty: "No counterparty",
  noPurpose: "No payment reference",
  line: "Transaction",
  privateAccount: (label) => `Private account · ${label}`,

  aiFetch: "Get AI suggestions",
  aiSent: (n) => `Sent ${n === 1 ? "1 payment" : `${n} payments`} to the AI.`,
  aiSoon: " The suggestions appear in a few minutes.",
  aiHeldBack: (n) =>
    `${n === 1 ? "1 payment from private accounts was" : `${n} payments from private accounts were`} not sent: the AI ` +
    "for private accounts is switched off. Switch it on under ",
  settings: "Settings",
  aiHeldBackAfter: " › “Categorise private accounts with AI”, then “Get AI suggestions” here again.",

  bulkIntro: "Suggestions for private accounts, by category: a whole group can be confirmed at once.",
  bulkListLabel: "Private accounts' suggestions by category",
  hidePayments: "Hide payments",
  showPayments: "Show payments",
  reviewAll: (n) => `Review and confirm all ${n}`,
  reviewAllTitle: (category) => `${category}: choose payments and confirm`,
  bulkConfirmed: (n) => `${n === 1 ? "1 payment" : `${n} payments`} confirmed.`,
  bulkSkipped: (n) =>
    ` ${n === 1 ? "1 payment was" : `${n} payments were`} skipped because their suggestion has changed in the meantime; they stay in the list.`,
  bulkExplain: (label) =>
    `The ticked payments are confirmed as “${label}”, each as if with its own “Confirm”. A payment without a ` +
    "tick stays in the list. If a payment has a question under it, untick it when the answer means a different " +
    "category. No rule is learned; each payment can be changed on its own afterwards.",
  paymentsAs: (label) => `Payments as “${label}”`,
  selectAll: "Select all",
  date: "Date",
  payee: "Payee or payer",
  amount: "Amount",
  account: "Account",
  selectLine: (date, payee, amount) => `Select: ${date} ${payee} ${amount}`,
  confirmSelected: (n) => `Confirm selected (${n})`,

  questionnaire: (year) => `Yearly questions ${year ?? ""}`,
  forwardedMail: "Forwarded email",
  emailInbox: "Email inbox",
  secretsUnreadable: "Stored access keys can no longer be read",
  exchange: "Exchange",
  wallet: "Wallet",
  depotHoldings: "Depot holdings",
  assetPrice: "Price of an investment",
  bankSync: "Bank sync",
  dueOn: (date) => `due on ${date}`,
  deadline: "Deadline",
  privateBill: "Bill paid privately",
  statement: "Statement",
  transaction: "Transaction",
  document: "Document",

  starting: "Starting …",
  fetchNow: "Fetch now",
  openDocument: "Open document",
  openAccounts: "Open accounts",
  reviewProposal: "Review suggestion",
  enterClosing: "Enter closing balance",
  closingTitle: "Closing balance per online banking",
  checkStatement: "Check statement",
  answerQuestions: (year) => `Answer the yearly questions ${year}`,
  viewStatement: "View statement",
  toBankSync: "Go to bank sync",
  openAccount: "Open account",
  uploadWithdrawals: "Upload the withdrawal history",
  addOrFetchWallet: "Add or fetch a wallet",
  importOtherAccount: "Import another account",
  openLine: "Open transaction",
  openReceipt: "Open receipt",
  enterHoldings: "Enter the balance per the exchange",
  answerSingly: "Answer one by one",
  toExchange: "Go to the exchange account",
  toWallet: "Go to the wallet",
  answerOnWallet: "Answer on the wallet",
  uploadSettlement: "Upload a trade confirmation",
  openReport: "Open report",
  toDepot: "Go to the depot",
  enterPrice: "Enter price",
  choosePriceSource: "Choose price source",
  openSecrets: "Open access keys",

  bookingTrades: "Booking …",
  bookFromReport: (n) => `Book transactions from the report (${n})`,
  notFromReport: (names) =>
    `Not booked from the report: ${names} (the holdings would not match the report). For these, please upload the ` +
    "trade confirmations under Documents.",
  and: "and",

  learnDeposit: (from, sentOn, sent, to, receivedOn, received) =>
    `Remember the address ${from} sent ${sent} to on ${sentOn} as the deposit address of ${to} (deposit of ${receivedOn}, ${received}); the app then links further deposits there itself`,
  learnWithdrawal: (from, to, sentOn, sent, receivedOn, received) =>
    `Link withdrawals from ${from} to this address of ${to} automatically from now on (withdrawal of ${sentOn}, ${sent}; receipt of ${receivedOn}, ${received})`,
  rebook: (debit, date, category, card) =>
    `The ${debit ? "debit" : "credit"} of ${date} ` +
    `${category ? `is booked as “${category}” so far and so counts as ${debit ? "spending" : "income"}` : "is not booked yet"}` +
    `; it is booked as a transfer, ${card ? "because the card's transactions are counted already" : "because the money only moves between two own accounts"}.`,
  counterpart: "Matching transaction",
  linking: "Linking …",
  linkDeposit: "Link as deposit",
  linkRebook: "Book as transfer and link",
  link: "Link",
  notTransfer: "Not a transfer – categorise the payment differently",
  ownWallet: "Own wallet",
  pairedWaits: (from, to) =>
    `The app links withdrawals from ${from} to ${to} itself once the wallet's fetched balance contains them. ` +
    "This one waits because the last balance does not contain it yet: fetch the wallet, or assign it here yourself.",
  pairedMany: (from, n) =>
    `Withdrawals from ${from} are learned to ${n} own addresses on this chain. The app cannot tell which one ` +
    "this went to, so it does not link it itself: please assign it here.",
  toOwnWallet: (label, chain, address) => `To own wallet: ${label} (${chain}) · ${address}`,
  walletBooks: "The app books the deposit on the wallet itself: the withdrawn quantity less the exchange's fee, on the day of the withdrawal.",
  rememberPair: (from) => `Link withdrawals from ${from} to this address automatically from now on`,
  booking: "Booking …",
  bookToWallet: "Book to own wallet",

  returnedDebit: "Returned debit",
  cameBack: (text) => `Came back: ${text}`,
  returnCancels: "The debit and the credit cancel out: together they count neither as spending nor as income nor as a transfer.",
  returnRebooked: (category) => ` The debit is booked as “${category}” so far and is re-booked.`,
  bookReturn: "Book as returned payment",
  notReturn: "Not a returned payment – categorise the payment differently",

  taxYear: (year) => ` · tax year ${year}`,
  applying: "Applying …",
  apply: "Apply",
  moving: "Moving …",
  countIn: (year) => `Count in ${year}`,
  yearPaid: "Privately paid Werbungskosten (deductible expenses) count in the year of payment (§ 11 Abs. 2 EStG).",
  uploadReceipt: "Upload receipt",
};

export const inbox = defineMessages({ de, en });
