// The Bankabruf card on an account page (components/bankSync.tsx: the FinTS connection, its PIN,
// the switches, the runs). German first, English beside it (glossary.md). A PIN is never part
// of any text here; only the labels around it are. The bank's answer codes of a failed run are
// explained by the error texts (i18n/errors.ts), not here.
import { defineMessages } from "./core";

export const bankSync = defineMessages({
  de: {
    title: "Bankabruf",
    intro:
      "Die PIN Ihres Online-Bankings speichert Hausbuch nur verschlüsselt und zeigt sie nie wieder an. Die Produkt-ID steht unter Einstellungen › Zugangsschlüssel oder in der Serverkonfiguration.",
    autoSync: "Täglich automatisch abrufen",
    fetchStatements: "Kontoauszüge als PDF abrufen",
    starting: "Startet …",
    running: "Abruf läuft …",
    syncAgain: "Erneut abrufen",
    syncNow: "Jetzt abrufen",
    changeLogin: "Zugang ändern",
    dialogTitle: (label: string) => `Bankabruf: ${label}`,
    // --- the PIN ---
    pinRetypeLead: "Bitte die PIN einmal neu eingeben",
    pinRetypeText:
      ": Hausbuch sendet eine gespeicherte PIN nur an die FinTS-Adresse, für die sie eingegeben wurde. Eine PIN, die vor dem 16.11.2026 eingegeben wurde, nennt noch keine Adresse; sonst wurde die FinTS-Adresse dieses Zugangs seither geändert. „PIN ändern“ wählen und die PIN für diese Adresse neu eingeben.",
    pinSavedInApp: (date: string) => `PIN in der App gespeichert am ${date} (verschlüsselt).`,
    pinUnreadableLead: "Die gespeicherte PIN lässt sich nicht mehr lesen",
    pinUnreadableText: ": der Schlüssel des Servers wurde gewechselt. Bitte „PIN ändern“ wählen und die PIN neu eingeben.",
    pinOnServerBefore: "PIN auf dem Server gesetzt (",
    pinOnServerAfter: ").",
    noPinYet: "Noch keine PIN hinterlegt.",
    pinLabel: "PIN (Online-Banking)",
    pinHint:
      "Die PIN, mit der Sie sich im Online-Banking anmelden. Hausbuch speichert sie verschlüsselt und zeigt sie nie wieder an, auch Ihnen nicht.",
    pinLength: "Die PIN hat 5 bis 64 Zeichen.",
    savePin: "PIN speichern",
    afterDeleteServer: "Danach nutzt der Abruf wieder die PIN auf dem Server.",
    afterDeleteNone: "Danach endet jeder Abruf mit „PIN fehlt“, bis wieder eine PIN gespeichert ist.",
    confirmDeletePin: (slot: string, after: string) =>
      `Die in Hausbuch gespeicherte PIN für den Zugang „${slot}“ löschen? ${after}`,
    pin: "PIN",
    pinNoneAfter:
      " Hausbuch speichert die PIN verschlüsselt und zeigt sie danach nie wieder an. Ohne PIN endet jeder Abruf mit „PIN fehlt“.",
    pinBeforeServer: " Sie gilt vor der PIN auf dem Server.",
    changePin: "PIN ändern",
    deletePin: "PIN löschen",
    savePinInApp: "PIN in der App speichern",
    unusedPins: "Gespeicherte PINs ohne Bankzugang",
    noConnectionUses: "Kein Bankzugang nutzt sie.",
    unusedPinsExplain:
      "Diese PINs sind in Hausbuch gespeichert, aber kein Bankzugang trägt ihren Namen, etwa nach einer abgelehnten Einrichtung oder einem umbenannten Zugang. Hausbuch löscht sie nie von selbst; ein Zugang mit diesem Namen würde sie wieder nutzen.",
    unusedPin: (slot: string, date: string) => `Zugang „${slot}“, gespeichert am ${date}`,
    unreadable: " (nicht mehr lesbar)",
    // --- the connection's facts ---
    factBankCode: "BLZ",
    factBankName: "Bank (laut FinTS)",
    factUrl: "FinTS-Adresse",
    factLogin: "Anmeldename",
    loginUnreadableLead: "lässt sich nicht mehr lesen",
    loginUnreadableText:
      ": der Schlüssel des Servers wurde gewechselt. „Zugang ändern“ wählen, den Anmeldenamen neu eingeben.",
    factSlot: "Zugang",
    quotedSlot: (slot: string) => `„${slot}“`,
    factApproval: "Freigabe",
    approvalLast: (last: string, until: string) => `zuletzt am ${last}, gültig bis etwa ${until} (geschätzt)`,
    approvalNone: (approval: string) =>
      `noch keine: beim ersten Abruf fragt die Bank nach einer Freigabe („${approval}“)`,
    factLastSync: "Letzter Abruf",
    linesUntil: (date: string) => `, Umsätze bis ${date}`,
    noSyncYet: "noch keiner",
    // --- the switches ---
    autoSyncHint:
      "Hausbuch fragt die Bank jeden Morgen gegen halb acht (im Sommer gegen halb neun) und holt nur die Umsätze seit dem letzten Abruf, zur Sicherheit ab zwei Wochen davor; schon vorhandene erkennt Hausbuch und bucht sie nicht doppelt. Verlangt die Bank dafür einmal eine Freigabe, erscheint unter „Zu prüfen“ eine Karte; bis dahin ruft Hausbuch dieses Konto nicht automatisch ab, damit keine weiteren Anfragen aufs Handy kommen.",
    autoSyncWaits:
      "Noch nicht möglich: diese Bank (DKB) fragt womöglich bei jedem Abruf nach einer Freigabe in der DKB-App, und dann käme jeden Morgen eine Anfrage aufs Handy. Der tägliche Abruf lässt sich einschalten, sobald zwei Abrufe von Hand nacheinander ohne Freigabe durchgelaufen sind. Bis dahin „Jetzt abrufen“ von Hand nutzen oder den CSV-Export hochladen.",
    fetchStatementsHint:
      "Bei jedem Abruf holt Hausbuch nach den Umsätzen auch die Kontoauszüge der Bank als PDF, die noch fehlen, nach ihrer Nummer. Sie liegen dann unten in der Liste und im Jahresarchiv unter „kontoauszuege/“, als Nachweis, den das Finanzamt kennt. Gebucht wird weiter nur aus den Umsätzen; ein Auszug ändert keine Buchung. Hausbuch fragt nur nach Auszügen, die die Bank schon erstellt hat, und fordert nie einen neuen an.",
    // --- the PDF statements ---
    refusal9370: "Signaturberechtigung reicht nicht aus",
    refusal9380: "keine Auftragsberechtigung",
    refusalCode: (code: string) => `Code ${code}`,
    refusalCodeMeaning: (code: string, meaning: string) => `Code ${code}: ${meaning}`,
    statementsRefused: (bankName: string | null, code: string) => {
      const bank = bankName ?? "Bank";
      return `Die ${bank} erlaubt diesem Online-Banking-Zugang den Abruf der Kontoauszüge als PDF nicht (${code}). Freischalten kann das nur die ${bank}: sie bitten, für diesen Zugang den Abruf elektronischer Kontoauszüge als PDF über FinTS/HBCI (HKEKP) für dieses Konto freizuschalten. Das elektronische Postfach allein genügt nicht. Bis dahin diesen Schalter ausschalten; die Zahlungsnachweise im Jahresarchiv erstellt Hausbuch ohnehin. Sobald der Abruf klappt, verschwindet dieser Hinweis.`;
    },
    bankStatements: "Kontoauszüge der Bank (PDF)",
    noStatementsYet:
      "Noch keiner abgerufen. Beim nächsten Abruf fragt Hausbuch die Bank nach ihren Kontoauszügen als PDF.",
    fetchedLast: (count: number) =>
      `Beim letzten Abruf ${count === 1 ? "1 Kontoauszug" : `${count} Kontoauszüge`} neu abgerufen.`,
    statementNumber: (number: number, year: number) => `Kontoauszug ${number}/${year}`,
    fetchedAt: (when: string) => `abgerufen ${when}`,
    // --- the runs ---
    approvalSent: " Die Bank hat eine Freigabe für den Abruf an Ihr Handy geschickt.",
    timeLeft: (clock: string) => `Noch ${clock} Minuten; danach bricht Hausbuch den Abruf ab.`,
    approvalExpired:
      "Die Freigabe wurde nicht innerhalb von fünf Minuten bestätigt. „Erneut abrufen“ drücken und die neue Anfrage in der App freigeben.",
    lastScheduled: "Letzter automatischer Abruf",
    lastManual: "Letzter Abruf",
    stuck: (when: string) =>
      `Der Abruf vom ${when} kommt nicht voran, zum Beispiel nach einem Neustart des Servers. „Erneut abrufen“ drücken, um ihn neu zu starten.`,
    scheduledNeedsApproval: (approval: string) =>
      `Der automatische Abruf braucht eine Freigabe: „Jetzt abrufen“ drücken. ${approval}.`,
    scheduledAsked: (when: string) =>
      `Die Bank hat beim Abruf am ${when} nach einer Freigabe gefragt; Hausbuch hat ihn beendet, weil niemand aufs Handy schaut. „Jetzt abrufen“ drücken und die neue Anfrage gleich in der App freigeben. Bis dahin ruft Hausbuch dieses Konto nicht automatisch ab.`,
    queued: "Der Abruf ist angestoßen und startet gleich …",
    fetching: "Hausbuch fragt die Bank nach den Umsätzen …",
    importing: "Die Umsätze der Bank werden importiert …",
    doneLine: (last: string, when: string, lines: string) => `${last} ${when}: ${lines} importiert.`,
    showNewLines: "Die neuen Umsätze anzeigen",
    nothingNew: (last: string, when: string) => `${last} ${when}: nichts Neues, alle Umsätze der Bank waren schon da.`,
    balanceDrift:
      " Der Kontostand der Bank weicht aber vom letzten abgeglichenen Saldo ab: fehlt ein Kontoauszug? Siehe „Zu prüfen“.",
    balanceMatches: " Der Kontostand der Bank passt zum letzten abgeglichenen Saldo.",
    failedLine: (last: string, when: string) => `${last} ${when} fehlgeschlagen:`,
    failedFallback: "Der Abruf ist fehlgeschlagen. Erneut abrufen.",
    resultFailedCode: (code: string) => `fehlgeschlagen (${code})`,
    resultFailed: "fehlgeschlagen",
    resultDrift: "nichts Neues, Kontostand weicht ab",
    balanceCompared: "mit dem Kontostand der Bank verglichen",
    recentRuns: "Letzte Abrufe",
    colStarted: "Gestartet",
    colPeriod: "Zeitraum",
    colResult: "Ergebnis",
    colBalance: "Saldo",
    // --- the connection's forms ---
    bankCodeHint:
      "8 Ziffern: in Ihrer IBAN die Stellen 5 bis 12, nach „DE“ und den zwei Prüfziffern. Sie können auch die ganze IBAN einfügen.",
    urlHint:
      "Beginnt mit https://. Ihre Sparkasse nennt sie auf ihrer Website unter „FinTS“ oder „HBCI PIN/TAN“; sonst steht sie in der Bankenliste der Deutschen Kreditwirtschaft.",
    loginHint:
      "Der Anmeldename oder die Legitimations-ID, mit dem Sie sich im Online-Banking anmelden. Hausbuch speichert ihn verschlüsselt und zeigt ihn nur gekürzt an.",
    slotHint:
      "Ein kurzer Name für diesen Online-Banking-Zugang, klein geschrieben, z. B. „dkb“ oder „sparkasse_b“; „primary“ ist der Zugang des Mietkontos. Konten unter demselben Zugang tragen denselben Namen und nutzen dieselbe PIN.",
    slotInvalid: "Kleinbuchstaben, Ziffern und _ , beginnend mit einem Buchstaben, höchstens 32 Zeichen (z. B. dkb).",
    urlRequired: "Bitte die FinTS-Adresse Ihrer Bank eingeben.",
    urlHttps: "Die FinTS-Adresse muss mit https:// beginnen.",
    bankCodeInvalid: "Die BLZ hat genau 8 Ziffern.",
    loginRequired: "Bitte den Anmeldenamen eingeben.",
    setUp: "Bankabruf einrichten",
    setUpIntro:
      "Hausbuch kann die Umsätze dieses Kontos selbst bei der Bank abrufen (FinTS, nur lesen). Beim ersten Abruf geben Sie ihn in der App Ihrer Bank frei (Sparkasse: S-pushTAN-App, DKB: DKB-App).",
    slotLabel: "Name des Zugangs",
    pinNoneNote:
      "Die PIN wird verschlüsselt gespeichert und danach nie wieder angezeigt. Sie können das Feld auch leer lassen und die PIN später speichern; ohne PIN endet jeder Abruf mit „PIN fehlt“.",
    loginRetype: "Bitte den Anmeldenamen neu eingeben: der gespeicherte lässt sich nicht mehr lesen.",
    editNote: (approval: string) =>
      `Eine neue Adresse oder ein neuer Anmeldename verwirft die gespeicherte Verbindung: der nächste Abruf fragt wieder nach einer Freigabe („${approval}“). Ein neuer Name des Zugangs ändert nur, welche PIN der Abruf nutzt. Die PIN selbst ändern Sie auf der Karte mit „PIN ändern“.`,
    newLogin: "Neuer Anmeldename",
    keepLogin: (masked: string) => `Leer lassen, um ${masked} zu behalten.`,
    loginUnreadableHint:
      "Der gespeicherte Anmeldename lässt sich nicht mehr lesen (der Schlüssel des Servers wurde gewechselt); bitte neu eingeben.",
  },
  en: {
    title: "Bank sync",
    intro:
      "Hausbuch stores your online banking PIN only encrypted and never shows it again. The product id is under Settings › Access keys or in the server configuration.",
    autoSync: "Sync automatically every day",
    fetchStatements: "Fetch statements as PDF",
    starting: "Starting …",
    running: "Sync running …",
    syncAgain: "Sync again",
    syncNow: "Sync now",
    changeLogin: "Change login",
    dialogTitle: (label: string) => `Bank sync: ${label}`,
    pinRetypeLead: "Please enter the PIN once more",
    pinRetypeText:
      ": Hausbuch sends a stored PIN only to the FinTS address it was entered for. A PIN entered before 16 Nov 2026 names no address yet; otherwise this login's FinTS address has changed since. Choose “Change PIN” and enter the PIN for this address again.",
    pinSavedInApp: (date: string) => `PIN stored in the app on ${date} (encrypted).`,
    pinUnreadableLead: "The stored PIN can no longer be read",
    pinUnreadableText: ": the server's key was changed. Please choose “Change PIN” and enter the PIN again.",
    pinOnServerBefore: "PIN set on the server (",
    pinOnServerAfter: ").",
    noPinYet: "No PIN stored yet.",
    pinLabel: "PIN (online banking)",
    pinHint:
      "The PIN you sign in to online banking with. Hausbuch stores it encrypted and never shows it again, not even to you.",
    pinLength: "The PIN has 5 to 64 characters.",
    savePin: "Save PIN",
    afterDeleteServer: "The sync then uses the PIN on the server again.",
    afterDeleteNone: "Every sync then ends with “PIN missing” until a PIN is stored again.",
    confirmDeletePin: (slot: string, after: string) => `Delete the PIN stored in Hausbuch for the login “${slot}”? ${after}`,
    pin: "PIN",
    pinNoneAfter:
      " Hausbuch stores the PIN encrypted and never shows it again afterwards. Without a PIN every sync ends with “PIN missing”.",
    pinBeforeServer: " It takes precedence over the PIN on the server.",
    changePin: "Change PIN",
    deletePin: "Delete PIN",
    savePinInApp: "Store PIN in the app",
    unusedPins: "Stored PINs without a bank login",
    noConnectionUses: "No bank login uses it.",
    unusedPinsExplain:
      "These PINs are stored in Hausbuch, but no bank login carries their label, for instance after a refused setup or a renamed login. Hausbuch never deletes them by itself; a login with this label would use them again.",
    unusedPin: (slot: string, date: string) => `Login “${slot}”, stored on ${date}`,
    unreadable: " (no longer readable)",
    factBankCode: "BLZ (bank code)",
    factBankName: "Bank (per FinTS)",
    factUrl: "FinTS address",
    factLogin: "Login name",
    loginUnreadableLead: "can no longer be read",
    loginUnreadableText: ": the server's key was changed. Choose “Change login” and enter the login name again.",
    factSlot: "Login",
    quotedSlot: (slot: string) => `“${slot}”`,
    factApproval: "Approval",
    approvalLast: (last: string, until: string) => `last on ${last}, valid until about ${until} (estimated)`,
    approvalNone: (approval: string) => `none yet: on the first sync the bank asks for an approval (“${approval}”)`,
    factLastSync: "Last sync",
    linesUntil: (date: string) => `, transactions up to ${date}`,
    noSyncYet: "none yet",
    autoSyncHint:
      "Hausbuch asks the bank every morning around half past seven (around half past eight in summer) and fetches only the transactions since the last sync, starting two weeks earlier to be safe; Hausbuch recognises the ones already there and does not book them twice. If the bank asks for an approval for this, a card appears under “To review”; until then Hausbuch does not sync this account automatically, so no further requests reach your phone.",
    autoSyncWaits:
      "Not possible yet: this bank (DKB) may ask for an approval in the DKB app on every sync, and then a request would reach your phone every morning. The daily sync can be switched on once two manual syncs in a row have run without an approval. Until then use “Sync now” by hand or upload the CSV export.",
    fetchStatementsHint:
      "On every sync, after the transactions, Hausbuch also fetches the bank's statements as PDF that are still missing, by their number. They then appear in the list below and in the yearly archive under “kontoauszuege/”, as proof the Finanzamt (tax office) recognises. Bookings still come from the transactions only; a statement changes no booking. Hausbuch only asks for statements the bank has already created and never requests a new one.",
    refusal9370: "signing authority is not sufficient",
    refusal9380: "no authorisation for this order",
    refusalCode: (code: string) => `code ${code}`,
    refusalCodeMeaning: (code: string, meaning: string) => `code ${code}: ${meaning}`,
    statementsRefused: (bankName: string | null, code: string) => {
      const bank = bankName ?? "the bank";
      return `${bankName ?? "The bank"} does not allow this online banking login to fetch the statements as PDF (${code}). Only ${bank} can unlock it: ask them to enable fetching electronic statements as PDF over FinTS/HBCI (HKEKP) for this login and this account. The electronic mailbox alone is not enough. Until then switch this off; Hausbuch creates the proofs of payment in the yearly archive anyway. Once fetching works, this notice disappears.`;
    },
    bankStatements: "The bank's statements (PDF)",
    noStatementsYet: "None fetched yet. On the next sync Hausbuch asks the bank for its statements as PDF.",
    fetchedLast: (count: number) =>
      `${count === 1 ? "1 statement" : `${count} statements`} newly fetched on the last sync.`,
    statementNumber: (number: number, year: number) => `Statement ${number}/${year}`,
    fetchedAt: (when: string) => `fetched ${when}`,
    approvalSent: " The bank has sent an approval request for the sync to your phone.",
    timeLeft: (clock: string) => `${clock} minutes left; then Hausbuch cancels the sync.`,
    approvalExpired:
      "The approval was not confirmed within five minutes. Press “Sync again” and approve the new request in the app.",
    lastScheduled: "Last automatic sync",
    lastManual: "Last sync",
    stuck: (when: string) =>
      `The sync of ${when} is not making progress, for example after a server restart. Press “Sync again” to restart it.`,
    scheduledNeedsApproval: (approval: string) =>
      `The automatic sync needs an approval: press “Sync now”. ${approval}.`,
    scheduledAsked: (when: string) =>
      `The bank asked for an approval during the sync on ${when}; Hausbuch ended it because nobody is watching the phone. Press “Sync now” and approve the new request in the app straight away. Until then Hausbuch does not sync this account automatically.`,
    queued: "The sync has been started and begins shortly …",
    fetching: "Hausbuch is asking the bank for the transactions …",
    importing: "The bank's transactions are being imported …",
    doneLine: (last: string, when: string, lines: string) => `${last} ${when}: ${lines} imported.`,
    showNewLines: "Show the new transactions",
    nothingNew: (last: string, when: string) =>
      `${last} ${when}: nothing new, all of the bank's transactions were already there.`,
    balanceDrift:
      " But the bank's balance differs from the last reconciled balance: is a statement missing? See “To review”.",
    balanceMatches: " The bank's balance matches the last reconciled balance.",
    failedLine: (last: string, when: string) => `${last} ${when} failed:`,
    failedFallback: "The sync failed. Sync again.",
    resultFailedCode: (code: string) => `failed (${code})`,
    resultFailed: "failed",
    resultDrift: "nothing new, balance differs",
    balanceCompared: "compared with the bank's balance",
    recentRuns: "Recent syncs",
    colStarted: "Started",
    colPeriod: "Period",
    colResult: "Result",
    colBalance: "Balance",
    bankCodeHint:
      "8 digits: positions 5 to 12 of your IBAN, after “DE” and the two check digits. You can also paste the whole IBAN.",
    urlHint:
      "Starts with https://. Your Sparkasse names it on its website under “FinTS” or “HBCI PIN/TAN”; otherwise it is in the German banking industry's bank list (Deutsche Kreditwirtschaft).",
    loginHint:
      "The login name or legitimation ID you sign in to online banking with. Hausbuch stores it encrypted and shows it only shortened.",
    slotHint:
      "A short label for this online banking login, in lower case, e.g. “dkb” or “sparkasse_b”; “primary” is the rental account's login. Accounts under the same login carry the same label and use the same PIN.",
    slotInvalid: "Lower-case letters, digits and _ , starting with a letter, at most 32 characters (e.g. dkb).",
    urlRequired: "Please enter your bank's FinTS address.",
    urlHttps: "The FinTS address must start with https://.",
    bankCodeInvalid: "The BLZ (bank code) has exactly 8 digits.",
    loginRequired: "Please enter the login name.",
    setUp: "Set up bank sync",
    setUpIntro:
      "Hausbuch can fetch this account's transactions from the bank itself (FinTS, read only). On the first sync you approve it in your bank's app (Sparkasse: S-pushTAN app, DKB: DKB app).",
    slotLabel: "Login label",
    pinNoneNote:
      "The PIN is stored encrypted and never shown again afterwards. You can also leave the field empty and store the PIN later; without a PIN every sync ends with “PIN missing”.",
    loginRetype: "Please enter the login name again: the stored one can no longer be read.",
    editNote: (approval: string) =>
      `A new address or a new login name discards the stored connection: the next sync asks for an approval again (“${approval}”). A new login label only changes which PIN the sync uses. You change the PIN itself on the card with “Change PIN”.`,
    newLogin: "New login name",
    keepLogin: (masked: string) => `Leave empty to keep ${masked}.`,
    loginUnreadableHint:
      "The stored login name can no longer be read (the server's key was changed); please enter it again.",
  },
});
