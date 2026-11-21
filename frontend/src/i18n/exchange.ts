// An exchange account's page and its cards in Zu prüfen (components/exchange.tsx, phase 6 D166,
// D178, D261). German first, English beside it (glossary.md). The texts the server writes (a
// rule code's text, a movement's question) come through api/errors.ts.
import { defineMessages } from "./core";

const de = {
  saving: "Speichert …",

  // the export upload
  uploadExport: "Export der Börse hochladen",
  readingIn: "Liest ein …",
  binanceHint:
    "Binance: zuerst den Transaktionsverlauf („Transaction History“), danach den Auszahlungsverlauf („Withdrawal History“), " +
    "beide als CSV oder als die ZIP-Datei, in der Binance sie schickt. Den Spot-Handelsverlauf braucht Hausbuch nicht. " +
    "Hausbuch liest die Dateien selbst, ohne KI; die Nutzer-ID und die Zieladressen der Auszahlungen speichert es nicht. " +
    "Jedes andere Dokument kommt unter Dokumente, und die KI liest es. Datei hierher ziehen oder auswählen.",
  bisonHint:
    "BISON: der Export der Transaktionen als CSV aus der App (Einstellungen, Steuern und Exporte). Hausbuch liest ihn " +
    "selbst, ohne KI. Jedes andere Dokument kommt unter Dokumente, und die KI liest es. Datei hierher ziehen oder auswählen.",
  notAnExport: "kein Export der Börse, als Dokument übernommen.",
  historyDone: (fileName: string, done: number) =>
    `Auszahlungsverlauf ${fileName}: ${done === 1 ? "1 Auszahlung ergänzt" : `${done} Auszahlungen ergänzt`} (Netzwerk und Transaktions-Hash).`,
  historyOpen: (open: number, reason: string) =>
    ` ${open === 1 ? "1 Auszahlung passt" : `${open} Auszahlungen passen`} zu keinem Eintrag: ${reason}`,
  alreadyRead: (fileName: string) =>
    `Bereits eingelesen: ${fileName}. Diese Datei wurde schon einmal hochgeladen; es wurde nichts hinzugefügt.`,
  readIn: (fileName: string, rowsNew: number, skipped: number) =>
    `Eingelesen: ${fileName} · ${rowsNew === 1 ? "1 neue Bewegung" : `${rowsNew} neue Bewegungen`}` +
    `${skipped > 0 ? `, ${skipped} schon vorhanden` : ""}. Als Nächstes unten „Bestand laut Börse eingeben“.`,

  // the page's sections
  holdings: "Bestand",
  imports: "Eingelesene Exporte",
  movements: "Bewegungen",

  // the to-dos
  reuploadTransactions: "Transaktionsverlauf erneut hochladen",
  zoneUnknown:
    " Bitte den Transaktionsverlauf noch einmal so hochladen, wie Binance ihn liefert (die ZIP-Datei mit unverändertem Namen): " +
    "Hausbuch liest die Zeitzone aus dem Dateinamen und verschiebt die schon eingelesenen Zeiten, Verknüpfungen und Antworten " +
    "bleiben erhalten.",
  uploadTransactions: "Transaktionsverlauf hochladen",
  uploadWithdrawals: "Auszahlungsverlauf hochladen",
  withdrawalsOpen: (n: number) =>
    n === 1
      ? "1 Auszahlung von Coins hat noch kein Netzwerk und keinen Transaktions-Hash."
      : `${n} Auszahlungen von Coins haben noch kein Netzwerk und keinen Transaktions-Hash.`,
  historyWhy:
    "Binance nennt beides nur im Auszahlungsverlauf („Withdrawal History“). Ohne ihn lässt sich später nicht zeigen, dass die " +
    "Coins auf eine eigene Wallet gingen und wann sie gekauft wurden.",
  withdrawalNotFound: "Auszahlung nicht im Auszahlungsverlauf gefunden",
  noMatchHint:
    " Fehlt die Auszahlung im Verlauf (anderer Zeitraum, noch nicht abgeschlossen), bitte einen Verlauf hochladen, der sie enthält.",
  uploadOtherWithdrawals: "Anderen Auszahlungsverlauf hochladen",
  enterHoldings: "Bestand laut Börse eingeben",
  exportUntil: (day: string) => ` · Export bis ${day}`,
  holdingsWhy: (day: string) =>
    `Damit Hausbuch prüfen kann, dass der Export vollständig ist: je Coin den Bestand, den die Börse zum ${day} zeigt.`,
  answerMovements: "Bewegungen beantworten",
  toAnswer: (n: number) =>
    `${n === 1 ? "1 Bewegung wartet auf eine Antwort" : `${n} Bewegungen warten auf eine Antwort`}` +
    ": unten unter „Bewegungen“, oder für alle Auszahlungen eines Exports auf einmal unter „Eingelesene Exporte“.",

  // the reconciliation
  agrees: "stimmt",
  differs: "weicht ab",
  notEntered: "noch nicht eingegeben",
  withinRounding: (cents: number) => `innerhalb der Rundung (${cents < 1 ? "unter 1 Cent" : `${Math.round(cents)} Cent`})`,
  noExport: "Noch kein Export eingelesen.",
  coin: "Coin",
  perExport: "Laut Export",
  perExchange: "Laut Börse",
  reconciliation: "Abgleich",
  belowZero: "Weniger als null: ein Teil der Käufe fehlt im Export.",
  onDay: (date: string) => `am ${date}`,

  // the imports
  period: "Zeitraum",
  status: "Status",
  importedOn: (when: string) => `eingelesen ${when}`,
  newRows: (n: number) => (n === 1 ? "1 neue" : `${n} neue`),
  alreadyThere: (n: number) => `, ${n} schon vorhanden`,
  holdingsMissing: "Bestand fehlt",
  typedVsHeld: (typed: string, held: string) => `: laut Börse ${typed}, laut Export ${held}`,
  wrongHint:
    "Fehlt ein Teil des Exports, oder wurde ein Bestand falsch abgelesen? Einen vollständigen Export hochladen oder den Bestand " +
    "neu eingeben; Hausbuch gleicht nie selbst aus.",
  enterAgain: "Bestand neu eingeben",

  // „Bestand laut Börse eingeben“
  holdingsTitle: "Bestand laut Börse",
  enterHolding: (symbol: string) => `Bitte den Bestand an ${symbol} eingeben (0, wenn keiner).`,
  holdingsIntro: (day: string) =>
    `Je Coin der Bestand, den die Börse nach der letzten Bewegung dieses Exports zeigt (${day}), wie in der App ` +
    "oder auf der Website der Börse. Hausbuch vergleicht ihn mit dem, was die Bewegungen des Exports ergeben.",
  symbolPerExchange: (symbol: string) => `${symbol} laut Börse`,
  heldPerExport: (held: string) => `Laut Export: ${held}`,
  unitsHint: "Die Stückzahl, nicht der Wert in Euro. Komma oder Punkt als Dezimalzeichen, z. B. 0,00009965.",
  reconcile: "Abgleichen",

  // „Differenz als Korrektur buchen“
  bookCorrection: "Differenz als Korrektur buchen",
  symbolCorrection: (symbol: string) => `${symbol}: Differenz als Korrektur buchen`,
  reasonTooShort: "Bitte in ein paar Worten den Grund eintragen (mindestens 3 Zeichen).",
  correctionFigures: (typed: string, held: string, difference: string, symbol: string) =>
    `: laut Börse ${typed}, laut Export ${held}, Differenz ${difference} ${symbol}.`,
  correctionWhen:
    "Das passt, wenn im Export der Börse eine Bewegung fehlt, die die Börse selbst in keinem Export aufführt (zum Beispiel " +
    "eine kleine Gutschrift), und der Bestand richtig abgelesen ist. Fehlt dagegen ein Teil des Exports oder wurde der Bestand " +
    "falsch abgelesen, bitte den vollständigen Export hochladen oder den Bestand neu eingeben.",
  correctionHow: (difference: string, symbol: string, day: string) =>
    `Hausbuch bucht die Differenz einmal als Korrektur von ${difference} ${symbol} zum ${day}, mit dem ` +
    "Grund, den Sie hier eintragen. Sie steht danach unter „Bewegungen“ als „Korrektur (von Hand)“. Rückgängig machen lässt " +
    "sie sich nur durch eine Gegenkorrektur: den Bestand neu eingeben und die neue Differenz buchen.",
  reason: "Grund",
  reasonHint: "z. B. Gutschrift fehlt im Export",
  bookCorrectionButton: "Korrektur buchen",
  checkExport: "Export oder Bestand prüfen",

  // withdrawals and answers
  allWithdrawalsN: (n: number) => `Alle ${n} Auszahlungen gingen an eigene Wallets`,
  oneWithdrawal: "Die Auszahlung ging an eine eigene Wallet",
  allWithdrawals: "Alle Auszahlungen gingen an eigene Wallets",
  learnedDeposit: "die gemerkte Einzahlungsadresse",
  learnedWithdrawal: "die gemerkte Wallet für Abhebungen",
  releaseQuestion: (learned: string) =>
    `Diese automatische Verknüpfung lösen? Beide Seiten warten dann wieder in „Zu prüfen“, und ${learned} wird vergessen, damit die App sie nicht wieder verknüpft.`,
  linkedAutomatically: "Automatisch verknüpft (gemerkte Adresse)",
  releasing: "Löst …",
  release: "Verknüpfung lösen",

  // the movements
  noMovements: "Noch keine Bewegungen.",
  date: "Datum",
  kind: "Art",
  quantity: "Menge",
  euro: "Euro",
  network: (network: string) => `Netzwerk ${network}`,
  noteReason: (note: string) => `Grund: ${note}`,
  fee: (quantity: string, symbol: string) => `Gebühr ${quantity} ${symbol}`,
  confirmed: "bestätigt",

  // learned addresses
  learnedAddresses: "Gelernte Adressen",
  learnedHint: "Überträge an diese Adressen und von hier an diese Wallets verknüpft die App selbst, wenn genau eine Bewegung passt.",
  networkHeading: "Netzwerk",
  address: "Adresse",
  since: "Seit",
  depositAddress: "Einzahlungsadresse",
  withdrawalsTo: (wallet: string | null | undefined) => `Abhebungen an ${wallet ?? "eigene Wallet"}`,
  forgetting: "Vergisst …",
  forget: "Vergessen",
};

