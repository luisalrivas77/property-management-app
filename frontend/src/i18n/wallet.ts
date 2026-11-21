// A wallet account's screens: components/wallet.tsx (addresses) and components/wallet-cards.tsx
// (sync state, holdings, transaction cards, hidden tokens). The cards' own sentences, questions
// and the address status texts come from the server; only the app's labels are here.
import { defineMessages } from "./core";

export const wallet = defineMessages({
  de: {
    // wallet.tsx
    shapeBitcoin: "Beginnt mit bc1 (SegWit, Taproot), 1 oder 3.",
    shapeEvm: "0x und 40 Hex-Zeichen (0–9, a–f), wie die Wallet sie anzeigt.",
    shapeSolana: "32 bis 44 Zeichen in Base58 (ohne 0, O, I und l).",
    noAddressYet: "noch keine Adresse",
    chainCount: (n: number) => (n === 1 ? "1 Chain" : `${n} Chains`),
    addressCount: (n: number) => (n === 1 ? "1 Adresse" : `${n} Adressen`),
    intro:
      "Wallet: Hausbuch kennt nur ihre öffentlichen Adressen, nie eine Seed-Phrase oder einen privaten Schlüssel, und kann nichts von ihr senden. Die Adressen werden nur gekürzt angezeigt; „kopieren“ legt die volle Adresse in die Zwischenablage, damit Sie sie mit der Wallet vergleichen können.",
    addresses: "Adressen",
    noAddressAddBelow: "Noch keine Adresse. Fügen Sie unten die erste hinzu.",
    bitcoinChange:
      "Eine Bitcoin-Wallet schickt das Wechselgeld einer Zahlung meist an eine neue Adresse. Tragen Sie hier die Adressen ein, die Sie kennen; findet Hausbuch beim Abruf eine Zahlung an eine Adresse, die Ihr Wechselgeld sein könnte, fragt es nach, statt sie als Ausgabe zu zählen.",
    colChain: "Chain",
    colAddress: "Adresse",
    colKind: "Art",
    colStatus: "Stand",
    colActions: "Aktionen",
    retiredTag: "stillgelegt",
    copiedToClipboard: "Volle Adresse in die Zwischenablage kopiert.",
    clipboardBlocked: "Der Browser erlaubt hier keinen Zugriff auf die Zwischenablage; bitte erneut versuchen.",
    notRead: "wird nicht abgerufen",
    copy: "kopieren",
    changeLabel: "Bezeichnung ändern",
    retireAddress: "Adresse stilllegen",
    reactivate: "Wieder aktivieren",
    whatToDo: (todo: string) => `Was tun: ${todo}`,
    keySetting: "Unter Einstellungen › Zugangsschlüssel eintragen, oder als Variable im Scaleway-Container:",
    label: "Bezeichnung",
    labelEmptyHint: "Leer lassen: keine Bezeichnung.",
    addAddress: "Adresse hinzufügen",
    pasteAddress: "Bitte die öffentliche Adresse aus der Wallet einfügen.",
    chainDescription:
      "Die Blockchain, auf der die Adresse liegt. Eine 0x-Adresse einer Hardware-Wallet ist auf Ethereum, Arbitrum und BNB Smart Chain dieselbe; Hausbuch liest sie auf jeder Chain, die Sie eintragen.",
    publicAddress: "Öffentliche Adresse",
    publicAddressDescription:
      "Die Empfangsadresse, wie die Wallet sie anzeigt. Nie die Seed-Phrase (12 oder 24 Wörter) oder einen privaten Schlüssel eingeben: Hausbuch braucht sie nicht und nimmt sie nicht an.",
    labelOptional: "Bezeichnung (optional)",
    labelExample: "z. B. „Tangem Karte 1“",
    labelDescription: "Ein Name, unter dem Sie die Adresse wiedererkennen.",
    alsoEvm: "Auch auf Arbitrum und BNB Smart Chain lesen",
    alsoEvmHint:
      "Legt dieselbe Adresse zusätzlich für Arbitrum und BNB Smart Chain an; jede wird für sich abgerufen. Eine Chain, auf der diese Wallet die Adresse schon hat, wird übersprungen.",
    addedFor: (chains: string) => `Hinzugefügt für ${chains}.`,
    // wallet-cards.tsx: state badges
    stateRetired: "stillgelegt",
    stateKeyMissing: "Schlüssel fehlt",
    stateKeyUnreadable: "Schlüssel nicht lesbar",
    stateSourceUnavailable: "noch nicht lesbar",
    stateNotSynced: "noch nicht abgerufen",
    stateFailed: "Abruf fehlgeschlagen",
    stateThrottled: "später erneut",
    stateRefused: "Zugang abgelehnt",
    stateDeferred: "wird noch gelesen",
    stateReconciled: "stimmt",
    stateWaiting: "wartet auf Antwort",
    statePending: "mit anderer Adresse",
    stateUnreconciled: "weicht ab",
    stateCoinsLeft: "Coins abgegangen",
    // answers
    answerPayment: "Bezahlung",
    answerGift: "Geschenk oder Zahlung erhalten",
    answerReward: "Belohnung",
    answerSpam: "Spam",
    // SyncPanel and holdings
    holdingsTitle: "Bestand laut Blockchain",
    holdingsIntro:
      "Hausbuch liest jede Adresse täglich. Je Coin oder Token steht hier der Bestand, den die Blockchain beim letzten Abruf meldete, neben dem, was Hausbuch aus den gebuchten Bewegungen zählt. Stimmen beide überein, ist die Adresse abgeglichen.",
    noHoldingsYet: "Noch kein Bestand gelesen.",
    starting: "Startet …",
    syncRunning: "Abruf läuft …",
    syncNow: "Jetzt abrufen",
    nothingToSync: "Diese Wallet hat keine aktive Adresse; es gibt nichts abzurufen.",
    syncDone: "Abruf fertig; Bestand und Transaktionen auf dieser Seite sind aktualisiert.",
    syncQueued: (n: number) =>
      `Abruf läuft für ${n === 1 ? "1 Adresse" : `${n} Adressen`}. Das Ergebnis erscheint hier von selbst, sobald er fertig ist.`,
    syncLate:
      "Der Abruf dauert länger als üblich, etwa weil eine Wallet viele Transaktionen hat. Er läuft weiter; das Ergebnis erscheint, wenn Sie diese Seite später wieder öffnen.",
    colCoinToken: "Coin / Token",
    colPerChain: "Laut Blockchain",
    colBooked: "In Hausbuch gebucht",
    colReconciliation: "Abgleich",
    allAddresses: "alle Adressen",
    readAt: (when: string) => `gelesen ${when}`,
    // GroupCards
    reviewTransactions: "Transaktionen prüfen",
    reviewIntro:
      "Diese Transaktionen hat Hausbuch auf der Blockchain gefunden. „Bestätigen“ übernimmt den Vorschlag; stimmt er nicht, wählen Sie, was die Transaktion war.",
    alreadyConfirmed: " (bereits bestätigt)",
    unreadable:
      "Hausbuch kann die Beträge dieser Transaktion nicht lesen. Sie bleibt hier stehen, bis ein späterer Ausbauschritt sie lesen kann; der Abgleich der Adresse zeigt solange, ob etwas fehlt.",
    unstorable: "Diese Transaktion lässt sich nicht als Bewegung speichern; sie ist hier nur zur Kenntnis.",
    confirm: "Bestätigen",
    transferHint:
      "Ging das an eine eigene Börse oder Wallet oder kam es von dort, lassen Sie die Transaktion vorerst offen: Hausbuch verknüpft solche Überträge bald selbst, und eine Antwort hier würde sie als Kauf oder Ausgabe zählen.",
    costLabel: "Kaufpreis in Euro, falls bezahlt (optional)",
    costHint:
      "Für „Geschenk oder Zahlung erhalten“: wurde für die Coins bezahlt (ein Kauf außerhalb einer Börse), hier den Betrag eintragen; er wird ihr Kaufpreis. Sonst leer lassen.",
    // ChangeAnswers
    changeOriginalMissing:
      "Die Daten zu dieser Zahlung wurden beim Abruf nicht gespeichert, deshalb lässt sich die Frage hier nicht beantworten.",
    changeQuestion: "Ging einer dieser Beträge an eine eigene Adresse dieser Wallet (Wechselgeld) oder an einen Empfänger?",
    btcTo: "an",
    changeOwn: "Ja, eigene Adresse",
    changePayee: "Nein, Empfänger",
    // HiddenTokens
    hiddenTokens: (n: number) => `Als Spam verborgene Tokens (${n})`,
    hiddenIntro:
      "Diese Tokens kamen ungefragt an diese Wallet. Hausbuch zählt sie nicht und bewertet sie nicht. Ist einer doch echt, „Kein Spam“ wählen: seine Transaktionen erscheinen dann oben zum Bestätigen.",
    colToken: "Token",
    colQuantity: "Menge",
    notSpam: "Kein Spam",
    // AnsweredGroups
    answeredTransactions: (n: number) => `Beantwortete Transaktionen (${n})`,
    answeredIntro:
      "Hier stehen die Transaktionen, die Sie bestätigt oder beantwortet haben. War eine Antwort falsch, „Antwort zurücknehmen“ wählen: die Transaktion erscheint dann oben wieder zum Prüfen; Ihre frühere Antwort bleibt im Verlauf.",
    undoAnswer: "Antwort zurücknehmen",
    linkedNotUndoable: "mit einem Übertrag verknüpft, nicht zurücknehmbar",
  },
  en: {
    // wallet.tsx
    shapeBitcoin: "Starts with bc1 (SegWit, Taproot), 1 or 3.",
    shapeEvm: "0x and 40 hex characters (0–9, a–f), as the wallet shows it.",
    shapeSolana: "32 to 44 characters in Base58 (without 0, O, I and l).",
    noAddressYet: "no address yet",
    chainCount: (n: number) => (n === 1 ? "1 chain" : `${n} chains`),
    addressCount: (n: number) => (n === 1 ? "1 address" : `${n} addresses`),
    intro:
      "Wallet: Hausbuch knows only its public addresses, never a seed phrase or a private key, and cannot send anything from it. The addresses are only shown shortened; “copy” puts the full address on the clipboard so you can compare it with the wallet.",
    addresses: "Addresses",
    noAddressAddBelow: "No address yet. Add the first one below.",
    bitcoinChange:
      "A Bitcoin wallet usually sends a payment's change to a new address. Enter the addresses you know here; if Hausbuch finds a payment to an address that could be your change when it reads the chain, it asks instead of counting it as spending.",
    colChain: "Chain",
    colAddress: "Address",
    colKind: "Kind",
    colStatus: "Status",
    colActions: "Actions",
    retiredTag: "retired",
    copiedToClipboard: "Full address copied to the clipboard.",
    clipboardBlocked: "The browser does not allow access to the clipboard here; please try again.",
    notRead: "not read",
    copy: "copy",
    changeLabel: "Change label",
    retireAddress: "Retire address",
    reactivate: "Reactivate",
    whatToDo: (todo: string) => `What to do: ${todo}`,
    keySetting: "Enter it under Settings › Access keys, or as a variable in the Scaleway container:",
    label: "Label",
    labelEmptyHint: "Leave empty: no label.",
    addAddress: "Add address",
    pasteAddress: "Please paste the public address from the wallet.",
    chainDescription:
      "The blockchain the address is on. A hardware wallet's 0x address is the same on Ethereum, Arbitrum and BNB Smart Chain; Hausbuch reads it on every chain you enter.",
    publicAddress: "Public address",
    publicAddressDescription:
      "The receiving address as the wallet shows it. Never enter the seed phrase (12 or 24 words) or a private key: Hausbuch does not need them and does not accept them.",
    labelOptional: "Label (optional)",
    labelExample: "e.g. “Tangem card 1”",
    labelDescription: "A name you will recognise the address by.",
    alsoEvm: "Also read on Arbitrum and BNB Smart Chain",
    alsoEvmHint:
      "Adds the same address for Arbitrum and BNB Smart Chain as well; each is read on its own. A chain on which this wallet already has the address is skipped.",
    addedFor: (chains: string) => `Added for ${chains}.`,
    // wallet-cards.tsx: state badges
    stateRetired: "retired",
    stateKeyMissing: "key missing",
    stateKeyUnreadable: "key unreadable",
    stateSourceUnavailable: "not readable yet",
    stateNotSynced: "not read yet",
    stateFailed: "read failed",
    stateThrottled: "again later",
    stateRefused: "access refused",
    stateDeferred: "still being read",
    stateReconciled: "matches",
    stateWaiting: "waiting for an answer",
    statePending: "with another address",
    stateUnreconciled: "differs",
    stateCoinsLeft: "coins left",
    // answers
    answerPayment: "Payment",
    answerGift: "Gift or payment received",
    answerReward: "Reward",
    answerSpam: "Spam",
    // SyncPanel and holdings
    holdingsTitle: "Holdings according to the blockchain",
    holdingsIntro:
      "Hausbuch reads every address daily. For each coin or token, this shows the holding the blockchain reported at the last read, beside what Hausbuch counts from the booked movements. When both agree, the address is reconciled.",
    noHoldingsYet: "No holdings read yet.",
    starting: "Starting …",
    syncRunning: "Reading …",
    syncNow: "Read now",
    nothingToSync: "This wallet has no active address; there is nothing to read.",
    syncDone: "Read finished; the holdings and transactions on this page are up to date.",
    syncQueued: (n: number) =>
      `Reading ${n === 1 ? "1 address" : `${n} addresses`}. The result appears here by itself as soon as it is finished.`,
    syncLate:
      "The read is taking longer than usual, for example because a wallet has many transactions. It carries on; the result appears when you open this page again later.",
    colCoinToken: "Coin / token",
    colPerChain: "According to the blockchain",
    colBooked: "Booked in Hausbuch",
    colReconciliation: "Reconciliation",
    allAddresses: "all addresses",
    readAt: (when: string) => `read ${when}`,
    // GroupCards
    reviewTransactions: "Review transactions",
    reviewIntro:
      "Hausbuch found these transactions on the blockchain. “Confirm” accepts the suggestion; if it is wrong, choose what the transaction was.",
    alreadyConfirmed: " (already confirmed)",
    unreadable:
      "Hausbuch cannot read the amounts of this transaction. It stays here until a later version can read it; meanwhile the address's reconciliation shows whether anything is missing.",
    unstorable: "This transaction cannot be stored as a movement; it is shown here for information only.",
    confirm: "Confirm",
    transferHint:
      "If this went to or came from your own exchange or wallet, leave the transaction open for now: Hausbuch will soon link such transfers itself, and an answer here would count it as a purchase or spending.",
    costLabel: "Purchase price in euros, if paid (optional)",
    costHint:
      "For “Gift or payment received”: if the coins were paid for (a purchase outside an exchange), enter the amount here; it becomes their purchase price. Otherwise leave it empty.",
    // ChangeAnswers
    changeOriginalMissing:
      "The data for this payment was not stored when it was read, so the question cannot be answered here.",
    changeQuestion: "Did one of these amounts go to an address of this wallet (change) or to a recipient?",
    btcTo: "to",
    changeOwn: "Yes, own address",
    changePayee: "No, recipient",
    // HiddenTokens
    hiddenTokens: (n: number) => `Tokens hidden as spam (${n})`,
    hiddenIntro:
      "These tokens arrived at this wallet unasked. Hausbuch neither counts nor values them. If one is real after all, choose “Not spam”: its transactions then appear above to be confirmed.",
    colToken: "Token",
    colQuantity: "Quantity",
    notSpam: "Not spam",
    // AnsweredGroups
    answeredTransactions: (n: number) => `Answered transactions (${n})`,
    answeredIntro:
      "These are the transactions you confirmed or answered. If an answer was wrong, choose “Undo answer”: the transaction then appears above again for review; your earlier answer stays in the history.",
    undoAnswer: "Undo answer",
    linkedNotUndoable: "linked to a transfer, cannot be undone",
  },
});