type Messages = typeof de;

const en: Messages = {
  saving: "Saving …",

  uploadExport: "Upload the exchange's export",
  readingIn: "Reading …",
  binanceHint:
    "Binance: first the transaction history (“Transaction History”), then the withdrawal history (“Withdrawal History”), " +
    "both as CSV or as the ZIP file Binance sends them in. Hausbuch does not need the spot trade history. " +
    "Hausbuch reads the files itself, without AI; it does not store the user ID or the withdrawals' destination addresses. " +
    "Any other document goes to Documents, and the AI reads it. Drag a file here or choose one.",
  bisonHint:
    "BISON: the export of the transactions as CSV from the app (Settings, Taxes and exports). Hausbuch reads it " +
    "itself, without AI. Any other document goes to Documents, and the AI reads it. Drag a file here or choose one.",
  notAnExport: "not an export of the exchange, kept as a document.",
  historyDone: (fileName, done) =>
    `Withdrawal history ${fileName}: ${done === 1 ? "1 withdrawal completed" : `${done} withdrawals completed`} (network and transaction hash).`,
  historyOpen: (open, reason) => ` ${open === 1 ? "1 withdrawal fits" : `${open} withdrawals fit`} no entry: ${reason}`,
  alreadyRead: (fileName) => `Already read: ${fileName}. This file was uploaded before; nothing was added.`,
  readIn: (fileName, rowsNew, skipped) =>
    `Read: ${fileName} · ${rowsNew === 1 ? "1 new movement" : `${rowsNew} new movements`}` +
    `${skipped > 0 ? `, ${skipped} already there` : ""}. Next, “Enter the balance per the exchange” below.`,

  holdings: "Holdings",
  imports: "Exports read",
  movements: "Movements",

  reuploadTransactions: "Upload the transaction history again",
  zoneUnknown:
    " Please upload the transaction history again as Binance delivers it (the ZIP file with its name unchanged): " +
    "Hausbuch reads the time zone from the file name and moves the times already read; links and answers are kept.",
  uploadTransactions: "Upload the transaction history",
  uploadWithdrawals: "Upload the withdrawal history",
  withdrawalsOpen: (n) =>
    n === 1
      ? "1 coin withdrawal has no network and no transaction hash yet."
      : `${n} coin withdrawals have no network and no transaction hash yet.`,
  historyWhy:
    "Binance names both only in the withdrawal history (“Withdrawal History”). Without it, it cannot be shown later that the " +
    "coins went to an own wallet and when they were bought.",
  withdrawalNotFound: "Withdrawal not found in the withdrawal history",
  noMatchHint:
    " If the withdrawal is missing from the history (another period, not completed yet), please upload a history that contains it.",
  uploadOtherWithdrawals: "Upload another withdrawal history",
  enterHoldings: "Enter the balance per the exchange",
  exportUntil: (day) => ` · export up to ${day}`,
  holdingsWhy: (day) =>
    `So that Hausbuch can check the export is complete: for each coin, the balance the exchange shows on ${day}.`,
  answerMovements: "Answer movements",
  toAnswer: (n) =>
    `${n === 1 ? "1 movement is waiting for an answer" : `${n} movements are waiting for an answer`}` +
    ": below under “Movements”, or for every withdrawal of an export at once under “Exports read”.",

  agrees: "agrees",
  differs: "differs",
  notEntered: "not entered yet",
  withinRounding: (cents) => `within rounding (${cents < 1 ? "under 1 cent" : `${Math.round(cents)} cents`})`,
  noExport: "No export read yet.",
  coin: "Coin",
  perExport: "Per export",
  perExchange: "Per exchange",
  reconciliation: "Check",
  belowZero: "Below zero: some of the purchases are missing from the export.",
  onDay: (date) => `on ${date}`,

  period: "Period",
  status: "Status",
  importedOn: (when) => `read ${when}`,
  newRows: (n) => `${n} new`,
  alreadyThere: (n) => `, ${n} already there`,
  holdingsMissing: "Balance missing",
  typedVsHeld: (typed, held) => `: per exchange ${typed}, per export ${held}`,
  wrongHint:
    "Is part of the export missing, or was a balance misread? Upload a complete export or enter the balance again; " +
    "Hausbuch never balances it out by itself.",
  enterAgain: "Enter the balance again",

  holdingsTitle: "Balance per the exchange",
  enterHolding: (symbol) => `Please enter the balance of ${symbol} (0 if none).`,
  holdingsIntro: (day) =>
    `For each coin, the balance the exchange shows after this export's last movement (${day}), as in the exchange's app ` +
    "or website. Hausbuch compares it with what the export's movements add up to.",
  symbolPerExchange: (symbol) => `${symbol} per exchange`,
  heldPerExport: (held) => `Per export: ${held}`,
  unitsHint: "The number of units, not the value in euros. Comma or point as the decimal separator, e.g. 0.00009965.",
  reconcile: "Check",

  bookCorrection: "Book the difference as a correction",
  symbolCorrection: (symbol) => `${symbol}: book the difference as a correction`,
  reasonTooShort: "Please enter the reason in a few words (at least 3 characters).",
  correctionFigures: (typed, held, difference, symbol) =>
    `: per exchange ${typed}, per export ${held}, difference ${difference} ${symbol}.`,
  correctionWhen:
    "This fits when the exchange's export is missing a movement the exchange itself lists in no export (for example " +
    "a small credit), and the balance was read correctly. If instead part of the export is missing or the balance was " +
    "misread, please upload the complete export or enter the balance again.",
  correctionHow: (difference, symbol, day) =>
    `Hausbuch books the difference once as a correction of ${difference} ${symbol} on ${day}, with the ` +
    "reason you enter here. It then appears under “Movements” as “Correction (by hand)”. It can only be undone " +
    "by a counter-correction: enter the balance again and book the new difference.",
  reason: "Reason",
  reasonHint: "e.g. credit missing from the export",
  bookCorrectionButton: "Book correction",
  checkExport: "Check the export or balance",

  allWithdrawalsN: (n) => `All ${n} withdrawals went to own wallets`,
  oneWithdrawal: "The withdrawal went to an own wallet",
  allWithdrawals: "All withdrawals went to own wallets",
  learnedDeposit: "the remembered deposit address",
  learnedWithdrawal: "the remembered wallet for withdrawals",
  releaseQuestion: (learned) =>
    `Undo this automatic link? Both sides then wait in “To review” again, and ${learned} is forgotten so the app does not link them again.`,
  linkedAutomatically: "Linked automatically (remembered address)",
  releasing: "Undoing …",
  release: "Undo link",

  noMovements: "No movements yet.",
  date: "Date",
  kind: "Kind",
  quantity: "Quantity",
  euro: "Euro",
  network: (network) => `Network ${network}`,
  noteReason: (note) => `Reason: ${note}`,
  fee: (quantity, symbol) => `Fee ${quantity} ${symbol}`,
  confirmed: "confirmed",

  learnedAddresses: "Learned addresses",
  learnedHint: "The app links transfers to these addresses and from here to these wallets itself when exactly one movement fits.",
  networkHeading: "Network",
  address: "Address",
  since: "Since",
  depositAddress: "Deposit address",
  withdrawalsTo: (wallet) => `Withdrawals to ${wallet ?? "own wallet"}`,
  forgetting: "Forgetting …",
  forget: "Forget",
};

export const exchange = defineMessages({ de, en });
